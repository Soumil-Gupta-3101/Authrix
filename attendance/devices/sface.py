"""Offline landmark-aligned SFace matching, including existing grayscale archives."""
import numpy as np

from .common import DeviceError
from .face import FaceRecognizer
from ..face_models import MODELS, model_directory, verify_model


class EmbeddingModel:
    def __init__(self, student_id, features):
        self.student_id = student_id
        self.features = np.asarray(features, dtype=np.float32)

    def predict(self, feature):
        # Cosine distance: zero is identical. Always retain unknown rejection.
        similarity = float(np.max(self.features @ feature))
        return self.student_id, 1 - float(np.clip(similarity, -1, 1))


class SFaceRecognizer(FaceRecognizer):
    sample_shapes = ((160, 160), (112, 112, 3))

    def __init__(self, camera, data_dir, options):
        super().__init__(camera, data_dir, options)
        self.detector = self.recognizer = None

    def _opencv(self):
        import cv2
        if tuple(map(int, cv2.__version__.split(".")[:2])) < (4, 8) or not hasattr(cv2, "FaceDetectorYN_create") or not hasattr(cv2, "FaceRecognizerSF_create"):
            raise DeviceError("SFace requires OpenCV 4.8 or newer. Install requirements-pi.txt on the Pi or requirements-laptop.txt on the laptop; see the setup guide")
        return cv2

    def _detector(self):
        if self.detector is None:
            cv2 = self._opencv()
            directory = model_directory(self.options)
            try:
                for name, (_, checksum) in MODELS.items():
                    verify_model(directory / name, checksum)
            except (OSError, ValueError) as exc:
                raise DeviceError("SFace models are missing or damaged. Run setup-face-models with the same --config used to start this server") from exc
            self.recognizer = cv2.FaceRecognizerSF_create(
                str(directory / "face_recognition_sface_2021dec.onnx"), "")
            self.detector = cv2.FaceDetectorYN_create(
                str(directory / "face_detection_yunet_2023mar.onnx"), "", (320, 320), .8)
        return self.detector

    def check_models(self):
        """Exercise both networks without a camera, enrollment, or GPIO access."""
        self._detect(np.zeros((320, 320, 3), dtype=np.uint8), self._detector())
        self._normalize(np.full((112, 112, 3), 128, dtype=np.uint8))

    def _detect(self, image, detector):
        cv2 = self._opencv()
        scale = min(1, 320 / max(image.shape[:2]))
        small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
        detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = detector.detect(small)
        if faces is not None:
            faces[:, :14] /= scale
        return faces

    def _crop(self, frame, detector):
        cv2 = self._opencv()
        faces = self._detect(frame, detector)
        if faces is None or len(faces) == 0:
            return None, "No face detected. Look directly at the camera"
        if len(faces) != 1:
            return None, "Show exactly one face"
        face = faces[0]
        x, y, w, h = [int(v) for v in face[:4]]
        overlay = frame.copy()
        cv2.rectangle(overlay, (x, y), (x+w, y+h), (80, 190, 120), 2)
        self.camera.publish(overlay)
        if min(w, h) < self.options.get("min_face_pixels", 100):
            return None, "Move a little closer"
        crop = frame[max(y, 0):min(y+h, frame.shape[0]), max(x, 0):min(x+w, frame.shape[1])]
        if not crop.size:
            return None, "Keep your whole face in view"
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        if not 35 < float(gray.mean()) < 220:
            return None, "Improve face lighting"
        if cv2.Laplacian(gray, cv2.CV_64F).var() < self.options.get("min_sharpness", 45):
            return None, "Hold still; image blurred"
        return self.recognizer.alignCrop(frame, face), "Face detected"

    def _normalize(self, crop):
        self._detector()
        feature = self.recognizer.feature(crop).reshape(-1).copy()
        norm = float(np.linalg.norm(feature))
        if not np.all(np.isfinite(feature)) or norm <= 0:
            raise DeviceError("Could not compute face features; try capturing again")
        return feature / norm

    def _train(self, student_id, samples):
        cv2 = self._opencv()
        detector = self._detector()
        features = []
        for sample in samples:
            if sample.ndim == 2:
                # Older enrollment remains untouched on disk. Derive aligned
                # features from it every time this process loads an archive.
                image = cv2.cvtColor(sample, cv2.COLOR_GRAY2BGR)
                faces = self._detect(image, detector)
                if faces is None or len(faces) != 1:
                    continue
                sample = self.recognizer.alignCrop(image, faces[0])
            features.append(self._normalize(sample))
        if len(features) < 5:
            raise DeviceError(f"Not enough usable face images for student {student_id}; recapture this face")
        return EmbeddingModel(student_id, features)

    def _distance_threshold(self):
        return 1 - self.options.get("cosine_threshold", .45)

    def _ambiguity_margin(self):
        return self.options.get("similarity_margin", .08)
