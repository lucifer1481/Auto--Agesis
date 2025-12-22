#!/usr/bin/env python3
# D-Bot — Face detection, recognition, cloud alerts, and TTS announcements
# ROS2 version: subscribes to /camera/image_raw/compressed instead of using cv2.VideoCapture

import os
import cv2
import numpy as np
import face_recognition
from ultralytics import YOLO
import pyttsx3
import time
import boto3
import uuid
import json
import threading
import traceback
import re
from datetime import datetime, timedelta, timezone
from boto3.dynamodb.conditions import Attr
import queue  # fixed: queue lives here

# ROS2 imports
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import CompressedImage
from std_msgs.msg import Bool
from geometry_msgs.msg import Twist

# ======================== CONFIG / CONSTANTS ========================
# AWS placeholders (replace with your real creds or use env vars)
AWS_ACCESS_KEY = "AKIASA6MXWGTHHCJI3QO"
AWS_SECRET_KEY = "7w3v4q1PnpYjInq8PzkrpggzgIMd01uXyY385jYw"
AWS_REGION = "ap-south-1"
BUCKET_NAME = "dbot-alert-images"
DYNAMO_TABLE = "Alerts"

# Device / model constants
FACE_DB_DIR = "dataset"
FACE_DISTANCE_THRESHOLD = 0.45
FRAME_PROCESS_INTERVAL = 5
FACE_MIN_AREA = 16000           # minimal person bbox area to attempt face recognition (px^2)
UNKNOWN_DEDUP_WINDOW = 30.0     # seconds (dedupe unknown alerts)
ALERT_COOLDOWN = 60             # seconds between AWS uploads for unknown person

# TTS tuning
TTS_RATE = 120                  
GLOBAL_COOLDOWN = 2.0           
PER_TRACK_COOLDOWN_MULT = 5.0   

# =========================================================
# AWS clients
# =========================================================
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

dynamodb = boto3.resource(
    "dynamodb",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)
alerts_table = dynamodb.Table(DYNAMO_TABLE)

# =========================================================
# Utilities: timeframe parsing & counting 
# =========================================================
unknown_face_timestamps_session = []

def parse_timeframe_from_text(text):
    now = datetime.now(timezone.utc)
    text = text.lower()

    if "today" in text:
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        return int(start.timestamp()), int(now.timestamp())

    if "this week" in text:
        start = now - timedelta(days=7)
        return int(start.timestamp()), int(now.timestamp())

    m = re.search(r"last\s+(\d+)\s+days", text)
    if m:
        n = int(m.group(1))
        start = now - timedelta(days=n)
        return int(start.timestamp()), int(now.timestamp())

    return None, None

def count_unknown_faces_between(start_ts, end_ts):
    try:
        resp = alerts_table.scan(
            FilterExpression=Attr('alertType').eq('unknown_person_detected'),
            ProjectionExpression='timestamp'
        )
        items = resp.get('Items', [])

        while 'LastEvaluatedKey' in resp:
            resp = alerts_table.scan(
                FilterExpression=Attr('alertType').eq('unknown_person_detected'),
                ProjectionExpression='timestamp',
                ExclusiveStartKey=resp['LastEvaluatedKey']
            )
            items.extend(resp.get('Items', []))

        count = 0
        for it in items:
            try:
                ts = int(it.get('timestamp', '0'))
            except:
                continue
            if start_ts <= ts <= end_ts:
                count += 1
        return count

    except Exception:
        return sum(1 for ts in unknown_face_timestamps_session if start_ts <= ts <= end_ts)

# =========================================================
# AWS UPLOAD / ALERTS
# =========================================================
def upload_to_s3(image_path):
    try:
        filename = f"alert_{int(time.time())}.jpg"
        s3.upload_file(image_path, BUCKET_NAME, filename, ExtraArgs={'ACL': 'public-read'})
        return f"https://{BUCKET_NAME}.s3.{AWS_REGION}.amazonaws.com/{filename}"
    except Exception as e:
        print("❌ S3 Upload Error:", e)
        return None

def send_to_dynamodb(alert_type, image_url):
    try:
        item = {
            "alert_id": str(uuid.uuid4()),
            "timestamp": str(int(time.time())),
            "deviceId": "camera01",
            "alertType": alert_type,
            "message": "Unknown person detected",
            "image_url": image_url,
            "status": "new"
        }
        alerts_table.put_item(Item=item)
    except Exception as e:
        print("❌ DynamoDB Error:", e)

def send_aws_alert(image_path):
    url = upload_to_s3(image_path)
    if url:
        send_to_dynamodb("unknown_person_detected", url)

# =========================================================
# LOAD YOLO + FACE DB
# =========================================================
print("✅ Loading YOLOv8 model...")
yolo_model = YOLO("yolov8n.pt")

print("🧠 Loading known faces...")
known_face_encodings = []
known_face_names = []

if os.path.isdir(FACE_DB_DIR):
    for person_name in os.listdir(FACE_DB_DIR):
        folder = os.path.join(FACE_DB_DIR, person_name)
        if not os.path.isdir(folder):
            continue
        for file in os.listdir(folder):
            if file.lower().endswith((".jpg", ".jpeg", ".png")):
                img_path = os.path.join(folder, file)
                try:
                    image = face_recognition.load_image_file(img_path)
                    enc = face_recognition.face_encodings(image)
                    if len(enc) > 0:
                        known_face_encodings.append(enc[0])
                        known_face_names.append(person_name)
                except:
                    pass

print("Faces loaded:", len(known_face_names))

# =========================================================
# TTS SYSTEM
# =========================================================
tts_queue = queue.Queue()
engine = pyttsx3.init()
engine.setProperty('rate', TTS_RATE)

_tts_worker_running = True
_tts_thread = None

def sanitize_for_speech(text):
    if not text:
        return ""
    s = str(text)
    s = re.sub(r'[\r\n\t]+', ' ', s)
    s = re.sub(r'[^0-9A-Za-z\s]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def _tts_worker():
    while _tts_worker_running:
        item = tts_queue.get()
        if item is None:
            break
        txt = item.get("text", "")
        try:
            engine.say(txt)
            engine.runAndWait()
        except:
            pass
        tts_queue.task_done()

_tts_thread = threading.Thread(target=_tts_worker, daemon=True)
_tts_thread.start()

last_speak_time_global = 0
per_track_last_speak = {}

def speak(text, force=False, track_id=None):
    global last_speak_time_global
    now = time.time()

    if not force:
        if now - last_speak_time_global < GLOBAL_COOLDOWN:
            return
        if track_id is not None:
            last_track = per_track_last_speak.get(track_id, 0)
            if now - last_track < GLOBAL_COOLDOWN * PER_TRACK_COOLDOWN_MULT:
                return

    last_speak_time_global = now
    if track_id is not None:
        per_track_last_speak[track_id] = now

    clean = sanitize_for_speech(text)
    if clean:
        tts_queue.put({"text": clean})
# =========================================================
# FACE RECOGNITION HELPER
# =========================================================
def recognize_face_from_person_roi(person_roi):
    if person_roi is None or person_roi.size == 0:
        return "Human Detected"

    try:
        rgb_small = cv2.cvtColor(person_roi, cv2.COLOR_BGR2RGB)
    except:
        return "Human Detected"

    face_locs = face_recognition.face_locations(rgb_small, model="hog")
    if not face_locs:
        return "Human Detected"

    encs = face_recognition.face_encodings(rgb_small, face_locs)
    if not encs:
        return "Human Detected"

    enc = encs[0]

    if len(known_face_encodings) == 0:
        return "Unknown Face"

    try:
        distances = face_recognition.face_distance(known_face_encodings, enc)
        idx = np.argmin(distances)

        if distances[idx] < FACE_DISTANCE_THRESHOLD:
            return known_face_names[idx]
        elif distances[idx] < FACE_DISTANCE_THRESHOLD + 0.05:
            return known_face_names[idx] + "*"

        return "Unknown Face"

    except:
        return "Unknown Face"


# =========================================================
# GLOBAL STATE
# =========================================================
frame_counter = 0
object_cache = {}
person_cache = {}
last_unknown_alert_time = 0
unknown_seen = {}

GLOBAL_VISION_NODE = None
tracked_person_id = None
tracked_person_last_seen = 0.0


def engage_person_lock(duration=30.0):
    global GLOBAL_VISION_NODE
    if GLOBAL_VISION_NODE is None:
        return

    node = GLOBAL_VISION_NODE
    now = time.time()

    if not node.person_lock_active:
        node.person_lock_active = True
        node.person_lock_until = now + duration
        node.person_lock_pub.publish(Bool(data=True))
        node.get_logger().info(f"🔒 Person lock engaged for {duration} seconds.")


def compute_tracking_twist(cx, img_w, max_angular=0.6):
    center_x = img_w // 2
    err_x = cx - center_x

    if center_x == 0:
        norm = 0.0
    else:
        norm = err_x / float(center_x)

    norm = max(-1.0, min(1.0, norm))

    twist = Twist()
    twist.linear.x = 0.0
    twist.angular.z = -norm * max_angular

    return twist


# =========================================================
# MAIN FRAME PROCESSOR
def process_frame_from_ros(frame):
    """
    One iteration of the original main loop, but using a frame provided
    by ROS2 instead of cv2.VideoCapture.
    """
    global frame_counter, object_cache, person_cache
    global last_unknown_alert_time, unknown_seen
    global tracked_person_id, tracked_person_last_seen

    frame_counter += 1
    img_h, img_w = frame.shape[:2]

    try:
        results = yolo_model.track(
            frame,
            verbose=False,
            persist=True,
            tracker="bytetrack.yaml"
        )[0]
    except Exception as e:
        print("⚠ YOLO track error, trying detect:", e)
        try:
            results = yolo_model(frame, verbose=False)[0]
        except Exception as ex:
            print("⚠ YOLO detect failed:", ex)
            return

    # periodic cleanup of caches
    if frame_counter % (FRAME_PROCESS_INTERVAL * 10) == 0:
        cutoff = time.time() - 10.0
        object_cache = {k: v for k, v in object_cache.items() if v[2] > cutoff}
        person_cache = {k: v for k, v in person_cache.items() if v[1] > cutoff}

    tracked_seen_this_frame = False

    for box in results.boxes:
        try:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
        except Exception:
            continue

        # clamp coords
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(img_w - 1, x2)
        y2 = min(img_h - 1, y2)

        label = results.names[int(box.cls[0])]
        track_id = int(box.id[0]) if box.id is not None else None
        cache_key = track_id if track_id is not None else f"{x1}_{y1}_{x2}_{y2}"

        if label == "person":
            h = y2 - y1
            w = x2 - x1
            bbox_area = w * h

            # center of this person box
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            now_ts = time.time()

            # build dedupe key for unknowns
            if track_id is not None:
                unknown_key = f"t_{track_id}"
            else:
                gx = (cx // 32) * 32
                gy = (cy // 32) * 32
                unknown_key = f"sp_{gx}_{gy}"

            # -------- CLOSE PERSON: try face recognition --------
            if bbox_area >= FACE_MIN_AREA:
                pc = person_cache.get(cache_key)
                identity = None

                if pc and now_ts - pc[1] < 3.0:
                    identity = pc[0]
                else:
                    person_roi = frame[y1:y2, x1:x2]
                    identity = recognize_face_from_person_roi(person_roi)
                    person_cache[cache_key] = (identity, now_ts)

                # === CASE 1: Unknown Face → STOP + LOCK + TRACK ===
                if identity == "Unknown Face":
                    # Engage lock ONLY now (not for generic humans)
                    engage_person_lock(duration=30.0)

                    # Tracking only while locked
                    if (GLOBAL_VISION_NODE is not None and
                        GLOBAL_VISION_NODE.person_lock_active and
                        track_id is not None):

                        global tracked_person_id, tracked_person_last_seen
                        if tracked_person_id is None:
                            tracked_person_id = track_id
                            tracked_person_last_seen = now_ts
                        elif tracked_person_id == track_id:
                            tracked_person_last_seen = now_ts

                        if tracked_person_id == track_id:
                            twist = compute_tracking_twist(cx, img_w)
                            GLOBAL_VISION_NODE.tracking_pub.publish(twist)
                            tracked_seen_this_frame = True

                    # Unknown face alerts / TTS (same as before)
                    last_seen = unknown_seen.get(unknown_key, 0)
                    if now_ts - last_seen > UNKNOWN_DEDUP_WINDOW:
                        unknown_face_timestamps_session.append(int(now_ts))
                        unknown_seen[unknown_key] = now_ts

                        speak("Unknown face detected", track_id=cache_key)

                        if now_ts - last_unknown_alert_time > ALERT_COOLDOWN:
                            try:
                                cv2.imwrite("alert_unknown.jpg", person_roi)
                                send_aws_alert("alert_unknown.jpg")
                                last_unknown_alert_time = now_ts
                            except Exception as e:
                                print("⚠ Failed to save/send alert:", e)
                                traceback.print_exc()

                    color = (0, 0, 255)
                    text = "Unknown Face"

                # === CASE 2: Human Detected (no face) → NO STOP ===
                elif identity == "Human Detected":
                    # Do NOT lock, do NOT stop robot.
                    # Still announce if you want, but navigation continues.
                    speak("Human detected", track_id=cache_key)
                    color = (255, 255, 0)
                    text = "Human Detected"

                # === CASE 3: Known person → STOP + LOCK + TRACK ===
                else:
                    # Here identity is a known name or name+"*"
                    engage_person_lock(duration=30.0)

                    if (GLOBAL_VISION_NODE is not None and
                        GLOBAL_VISION_NODE.person_lock_active and
                        track_id is not None):

                     
                        if tracked_person_id is None:
                            tracked_person_id = track_id
                            tracked_person_last_seen = now_ts
                        elif tracked_person_id == track_id:
                            tracked_person_last_seen = now_ts

                        if tracked_person_id == track_id:
                            twist = compute_tracking_twist(cx, img_w)
                            GLOBAL_VISION_NODE.tracking_pub.publish(twist)
                            tracked_seen_this_frame = True

                    speak(f"{identity} detected", track_id=cache_key)
                    color = (0, 255, 0)
                    text = f"{identity}"

            # -------- FAR PERSON: no lock, just info --------
            else:
                last_seen = unknown_seen.get(unknown_key, 0)
                if now_ts - last_seen > UNKNOWN_DEDUP_WINDOW:
                    unknown_seen[unknown_key] = now_ts
                    speak("Human detected cannot confirm identity", track_id=cache_key)

                color = (0, 165, 255)
                text = "Human (Far)"

        else:
            # NON-PERSON OBJECTS
            now_ts = time.time()
            if frame_counter % FRAME_PROCESS_INTERVAL == 0 or cache_key not in object_cache:
                text = label
                color = (255, 255, 0)
                object_cache[cache_key] = (text, color, now_ts)
            else:
                text, color, _ = object_cache.get(
                    cache_key,
                    ("Processing...", (128, 128, 128))
                )

        # Draw box + label
        try:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                text,
                (x1, max(15, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2
            )
        except Exception:
            pass

    # If locked but tracked person not seen this frame → stop spinning
    if GLOBAL_VISION_NODE is not None and GLOBAL_VISION_NODE.person_lock_active:
        if tracked_person_id is not None and not tracked_seen_this_frame:
            zero = Twist()
            GLOBAL_VISION_NODE.tracking_pub.publish(zero)

    # cleanup old unknown_seen entries
    now_cleanup = time.time()
    to_delete = [
        k for k, v in unknown_seen.items()
        if now_cleanup - v > (UNKNOWN_DEDUP_WINDOW * 4)
    ]
    for k in to_delete:
        del unknown_seen[k]

    # Show debug window
    cv2.imshow("D-Bot Security - Face Detection (ROS)", frame)
    cv2.waitKey(1)
def process_frame_from_ros(frame):
    """
    One iteration of the original main loop, but using a frame provided
    by ROS2 instead of cv2.VideoCapture.
    """
    global frame_counter, object_cache, person_cache
    global last_unknown_alert_time, unknown_seen
    global tracked_person_id, tracked_person_last_seen

    frame_counter += 1
    img_h, img_w = frame.shape[:2]

    try:
        results = yolo_model.track(
            frame,
            verbose=False,
            persist=True,
            tracker="bytetrack.yaml"
        )[0]
    except Exception as e:
        print("⚠ YOLO track error, trying detect:", e)
        try:
            results = yolo_model(frame, verbose=False)[0]
        except Exception as ex:
            print("⚠ YOLO detect failed:", ex)
            return

    # periodic cleanup of caches
    if frame_counter % (FRAME_PROCESS_INTERVAL * 10) == 0:
        cutoff = time.time() - 10.0
        object_cache = {k: v for k, v in object_cache.items() if v[2] > cutoff}
        person_cache = {k: v for k, v in person_cache.items() if v[1] > cutoff}

    tracked_seen_this_frame = False

    for box in results.boxes:
        try:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
        except Exception:
            continue

        # clamp coords
        x1 = max(0, x1)
        y1 = max(0, y1)
        x2 = min(img_w - 1, x2)
        y2 = min(img_h - 1, y2)

        label = results.names[int(box.cls[0])]
        track_id = int(box.id[0]) if box.id is not None else None
        cache_key = track_id if track_id is not None else f"{x1}_{y1}_{x2}_{y2}"

        if label == "person":
            h = y2 - y1
            w = x2 - x1
            bbox_area = w * h

            # center of this person box
            cx = (x1 + x2) // 2
            cy = (y1 + y2) // 2
            now_ts = time.time()

            # build dedupe key for unknowns
            if track_id is not None:
                unknown_key = f"t_{track_id}"
            else:
                gx = (cx // 32) * 32
                gy = (cy // 32) * 32
                unknown_key = f"sp_{gx}_{gy}"

            # -------- CLOSE PERSON: try face recognition --------
            if bbox_area >= FACE_MIN_AREA:
                pc = person_cache.get(cache_key)
                identity = None

                if pc and now_ts - pc[1] < 3.0:
                    identity = pc[0]
                else:
                    person_roi = frame[y1:y2, x1:x2]
                    identity = recognize_face_from_person_roi(person_roi)
                    person_cache[cache_key] = (identity, now_ts)

                # === CASE 1: Unknown Face → STOP + LOCK + TRACK ===
                if identity == "Unknown Face":
                    # Engage lock ONLY now (not for generic humans)
                    engage_person_lock(duration=30.0)

                    # Tracking only while locked
                    if (GLOBAL_VISION_NODE is not None and
                        GLOBAL_VISION_NODE.person_lock_active and
                        track_id is not None):

                        global tracked_person_id, tracked_person_last_seen
                        if tracked_person_id is None:
                            tracked_person_id = track_id
                            tracked_person_last_seen = now_ts
                        elif tracked_person_id == track_id:
                            tracked_person_last_seen = now_ts

                        if tracked_person_id == track_id:
                            twist = compute_tracking_twist(cx, img_w)
                            GLOBAL_VISION_NODE.tracking_pub.publish(twist)
                            tracked_seen_this_frame = True

                    # Unknown face alerts / TTS (same as before)
                    last_seen = unknown_seen.get(unknown_key, 0)
                    if now_ts - last_seen > UNKNOWN_DEDUP_WINDOW:
                        unknown_face_timestamps_session.append(int(now_ts))
                        unknown_seen[unknown_key] = now_ts

                        speak("Unknown face detected", track_id=cache_key)

                        if now_ts - last_unknown_alert_time > ALERT_COOLDOWN:
                            try:
                                cv2.imwrite("alert_unknown.jpg", person_roi)
                                send_aws_alert("alert_unknown.jpg")
                                last_unknown_alert_time = now_ts
                            except Exception as e:
                                print("⚠ Failed to save/send alert:", e)
                                traceback.print_exc()

                    color = (0, 0, 255)
                    text = "Unknown Face"

                # === CASE 2: Human Detected (no face) → NO STOP ===
                elif identity == "Human Detected":
                    # Do NOT lock, do NOT stop robot.
                    # Still announce if you want, but navigation continues.
                    speak("Human detected", track_id=cache_key)
                    color = (255, 255, 0)
                    text = "Human Detected"

                # === CASE 3: Known person → STOP + LOCK + TRACK ===
                else:
                    # Here identity is a known name or name+"*"
                    engage_person_lock(duration=30.0)

                    if (GLOBAL_VISION_NODE is not None and
                        GLOBAL_VISION_NODE.person_lock_active and
                        track_id is not None):

                        if tracked_person_id is None:
                            tracked_person_id = track_id
                            tracked_person_last_seen = now_ts
                        elif tracked_person_id == track_id:
                            tracked_person_last_seen = now_ts

                        if tracked_person_id == track_id:
                            twist = compute_tracking_twist(cx, img_w)
                            GLOBAL_VISION_NODE.tracking_pub.publish(twist)
                            tracked_seen_this_frame = True

                    speak(f"{identity} detected", track_id=cache_key)
                    color = (0, 255, 0)
                    text = f"{identity}"

            # -------- FAR PERSON: no lock, just info --------
            else:
                last_seen = unknown_seen.get(unknown_key, 0)
                if now_ts - last_seen > UNKNOWN_DEDUP_WINDOW:
                    unknown_seen[unknown_key] = now_ts
                    speak("Human detected cannot confirm identity", track_id=cache_key)

                color = (0, 165, 255)
                text = "Human (Far)"

        else:
            # NON-PERSON OBJECTS
            now_ts = time.time()
            if frame_counter % FRAME_PROCESS_INTERVAL == 0 or cache_key not in object_cache:
                text = label
                color = (255, 255, 0)
                object_cache[cache_key] = (text, color, now_ts)
            else:
                text, color, _ = object_cache.get(
                    cache_key,
                    ("Processing...", (128, 128, 128))
                )

        # Draw box + label
        try:
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
            cv2.putText(
                frame,
                text,
                (x1, max(15, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2
            )
        except Exception:
            pass

    # If locked but tracked person not seen this frame → stop spinning
    if GLOBAL_VISION_NODE is not None and GLOBAL_VISION_NODE.person_lock_active:
        if tracked_person_id is not None and not tracked_seen_this_frame:
            zero = Twist()
            GLOBAL_VISION_NODE.tracking_pub.publish(zero)

    # cleanup old unknown_seen entries
    now_cleanup = time.time()
    to_delete = [
        k for k, v in unknown_seen.items()
        if now_cleanup - v > (UNKNOWN_DEDUP_WINDOW * 4)
    ]
    for k in to_delete:
        del unknown_seen[k]

    # Show debug window
    cv2.imshow("D-Bot Security - Face Detection (ROS)", frame)
    cv2.waitKey(1)
# =========================================================
# ROS2 NODE: CAMERA SUB + PERSON LOCK / TRACKING PUBS
# =========================================================
class DbotVisionNode(Node):
    def __init__(self):
        super().__init__('dbot_vision_node')

        # Subscribe to compressed camera images
        self.subscription = self.create_subscription(
            CompressedImage,
            '/camera/image_raw/compressed',
            self.image_callback,
            10
        )
        self.get_logger().info("Subscribed to /camera/image_raw/compressed")

        # Person lock publisher & state
        self.person_lock_pub = self.create_publisher(Bool, '/person_lock', 10)
        self.person_lock_active = False
        self.person_lock_until = 0.0

        # Tracking velocity publisher (for rotation only)
        self.tracking_pub = self.create_publisher(Twist, '/diff_cont/cmd_vel_unstamped', 10)

        # Timer to check when to auto-release lock
        self.create_timer(0.1, self._person_lock_timer_cb)

    def _person_lock_timer_cb(self):
        global tracked_person_id
        now = time.time()
        if self.person_lock_active and now >= self.person_lock_until:
            self.person_lock_active = False
            self.person_lock_pub.publish(Bool(data=False))
            tracked_person_id = None
            self.get_logger().info("🔓 Person lock released after timeout.")

    def image_callback(self, msg: CompressedImage):
        try:
            np_arr = np.frombuffer(msg.data, np.uint8)
            frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
            if frame is None:
                self.get_logger().warn("Failed to decode compressed image")
                return
            process_frame_from_ros(frame)
        except Exception as e:
            self.get_logger().error(f"Error in image_callback: {e}")
            traceback.print_exc()


# =========================================================
# CLEANUP HELPERS
# =========================================================
def shutdown_tts_and_opencv():
    global _tts_worker_running
    try:
        _tts_worker_running = False
        try:
            tts_queue.put(None)
        except Exception:
            pass
        try:
            if _tts_thread is not None:
                _tts_thread.join(timeout=2)
        except Exception:
            pass
    except Exception:
        pass

    try:
        cv2.destroyAllWindows()
    except Exception:
        pass


# =========================================================
# MAIN (ROS2 ENTRY POINT)
# =========================================================
def main(args=None):
    global GLOBAL_VISION_NODE

    print("🚀 D-Bot (ROS2 compressed image) Running...")
    rclpy.init(args=args)
    node = DbotVisionNode()
    GLOBAL_VISION_NODE = node

    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        print("⏹️ Exiting on user interrupt...")
    except Exception as e:
        print("❌ Unhandled exception in ROS spin:", e)
        traceback.print_exc()
    finally:
        node.destroy_node()
        rclpy.shutdown()
        shutdown_tts_and_opencv()
        print("✅ D-Bot stopped.")


if __name__ == '__main__':
    main()