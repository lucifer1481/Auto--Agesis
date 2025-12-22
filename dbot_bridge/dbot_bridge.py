import rclpy
from rclpy.node import Node
from rclpy.parameter import Parameter
from rclpy.executors import MultiThreadedExecutor
from rclpy.callback_groups import ReentrantCallbackGroup
from rclpy.qos import QoSProfile, QoSDurabilityPolicy, QoSReliabilityPolicy
from nav_msgs.msg import OccupancyGrid
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav2_simple_commander.robot_navigator import BasicNavigator
import paho.mqtt.client as mqtt
import json
import ssl
import math
import time
import uuid
import zlib
import base64
import io
import threading
import requests
import numpy as np
from PIL import Image
# === NEW IMPORTS FOR RELOCALIZATION ===
import subprocess 
import os
import signal
from std_srvs.srv import Empty 
# ======================================

# ==========================================
# 🔧 CONFIGURATION
# ==========================================
MQTT_HOST = "ads53iebm3omb-ats.iot.ap-south-1.amazonaws.com"
MQTT_PORT = 8883
CA_PATH = "AmazonRootCA1.pem"
CERT_PATH = "device.pem.crt"
KEY_PATH = "private.pem.key"
# ==========================================

class DBotBridge(Node):
    # FIX: Corrected __init__ signature
    def __init__(self):
        # FIX: Corrected super().__init__
        super().__init__('dbot_bridge')
        
        self.callback_group = ReentrantCallbackGroup()
        self.set_parameters([Parameter('use_sim_time', Parameter.Type.BOOL, False)])
        self.navigator = BasicNavigator()

        # --- MQTT CONNECTION ---
        client_id = f"real-dbot-{uuid.uuid4()}"
        self.client = mqtt.Client(client_id=client_id, callback_api_version=mqtt.CallbackAPIVersion.VERSION2)
        self.client.tls_set(ca_certs=CA_PATH, certfile=CERT_PATH, keyfile=KEY_PATH, tls_version=ssl.PROTOCOL_TLSv1_2)
        self.client.on_connect = self.on_mqtt_connect
        self.client.on_message = self.on_mqtt_message
        self.client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        self.client.loop_start()

        # --- SUBSCRIBERS ---
        map_qos_profile = QoSProfile(
            depth=1,
            durability=QoSDurabilityPolicy.TRANSIENT_LOCAL,
            reliability=QoSReliabilityPolicy.RELIABLE
        )
        
        self.create_subscription(OccupancyGrid, '/map', self.map_callback, map_qos_profile, callback_group=self.callback_group)
        self.create_subscription(PoseWithCovarianceStamped, '/amcl_pose', self.pose_callback, 10, callback_group=self.callback_group)

        self.last_map_time = 0
        self.current_map_info = None 
        self.custom_patrol_path = [] 
        self.get_logger().info("🚀 REAL D-Bot Bridge Started!")

    # ---------------------------------------------------------
    # 1. SEND POSITION (RAW - CORRECTED)
    # ---------------------------------------------------------
    def pose_callback(self, msg):
        # 🔥 FIX: Send RAW coordinates. Do NOT modify Y here.
        x = msg.pose.pose.position.x
        y = msg.pose.pose.position.y
        
        q = msg.pose.pose.orientation
        siny_cosp = 2 * (q.w * q.z + q.x * q.y)
        cosy_cosp = 1 - 2 * (q.y * q.y + q.z * q.z)
        angle_deg = math.degrees(math.atan2(siny_cosp, cosy_cosp))

        payload = {"x": x, "y": y, "angle": angle_deg}
        self.client.publish("robot/telemetry/pose", json.dumps(payload), qos=0)

    # ---------------------------------------------------------
    # 2. SEND MAP (VERTICAL FLIP ONLY)
    # ---------------------------------------------------------
    def map_callback(self, msg):
        self.current_map_info = msg.info
        if time.time() - self.last_map_time < 2.0:
            return
        self.last_map_time = time.time()

        self.get_logger().info("🗺 Processing Map...")
        
        try:
            arr = np.array(msg.data, dtype=np.int8)
            grid = arr.reshape(msg.info.height, msg.info.width)

            # ---------------------------------------------------------
            # 🔥 FIX: VERTICAL FLIP ONLY
            # This fixes "Upside Down". Left/Right remains standard.
            grid_fixed = np.flipud(grid) 
            # ---------------------------------------------------------

            map_bytes = grid_fixed.tobytes()
            compressed = zlib.compress(map_bytes)
            encoded_data = base64.b64encode(compressed).decode('utf-8')

            payload = {
                "info": {
                    "width": msg.info.width,
                    "height": msg.info.height,
                    "resolution": msg.info.resolution,
                    "origin": {
                        # 🔥 FIX: Send the REAL origin from ROS. Do NOT overwrite with 0.0.
                        "x": msg.info.origin.position.x, 
                        "y": msg.info.origin.position.y  
                    }
                },
                "data": encoded_data,
                "format": "zlib_base64"
            }
            self.client.publish("robot/telemetry/map", json.dumps(payload), qos=1)
            self.get_logger().info("✅ Map Sent!")
            
        except Exception as e:
            self.get_logger().error(f"❌ Error: {e}")

    # ---------------------------------------------------------
    # 3. COMMAND HANDLER (Updated to include relocalize)
    # ---------------------------------------------------------
    def on_mqtt_connect(self, client, userdata, flags, reason_code, properties):
        self.get_logger().info("✅ MQTT Connected!")
        client.subscribe("robot/command/#")

    def on_mqtt_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            command = data.get("command")
            
            if command == "navigate":
                gx = float(data["goal"]["x"])
                gy = float(data["goal"]["y"])
                threading.Thread(target=self.go_to_point, args=(gx, gy), daemon=True).start()
            elif command == "start":
                if "points" in data: self.custom_patrol_path = data["points"]
                threading.Thread(target=self.start_patrol_route, daemon=True).start()
            elif command == "stop":
                self.stop_robot()
            elif command == "upload_map":
                threading.Thread(target=self.process_uploaded_route, args=(data,), daemon=True).start()
            # === NEW COMMAND HANDLER ===
            elif command == "relocalize":
                threading.Thread(target=self.relocalize_robot, daemon=True).start()
            # ===========================
        except Exception as e:
            self.get_logger().error(f"Error: {e}")

    # ---------------------------------------------------------
    # 4. NAVIGATION ACTIONS (Updated to include relocalize)
    # ---------------------------------------------------------

    # === NEW RELOCALIZATION ACTION ===
    def relocalize_robot(self):
        """Executes the sequence of ROS commands for global relocalization."""
        self.get_logger().warn("🚨 Starting Kidnapped Robot Relocalization process...")

        # 0) Sanity check and cancel any running navigation task
        self.stop_robot()
        
        # Helper function to run terminal commands
        def run_cmd(cmd, shell=True, ignore_error=False):
            try:
                subprocess.run(cmd, shell=shell, check=not ignore_error, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                if not ignore_error:
                    self.get_logger().warn(f"Command failed: {cmd.split('|')[0]}")
            except Exception as e:
                self.get_logger().error(f"Error running subprocess: {e}")

        # 1) Mute joystick teleop to prevent override
        self.get_logger().info("1) Muting joystick teleop...")
        run_cmd("pkill -f teleop_twist_joy || true", ignore_error=True)
        run_cmd("pkill -f 'ros2 run joy' || pkill -f joy_node || true", ignore_error=True)
        run_cmd("pkill -f 'dbot.*joystick.launch.py' || true", ignore_error=True)

        # 2) Scatter AMCL particles over the whole map (Global Localization Reset)
        self.get_logger().info("2) Scattering AMCL particles (Calling /reinitialize_global_localization)...")
        # We use a direct service call since we are in a ROS 2 node
        try:
            reinit_cli = self.create_client(Empty, '/reinitialize_global_localization')
            if reinit_cli.wait_for_service(timeout_sec=3.0):
                future = reinit_cli.call_async(Empty.Request())
                self.get_logger().info("   /reinitialize_global_localization service called.")
            else:
                self.get_logger().error("   /reinitialize_global_localization service not available.")
        except Exception as e:
            self.get_logger().error(f"   Error calling service: {e}")

        # 3) Spin in place using cmd_vel_joy topic
        self.get_logger().info("3) Starting slow spin for 100 seconds to gather features...")
        spin_command = "ros2 topic pub /cmd_vel_joy geometry_msgs/msg/Twist \"{angular: {z: 0.20}}\" -r 10 &"
        
        # Use Popen to run the background process and capture PID
        try:
            # os.setsid ensures the process group is separate, making it easier to kill later
            spin_process = subprocess.Popen(spin_command, shell=True, preexec_fn=os.setsid)
            spin_pid = spin_process.pid
        except Exception as e:
            self.get_logger().error(f"   Failed to start spin process: {e}")
            return

        # 4) Let it rotate for a fixed time to converge 
        time.sleep(100) # Wait for 100 seconds
        
        # 5) Stop the spin process and publish hard stop
        self.get_logger().info("5) Stopping spin and publishing hard stop...")
        
        # Kill the entire process group started in step 3
        try:
            os.killpg(os.getpgid(spin_pid), signal.SIGTERM)
        except Exception:
            # Fallback for systems where process group killing fails or PID is gone
            pass
            
        # Hard stop command
        run_cmd("ros2 topic pub /cmd_vel_joy geometry_msgs/msg/Twist \"{}\" -1")

        # 7) Bring joystick back 
        self.get_logger().info("7) Re-enabling joystick (using launch file)...")
        # Run in background
        run_cmd("ros2 launch dbot joystick.launch.py &", ignore_error=True)

        self.get_logger().warn("🎉 Robot Relocalization Attempt Finished.")
    # ==================================

    def go_to_point(self, x, y):
        self.navigator.cancelTask()
        goal = PoseStamped()
        goal.header.frame_id = 'map'
        goal.header.stamp = self.navigator.get_clock().now().to_msg()
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.orientation.w = 1.0
        self.navigator.goToPose(goal)
        while not self.navigator.isTaskComplete(): time.sleep(0.1)

    def stop_robot(self):
        self.navigator.cancelTask()

    def start_patrol_route(self):
        self.navigator.cancelTask()
        path = []
        points = self.custom_patrol_path if self.custom_patrol_path else [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
        for pt in points:
            p = PoseStamped()
            p.header.frame_id = 'map'
            p.pose.position.x = float(pt[0])
            p.pose.position.y = float(pt[1])
            p.pose.orientation.w = 1.0
            path.append(p)
        self.navigator.followWaypoints(path)
        while not self.navigator.isTaskComplete(): time.sleep(0.1)

    def process_uploaded_route(self, data):
        try:
            url = data.get('get_url')
            if not url or self.current_map_info is None: return
            resp = requests.get(url)
            img = Image.open(io.BytesIO(resp.content)).convert('RGB')
            pixels = img.load(); w, h = img.size
            self.custom_patrol_path = []
            res = self.current_map_info.resolution
            ox = self.current_map_info.origin.position.x
            oy = self.current_map_info.origin.position.y
            for x in range(0, w, 5):
                for y in range(0, h, 5):
                    r, g, b = pixels[x, y]
                    if g > 150 and r < 100 and b < 100:
                        self.custom_patrol_path.append([(x * res) + ox, ((h - y) * res) + oy])
            self.get_logger().info(f"✅ Route Processed.")
        except Exception as e: self.get_logger().error(f"Route Error: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = DBotBridge()
    executor = MultiThreadedExecutor()
    executor.add_node(node)
    try: executor.spin()
    except KeyboardInterrupt: pass
    finally: node.destroy_node(); rclpy.shutdown()

if __name__ == '__main__': main()