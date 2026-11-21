"""SQLite persistence. Attendance acceptance is a single serialized transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import sqlite3


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, directory, mode):
        self.directory = directory
        self.path = directory / "attendance.sqlite3"
        directory.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS students(
                    id INTEGER PRIMARY KEY, roll TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
                    class_name TEXT NOT NULL, face_ready INTEGER NOT NULL DEFAULT 0,
                    fingerprint_id INTEGER UNIQUE, created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS fingerprint_cleanup(
                    template_id INTEGER PRIMARY KEY,
                    student_id INTEGER REFERENCES students(id));
                CREATE TABLE IF NOT EXISTS sessions(
                    id INTEGER PRIMARY KEY, subject TEXT NOT NULL, class_name TEXT NOT NULL,
                    started_at TEXT NOT NULL, ended_at TEXT, active INTEGER NOT NULL DEFAULT 1);
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_session ON sessions(active) WHERE active=1;
                CREATE TABLE IF NOT EXISTS roster(
                    session_id INTEGER NOT NULL REFERENCES sessions(id),
                    student_id INTEGER NOT NULL REFERENCES students(id),
                    roll TEXT NOT NULL, name TEXT NOT NULL,
                    PRIMARY KEY(session_id,student_id));
                CREATE TABLE IF NOT EXISTS attendance(
                    id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES sessions(id),
                    student_id INTEGER NOT NULL REFERENCES students(id), recorded_at TEXT NOT NULL,
                    method TEXT NOT NULL CHECK(method='Face + Fingerprint'), mode TEXT NOT NULL,
                    UNIQUE(session_id,student_id));
                CREATE TABLE IF NOT EXISTS attempts(
                    id INTEGER PRIMARY KEY, session_id INTEGER REFERENCES sessions(id),
                    face_id INTEGER, fingerprint_id INTEGER, outcome TEXT NOT NULL,
                    created_at TEXT NOT NULL, mode TEXT NOT NULL);
            """)
            db.execute("INSERT OR IGNORE INTO metadata VALUES ('mode',?)", (mode,))
            actual = db.execute("SELECT value FROM metadata WHERE key='mode'").fetchone()[0]
            if actual != mode:
                raise ValueError(f"Database belongs to {actual}; use a separate data_dir for {mode}")
        self.mode = mode

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def students(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM students ORDER BY roll")]

    def student(self, student_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM students WHERE id=?", (student_id,)).fetchone()
            if row is None:
                raise ValueError("Student not found")
            return dict(row)

    def add_student(self, roll, name, class_name):
        fields = [str(x).strip() if isinstance(x, str) else "" for x in (roll, name, class_name)]
        if any(not x or len(x) > 100 or any(ord(c) < 32 for c in x) for x in fields):
            raise ValueError("Roll, name and class are required (1-100 characters)")
        with self.connect() as db:
            return db.execute("INSERT INTO students(roll,name,class_name,created_at) VALUES (?,?,?,?)",
                              (*fields, now_iso())).lastrowid

    def set_face(self, student_id):
        with self.connect() as db:
            db.execute("UPDATE students SET face_ready=1 WHERE id=?", (student_id,))

    def set_fingerprint(self, student_id, template_id):
        self.student(student_id)
        with self.connect() as db:
            db.execute("UPDATE students SET fingerprint_id=? WHERE id=?", (template_id, student_id))

    def student_for_fingerprint(self, template_id):
        with self.connect() as db:
            row = db.execute("SELECT id FROM students WHERE fingerprint_id=?", (template_id,)).fetchone()
            if row is None:
                row = db.execute("SELECT student_id FROM fingerprint_cleanup WHERE template_id=? AND student_id IS NOT NULL",
                                 (template_id,)).fetchone()
            return row[0] if row else None

    def reserve_fingerprint_template(self, template_id):
        """Record a candidate before writing sensor flash, for crash recovery."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sessions WHERE active=1").fetchone():
                raise ValueError("Close the session before changing biometric enrollment")
            if db.execute("SELECT 1 FROM students WHERE fingerprint_id=?", (template_id,)).fetchone():
                raise ValueError("Fingerprint slot is already assigned")
            db.execute("INSERT INTO fingerprint_cleanup(template_id) VALUES (?)", (template_id,))

    def replace_fingerprint(self, student_id, template_id, previous_slot):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sessions WHERE active=1").fetchone():
                raise ValueError("Close the session before changing biometric enrollment")
            updated = db.execute("UPDATE students SET fingerprint_id=? WHERE id=? AND fingerprint_id IS ?",
                                 (template_id, student_id, previous_slot))
            if updated.rowcount != 1:
                raise ValueError("Fingerprint enrollment changed during capture. Retry the operation")
            db.execute("DELETE FROM fingerprint_cleanup WHERE template_id=?", (template_id,))
            if previous_slot is not None and previous_slot != template_id:
                db.execute("INSERT INTO fingerprint_cleanup(template_id,student_id) VALUES (?,?)",
                           (previous_slot, student_id))

    def pending_fingerprint_cleanup(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM fingerprint_cleanup ORDER BY template_id")]

    def finish_fingerprint_cleanup(self, template_id):
        with self.connect() as db:
            db.execute("DELETE FROM fingerprint_cleanup WHERE template_id=?", (template_id,))

    def sessions(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM sessions ORDER BY id DESC")]

    def active_session(self):
        with self.connect() as db:
            row = db.execute("SELECT * FROM sessions WHERE active=1").fetchone()
            return dict(row) if row else None

    def start_session(self, subject, class_name):
        if any(not isinstance(x, str) or not x.strip() or len(x) > 100 for x in (subject, class_name)):
            raise ValueError("Subject and class are required (up to 100 characters)")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM sessions WHERE active=1").fetchone():
                raise ValueError("Close the active session first")
            roster = db.execute("SELECT id,roll,name FROM students WHERE class_name=?", (class_name.strip(),)).fetchall()
            if not roster:
                raise ValueError("Register students in this class before starting a session")
            sid = db.execute("INSERT INTO sessions(subject,class_name,started_at) VALUES (?,?,?)",
                             (subject.strip(), class_name.strip(), now_iso())).lastrowid
            db.executemany("INSERT INTO roster VALUES (?,?,?,?)", [(sid, *tuple(r)) for r in roster])
            return sid

    def close_session(self):
        with self.connect() as db:
            db.execute("UPDATE sessions SET active=0, ended_at=? WHERE active=1", (now_iso(),))

    def decide(self, session_id, face_id, fingerprint_student_id, failure=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT * FROM sessions WHERE active=1").fetchone()
            known_session = db.execute("SELECT id FROM sessions WHERE id=?", (session_id,)).fetchone()
            result = failure
            if result is None:
                if not active or active["id"] != session_id:
                    result = "session_changed"
                elif face_id is None or fingerprint_student_id is None:
                    result = "missing_biometric"
                elif face_id != fingerprint_student_id:
                    result = "identity_mismatch"
                else:
                    student = db.execute("SELECT * FROM students WHERE id=?", (face_id,)).fetchone()
                    if not student or not student["face_ready"] or student["fingerprint_id"] is None:
                        result = "not_enrolled"
                    elif not db.execute("SELECT 1 FROM roster WHERE session_id=? AND student_id=?", (session_id, face_id)).fetchone():
                        result = "wrong_class"
                    elif db.execute("SELECT 1 FROM attendance WHERE session_id=? AND student_id=?", (session_id, face_id)).fetchone():
                        result = "duplicate"
                    else:
                        db.execute("INSERT INTO attendance(session_id,student_id,recorded_at,method,mode) VALUES (?,?,?,'Face + Fingerprint',?)",
                                   (session_id, face_id, now_iso(), self.mode))
                        result = "accepted"
            db.execute("INSERT INTO attempts(session_id,face_id,fingerprint_id,outcome,created_at,mode) VALUES (?,?,?,?,?,?)",
                       (session_id if known_session else None, face_id, fingerprint_student_id, result, now_iso(), self.mode))
            return {"outcome": result, "accepted": result == "accepted", "student_id": face_id}

    def report(self, session_id):
        with self.connect() as db:
            session = db.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
            if not session:
                raise ValueError("Session not found")
            rows = db.execute("""SELECT r.roll,r.name,s.class_name,s.subject,s.started_at,
                       a.recorded_at,a.method,COALESCE(a.mode,?) AS mode,
                       CASE WHEN a.id IS NOT NULL THEN 'Present'
                            WHEN s.active=1 THEN 'Pending' ELSE 'Absent' END AS status
                       FROM roster r JOIN sessions s ON s.id=r.session_id
                       LEFT JOIN attendance a ON a.student_id=r.student_id AND a.session_id=r.session_id
                       WHERE r.session_id=? ORDER BY r.roll""", (self.mode, session_id)).fetchall()
            return [dict(r) for r in rows]

    def recent_attempts(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM attempts ORDER BY id DESC LIMIT 20")]
