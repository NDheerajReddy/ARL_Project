# PiCar-X Human Following Robot using YOLO

This project uses a **SunFounder PiCar-X V2.0** robot with a **Raspberry Pi 5**, **Robot HAT V4**, and a **5MP OV5647 camera** to create a live camera stream and run YOLO-based human detection from a separate computer.

The robot follows a detected person by steering toward their position in the camera frame and moving forward until the person is close enough, then it stops.

---

## Hardware Used

- SunFounder PiCar-X V2.0
- Raspberry Pi 5
- Robot HAT V4
- 5MP OV5647 Pi Camera
- Windows laptop/desktop for YOLO inference
- Same Wi-Fi network for Raspberry Pi and Windows computer

---

## System Architecture

Raspberry Pi Camera
        |
        v
Camera stream server on Raspberry Pi
http://<PI_IP>:8000/stream.mjpg
        |
        v
Windows computer runs YOLO object detection
        |
        v
Windows sends movement commands to Raspberry Pi
http://<PI_IP>:5000/move?speed=<value>&steer=<value>
        |
        v
PiCar-X follows the detected person
