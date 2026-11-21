import argparse
from contextlib import closing
import getpass
import json
import logging
import os
from pathlib import Path
import secrets
import shutil
import signal
import sqlite3
import sys

from .config import load_settings
from .database import Database
from .devices.common import DeviceError
from .locking import instance_lock


def initialize(settings, db, demo=False, reset_password=False):
    from werkzeug.security import generate_password_hash
    if demo and settings.mode != "simulation":
        raise ValueError("--demo is only allowed with simulation.toml")
    credentials = settings.data_dir / "credentials.json"
    if not credentials.exists() or reset_password:
        password = os.environ.get("ATTENDANCE_ADMIN_PASSWORD") or getpass.getpass("Create a teacher password (at least 12 characters): ")
        if len(password) < 12:
            raise ValueError("Use at least 12 characters for the teacher password")
        if "ATTENDANCE_ADMIN_PASSWORD" not in os.environ and password != getpass.getpass("Repeat password: "):
            raise ValueError("Passwords do not match")
        credentials.write_text(json.dumps({"secret_key": secrets.token_hex(32), "password_hash": generate_password_hash(password)}), encoding="utf-8")
        try:
            credentials.chmod(0o600)
            settings.data_dir.chmod(0o700)
        except OSError:
            pass
    if demo:
        if not db.students():
            for roll, name in [("DEMO001", "Demo Student One"), ("DEMO002", "Demo Student Two")]:
                sid = db.add_student(roll, name, "ECE-A")
                db.set_face(sid)
                db.set_fingerprint(sid, sid)
    print(f"Initialized {settings.mode}. Data: {settings.data_dir}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Portable face AND fingerprint attendance")
    parser.add_argument("--config", default="config/simulation.toml")
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init", help="Create database and teacher password")
    init.add_argument("--demo", action="store_true")
    init.add_argument("--reset-password", action="store_true")
    serve = sub.add_parser("serve", help="Start teacher dashboard")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    sub.add_parser("doctor", help="Check software imports and configuration without opening hardware")
    sub.add_parser("setup-face-models", help="Download and verify the offline YuNet/SFace models (about 39 MB)")
    backup = sub.add_parser("backup", help="Back up database and face files; stop the server first")
    backup.add_argument("destination")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings(args.config)
    if args.command == "setup-face-models":
        from .face_models import download_models, model_directory
        download_models(model_directory(settings.section("face")))
        return
    if args.command == "doctor":
        import importlib
        modules = ["flask", "waitress", "openpyxl"]
        if settings.mode != "simulation":
            modules += ["numpy", "cv2"]
        if settings.mode == "raspberry_pi":
            modules += ["picamera2", "libcamera", "serial"]
        for name in modules:
            module = importlib.import_module(name)
            if name == "cv2" and not hasattr(module, "face"):
                raise ValueError("cv2.face is missing; install OpenCV contrib")
            print(f"OK {name}")
        if settings.mode == "raspberry_pi" and settings.section("lcd").get("enabled", True):
            for name in ("RPLCD.i2c", "smbus"):
                try:
                    importlib.import_module(name)
                    print(f"OK optional LCD dependency {name}")
                except (ImportError, OSError) as exc:
                    print(f"Optional LCD dependency unavailable: {name} ({exc}). Dashboard feedback is available.")
        print(f"Mode: {settings.mode}; data: {settings.data_dir}")
        if settings.mode != "simulation":
            from .devices.face import FaceRecognizer
            face = FaceRecognizer(None, settings.data_dir, settings.section("face"))
            if settings.section("face").get("backend", "lbph") == "sface":
                from .devices.sface import SFaceRecognizer
                face = SFaceRecognizer(None, settings.data_dir, settings.section("face"))
                face.check_models()
                print("OK YuNet/SFace models (SHA-256 verified; both networks ran)")
            db_path = settings.data_dir / "attendance.sqlite3"
            if db_path.is_file():
                with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as connection:
                    connection.row_factory = sqlite3.Row
                    students = [dict(row) for row in connection.execute("SELECT * FROM students")]
                status = face.enrollment_status(students)
                count = sum(item["face_status"] == "ready" for item in status.values())
                print(f"Registered students: {len(students)}; available face enrollments: {count}")
                for sid, item in status.items():
                    if item["face_error"]:
                        print(f"NEEDS ATTENTION student {sid}: {item['face_error']}")
        print("Software imports passed. Run Device test from the dashboard to test connected hardware.")
        return
    with instance_lock(settings.data_dir):
        run_command(args, settings)


def run_command(args, settings):
    db = Database(settings.data_dir, settings.mode)
    if args.command == "init":
        initialize(settings, db, args.demo, args.reset_password)
    elif args.command == "backup":
        destination = Path(args.destination).resolve()
        if destination == settings.data_dir or settings.data_dir in destination.parents:
            raise ValueError("Choose a new backup directory outside the data directory")
        destination.mkdir(parents=True, exist_ok=False)
        with db.connect() as source, sqlite3.connect(destination / "attendance.sqlite3") as target:
            source.backup(target)
        for name in ("faces", "credentials.json"):
            path = settings.data_dir / name
            if path.is_dir():
                shutil.copytree(path, destination / name)
            elif path.exists():
                shutil.copy2(path, destination / name)
        destination.chmod(0o700)
        print(f"Backup saved to {destination}. Store privately; includes biometric samples and credentials.")
    else:
        from waitress import serve
        from .web import create_app
        app = create_app(settings, db)
        host = args.host or settings.section("web").get("host", "127.0.0.1")
        port = args.port or settings.section("web").get("port", 8765)
        print(f"Attendance ({settings.mode}): http://{host}:{port}", flush=True)
        print(f"Data directory: {settings.data_dir}", flush=True)
        def stop_server(signum, frame):
            raise KeyboardInterrupt
        signal.signal(signal.SIGTERM, stop_server)
        try:
            serve(app, host=host, port=port, threads=6)
        finally:
            app.extensions["attendance_runtime"].close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Attendance server stopped.")
    except (ValueError, ImportError, OSError, DeviceError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        sys.exit(1)
