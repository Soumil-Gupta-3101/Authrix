"""Pi-profile restart path with mocked physical devices and neural inference."""
from contextlib import chdir
from copy import deepcopy
from dataclasses import replace
import importlib
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

# Load the native extension before patch.dict restores sys.modules in device tests.
import cv2
import numpy as np

from attendance.config import load_settings
from attendance.__main__ import main
from attendance.database import Database
from attendance.devices.fingerprint import R307
from attendance.devices.sface import SFaceRecognizer
from attendance.runtime import Runtime
from scripts.hardware_test import check_saved_face


ROOT = Path(__file__).resolve().parents[1]


class PiFaceRestartTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        profile = load_settings(ROOT / "config/raspberry_pi.toml")
        values = deepcopy(profile.values)
        values["face"]["samples"] = 5
        self.settings = replace(profile, data_dir=self.path, values=values)
        self.db = Database(self.path, "raspberry_pi")

    def test_profiles_share_matcher_models_and_decision_settings(self):
        laptop = load_settings(ROOT / "config/laptop.toml")
        with chdir(self.path):
            pi = load_settings(ROOT / "config/raspberry_pi.toml")
        for key in ("backend", "model_dir", "cosine_threshold", "similarity_margin", "consecutive_frames"):
            self.assertEqual(pi.section("face")[key], laptop.section("face")[key], key)
        self.assertEqual(pi.section("face")["backend"], "sface")
        self.assertEqual(pi.data_dir, ROOT / "data/raspberry_pi")
        self.assertNotEqual(pi.data_dir, laptop.data_dir)

    def test_pi_camera_enrollment_and_sensor_mapping_survive_fresh_runtime(self):
        sid = self.db.add_student("PI001", "Pi Test One", "PI-A")
        other = self.db.add_student("PI002", "Pi Test Two", "PI-A")
        self.db.set_fingerprint(other, 77)
        original = Runtime(self.settings, self.db)
        original.feedback = MagicMock()
        self.addCleanup(original.close)
        self.assertIsInstance(original.face, SFaceRecognizer)
        self.assertIsInstance(original.fingerprint, R307)
        self.assertEqual(original.camera.mode, "raspberry_pi")
        device = MagicMock()
        device.camera_controls = {}
        device.capture_array.return_value = np.full((480, 640, 3), [10, 20, 30], dtype=np.uint8)
        aligned = np.full((112, 112, 3), 100, dtype=np.uint8)
        modules = {"picamera2": SimpleNamespace(Picamera2=MagicMock(return_value=device))}
        with patch.dict("sys.modules", modules), patch("attendance.devices.camera.time.sleep"), \
                patch.object(original.face, "_detector"), \
                patch.object(original.face, "_crop", return_value=(aligned, "Face detected")):
            original.submit("face", {"student_id": sid})
            original.worker.join(5)
        self.assertEqual(original.snapshot()["status"], "done")
        device.capture_array.assert_called_with("main")
        device.create_preview_configuration.assert_called_once_with(main={"size": (640, 480), "format": "RGB888"})
        device.stop.assert_called_once()
        device.close.assert_called_once()
        original.fingerprint = MagicMock(spec=R307)
        original.fingerprint.enroll.return_value = 42
        original.submit("fingerprint", {"student_id": sid})
        original.worker.join(5)
        self.assertEqual(self.db.student(sid)["fingerprint_id"], 42)
        session_id = self.db.start_session("Pi restart", "PI-A")
        archive = original.face.directory / f"{sid}.npz"
        saved = archive.read_bytes()
        original.close()

        with patch.object(SFaceRecognizer, "_detector"), \
                patch.object(SFaceRecognizer, "_normalize", return_value=np.array([1, 0, 0])):
            restarted = Runtime(self.settings, Database(self.path, "raspberry_pi"))
            restarted.feedback = MagicMock()
            self.addCleanup(restarted.close)
            self.assertIsInstance(restarted.fingerprint, R307)
            self.assertEqual(restarted.students()[0]["face_samples"], 5)
            self.assertFalse(restarted.camera.device)
            restarted.fingerprint = MagicMock(spec=R307)
            restarted.fingerprint.identify.side_effect = [77, 999, 42]
            with patch.dict("sys.modules", modules), patch("attendance.devices.camera.time.sleep"), \
                    patch.object(restarted.face, "_crop", return_value=(aligned, "Face detected")):
                payload = {"session_id": session_id}
                self.assertEqual(restarted.attempt(payload)["outcome"], "identity_mismatch")
                self.assertEqual(restarted.attempt(payload)["outcome"], "fingerprint_unknown")
                self.assertTrue(restarted.attempt(payload)["accepted"])
                restarted.camera.close()
            self.assertEqual(restarted.fingerprint.identify.call_count, 3)
            with self.assertRaisesRegex(ValueError, "Simulated biometric input"):
                restarted.submit("attendance", {"sim_face": sid})
        self.assertEqual(archive.read_bytes(), saved)
        self.assertEqual(self.db.report(session_id)[0]["status"], "Present")
        self.assertEqual(self.db.report(session_id)[1]["status"], "Pending")

    def test_pi_enrollment_and_attendance_work_with_missing_lcd(self):
        sid = self.db.add_student("PI001", "Pi Test One", "PI-A")
        runtime = Runtime(self.settings, self.db)
        self.addCleanup(runtime.close)
        device = MagicMock()
        device.camera_controls = {}
        device.capture_array.return_value = np.full((480, 640, 3), 100, dtype=np.uint8)
        aligned = np.full((112, 112, 3), 100, dtype=np.uint8)
        sensor = MagicMock(spec=R307)
        runtime.fingerprint = sensor

        def enroll_finger(reserved, cancel, progress, **options):
            progress("Place your finger")
            options["before_store"](42)
            return 42

        sensor.enroll.side_effect = enroll_finger
        modules = {"picamera2": SimpleNamespace(Picamera2=MagicMock(return_value=device)),
                   "RPLCD": SimpleNamespace(),
                   "RPLCD.i2c": SimpleNamespace(CharLCD=MagicMock(side_effect=OSError("LCD absent"))),
                   "gpiozero": None}
        with patch.dict("sys.modules", modules), patch("attendance.devices.camera.time.sleep"), \
                patch.object(runtime.face, "_detector"), \
                patch.object(runtime.face, "_crop", return_value=(aligned, "Face detected")), \
                patch.object(runtime.face, "_normalize", return_value=np.array([1, 0, 0])):
            runtime.submit("face", {"student_id": sid})
            runtime.worker.join(5)
            self.assertEqual(runtime.snapshot()["status"], "done")
            self.assertTrue((runtime.face.directory / f"{sid}.npz").is_file())
            self.assertTrue(self.db.student(sid)["face_ready"])
            runtime.submit("fingerprint", {"student_id": sid})
            runtime.worker.join(5)
            self.assertEqual(runtime.snapshot()["status"], "done")
            self.assertEqual(self.db.student(sid)["fingerprint_id"], 42)
            session_id = self.db.start_session("Camera and fingerprint", "PI-A")
            sensor.identify.return_value = 42
            runtime.submit("attendance", {})
            runtime.worker.join(5)
            self.assertEqual(runtime.snapshot()["status"], "done")
            self.assertTrue(runtime.snapshot()["result"]["accepted"])
            self.assertEqual(self.db.report(session_id)[0]["status"], "Present")
            self.assertEqual(runtime.feedback.snapshot()["lines"][0], "Attendance saved")
            self.assertEqual(runtime.feedback.snapshot()["lcd_status"], "unavailable")
            runtime.submit("attendance", {})
            runtime.worker.join(5)
            self.assertEqual(runtime.snapshot()["result"]["outcome"], "duplicate")
            sensor.identify.return_value = None
            runtime.submit("attendance", {})
            runtime.worker.join(5)
            self.assertEqual(runtime.snapshot()["result"]["outcome"], "fingerprint_unknown")

    def test_pi_doctor_allows_missing_optional_lcd_dependencies(self):
        config = self.path / "pi.toml"
        config.write_text('mode = "raspberry_pi"\ndata_dir = "data"\n[face]\nbackend = "sface"\n', encoding="utf-8")
        real_import = importlib.import_module

        def import_module(name):
            if name in ("RPLCD.i2c", "smbus"):
                raise ModuleNotFoundError(name)
            if name in ("picamera2", "libcamera", "serial"):
                return SimpleNamespace()
            if name == "gpiozero":
                raise AssertionError("GPIO libraries must not be required")
            return real_import(name)

        output = StringIO()
        with patch.object(SFaceRecognizer, "check_models") as check_models, \
                patch("importlib.import_module", side_effect=import_module), \
                patch("sys.stdout", output):
            main(["--config", str(config), "doctor"])
        self.assertIn("Optional LCD dependency unavailable: RPLCD.i2c", output.getvalue())
        self.assertIn("Optional LCD dependency unavailable: smbus", output.getvalue())
        self.assertIn("Software imports passed", output.getvalue())
        check_models.assert_called_once()

    def test_standalone_face_check_is_read_only_and_checks_expected_roll(self):
        sid = self.db.add_student("PI001", "Pi Test", "PI-A")
        self.db.set_face(sid)
        camera, face = MagicMock(), MagicMock()
        face._models.return_value = {sid: object()}
        face.identify.return_value = sid
        before = self.db.path.read_bytes()
        with patch("scripts.hardware_test.Camera", return_value=camera), \
                patch("attendance.devices.sface.SFaceRecognizer", return_value=face) as factory:
            factory.__name__ = "SFaceRecognizer"
            self.assertEqual(check_saved_face(self.settings, "PI001"), sid)
            with self.assertRaisesRegex(ValueError, "needs face enrollment"):
                check_saved_face(self.settings, "NO-SUCH-ROLL")
        camera.close.assert_called_once()
        self.assertEqual(self.db.path.read_bytes(), before)
        self.assertEqual(self.db.recent_attempts(), [])


if __name__ == "__main__":
    unittest.main()
