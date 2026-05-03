#!/usr/bin/env python3

import io
import sys
import time
import termios
import tty
import select
import threading
from dataclasses import dataclass

from flask import Flask, Response
from picamera2 import Picamera2
from picamera2.encoders import JpegEncoder

try:
    from picarx import Picarx
except Exception as exc:
    print("Failed to import Picarx.")
    print(f"Import error: {exc}")
    sys.exit(1)


@dataclass
class Config:
    speed: int = 30
    min_speed: int = 10
    max_speed: int = 80
    speed_step: int = 5
    steer_step: int = 6
    max_steer: int = 30
    loop_delay: float = 0.05
    frame_width: int = 640
    frame_height: int = 480
    jpeg_quality: int = 85
    host: str = "0.0.0.0"
    port: int = 5000


class RawTerminal:
    def __enter__(self):
        self.fd = sys.stdin.fileno()
        self.old_settings = termios.tcgetattr(self.fd)
        tty.setcbreak(self.fd)
        return self

    def __exit__(self, exc_type, exc, tb):
        termios.tcsetattr(self.fd, termios.TCSADRAIN, self.old_settings)


class TeleopCar:
    def __init__(self, config: Config):
        self.cfg = config
        self.px = Picarx()
        self.current_speed = config.speed
        self.current_steer = 0
        self.motion = "stopped"

    def clamp(self, value: int, low: int, high: int) -> int:
        return max(low, min(high, value))

    def apply_steering(self) -> None:
        self.current_steer = self.clamp(
            self.current_steer,
            -self.cfg.max_steer,
            self.cfg.max_steer,
        )
        self.px.set_dir_servo_angle(self.current_steer)

    def forward(self) -> None:
        self.motion = "forward"
        self.px.forward(self.current_speed)

    def reverse(self) -> None:
        self.motion = "reverse"
        self.px.backward(self.current_speed)

    def stop(self) -> None:
        self.motion = "stopped"
        self.px.stop()

    def steer_left(self) -> None:
        self.current_steer -= self.cfg.steer_step
        self.apply_steering()

    def steer_right(self) -> None:
        self.current_steer += self.cfg.steer_step
        self.apply_steering()

    def center_steering(self) -> None:
        self.current_steer = 0
        self.apply_steering()

    def increase_speed(self) -> None:
        self.current_speed = self.clamp(
            self.current_speed + self.cfg.speed_step,
            self.cfg.min_speed,
            self.cfg.max_speed,
        )
        self.reapply_motion()

    def decrease_speed(self) -> None:
        self.current_speed = self.clamp(
            self.current_speed - self.cfg.speed_step,
            self.cfg.min_speed,
            self.cfg.max_speed,
        )
        self.reapply_motion()

    def reapply_motion(self) -> None:
        if self.motion == "forward":
            self.px.forward(self.current_speed)
        elif self.motion == "reverse":
            self.px.backward(self.current_speed)

    def status_line(self) -> str:
        return (
            f"motion={self.motion:<7} "
            f"speed={self.current_speed:>2} "
            f"steer={self.current_steer:>3}"
        )

    def shutdown(self) -> None:
        try:
            self.stop()
        finally:
            try:
                self.center_steering()
            except Exception:
                pass


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


cfg = Config()
app = Flask(__name__)
output = StreamingOutput()


def create_camera():
    picam2 = Picamera2()
    config = picam2.create_video_configuration(
        main={"size": (cfg.frame_width, cfg.frame_height)}
    )
    picam2.configure(config)
    encoder = JpegEncoder(q=cfg.jpeg_quality)
    picam2.start_recording(encoder, output)
    return picam2


@app.route("/")
def index():
    return """
    <html>
      <head>
        <title>PiCar-X Live Stream</title>
      </head>
      <body style="font-family: Arial; background: #111; color: #eee; text-align: center;">
        <h2>PiCar-X Live Camera Stream</h2>
        <p>Keyboard teleoperation runs in the SSH terminal.</p>
        <img src="/stream.mjpg" width="800" />
      </body>
    </html>
    """


def generate_stream():
    while True:
        with output.condition:
            output.condition.wait()
            frame = output.frame
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )


@app.route("/stream.mjpg")
def stream_mjpg():
    return Response(
        generate_stream(),
        mimetype="multipart/x-mixed-replace; boundary=frame"
    )


def run_web_server():
    app.run(host=cfg.host, port=cfg.port, threaded=True, debug=False, use_reloader=False)


def read_key(timeout: float = 0.05):
    readable, _, _ = select.select([sys.stdin], [], [], timeout)
    if readable:
        return sys.stdin.read(1)
    return None


def main():
    car = TeleopCar(cfg)

    try:
        picam2 = create_camera()
    except Exception as exc:
        print(f"Camera startup failed: {exc}")
        return 1

    server_thread = threading.Thread(target=run_web_server, daemon=True)
    server_thread.start()

    print("Teleoperation controls")
    print("----------------------")
    print("w : forward")
    print("s : reverse")
    print("a : steer left")
    print("d : steer right")
    print("c : center steering")
    print("space : stop")
    print("+ / = : speed up")
    print("- : speed down")
    print("q : quit")
    print()
    print(f"Open live stream in browser: http://<PI_IP>:{cfg.port}")
    print("Starting teleop. Press q to quit.")
    print(car.status_line(), end="", flush=True)

    try:
        with RawTerminal():
            while True:
                key = read_key(cfg.loop_delay)

                if key == "w":
                    car.forward()
                elif key == "s":
                    car.reverse()
                elif key == "a":
                    car.steer_left()
                elif key == "d":
                    car.steer_right()
                elif key == "c":
                    car.center_steering()
                elif key == " ":
                    car.stop()
                elif key in ("+", "="):
                    car.increase_speed()
                elif key == "-":
                    car.decrease_speed()
                elif key == "q":
                    break
                elif key is not None and ord(key) == 3:
                    break

                print("\r" + car.status_line() + " " * 10, end="", flush=True)

    except KeyboardInterrupt:
        pass
    finally:
        print("\n\nStopping car and closing camera...")
        car.shutdown()
        try:
            picam2.stop_recording()
        except Exception:
            pass
        try:
            picam2.close()
        except Exception:
            pass

    print("Exited cleanly.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
