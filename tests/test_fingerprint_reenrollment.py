from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from attendance.config import Settings
from attendance.database import Database
from attendance.devices.common import DeviceError
from attendance.devices.fingerprint import R307
from attendance.runtime import Runtime


class FingerprintReenrollmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.settings = Settings("raspberry_pi", self.path, "Asia/Kolkata", {"lcd": {"enabled": False}})
        self.db = Database(self.path, "raspberry_pi")
        self.student = self.db.add_student("A001", "Student A", "ECE-A")
        self.other = self.db.add_student("A002", "Student B", "ECE-A")
        self.db.set_fingerprint(self.student, 0)
        self.db.set_fingerprint(self.other, 7)
        self.runtime = Runtime(self.settings, self.db)
        self.addCleanup(self.runtime.close)
        self.db.set_face(self.student)
        self.runtime.face = MagicMock()
        self.runtime.face.identify.return_value = self.student
        self.slots = {0, 7}
        self.sensor = MagicMock(spec=R307)
        self.sensor.occupied.side_effect = lambda: set(self.slots)
        self.sensor.enroll.side_effect = self.capture
        self.sensor.delete.side_effect = self.slots.discard
        self.runtime.fingerprint = self.sensor

    def capture(self, reserved, cancel, progress, *, replace_slot, before_store):
        self.assertIn(0, reserved)
        self.assertIn(7, reserved)
        self.assertEqual(replace_slot, 0)
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        before_store(1)
        self.slots.add(1)
        return 1

    def replace(self):
        self.runtime.submit("fingerprint", {"student_id": self.student})
        self.runtime.worker.join(3)
        self.assertFalse(self.runtime.worker.is_alive())
        return self.runtime.snapshot()

    def test_replacement_saves_mapping_before_deleting_only_own_old_slot(self):
        def delete(slot):
            self.assertEqual(self.db.student(self.student)["fingerprint_id"], 1)
            self.slots.discard(slot)

        self.sensor.delete.side_effect = delete
        result = self.replace()
        self.assertEqual(result["status"], "done")
        self.sensor.delete.assert_called_once_with(0)
        self.assertEqual(self.slots, {1, 7})
        self.assertEqual(self.db.student(self.other)["fingerprint_id"], 7)
        self.assertIsNone(self.db.student_for_fingerprint(0))
        self.assertEqual(self.db.student_for_fingerprint(1), self.student)
        self.assertEqual(self.db.pending_fingerprint_cleanup(), [])
        self.assertEqual(self.runtime.feedback.snapshot()["lines"][0].strip(), "Enrollment saved")

    def test_capture_failure_keeps_old_mapping_and_template(self):
        self.sensor.enroll.side_effect = DeviceError("Capture failed")
        result = self.replace()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        self.assertEqual(self.slots, {0, 7})
        self.sensor.delete.assert_not_called()

    def test_database_failure_removes_candidate_and_keeps_old_template(self):
        with patch.object(self.db, "replace_fingerprint", side_effect=RuntimeError("Database write failed")):
            result = self.replace()
        self.assertEqual(result["status"], "error")
        self.sensor.delete.assert_called_once_with(1)
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        self.assertEqual(self.slots, {0, 7})
        self.assertEqual(self.db.pending_fingerprint_cleanup(), [])

    def test_cancellation_after_capture_removes_candidate(self):
        def capture(*args, **kwargs):
            slot = self.capture(*args, **kwargs)
            self.runtime.cancel_event.set()
            return slot

        self.sensor.enroll.side_effect = capture
        result = self.replace()
        self.assertEqual(result["status"], "cancelled")
        self.sensor.delete.assert_called_once_with(1)
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        self.assertEqual(self.slots, {0, 7})

    def test_uncertain_sensor_write_is_cleaned_up(self):
        def capture(*args, **kwargs):
            self.capture(*args, **kwargs)
            raise DeviceError("Store acknowledgement timed out")

        self.sensor.enroll.side_effect = capture
        result = self.replace()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        self.assertEqual(self.slots, {0, 7})
        self.assertEqual(self.db.pending_fingerprint_cleanup(), [])

    def test_pending_old_template_stays_linked_and_cleanup_retries_on_scan(self):
        self.sensor.delete.side_effect = DeviceError("Delete failed")
        result = self.replace()
        self.assertEqual(result["status"], "done")
        self.assertIn("cleanup pending", result["warning"])
        reopened = Database(self.path, "raspberry_pi")
        self.assertEqual(reopened.student(self.student)["fingerprint_id"], 1)
        self.assertEqual(reopened.student_for_fingerprint(0), self.student)
        self.assertEqual(reopened.pending_fingerprint_cleanup(), [{"template_id": 0, "student_id": self.student}])
        self.db.start_session("Fingerprint replacement", "ECE-A")
        self.sensor.identify.return_value = 0
        self.runtime.submit("attendance", {})
        self.runtime.worker.join(3)
        self.assertTrue(self.runtime.snapshot()["result"]["accepted"])
        self.sensor.delete.side_effect = self.slots.discard
        self.sensor.identify.return_value = 1
        self.runtime.submit("attendance", {})
        self.runtime.worker.join(3)
        self.assertEqual(self.runtime.snapshot()["result"]["outcome"], "duplicate")
        self.assertIsNone(self.runtime.snapshot()["warning"])
        self.assertIsNone(reopened.student_for_fingerprint(0))
        self.assertEqual(reopened.pending_fingerprint_cleanup(), [])
        self.assertEqual(self.slots, {1, 7})

    def test_interrupted_candidate_is_recovered_before_next_replacement(self):
        self.db.reserve_fingerprint_template(2)
        self.slots.add(2)
        reopened = Database(self.path, "raspberry_pi")
        self.assertEqual(reopened.pending_fingerprint_cleanup(), [{"template_id": 2, "student_id": None}])
        self.assertIsNone(reopened.student_for_fingerprint(2))
        self.assertEqual(self.replace()["status"], "done")
        self.assertEqual([call.args[0] for call in self.sensor.delete.call_args_list], [2, 0])
        self.assertEqual(self.slots, {1, 7})

    def test_session_started_during_capture_aborts_replacement(self):
        def capture(*args, **kwargs):
            slot = self.capture(*args, **kwargs)
            self.db.start_session("Concurrent session", "ECE-A")
            return slot

        self.sensor.enroll.side_effect = capture
        result = self.replace()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 0)
        self.assertEqual(self.slots, {0, 7})

    def test_concurrent_mapping_change_is_preserved(self):
        def capture(*args, **kwargs):
            slot = self.capture(*args, **kwargs)
            self.db.set_fingerprint(self.student, 3)
            self.slots.add(3)
            return slot

        self.sensor.enroll.side_effect = capture
        result = self.replace()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.db.student(self.student)["fingerprint_id"], 3)
        self.sensor.delete.assert_called_once_with(1)
        self.assertEqual(self.slots, {0, 3, 7})

    def test_existing_database_gains_recovery_table_and_keeps_enrollments(self):
        with self.db.connect() as connection:
            connection.execute("DROP TABLE fingerprint_cleanup")
        reopened = Database(self.path, "raspberry_pi")
        self.assertEqual(reopened.student(self.student)["fingerprint_id"], 0)
        self.assertTrue(reopened.student(self.student)["face_ready"])
        self.assertEqual(reopened.student(self.other)["fingerprint_id"], 7)
        self.assertEqual(reopened.pending_fingerprint_cleanup(), [])

    def test_cleanup_preserves_a_slot_assigned_to_another_student(self):
        self.db.reserve_fingerprint_template(2)
        third = self.db.add_student("A003", "Student C", "ECE-A")
        self.db.set_fingerprint(third, 2)
        self.slots.add(2)
        self.assertEqual(self.replace()["status"], "done")
        self.sensor.delete.assert_called_once_with(0)
        self.assertEqual(self.db.student_for_fingerprint(2), third)
        self.assertEqual(self.slots, {1, 2, 7})


if __name__ == "__main__":
    unittest.main()
