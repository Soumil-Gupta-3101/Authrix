"""Independent bring-up checks. Stop the dashboard before running this script."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
from threading import Event
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from attendance.config import load_settings
from attendance.devices.camera import Camera
from attendance.devices.fingerprint import R307
from attendance.locking import instance_lock


def check_saved_face(settings, expected_roll=None):
    """Reload disk enrollment and use the Pi camera without writing attendance."""
    from attendance.devices.face import FaceRecognizer
    from attendance.devices.sface import SFaceRecognizer

    path = settings.data_dir / "attendance.sqlite3"
    if not path.is_file():
        raise ValueError("Initialize the Pi profile and enroll a face first")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        mode = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()
        if not mode or mode[0] != "raspberry_pi":
            raise ValueError("Use the Pi database, not a copied laptop/simulation database")
        students = [dict(row) for row in db.execute("SELECT id,roll,face_ready FROM students")]
    if expected_roll is not None and not any(s["roll"] == expected_roll and s["face_ready"] for s in students):
        raise ValueError("The requested roll number needs face enrollment on the Pi first")
    camera = Camera(settings.mode, settings.section("camera"))
    cls = SFaceRecognizer if settings.section("face").get("backend", "lbph") == "sface" else FaceRecognizer
    face = cls(camera, settings.data_dir, settings.section("face"))
    try:
        models = face._models(students)
        print(f"Loaded {len(models)} face enrollments from {face.directory} ({cls.__name__})", flush=True)
        deadline = time.monotonic() + settings.section("authentication").get("face_timeout", 15)
        sid = face.identify(students, deadline, Event(), lambda message: print(message, flush=True))
        roll = next(s["roll"] for s in students if s["id"] == sid)
        if expected_roll is not None and roll != expected_roll:
            raise ValueError(f"Face check failed: recognized {roll}, expected {expected_roll}")
        print(f"Face check passed: roll {roll}. No enrollment or attendance was changed.")
        return sid
    finally:
        camera.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", choices=["camera", "face", "fingerprint", "lcd"])
    parser.add_argument("--config", default="config/raspberry_pi.toml")
    parser.add_argument("--roll", help="Expected enrolled roll number for the face check")
    args = parser.parse_args()
    settings = load_settings(args.config)
    if settings.mode != "raspberry_pi":
        raise ValueError("Use the Raspberry Pi configuration for hardware checks")
    with instance_lock(settings.data_dir):
        if args.device == "camera":
            camera = Camera(settings.mode, settings.section("camera"))
            try:
                frame = camera.read()
                print(f"Camera returned {frame.shape}. Preview test passes; no images saved.")
            finally:
                camera.close()
        elif args.device == "face":
            check_saved_face(settings, args.roll)
        elif args.device == "fingerprint":
            sensor = R307(settings.section("fingerprint"))
            try:
                slots = sensor.occupied()
                print(f"UART handshake passed. Capacity: {sensor.capacity}. Occupied slots: {sorted(slots)}")
                print("No fingerprints were enrolled or deleted.")
            finally:
                sensor.close()
        elif args.device == "lcd":
            from RPLCD.i2c import CharLCD
            options = settings.section("lcd")
            lcd = CharLCD(i2c_expander=options.get("expander", "PCF8574"), address=options.get("address", 0x27),
                          port=options.get("bus", 1), cols=16, rows=2, auto_linebreaks=False)
            try:
                lcd.write_string("LCD 16x2 ready  ")
                lcd.cursor_pos = (1, 0)
                lcd.write_string("I2C test passed ")
                print("Confirm both LCD lines are readable. The display clears in five seconds.")
                time.sleep(5)
            finally:
                lcd.close(clear=True)


if __name__ == "__main__":
    main()
