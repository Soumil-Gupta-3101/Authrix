"""Exercise persisted enrollment and the real matcher without a webcam."""
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, patch

import cv2
import numpy as np

from attendance.config import Settings
from attendance.database import Database
from attendance.devices.face import FaceRecognizer
from attendance.devices.common import DeviceError
from attendance.runtime import Runtime


class FaceRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.settings = Settings("laptop", self.path, "Asia/Kolkata", {"face": {"samples": 5}})
        rng = np.random.default_rng(918)
        texture = cv2.GaussianBlur(rng.integers(0, 256, (160, 160), dtype=np.uint8), (0, 0), 3)
        texture = cv2.normalize(texture, None, 30, 220, cv2.NORM_MINMAX)
        self.samples = np.asarray([
            np.clip(texture.astype(float) + rng.normal(0, 1, texture.shape), 0, 255).astype(np.uint8)
            for _ in range(5)
        ])
        self.noisy_query = np.clip(texture.astype(float) + rng.normal(0, 10, texture.shape), 0, 255).astype(np.uint8)
        self.unknown = rng.integers(0, 256, texture.shape, dtype=np.uint8)

    def test_restarted_runtime_marks_using_saved_enrollment_despite_camera_noise(self):
        db = Database(self.path, "laptop")
        sid = db.add_student("TEST01", "Test Student", "Test Class")
        original = Runtime(self.settings, db)
        self.addCleanup(original.close)
        with patch.object(original.camera, "read"), \
                patch.object(original.face, "_crop", side_effect=[(s, "Face detected") for s in self.samples]), \
                patch("attendance.devices.face.time.sleep"):
            original.face.enroll(sid, Event(), lambda _: None)
        db.set_face(sid)
        db.set_fingerprint(sid, sid)
        session_id = db.start_session("Restart test", "Test Class")
        archive_path = original.face.directory / f"{sid}.npz"
        saved_bytes = archive_path.read_bytes()
        with np.load(archive_path, allow_pickle=False) as archive:
            np.testing.assert_array_equal(archive["samples"], self.samples)
        original.close()

        # Fresh database, runtime and recognizer: no enrollment objects survive.
        restarted = Runtime(self.settings, Database(self.path, "laptop"))
        restarted.feedback = MagicMock()
        self.addCleanup(restarted.close)
        with patch.object(restarted.camera, "read"), \
                patch.object(restarted.face, "_crop", return_value=(self.noisy_query, "Face detected")), \
                patch("attendance.devices.face.time.sleep"):
            result = restarted.attempt({"session_id": session_id, "sim_fingerprint": sid})
        self.assertEqual(result["outcome"], "accepted")
        self.assertEqual(restarted.db.report(session_id)[0]["status"], "Present")
        self.assertEqual(archive_path.read_bytes(), saved_bytes)

    def test_existing_archives_reload_consistently_and_reject_unrelated_images(self):
        face = FaceRecognizer(None, self.path, {})
        archive_path = face.directory / "1.npz"
        np.savez_compressed(archive_path, samples=self.samples)
        saved_bytes = archive_path.read_bytes()
        students = [{"id": 1, "face_ready": 1, "roll": "TEST01"}]
        distances = []
        for _ in range(2):
            fresh = FaceRecognizer(None, self.path, {})
            model = fresh._models(students)[1]
            known = model.predict(fresh._normalize(self.noisy_query))[1]
            unknown = model.predict(fresh._normalize(self.unknown))[1]
            self.assertEqual(fresh.choose([(1, known)]), 1)
            self.assertIsNone(fresh.choose([(1, unknown)]))
            distances.append(known)
        self.assertAlmostEqual(*distances)
        self.assertEqual(archive_path.read_bytes(), saved_bytes)

    def test_timeout_explains_failed_match_and_logs_distances(self):
        face = FaceRecognizer(MagicMock(), self.path, {})
        np.savez_compressed(face.directory / "1.npz", samples=self.samples)
        clock = [100.0]

        def advance(seconds):
            clock[0] += seconds

        with patch.object(face, "_crop", return_value=(self.unknown, "Face detected")), \
                patch("attendance.devices.face.time.monotonic", side_effect=lambda: clock[0]), \
                patch("attendance.devices.face.time.sleep", side_effect=advance), \
                self.assertLogs("attendance.devices.face", level="INFO") as logs:
            with self.assertRaisesRegex(TimeoutError, "Face scan timed out: Face not recognized"):
                face.identify([{"id": 1, "face_ready": 1, "roll": "TEST01"}],
                              time.monotonic() + .25, Event(), lambda _: None)
        self.assertIn("last distances=", logs.output[0])

    def test_missing_corrupt_and_restored_files_update_enrollment_status(self):
        face = FaceRecognizer(None, self.path, {})
        student = {"id": 1, "roll": "TEST01", "face_ready": 1}
        path = face.directory / "1.npz"
        self.assertEqual(face.enrollment_status([student])[1]["face_status"], "unavailable")
        with self.assertRaisesRegex(DeviceError, "TEST01"):
            face._models([student])
        np.savez_compressed(path, samples=self.samples)
        self.assertEqual(face.enrollment_status([student])[1]["face_samples"], 5)
        first = face._models([student])[1]
        self.assertIs(face._models([student])[1], first)
        path.write_bytes(b"damaged archive")
        self.assertEqual(face.enrollment_status([student])[1]["face_status"], "unavailable")
        with self.assertRaisesRegex(DeviceError, "Invalid face samples"):
            face._models([student])
        np.savez_compressed(path, samples=self.samples)
        self.assertIsNot(face._models([student])[1], first)
        self.assertEqual(face.enrollment_status([student])[1]["face_status"], "ready")
        self.assertEqual(student["face_ready"], 1)

    def test_invalid_shape_and_dtype_do_not_create_models(self):
        student = {"id": 1, "roll": "TEST01", "face_ready": 1}
        for samples in (self.samples.astype(float), self.samples[:4], np.zeros((5, 10, 10), dtype=np.uint8)):
            with self.subTest(shape=samples.shape, dtype=samples.dtype):
                face = FaceRecognizer(None, self.path, {})
                np.savez_compressed(face.directory / "1.npz", samples=samples)
                with self.assertRaises(DeviceError):
                    face._models([student])

    def test_failed_recapture_preserves_previous_archive(self):
        face = FaceRecognizer(MagicMock(), self.path, {"samples": 5})
        path = face.directory / "1.npz"
        np.savez_compressed(path, samples=self.samples)
        before = path.read_bytes()
        with patch.object(face, "_crop", return_value=(self.unknown, "Face detected")), \
                patch("attendance.devices.face.time.sleep"), \
                patch("attendance.devices.face.os.replace", side_effect=OSError("Disk failure")):
            with self.assertRaises(OSError):
                face.enroll(1, Event(), lambda _: None)
        self.assertEqual(path.read_bytes(), before)

    def test_successful_recapture_replaces_cached_identity_model(self):
        face = FaceRecognizer(MagicMock(), self.path, {"samples": 5})
        student = {"id": 1, "roll": "TEST01", "face_ready": 1}
        np.savez_compressed(face.directory / "1.npz", samples=self.samples)
        old_model = face._models([student])[1]
        with patch.object(face, "_crop", return_value=(self.unknown, "Face detected")), \
                patch("attendance.devices.face.time.sleep"):
            face.enroll(1, Event(), lambda _: None)
        new_model = face._models([student])[1]
        self.assertIsNot(new_model, old_model)
        self.assertLess(new_model.predict(face._normalize(self.unknown))[1], .01)


if __name__ == "__main__":
    unittest.main()
