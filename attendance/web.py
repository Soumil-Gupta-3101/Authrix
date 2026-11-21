from datetime import timedelta
from functools import wraps
from io import BytesIO
import json
import secrets
import sqlite3
from threading import Lock
import time

from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for
from werkzeug.security import check_password_hash

from .reports import export_csv, export_xlsx
from .runtime import Runtime


def create_app(settings, database, runtime=None):
    credentials_path = settings.data_dir / "credentials.json"
    if not credentials_path.is_file():
        raise ValueError("Run the init command first to create your teacher password")
    credentials = json.loads(credentials_path.read_text(encoding="utf-8"))
    app = Flask(__name__)
    app.config.update(SECRET_KEY=credentials["secret_key"], MAX_CONTENT_LENGTH=16 * 1024,
                      SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Strict",
                      PERMANENT_SESSION_LIFETIME=timedelta(hours=8))
    runtime = runtime or Runtime(settings, database)
    app.extensions["attendance_runtime"] = runtime
    failures, login_lock = [], Lock()

    def token():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        return session["csrf"]

    app.jinja_env.globals["csrf_token"] = token

    @app.before_request
    def csrf_protection():
        if request.method == "POST":
            provided = request.headers.get("X-CSRF-Token", "") or request.form.get("csrf", "")
            if not secrets.compare_digest(provided, session.get("csrf", "!")):
                return jsonify(error="Page expired. Reload and try again"), 403

    @app.after_request
    def headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        return response

    def teacher(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            if not session.get("teacher"):
                if request.path.startswith("/api/"):
                    return jsonify(error="Sign in to continue"), 401
                return redirect(url_for("login"))
            return fn(*args, **kwargs)
        return wrapped

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        if request.method == "POST":
            with login_lock:
                current = time.monotonic()
                failures[:] = [stamp for stamp in failures if current - stamp < 60]
                if len(failures) >= 5:
                    return render_template("login.html", error="Too many attempts. Wait one minute"), 429
                if check_password_hash(credentials["password_hash"], request.form.get("password", "")):
                    session.clear()
                    session["teacher"] = True
                    session.permanent = True
                    token()
                    failures.clear()
                    return redirect(url_for("index"))
                failures.append(current)
            error = "Incorrect teacher password"
        return render_template("login.html", error=error)

    @app.post("/logout")
    @teacher
    def logout():
        runtime.cancel()
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @teacher
    def index():
        return render_template("index.html", mode=settings.mode)

    @app.get("/api/state")
    @teacher
    def state():
        active = database.active_session()
        students = runtime.students()
        storage = {"directory": str(settings.data_dir), "database": str(database.path),
                   "faces_directory": str(settings.data_dir / "faces"),
                   "face_backend": settings.section("face").get("backend", "lbph") if runtime.face else "simulated",
                   "available_faces": sum(s["face_status"] in {"ready", "simulated"} for s in students)}
        return jsonify(mode=settings.mode, timezone=settings.timezone, students=students, storage=storage,
                       sessions=database.sessions(), active_session=active,
                       report=database.report(active["id"]) if active else [],
                       attempts=database.recent_attempts(), job=runtime.snapshot(), feedback=runtime.feedback.snapshot())

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError("A JSON object is required")
        return value

    @app.post("/api/students")
    @teacher
    def add_student():
        payload = data()
        if database.active_session():
            raise ValueError("Close the current session before changing the class roster")
        sid = database.add_student(payload.get("roll"), payload.get("name"), payload.get("class_name"))
        return jsonify(id=sid), 201

    @app.post("/api/sessions")
    @teacher
    def start_session():
        payload = data()
        if runtime.snapshot()["status"] == "running":
            raise ValueError("Stop the device operation before starting a session")
        return jsonify(id=database.start_session(payload.get("subject"), payload.get("class_name"))), 201

    @app.post("/api/sessions/close")
    @teacher
    def close_session():
        # Transactional session check rejects any operation already in flight.
        database.close_session()
        runtime.cancel()
        return jsonify(ok=True)

    @app.post("/api/jobs")
    @teacher
    def start_job():
        payload = data()
        return jsonify(runtime.submit(payload.get("kind"), payload)), 202

    @app.post("/api/jobs/cancel")
    @teacher
    def cancel_job():
        runtime.cancel()
        return jsonify(ok=True)

    @app.get("/api/camera.jpg")
    @teacher
    def camera():
        jpeg = runtime.camera.jpeg() if runtime.camera else None
        if jpeg is None:
            return "", 204
        return send_file(BytesIO(jpeg), mimetype="image/jpeg")

    @app.get("/api/reports/<int:session_id>.<format>")
    @teacher
    def report(session_id, format):
        if format not in {"csv", "xlsx"}:
            raise ValueError("Report format must be csv or xlsx")
        rows = database.report(session_id)
        out = export_csv(rows, settings.timezone) if format == "csv" else export_xlsx(rows, settings.timezone)
        mime = "text/csv" if format == "csv" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        return send_file(BytesIO(out), mimetype=mime, as_attachment=True,
                         download_name=f"attendance-{settings.mode}-session-{session_id}.{format}")

    @app.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    @app.errorhandler(sqlite3.IntegrityError)
    def conflict(exc):
        return jsonify(error="That roll number, fingerprint or active session already exists"), 409

    return app
