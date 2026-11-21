from threading import Lock
import time

from .common import DeviceError


class Camera:
    """BGR frames on both platforms. Only the job worker captures frames."""
    def __init__(self, mode, options):
        self.mode, self.options = mode, options
        self.device = None
        self.preview = None
        self.preview_at = 0
        self.lock = Lock()

    def open(self):
        if self.device is not None:
            return
        import cv2
        width, height = self.options.get("width", 640), self.options.get("height", 480)
        if self.mode == "raspberry_pi":
            from picamera2 import Picamera2
            camera = Picamera2()
            try:
                # Picamera2 RGB888 produces BGR byte order for OpenCV.
                camera.configure(camera.create_preview_configuration(main={"size": (width, height), "format": "RGB888"}))
                if "AfMode" in camera.camera_controls:
                    from libcamera import controls
                    camera.set_controls({"AfMode": controls.AfModeEnum.Continuous})
                camera.start()
                time.sleep(1)
            except Exception:
                camera.close()
                raise
            self.device = camera
        else:
            camera = cv2.VideoCapture(self.options.get("index", 0))
            if not camera.isOpened():
                camera.release()
                raise DeviceError("Cannot open webcam. Check camera permissions and camera.index")
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, width)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
            self.device = camera

    def read(self):
        self.open()
        if self.mode == "raspberry_pi":
            frame = self.device.capture_array("main")
        else:
            ok, frame = self.device.read()
            if not ok:
                raise DeviceError("Camera frame could not be read")
        if frame is None or frame.size == 0:
            raise DeviceError("Camera returned an empty frame")
        self.publish(frame)
        return frame

    def publish(self, frame):
        import cv2
        ok, jpeg = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        if ok:
            with self.lock:
                self.preview = jpeg.tobytes()
                self.preview_at = time.monotonic()

    def jpeg(self):
        with self.lock:
            return self.preview if time.monotonic() - self.preview_at < 5 else None

    def close(self):
        if self.device is not None:
            if self.mode == "raspberry_pi":
                self.device.stop()
                self.device.close()
            else:
                self.device.release()
            self.device = None
        with self.lock:
            self.preview = None
