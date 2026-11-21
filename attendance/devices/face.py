"""Offline OpenCV LBPH prototype; thresholds must be calibrated with real people.

One model per identity allows an ambiguity margin against the runner-up.
No pretrained identity downloads, network dependency, or claimed liveness.
"""
import logging
import os
from pathlib import Path
from threading import RLock
import time

from .common import DeviceError, check_deadline


log = logging.getLogger(__name__)


class FaceRecognizer:
    sample_shapes = ((160, 160),)

    def __init__(self, camera, data_dir, options):
        self.camera = camera
        self.directory = data_dir / "faces"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.options = options
        self._archive_lock = RLock()
        self._sample_cache, self._model_cache = {}, {}

    def _opencv(self):
        import cv2
        if not hasattr(cv2, "face"):
            raise DeviceError("OpenCV contrib is required: cv2.face is missing. See the installation guide")
        return cv2

    def _detector(self):
        cv2 = self._opencv()
        candidates = []
        if hasattr(cv2, "data"):
            candidates.append(Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml")
        candidates += [Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml")]
        for path in candidates:
            if path.is_file():
                detector = cv2.CascadeClassifier(str(path))
                if not detector.empty():
                    return detector
        raise DeviceError("Face cascade missing. On Pi install opencv-data")

    def _crop(self, frame, detector):
        cv2 = self._opencv()
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = detector.detectMultiScale(gray, 1.15, 6, minSize=(60, 60))
        if len(faces) != 1:
            return None, "Show exactly one face"
        x, y, w, h = [int(v) for v in faces[0]]
        overlay = frame.copy()
        cv2.rectangle(overlay, (x, y), (x + w, y + h), (80, 190, 120), 2)
        self.camera.publish(overlay)
        if min(w, h) < self.options.get("min_face_pixels", 100):
            return None, "Move a little closer"
        crop = gray[y:y+h, x:x+w]
        if not 35 < float(crop.mean()) < 220:
            return None, "Improve face lighting"
        if cv2.Laplacian(crop, cv2.CV_64F).var() < self.options.get("min_sharpness", 45):
            return None, "Hold still; image blurred"
        return cv2.equalizeHist(cv2.resize(crop, (160, 160))), "Face detected"

    def enroll(self, student_id, cancel, progress):
        import numpy as np
        detector = self._detector()
        samples, required = [], self.options.get("samples", 20)
        deadline = time.monotonic() + 90
        while len(samples) < required:
            check_deadline(deadline, cancel)
            crop, message = self._crop(self.camera.read(), detector)
            if crop is not None:
                samples.append(crop)
                progress(f"Face samples {len(samples)}/{required}. Vary your angle slightly")
            else:
                progress(message)
            time.sleep(.25)
        path = self.directory / f"{int(student_id)}.npz"
        temporary = path.with_suffix(".tmp")
        with temporary.open("wb") as stream:
            np.savez_compressed(stream, samples=np.asarray(samples, dtype=np.uint8))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        with self._archive_lock:
            self._sample_cache.pop(int(student_id), None)
            self._model_cache.pop(int(student_id), None)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _normalize(self, crop):
        """Suppress camera noise before LBPH compares neighboring pixels.

        Apply this to both saved crops and live crops. Archives retain the
        original equalized images, so existing enrollments need no migration.
        """
        return self._opencv().GaussianBlur(crop, (5, 5), 0)

    def _load_samples(self, student):
        """Validate persisted samples; a database flag alone is not enrollment."""
        import numpy as np
        sid = int(student["id"])
        path = self.directory / f"{sid}.npz"
        with self._archive_lock:
            try:
                stat = path.stat()
            except OSError as exc:
                self._sample_cache.pop(sid, None)
                self._model_cache.pop(sid, None)
                raise DeviceError(f"Face samples unavailable for {student['roll']}: {path}. Restore the file or recapture this face") from exc
            signature = (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size)
            cached = self._sample_cache.get(sid)
            if cached and cached[0] == signature:
                return cached
            self._model_cache.pop(sid, None)
            try:
                with np.load(path, allow_pickle=False) as archive:
                    samples = archive["samples"]
                if samples.dtype != np.uint8 or samples.shape[1:] not in self.sample_shapes or not 5 <= len(samples) <= 100:
                    raise ValueError("Expected 5-100 uint8 face images")
            except Exception as exc:
                self._sample_cache.pop(sid, None)
                raise DeviceError(f"Invalid face samples for {student['roll']}: {path}. Restore the file or recapture this face") from exc
            self._sample_cache[sid] = (signature, samples)
            return signature, samples

    def enrollment_status(self, students):
        status = {}
        for student in students:
            item = {"face_status": "needed", "face_samples": 0, "face_error": None}
            if student["face_ready"]:
                try:
                    _, samples = self._load_samples(student)
                    item.update(face_status="ready", face_samples=len(samples))
                except DeviceError as exc:
                    item.update(face_status="unavailable", face_error=str(exc))
            status[student["id"]] = item
        return status

    def _train(self, student_id, samples):
        import numpy as np
        cv2 = self._opencv()
        model = cv2.face.LBPHFaceRecognizer_create()
        model.train([self._normalize(sample) for sample in samples],
                    np.full(len(samples), student_id, dtype=np.int32))
        return model

    def _models(self, students):
        models = {}
        for student in students:
            if not student["face_ready"]:
                continue
            with self._archive_lock:
                signature, samples = self._load_samples(student)
                sid = student["id"]
                cached = self._model_cache.get(sid)
                if cached is None or cached[0] != signature:
                    cached = (signature, self._train(sid, samples))
                    self._model_cache[sid] = cached
                models[sid] = cached[1]
        if not models:
            raise DeviceError("Enroll at least one face before scanning")
        return models

    def _distance_threshold(self):
        return self.options.get("distance_threshold", 55)

    def _ambiguity_margin(self):
        return self.options.get("ambiguity_margin", 8)

    def choose(self, scores):
        """Reject weak and ambiguous matches, including invalid numeric scores."""
        import math
        if not scores or any(not math.isfinite(s) or s < 0 for _, s in scores):
            return None
        ranked = sorted(scores, key=lambda x: x[1])
        if ranked[0][1] > self._distance_threshold():
            return None
        if len(ranked) > 1 and ranked[1][1] - ranked[0][1] < self._ambiguity_margin():
            return None
        return ranked[0][0]

    def identify(self, students, deadline, cancel, progress):
        models, detector = self._models(students), self._detector()
        previous, consecutive = None, 0
        message, scores = "Waiting for a camera image", []
        try:
            while True:
                check_deadline(deadline, cancel)
                crop, message = self._crop(self.camera.read(), detector)
                candidate = None
                if crop is not None:
                    normalized = self._normalize(crop)
                    scores = [(sid, model.predict(normalized)[1]) for sid, model in models.items()]
                    candidate = self.choose(scores)
                    if candidate is not None:
                        message = "Confirming face"
                    elif min(score for _, score in scores) > self._distance_threshold():
                        message = "Face not recognized. Use even lighting and face the camera directly"
                    else:
                        message = "Face match is ambiguous. Face the camera directly"
                consecutive = consecutive + 1 if candidate is not None and candidate == previous else (1 if candidate else 0)
                previous = candidate
                progress(message)
                if consecutive >= self.options.get("consecutive_frames", 3):
                    check_deadline(deadline, cancel)
                    return candidate
                time.sleep(.1)
        except TimeoutError:
            log.info("Face scan timed out: %s; last distances=%s; threshold=%s; ambiguity_margin=%s",
                     message, [(sid, round(score, 2)) for sid, score in scores],
                     self._distance_threshold(), self._ambiguity_margin())
            raise TimeoutError(f"Face scan timed out: {message}") from None
