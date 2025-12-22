import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
import subprocess

class StreamNode(Node):
    def __init__(self):
        super().__init__('stream_node')

        # FFmpeg RTMP Streamer
        self.ffmpeg = subprocess.Popen(
            [
                'ffmpeg',
                '-f', 'image2pipe',
                '-vcodec', 'mjpeg',
                '-i', '-',                           # Read from ROS2 pipeline
                '-vcodec', 'libx264',
                '-preset', 'ultrafast',
                '-tune', 'zerolatency',
                '-pix_fmt', 'yuv420p',
                '-f', 'flv',
                'rtmp://3.6.139.35/live/stream'      # Your RTMP endpoint
            ],
            stdin=subprocess.PIPE
        )

        self.subscription = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.callback,
            10
        )

    def callback(self, msg):
        try:
            self.ffmpeg.stdin.write(msg.data)
        except Exception as e:
            self.get_logger().error(f"FFmpeg error: {e}")

def main(args=None):
    rclpy.init(args=args)
    node = StreamNode()
    rclpy.spin(node)
    node.destroy_node()
    rclpy.shutdown()

if __name__ == '__main__':
    main()