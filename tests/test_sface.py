"""Storage, identity decisions and restart behavior without camera/model downloads."""
from contextlib import chdir
import hashlib
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import MagicMock, patch

import numpy as np

from attendance.config import Settings, load_settings
from attendance.database import Database
from attendance.devices.common import DeviceError
from attendance.devices.sface import EmbeddingModel, SFaceRecognizer
from attendance.face_models import verify_model
from attendance.runtime import Runtime


class SFaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)

    def test_cosine_threshold_ambiguity_and_unknown_rejection(self):
        face = SFaceRecognizer(None, self.path, {})
        model = EmbeddingModel(1, [[1, 0, 0]])
        self.assertEqual(model.predict(np.array([1, 0, 0])), (1, 0))
        self.assertIsNone(face.choose([model.predict(np.array([0, 1, 0]))]))
        self.assertEqual(face.choose([(1, .2), (2, .8)]), 1)
        self.assertIsNone(face.choose([(1, .56)]))
        self.assertIsNone(face.choose([(1, .2), (2, .25)]))
        self.assertIsNone(face.choose([(1, float("nan"))]))

    def test_old_grayscale_archives_align_on_each_fresh_load_without_rewrite(self):
        student = {"id": 1, "roll": "TEST01", "face_ready": 1}
        path = self.path / "faces" / "1.npz"
        path.parent.mkdir()
        np.savez_compressed(path, samples=np.zeros((5, 160, 160), dtype=np.uint8))
        before = path.read_bytes()
        for _ in range(2):
            face = SFaceRecognizer(None, self.path, {})
            face.recognizer = MagicMock()
            with patch.object(face, "_detector"), \
                    patch.object(face, "_detect", return_value=np.ones((1, 15))), \
                    patch.object(face, "_normalize", return_value=np.array([1, 0, 0])):
                model = face._models([student])[1]
            self.assertEqual(face.recognizer.alignCrop.call_count, 5)
            self.assertEqual(model.predict(np.array([1, 0, 0])), (1, 0))
        self.assertEqual(path.read_bytes(), before)

    def test_legacy_images_without_landmarks_request_recapture(self):
        face = SFaceRecognizer(None, self.path, {})
        with patch.object(face, "_detector"), patch.object(face, "_detect", return_value=None):
            with self.assertRaisesRegex(DeviceError, "Not enough usable face images"):
                face._train(1, np.zeros((5, 160, 160), dtype=np.uint8))

    def test_new_color_enrollment_survives_runtime_restart_and_requires_same_finger(self):
        settings = Settings("laptop", self.path, "Asia/Kolkata", {"face": {"backend": "sface", "samples": 5}})
        db = Database(self.path, "laptop")
        sid = db.add_student("TEST01", "Test Student", "Test Class")
        original = Runtime(settings, db)
        self.addCleanup(original.close)
        crop = np.full((112, 112, 3), 100, dtype=np.uint8)
        with patch.object(original.face, "_detector"), patch.object(original.face, "_crop", return_value=(crop, "Face detected")), \
                patch.object(original.camera, "read"), patch("attendance.devices.face.time.sleep"):
            original.submit("face", {"student_id": sid})
            original.worker.join(3)
        self.assertEqual(original.snapshot()["status"], "done")
        db.set_fingerprint(sid, sid)
        session_id = db.start_session("Restart", "Test Class")
        path = original.face.directory / f"{sid}.npz"
        before = path.read_bytes()
        original.close()
        # Mock the neural network only; real persistence, similarity matching,
        # repeated frames and the independent fingerprint decision still run.
        with patch.object(SFaceRecognizer, "_detector"), \
                patch.object(SFaceRecognizer, "_normalize", return_value=np.array([1, 0, 0])):
            restarted = Runtime(settings, Database(self.path, "laptop"))
            self.addCleanup(restarted.close)
            restarted.feedback = MagicMock()
            with patch.object(restarted.face, "_crop", return_value=(crop, "Face detected")), \
                    patch.object(restarted.camera, "read"), patch("attendance.devices.face.time.sleep"):
                mismatch = restarted.attempt({"session_id": session_id, "sim_fingerprint": sid+1})
                self.assertEqual(mismatch["outcome"], "identity_mismatch")
                accepted = restarted.attempt({"session_id": session_id, "sim_fingerprint": sid})
                self.assertTrue(accepted["accepted"])
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(restarted.db.report(session_id)[0]["status"], "Present")

    def test_missing_models_fail_with_setup_instructions(self):
        face = SFaceRecognizer(None, self.path, {"model_dir": self.path / "missing"})
        with self.assertRaisesRegex(DeviceError, "setup-face-models"):
            face._detector()

    def test_model_checksum_is_verified(self):
        path = self.path / "model.onnx"
        path.write_bytes(b"test model")
        verify_model(path, hashlib.sha256(b"test model").hexdigest())
        with self.assertRaisesRegex(ValueError, "checksum"):
            verify_model(path, "0"*64)

    def test_profile_paths_are_independent_of_launch_directory(self):
        config = Path(__file__).resolve().parents[1] / "config/laptop.toml"
        expected = load_settings(config)
        with chdir(self.path):
            actual = load_settings(config)
        self.assertEqual(actual.data_dir, expected.data_dir)
        self.assertEqual(actual.section("face")["model_dir"], expected.section("face")["model_dir"])


if __name__ == "__main__":
    unittest.main()
