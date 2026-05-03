#!/usr/bin/env python3

from flask import Flask, Response, render_template_string
from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput
import io
import threading
import signal
import sys


HOST = "0.0.0.0"
PORT = 8000

# Best practical quality with acceptable latency for YOLO/person following
FRAME_SIZE = (1296, 972)
FPS = 30

# Use 2 for stability. Use 1 if you want even lower latency but it may be less stable.
BUFFER_COUNT = 2


app = Flask(__name__)


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = bytes(buf)
            self.condition.notify_all()
        return len(buf)


output = StreamingOutput()
picam2 = Picamera2()


HTML_PAGE = """
<!DOCTYPE html>
<html>
<head>
    <title>PiCar-X Camera Stream</title>
    <style>
        body {
            margin: 0;
            background: #111;
            color: white;
            font-family: Arial, sans-serif;
            text-align: center;
        }
        h2 {
            margin: 10px;
            font-size: 20px;
        }
        img {
            width: 100vw;
            max-width: 1100px;
            height: auto;
            background: black;
        }
    </style>
</head>
<body>
    <h2>PiCar-X Camera Stream - 1296x972 30FPS</h2>
    <img src="/stream.mjpg">
</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_PAGE)


@app.route("/stream.mjpg")
def stream():
    def generate():
        while True:
            with output.condition:
                output.condition.wait()
                frame = output.frame

            if frame is None:
                continue

            yield (
                b"--FRAME\r\n"
                b"Content-Type: image/jpeg\r\n"
                b"Cache-Control: no-cache, no-store, must-revalidate\r\n"
                b"Pragma: no-cache\r\n"
                b"Expires: 0\r\n\r\n" +
                frame +
                b"\r\n"
            )

    return Response(
        generate(),
        mimetype="multipart/x-mixed-replace; boundary=FRAME"
    )


def start_camera():
    config = picam2.create_video_configuration(
        main={
            "size": FRAME_SIZE,
            "format": "RGB888"
        },
        controls={
            "FrameRate": FPS,
            "AwbEnable": True,
            "AeEnable": True,
            "ExposureValue": -0.5,
            "Saturation": 1.1,
            "Contrast": 1.1,
            "Sharpness": 1.2
        },
        buffer_count=BUFFER_COUNT
    )

    picam2.configure(config)

    # Re-apply important controls after configure.
    try:
        picam2.set_controls({
            "FrameRate": FPS,
            "AwbEnable": True,
            "AeEnable": True,
            "ExposureValue": -0.5,
            "Saturation": 1.1,
            "Contrast": 1.1,
            "Sharpness": 1.2
        })
    except Exception as e:
        print(f"Camera control warning: {e}")

    encoder = MJPEGEncoder()

    picam2.start_recording(
        encoder,
        FileOutput(output)
    )

    print("Camera stream started successfully.")
    print(f"Resolution: {FRAME_SIZE[0]}x{FRAME_SIZE[1]}")
    print(f"FPS target: {FPS}")
    print(f"Buffer count: {BUFFER_COUNT}")
    print(f"Open from Windows: http://RASPBERRY_PI_IP:{PORT}")


def stop_camera(*args):
    print("\nStopping camera...")
    try:
        picam2.stop_recording()
    except Exception:
        pass

    try:
        picam2.close()
    except Exception:
        pass

    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, stop_camera)
    signal.signal(signal.SIGTERM, stop_camera)

    start_camera()

    app.run(
        host=HOST,
        port=PORT,
        threaded=True,
        debug=False,
        use_reloader=False
    )
