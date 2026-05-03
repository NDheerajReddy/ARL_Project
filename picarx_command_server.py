#!/usr/bin/env python3

from flask import Flask, request, jsonify
from picarx import Picarx
import time
import threading
import signal
import sys


app = Flask(__name__)

px = Picarx()

# Safety settings
MAX_SPEED = 25          # Keep low for first tests
MAX_STEER_ANGLE = 30    # PiCar-X steering limit
COMMAND_TIMEOUT = 0.7   # Stop if no command received for this many seconds

last_command_time = time.time()
current_speed = 0
current_steer = 0
lock = threading.Lock()


def clamp(value, min_value, max_value):
    return max(min_value, min(max_value, value))


def apply_motion(speed, steer):
    """
    speed: -100 to +100
    steer: -30 to +30
    Positive speed = forward
    Negative speed = backward
    Negative steer = left
    Positive steer = right
    """

    speed = int(clamp(speed, -MAX_SPEED, MAX_SPEED))
    steer = int(clamp(steer, -MAX_STEER_ANGLE, MAX_STEER_ANGLE))

    px.set_dir_servo_angle(steer)

    if speed > 0:
        px.forward(speed)
    elif speed < 0:
        px.backward(abs(speed))
    else:
        px.stop()


@app.route("/")
def home():
    return jsonify({
        "status": "PiCar-X command server running",
        "endpoints": {
            "move": "/move?speed=15&steer=0",
            "stop": "/stop"
        }
    })


@app.route("/move")
def move():
    global last_command_time, current_speed, current_steer

    try:
        speed = float(request.args.get("speed", 0))
        steer = float(request.args.get("steer", 0))
    except ValueError:
        return jsonify({"error": "Invalid speed or steer value"}), 400

    speed = clamp(speed, -MAX_SPEED, MAX_SPEED)
    steer = clamp(steer, -MAX_STEER_ANGLE, MAX_STEER_ANGLE)

    with lock:
        current_speed = speed
        current_steer = steer
        last_command_time = time.time()
        apply_motion(current_speed, current_steer)

    return jsonify({
        "status": "ok",
        "speed": current_speed,
        "steer": current_steer
    })


@app.route("/stop")
def stop():
    global current_speed, current_steer, last_command_time

    with lock:
        current_speed = 0
        current_steer = 0
        last_command_time = time.time()
        px.stop()
        px.set_dir_servo_angle(0)

    return jsonify({
        "status": "stopped"
    })


def watchdog_loop():
    global current_speed, current_steer

    while True:
        time.sleep(0.1)

        with lock:
            time_since_last_command = time.time() - last_command_time

            if time_since_last_command > COMMAND_TIMEOUT:
                if current_speed != 0 or current_steer != 0:
                    current_speed = 0
                    current_steer = 0
                    px.stop()
                    px.set_dir_servo_angle(0)


def cleanup(*args):
    print("\nStopping PiCar-X safely...")
    try:
        px.stop()
        px.set_dir_servo_angle(0)
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    watchdog_thread = threading.Thread(target=watchdog_loop, daemon=True)
    watchdog_thread.start()

    print("PiCar-X command server started.")
    print("Open from your computer:")
    print("http://RASPBERRY_PI_IP:5000")
    print()
    print("Example move command:")
    print("http://RASPBERRY_PI_IP:5000/move?speed=15&steer=0")

    app.run(
        host="0.0.0.0",
        port=5000,
        threaded=True,
        debug=False,
        use_reloader=False
    )
