import cv2
import time
import requests
from ultralytics import YOLO


# ============================================================
# Raspberry Pi connection
# ============================================================

PI_IP = "192.168.0.132"

CAMERA_STREAM_URL = f"http://{PI_IP}:8000/stream.mjpg"
ROBOT_MOVE_URL = f"http://{PI_IP}:5000/move"
ROBOT_STOP_URL = f"http://{PI_IP}:5000/stop"


# ============================================================
# YOLO settings
# ============================================================

MODEL_PATH = "yolov8n.pt"

# Lower = detects more easily, but more false detections.
CONFIDENCE_THRESHOLD = 0.40

# Keep 640 for speed. The stream can be 1296x972, YOLO will resize internally.
IMAGE_SIZE = 640

# COCO class ID for person
PERSON_CLASS_ID = 0


# ============================================================
# Robot behavior settings
# ============================================================

# Keep speed low while testing.
MAX_FORWARD_SPEED = 18
MIN_FORWARD_SPEED = 7

# Steering limit. PiCar-X usually works well around +/-30 degrees.
MAX_STEER_ANGLE = 30

# These are based on bounding box height / image height.
# When a person is far, the box height ratio is small.
# When a person is close, the box height ratio is large.
START_MOVING_BOX_RATIO = 0.48
STOP_NEAR_BOX_RATIO = 0.58
TOO_CLOSE_BOX_RATIO = 0.70

# Steering deadzone. Prevents jitter when person is almost centered.
CENTER_DEADZONE = 0.06

# Steering proportional gain.
# Increase if robot turns too slowly.
# Decrease if robot oversteers.
STEER_KP = 38.0

# Speed proportional gain.
SPEED_KP = 35.0

# Command rate limiting.
COMMAND_INTERVAL = 0.07

# Stop if person disappears.
NO_PERSON_STOP_TIME = 0.35

# Drop old frames to reduce lag.
DROP_OLD_FRAMES = True
FRAMES_TO_DROP = 2

# If steering direction is reversed, change this to True.
INVERT_STEERING = False


# ============================================================
# Internal state
# ============================================================

last_command_time = 0.0
last_person_seen_time = 0.0
last_sent_speed = None
last_sent_steer = None

smooth_steer = 0.0
smooth_speed = 0.0


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def send_robot_command(speed, steer):
    global last_command_time, last_sent_speed, last_sent_steer

    now = time.time()

    if now - last_command_time < COMMAND_INTERVAL:
        return

    speed = int(clamp(speed, -MAX_FORWARD_SPEED, MAX_FORWARD_SPEED))
    steer = int(clamp(steer, -MAX_STEER_ANGLE, MAX_STEER_ANGLE))

    if last_sent_speed == speed and last_sent_steer == steer:
        if now - last_command_time < 0.25:
            return

    try:
        requests.get(
            ROBOT_MOVE_URL,
            params={
                "speed": speed,
                "steer": steer
            },
            timeout=0.08
        )

        last_command_time = now
        last_sent_speed = speed
        last_sent_steer = steer

    except requests.RequestException:
        print("Warning: Could not send command to robot.")


def stop_robot(force=False):
    global last_command_time, last_sent_speed, last_sent_steer

    now = time.time()

    if not force and now - last_command_time < 0.15:
        return

    try:
        requests.get(ROBOT_STOP_URL, timeout=0.10)
    except requests.RequestException:
        pass

    last_command_time = now
    last_sent_speed = 0
    last_sent_steer = 0


def open_camera_stream():
    cap = cv2.VideoCapture(CAMERA_STREAM_URL)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open stream: {CAMERA_STREAM_URL}")

    return cap


def choose_person_box(result, frame_width, frame_height):
    """
    Select the person to follow.
    We choose the largest reliable person box, with a small preference for center.
    """

    best_box = None
    best_score = -1.0

    if result.boxes is None:
        return None

    for box in result.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])

        if cls_id != PERSON_CLASS_ID:
            continue

        if conf < CONFIDENCE_THRESHOLD:
            continue

        x1, y1, x2, y2 = box.xyxy[0].tolist()

        width = x2 - x1
        height = y2 - y1
        area = width * height

        if width <= 0 or height <= 0:
            continue

        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2

        image_center_x = frame_width / 2
        center_error = abs(center_x - image_center_x) / image_center_x

        # Prefer larger person, slightly prefer centered person.
        score = area * (1.0 - 0.25 * center_error)

        if score > best_score:
            best_score = score
            best_box = {
                "x1": x1,
                "y1": y1,
                "x2": x2,
                "y2": y2,
                "width": width,
                "height": height,
                "area": area,
                "center_x": center_x,
                "center_y": center_y,
                "conf": conf
            }

    return best_box


def compute_follow_command(person_box, frame_width, frame_height):
    """
    Convert person position and size into steering and speed.

    Behavior:
    - Person left  -> steer left
    - Person right -> steer right
    - Person far   -> move forward
    - Person near  -> stop
    - Person too close -> stop
    """

    global smooth_steer, smooth_speed

    image_center_x = frame_width / 2.0
    person_center_x = person_box["center_x"]

    # -1.0 = far left, +1.0 = far right
    horizontal_error = (person_center_x - image_center_x) / image_center_x

    if abs(horizontal_error) < CENTER_DEADZONE:
        horizontal_error = 0.0

    raw_steer = STEER_KP * horizontal_error

    if INVERT_STEERING:
        raw_steer = -raw_steer

    raw_steer = clamp(raw_steer, -MAX_STEER_ANGLE, MAX_STEER_ANGLE)

    box_height_ratio = person_box["height"] / frame_height

    if box_height_ratio >= TOO_CLOSE_BOX_RATIO:
        raw_speed = 0

    elif box_height_ratio >= STOP_NEAR_BOX_RATIO:
        # Person is close enough. Stop.
        raw_speed = 0

    elif box_height_ratio < START_MOVING_BOX_RATIO:
        # Person is far. Move forward.
        distance_error = START_MOVING_BOX_RATIO - box_height_ratio
        raw_speed = MIN_FORWARD_SPEED + SPEED_KP * distance_error
        raw_speed = clamp(raw_speed, MIN_FORWARD_SPEED, MAX_FORWARD_SPEED)

    else:
        # Between start and stop zone.
        raw_speed = MIN_FORWARD_SPEED

    # Smooth commands to avoid jerky motion.
    smooth_steer = 0.65 * smooth_steer + 0.35 * raw_steer
    smooth_speed = 0.70 * smooth_speed + 0.30 * raw_speed

    # If stopping, stop quickly.
    if raw_speed == 0:
        smooth_speed = 0

    speed = int(clamp(smooth_speed, 0, MAX_FORWARD_SPEED))
    steer = int(clamp(smooth_steer, -MAX_STEER_ANGLE, MAX_STEER_ANGLE))

    return speed, steer, horizontal_error, box_height_ratio


def draw_overlay(frame, person_box, speed, steer, horizontal_error, box_height_ratio, fps):
    frame_height, frame_width = frame.shape[:2]

    center_x = frame_width // 2
    deadzone_px = int(CENTER_DEADZONE * frame_width)

    cv2.line(frame, (center_x, 0), (center_x, frame_height), (255, 255, 255), 1)
    cv2.line(frame, (center_x - deadzone_px, 0), (center_x - deadzone_px, frame_height), (120, 120, 120), 1)
    cv2.line(frame, (center_x + deadzone_px, 0), (center_x + deadzone_px, frame_height), (120, 120, 120), 1)

    if person_box is not None:
        x1 = int(person_box["x1"])
        y1 = int(person_box["y1"])
        x2 = int(person_box["x2"])
        y2 = int(person_box["y2"])

        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

        label = f"PERSON {person_box['conf']:.2f}"
        cv2.putText(
            frame,
            label,
            (x1, max(30, y1 - 10)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2
        )

        cv2.circle(
            frame,
            (int(person_box["center_x"]), int(person_box["center_y"])),
            5,
            (0, 255, 0),
            -1
        )

    if person_box is None:
        mode = "NO PERSON - STOP"
    elif box_height_ratio >= STOP_NEAR_BOX_RATIO:
        mode = "PERSON NEAR - STOP"
    else:
        mode = "FOLLOWING"

    status_lines = [
        f"Mode: {mode}",
        f"YOLO FPS: {fps:.1f}",
        f"Speed: {speed}",
        f"Steer: {steer}",
        f"Horizontal error: {horizontal_error:.2f}",
        f"Box height ratio: {box_height_ratio:.2f}",
        "Q: quit | SPACE: emergency stop"
    ]

    y = 30
    for line in status_lines:
        cv2.putText(
            frame,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 255),
            2
        )
        y += 30

    return frame


def main():
    global last_person_seen_time

    print("Loading YOLO model...")
    model = YOLO(MODEL_PATH)

    print("Opening PiCar-X camera stream...")
    print(CAMERA_STREAM_URL)
    cap = open_camera_stream()

    print("Human follow mode started.")
    print("Press Q to quit.")
    print("Press SPACE for emergency stop.")

    prev_time = time.time()
    fps = 0.0

    stop_robot(force=True)

    try:
        while True:
            if DROP_OLD_FRAMES:
                for _ in range(FRAMES_TO_DROP):
                    cap.grab()

            ret, frame = cap.read()

            if not ret or frame is None:
                print("Lost camera stream. Reconnecting...")
                stop_robot(force=True)
                cap.release()
                time.sleep(1)
                cap = open_camera_stream()
                continue

            frame_height, frame_width = frame.shape[:2]

            results = model.predict(
                source=frame,
                imgsz=IMAGE_SIZE,
                conf=CONFIDENCE_THRESHOLD,
                verbose=False
            )

            result = results[0]
            person_box = choose_person_box(result, frame_width, frame_height)

            speed = 0
            steer = 0
            horizontal_error = 0.0
            box_height_ratio = 0.0

            if person_box is not None:
                last_person_seen_time = time.time()

                speed, steer, horizontal_error, box_height_ratio = compute_follow_command(
                    person_box,
                    frame_width,
                    frame_height
                )

                send_robot_command(speed, steer)

            else:
                if time.time() - last_person_seen_time > NO_PERSON_STOP_TIME:
                    stop_robot()

            now = time.time()
            dt = now - prev_time
            prev_time = now

            if dt > 0:
                fps = 0.9 * fps + 0.1 * (1.0 / dt)

            display_frame = draw_overlay(
                frame,
                person_box,
                speed,
                steer,
                horizontal_error,
                box_height_ratio,
                fps
            )

            cv2.imshow("PiCar-X Human Follow - YOLO", display_frame)

            key = cv2.waitKey(1) & 0xFF

            if key == ord("q"):
                break

            if key == 32:
                print("Emergency stop.")
                stop_robot(force=True)

    finally:
        print("Stopping robot and closing program...")
        stop_robot(force=True)
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()