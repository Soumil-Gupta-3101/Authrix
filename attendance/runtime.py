from copy import deepcopy
import logging
from threading import Event, Lock, Thread
import time

from .devices.camera import Camera
from .devices.common import Cancelled, DeviceError, check_deadline
from .devices.face import FaceRecognizer
from .devices.feedback import Feedback
from .devices.fingerprint import R307

log = logging.getLogger(__name__)

MESSAGES = {
    "accepted": "Attendance saved", "duplicate": "Already marked", "identity_mismatch": "Identity differs",
    "missing_biometric": "Both required", "not_enrolled": "Not enrolled", "wrong_class": "Wrong class",
    "session_changed": "Session changed", "timeout": "Scan timed out", "cancelled": "Cancelled",
    "device_error": "Device error", "fingerprint_unknown": "Finger unknown",
}


class Runtime:
    def __init__(self, settings, database):
        self.settings, self.db = settings, database
        self.feedback = Feedback(settings)
        self.camera = Camera(settings.mode, settings.section("camera")) if settings.mode != "simulation" else None
        self.face = FaceRecognizer(self.camera, settings.data_dir, settings.section("face")) if self.camera else None
        if self.camera and settings.section("face").get("backend", "lbph") == "sface":
            from .devices.sface import SFaceRecognizer
            self.face = SFaceRecognizer(self.camera, settings.data_dir, settings.section("face"))
        self.fingerprint = R307(settings.section("fingerprint")) if settings.mode == "raspberry_pi" else None
        self.gate, self.state_lock = Lock(), Lock()
        self.cancel_event = Event()
        self.worker = None
        self.job = {"status": "idle", "message": "Ready", "result": None, "kind": None}
        if self.face:
            students = self.db.students()
            log.info("Attendance database: %s; face samples: %s", self.db.path, self.face.directory)
            if any(s["face_ready"] for s in students):
                try:
                    models = self.face._models(students)
                    log.info("Loaded %s face enrollments from disk (%s)", len(models), type(self.face).__name__)
                except DeviceError as exc:
                    self.update(warning=str(exc))
                    log.warning("Face enrollment needs attention: %s", exc)

    def students(self):
        students = self.db.students()
        if self.face:
            status = self.face.enrollment_status(students)
            return [dict(s, **status[s["id"]]) for s in students]
        return [dict(s, face_status="simulated" if s["face_ready"] else "needed", face_samples=0,
                     face_error=None) for s in students]

    def snapshot(self):
        with self.state_lock:
            return deepcopy(self.job)

    def update(self, **values):
        with self.state_lock:
            self.job.update(values)

    def progress(self, message):
        self.update(message=message)
        self.feedback.show(message[:16], message[16:32])

    def submit(self, kind, payload):
        if kind not in {"attendance", "face", "fingerprint", "diagnostics"}:
            raise ValueError("Unknown operation")
        if self.settings.mode == "raspberry_pi" and any(k.startswith("sim_") for k in payload):
            raise ValueError("Simulated biometric input is disabled on Raspberry Pi")
        if self.settings.mode == "laptop" and "sim_face" in payload:
            raise ValueError("Laptop mode uses the real webcam for faces")
        if not self.gate.acquire(blocking=False):
            raise ValueError("A device operation is already running; stop it first")
        try:
            active = self.db.active_session()
            if kind == "attendance" and not active:
                raise ValueError("Start a class session first")
            if kind in {"face", "fingerprint"}:
                if active:
                    raise ValueError("Close the session before changing biometric enrollment")
                self.db.student(int(payload.get("student_id", 0)))
            payload = dict(payload)
            payload["session_id"] = active["id"] if active else None
            for key in ("sim_face", "sim_fingerprint"):
                if key in payload and payload[key] is not None and (type(payload[key]) is not int or payload[key] <= 0):
                    raise ValueError("Simulated identity must be a student ID or null")
            self.cancel_event.clear()
            self.update(status="running", message="Starting", kind=kind, result=None, warning=None)
            self.worker = Thread(target=self._run, args=(kind, payload), name="biometric-worker", daemon=True)
            self.worker.start()
        except Exception:
            self.gate.release()
            raise
        return self.snapshot()

    def _run(self, kind, payload):
        try:
            if kind == "attendance":
                while True:
                    result = self.attempt(payload)
                    self.update(result=result, message=MESSAGES.get(result["outcome"], result["outcome"]))
                    if not payload.get("continuous") or result["outcome"] in {"device_error", "session_changed", "cancelled"}:
                        break
                    if self.cancel_event.wait(2):
                        break
            elif kind == "diagnostics":
                self.diagnostics()
            else:
                sid = int(payload["student_id"])
                if kind == "face":
                    if self.face:
                        self.face.enroll(sid, self.cancel_event, self.progress)
                    self.db.set_face(sid)
                else:
                    if self.fingerprint:
                        previous_slot = self.db.student(sid)["fingerprint_id"]
                        self._cleanup_fingerprints()
                        reserved = [s["fingerprint_id"] for s in self.db.students() if s["fingerprint_id"] is not None]
                        reserved += [item["template_id"] for item in self.db.pending_fingerprint_cleanup()]
                        try:
                            slot = self.fingerprint.enroll(reserved, self.cancel_event, self.progress,
                                                          replace_slot=previous_slot,
                                                          before_store=self.db.reserve_fingerprint_template)
                            if self.cancel_event.is_set():
                                raise Cancelled("Operation cancelled")
                            self.db.replace_fingerprint(sid, slot, previous_slot)
                        except Exception:
                            self._cleanup_fingerprints()
                            raise
                        self._cleanup_fingerprints()
                    else:
                        self.db.set_fingerprint(sid, sid)
                self.progress("Enrollment saved")
            self.update(status="cancelled" if self.cancel_event.is_set() else "done")
        except Cancelled:
            self.update(status="cancelled", message="Operation cancelled")
        except Exception as exc:
            log.exception("Device operation failed")
            self.update(status="error", message=str(exc))
        finally:
            for device in (self.camera, self.fingerprint):
                if device:
                    try:
                        device.close()
                    except Exception:
                        log.exception("Device cleanup failed")
            self.gate.release()

    def _cleanup_fingerprints(self):
        warnings = []
        try:
            pending = self.db.pending_fingerprint_cleanup()
            if not pending:
                return
            current_slots = {s["fingerprint_id"] for s in self.db.students()}
            occupied = self.fingerprint.occupied()
            for item in pending:
                slot = item["template_id"]
                try:
                    if slot not in current_slots and slot in occupied:
                        self.fingerprint.delete(slot)
                    self.db.finish_fingerprint_cleanup(slot)
                except Exception as exc:
                    detail = "Previous fingerprint remains active. " if item["student_id"] is not None else ""
                    warnings.append(f"Fingerprint cleanup pending for slot {slot}. {detail}{exc}")
        except Exception as exc:
            warnings.append(f"Fingerprint cleanup will retry. A previous fingerprint may remain active. {exc}")
        for warning in warnings:
            log.warning(warning)
        if warnings:
            warnings.append("Cleanup will retry during the next fingerprint operation")
        self.update(warning=". ".join(warnings) or None)

    def attempt(self, payload):
        """Fresh evidence is local to this call and tied to one session ID."""
        self.update(warning=None)
        face_id = finger_id = None
        failure = None
        auth = self.settings.section("authentication")
        deadline = time.monotonic() + auth.get("attempt_timeout", 35)
        try:
            self.feedback.show("Look at camera", "Face + finger")
            if self.face:
                face_id = self.face.identify(self.db.students(), min(deadline, time.monotonic() + auth.get("face_timeout", 15)),
                                             self.cancel_event, self.progress)
            else:
                face_id = payload.get("sim_face")
            check_deadline(deadline, self.cancel_event)
            if face_id is None:
                failure = "missing_biometric"
            else:
                if self.fingerprint:
                    self._cleanup_fingerprints()
                    slot = self.fingerprint.identify(min(deadline, time.monotonic() + auth.get("fingerprint_timeout", 15)),
                                                     self.cancel_event, self.progress)
                    finger_id = self.db.student_for_fingerprint(slot) if slot is not None else None
                    if finger_id is None:
                        failure = "fingerprint_unknown"
                else:
                    finger_id = payload.get("sim_fingerprint")
                check_deadline(deadline, self.cancel_event)
        except Cancelled:
            failure = "cancelled"
        except TimeoutError as exc:
            self.update(warning=str(exc))
            failure = "timeout"
        except Exception as exc:
            log.exception("Attendance device failure")
            self.update(warning=str(exc))
            failure = "device_error"
        result = self.db.decide(payload["session_id"], face_id, finger_id, failure)
        try:
            detail = self.db.student(face_id)["roll"] if result["accepted"] else MESSAGES[result["outcome"]]
            self.feedback.result(result["accepted"], detail)
        except Exception as exc:
            # A committed attendance row stays committed if the display fails afterwards.
            log.exception("Feedback failed; attendance outcome remains %s", result["outcome"])
            self.update(warning=f"Feedback failed after decision: {exc}")
        return result

    def diagnostics(self):
        self.progress("Testing devices")
        if self.camera:
            self.camera.read()
            if self.face:
                self.face._detector()
        details = "Simulation devices ready"
        if self.fingerprint:
            self._cleanup_fingerprints()
            occupied = self.fingerprint.occupied()
            details = f"Fingerprint UART OK: {len(occupied)}/{self.fingerprint.capacity} slots used"
        self.feedback.show("Device test", "Devices ready")
        self.update(message=details + ". Check the station display for LCD status. No attendance was recorded.")

    def cancel(self):
        self.cancel_event.set()

    def close(self):
        self.cancel()
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=5)
        if not self.worker or not self.worker.is_alive():
            self.feedback.close()
