from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, patch

from attendance.config import Settings, load_settings
from attendance.database import Database
from attendance.devices.common import DeviceError
from attendance.devices.face import FaceRecognizer
from attendance.devices.feedback import Feedback, lcd_line
from attendance.locking import instance_lock
from attendance.reports import export_csv, export_xlsx
from attendance.runtime import Runtime
from attendance.web import create_app


class ProjectCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.settings = Settings("simulation", self.path, "Asia/Kolkata", {})
        self.db = Database(self.path, "simulation")
        self.a = self.db.add_student("A001", "Student A", "ECE-A")
        self.b = self.db.add_student("A002", "Student B", "ECE-A")
        self.c = self.db.add_student("B001", "Student C", "ECE-B")
        for sid in (self.a, self.b, self.c):
            self.db.set_face(sid)
            self.db.set_fingerprint(sid, sid)
        self.session_id = self.db.start_session("Embedded Systems", "ECE-A")


class RulesTests(ProjectCase):
    def test_both_same_identity_marks_once(self):
        self.assertTrue(self.db.decide(self.session_id, self.a, self.a)["accepted"])
        self.assertEqual(self.db.decide(self.session_id, self.a, self.a)["outcome"], "duplicate")
        self.assertEqual([r["status"] for r in self.db.report(self.session_id)], ["Present", "Pending"])

    def test_face_or_finger_alone_or_mismatch_never_mark(self):
        for face, finger in ((self.a, None), (None, self.a), (None, None), (self.a, self.b)):
            with self.subTest(face=face, finger=finger):
                self.assertFalse(self.db.decide(self.session_id, face, finger)["accepted"])
        self.assertTrue(all(r["status"] == "Pending" for r in self.db.report(self.session_id)))

    def test_unknown_and_unenrolled_rejected(self):
        self.assertEqual(self.db.decide(self.session_id, 999, 999)["outcome"], "not_enrolled")
        with self.db.connect() as db:
            db.execute("UPDATE students SET face_ready=0 WHERE id=?", (self.a,))
        self.assertEqual(self.db.decide(self.session_id, self.a, self.a)["outcome"], "not_enrolled")

    def test_wrong_class_rejected(self):
        self.assertEqual(self.db.decide(self.session_id, self.c, self.c)["outcome"], "wrong_class")

    def test_closed_or_changed_session_rejected(self):
        self.db.close_session()
        self.assertEqual(self.db.decide(self.session_id, self.a, self.a)["outcome"], "session_changed")
        second = self.db.start_session("Other subject", "ECE-A")
        self.assertEqual(self.db.decide(self.session_id, self.a, self.a)["outcome"], "session_changed")
        self.assertTrue(self.db.decide(second, self.a, self.a)["accepted"])

    def test_failures_audited_without_attendance(self):
        for failure in ("timeout", "cancelled", "device_error", "fingerprint_unknown"):
            self.assertFalse(self.db.decide(self.session_id, self.a, self.a, failure)["accepted"])
        self.assertEqual(len(self.db.recent_attempts()), 4)
        self.assertTrue(all(r["status"] == "Pending" for r in self.db.report(self.session_id)))

    def test_concurrent_duplicate_accepts_only_once(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: self.db.decide(self.session_id, self.a, self.a), range(16)))
        self.assertEqual(sum(r["accepted"] for r in results), 1)

    def test_single_active_session(self):
        with self.assertRaises(ValueError):
            self.db.start_session("Parallel", "ECE-A")

    def test_mode_cannot_reuse_test_database_for_hardware(self):
        with self.assertRaises(ValueError):
            Database(self.path, "raspberry_pi")

    def test_reports_absences_and_class_snapshot(self):
        self.db.decide(self.session_id, self.a, self.a)
        self.db.add_student("A003", "Late registration", "ECE-A")
        self.db.close_session()
        rows = self.db.report(self.session_id)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]["status"], "Absent")
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(export_xlsx(rows, "Asia/Kolkata")))
        self.assertEqual(book.active["H3"].value, "Absent")
        self.assertEqual(book.active["I2"].value, "simulation")
        self.assertIn("Face + Fingerprint", export_csv(rows, "Asia/Kolkata").decode("utf-8-sig"))

    def test_report_formula_injection_escaped(self):
        rows = self.db.report(self.session_id)
        rows[0]["name"] = '=HYPERLINK("bad")'
        from openpyxl import load_workbook
        sheet = load_workbook(BytesIO(export_xlsx(rows, "Asia/Kolkata"))).active
        self.assertEqual(sheet["B2"].data_type, "s")
        self.assertTrue(sheet["B2"].value.startswith("'="))

    def test_instance_lock_prevents_server_and_backup_collision(self):
        with instance_lock(self.path):
            with self.assertRaises(ValueError):
                with instance_lock(self.path):
                    pass


class RuntimeTests(ProjectCase):
    def setUp(self):
        super().setUp()
        self.runtime = Runtime(self.settings, self.db)
        self.runtime.feedback = MagicMock()
        self.addCleanup(self.runtime.close)

    def test_success_then_missing_does_not_reuse_evidence(self):
        payload = {"session_id": self.session_id, "sim_face": self.a, "sim_fingerprint": self.a}
        self.assertTrue(self.runtime.attempt(payload)["accepted"])
        payload.update(sim_face=self.b, sim_fingerprint=None)
        self.assertEqual(self.runtime.attempt(payload)["outcome"], "missing_biometric")
        self.assertEqual(self.db.report(self.session_id)[1]["status"], "Pending")

    def test_timeout_and_camera_error_fail_closed(self):
        self.runtime.face = MagicMock()
        for error, expected in [(TimeoutError(), "timeout"), (DeviceError("Disconnected"), "device_error")]:
            self.runtime.face.identify.side_effect = error
            self.assertEqual(self.runtime.attempt({"session_id": self.session_id, "sim_fingerprint": self.a})["outcome"], expected)

    def test_timeout_detail_is_shown_and_cleared_on_the_next_attempt(self):
        self.runtime.face = MagicMock()
        self.runtime.face.identify.side_effect = [TimeoutError("Face scan timed out: Improve face lighting"), self.a]
        payload = {"session_id": self.session_id, "sim_fingerprint": self.a}
        self.assertEqual(self.runtime.attempt(payload)["outcome"], "timeout")
        self.assertIn("Improve face lighting", self.runtime.snapshot()["warning"])
        self.assertTrue(self.runtime.attempt(payload)["accepted"])
        self.assertIsNone(self.runtime.snapshot()["warning"])

    def test_same_student_from_independent_hardware_adapters(self):
        self.runtime.face = MagicMock()
        self.runtime.face.identify.return_value = self.a
        self.runtime.fingerprint = MagicMock()
        self.runtime.fingerprint.identify.return_value = self.a
        self.assertTrue(self.runtime.attempt({"session_id": self.session_id})["accepted"])
        self.runtime.fingerprint.identify.assert_called_once()

    def test_fingerprint_failure_no_attendance(self):
        self.runtime.face = MagicMock()
        self.runtime.face.identify.return_value = self.a
        self.runtime.fingerprint = MagicMock()
        self.runtime.fingerprint.identify.side_effect = DeviceError("UART disconnected")
        self.assertEqual(self.runtime.attempt({"session_id": self.session_id})["outcome"], "device_error")

    def test_feedback_failure_preserves_committed_result(self):
        self.runtime.feedback.result.side_effect = DeviceError("LCD failed")
        result = self.runtime.attempt({"session_id": self.session_id, "sim_face": self.a, "sim_fingerprint": self.a})
        self.assertTrue(result["accepted"])
        self.assertEqual(self.db.report(self.session_id)[0]["status"], "Present")

    def test_cancelled_attempt_is_not_accepted(self):
        self.runtime.cancel()
        self.assertEqual(self.runtime.attempt({"session_id": self.session_id, "sim_face": self.a, "sim_fingerprint": self.a})["outcome"], "cancelled")

    def test_session_closed_during_capture_rejected(self):
        self.runtime.face = MagicMock()
        def capture(*args):
            self.db.close_session()
            return self.a
        self.runtime.face.identify.side_effect = capture
        self.assertEqual(self.runtime.attempt({"session_id": self.session_id, "sim_fingerprint": self.a})["outcome"], "session_changed")

    def test_pi_disallows_fake_biometrics(self):
        real = Settings("raspberry_pi", self.path, "Asia/Kolkata", {})
        runtime = Runtime(real, self.db)
        with self.assertRaises(ValueError):
            runtime.submit("attendance", {"sim_face": self.a, "sim_fingerprint": self.a})

    def test_laptop_disallows_fake_face(self):
        laptop = Runtime(Settings("laptop", self.path, "Asia/Kolkata", {}), self.db)
        with self.assertRaises(ValueError):
            laptop.submit("attendance", {"sim_face": self.a})

    def test_serialized_jobs_and_cancellation(self):
        self.runtime.face = MagicMock()
        def capture(*args):
            self.runtime.cancel_event.wait(3)
            return self.a
        self.runtime.face.identify.side_effect = capture
        self.runtime.submit("attendance", {"sim_fingerprint": self.a})
        with self.assertRaises(ValueError):
            self.runtime.submit("diagnostics", {})
        self.runtime.cancel()
        self.runtime.worker.join(3)
        self.assertFalse(self.runtime.worker.is_alive())
        self.assertEqual(self.runtime.snapshot()["result"]["outcome"], "cancelled")


class WebTests(ProjectCase):
    def setUp(self):
        super().setUp()
        from werkzeug.security import generate_password_hash
        (self.path / "credentials.json").write_text(json.dumps({"secret_key": "test-only-secret", "password_hash": generate_password_hash("test-password-123")}), encoding="utf-8")
        self.runtime = Runtime(self.settings, self.db)
        self.runtime.feedback = MagicMock()
        self.runtime.feedback.snapshot.return_value = {"lines": ["Ready", "Test"], "lcd_status": "dashboard", "warning": None}
        self.app = create_app(self.settings, self.db, self.runtime)
        self.app.testing = True
        self.client = self.app.test_client()
        self.addCleanup(self.runtime.close)

    def login(self):
        self.client.get("/login")
        with self.client.session_transaction() as session:
            csrf = session["csrf"]
        response = self.client.post("/login", data={"csrf": csrf, "password": "test-password-123"})
        self.assertEqual(response.status_code, 302)
        with self.client.session_transaction() as session:
            self.csrf = session["csrf"]

    def post(self, url, data):
        return self.client.post(url, json=data, headers={"X-CSRF-Token": self.csrf})

    def test_sign_in_and_csrf_required(self):
        self.assertEqual(self.client.get("/api/state").status_code, 401)
        self.assertEqual(self.client.get(f"/api/reports/{self.session_id}.csv").status_code, 401)
        self.login()
        self.assertEqual(self.client.post("/api/sessions/close").status_code, 403)
        self.assertEqual(self.client.get("/api/state").status_code, 200)

    def test_dashboard_renders_and_escapes_student_content(self):
        self.login()
        self.assertIn(b"Attendance station", self.client.get("/").data)
        # Student values arrive as JSON; browser renderer uses textContent, never HTML.
        with self.client.get("/static/app.js") as response:
            self.assertIn(b"textContent", response.data)

    def test_dashboard_counts_saved_files_separately_from_student_flags(self):
        import numpy as np
        self.runtime.face = FaceRecognizer(None, self.path, {})
        np.savez_compressed(self.runtime.face.directory / f"{self.a}.npz",
                            samples=np.zeros((5, 160, 160), dtype=np.uint8))
        self.login()
        state = self.client.get("/api/state").json
        self.assertEqual(state["storage"]["available_faces"], 1)
        self.assertEqual(state["storage"]["database"], str(self.db.path))
        students = {s["id"]: s for s in state["students"]}
        self.assertEqual(students[self.a]["face_status"], "ready")
        self.assertEqual(students[self.b]["face_status"], "unavailable")
        self.assertEqual(students[self.b]["face_ready"], 1)

    def test_web_end_to_end_simulated_dual_check_and_export(self):
        self.login()
        response = self.post("/api/jobs", {"kind": "attendance", "sim_face": self.a, "sim_fingerprint": self.a})
        self.assertEqual(response.status_code, 202)
        self.runtime.worker.join(3)
        self.assertTrue(self.client.get("/api/state").json["job"]["result"]["accepted"])
        self.assertEqual(self.client.get(f"/api/reports/{self.session_id}.xlsx").status_code, 200)

    def test_roster_and_enrollment_locked_during_session(self):
        self.login()
        self.assertEqual(self.post("/api/students", {"roll": "A004", "name": "New", "class_name": "ECE-A"}).status_code, 400)
        self.assertEqual(self.post("/api/jobs", {"kind": "face", "student_id": self.a}).status_code, 400)

    def test_fingerprint_reenrollment_is_available_through_dashboard_api(self):
        self.login()
        self.db.close_session()
        response = self.post("/api/jobs", {"kind": "fingerprint", "student_id": self.a})
        self.assertEqual(response.status_code, 202)
        self.runtime.worker.join(3)
        self.assertEqual(self.runtime.snapshot()["status"], "done")
        self.assertEqual(self.db.student(self.a)["fingerprint_id"], self.a)

    def test_register_then_enroll_then_start(self):
        self.login()
        self.post("/api/sessions/close", {})
        sid = self.post("/api/students", {"roll": "A004", "name": "New", "class_name": "ECE-A"}).json["id"]
        for kind in ("face", "fingerprint"):
            self.assertEqual(self.post("/api/jobs", {"kind": kind, "student_id": sid}).status_code, 202)
            self.runtime.worker.join(3)
        self.assertEqual(self.post("/api/sessions", {"subject": "Test", "class_name": "ECE-A"}).status_code, 201)

    def test_invalid_student_and_report_input(self):
        self.login()
        self.post("/api/sessions/close", {})
        self.assertEqual(self.post("/api/students", {"roll": "", "name": "", "class_name": ""}).status_code, 400)
        self.assertEqual(self.client.get("/api/reports/999.csv").status_code, 400)


class FaceAndConfigTests(unittest.TestCase):
    def test_face_threshold_and_ambiguity(self):
        with tempfile.TemporaryDirectory() as directory:
            face = FaceRecognizer(None, Path(directory), {})
            self.assertEqual(face.choose([(1, 25), (2, 65)]), 1)
            self.assertIsNone(face.choose([(1, 65)]))
            self.assertIsNone(face.choose([(1, 25), (2, 29)]))
            self.assertIsNone(face.choose([(1, float("nan"))]))

    def test_real_opencv_training_pipeline_with_synthetic_samples(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as directory:
            face = FaceRecognizer(None, Path(directory), {})
            samples = np.random.default_rng(7).integers(0, 255, size=(5, 160, 160), dtype=np.uint8)
            np.savez_compressed(face.directory / "1.npz", samples=samples)
            model = face._models([{"id": 1, "face_ready": 1, "roll": "Test"}])[1]
            label, distance = model.predict(face._normalize(samples[0]))
            self.assertEqual(label, 1)
            self.assertLess(distance, .01)
            self.assertFalse(face._detector().empty())

    def test_lcd_exactly_two_lines_of_sixteen_ascii_characters(self):
        for value in ["Attendance Marked", "Short", "Éléphant 😊"]:
            self.assertEqual(len(lcd_line(value)), 16)
            self.assertTrue(lcd_line(value).isascii())

    def test_committed_profiles(self):
        for mode, name in [("simulation", "simulation"), ("laptop", "laptop"), ("raspberry_pi", "raspberry_pi")]:
            self.assertEqual(load_settings(f"config/{name}.toml").mode, mode)


if __name__ == "__main__":
    unittest.main()
