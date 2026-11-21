# Smart multimodal biometric attendance

A local Python application for your Raspberry Pi 4 or 5, Pi camera and UART fingerprint sensor, with an optional **16×2 I²C LCD**. A teacher dashboard handles registration, sessions, enrollment, scans and reports, and displays all prompts and results. No cloud account is needed.

**Attendance requires face AND fingerprint recognition for the same enrolled student, in the active class session.** Missing inputs, unknown identities, different identities, timeouts, hardware failures, wrong classes and duplicates cannot create attendance. There is no face-only or fingerprint-only attendance mode.

## Start on this laptop

An isolated `.venv` has been created in this project. From PowerShell in this folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-laptop.txt
.\.venv\Scripts\python.exe -m attendance --config config/simulation.toml init --demo
.\.venv\Scripts\python.exe -m attendance --config config/simulation.toml serve
```

Create a teacher password when prompted. Open **http://127.0.0.1:8765** and sign in. Two explicitly named demo students are seeded. Start an `ECE-A` session, select the same student in both simulated input selectors, and click **Scan once**. Try a different student in each selector, omit an input, and repeat a successful scan to see the rejection rules.

For a fresh computer, install Python **3.12** (3.11–3.13 are supported), then create `.venv` with `py -3.12 -m venv .venv` first. Python 3.14 is not the target for the pinned laptop NumPy dependency. The optional launcher `powershell -ExecutionPolicy Bypass -File scripts/start.ps1 -Mode simulation` creates the environment, installs dependencies and starts the app; this policy applies only to that PowerShell process.

## Test real face recognition next

Stop the server with Ctrl+C, then run:

```powershell
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml setup-face-models
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml init
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml serve
```

Register a student, **Enroll face** while looking at the laptop webcam, then **Enroll finger** to create a simulated fingerprint mapping. Start a session, select that student's simulated fingerprint and scan your real face. The webcam belongs to the computer running Python, not to a phone viewing the dashboard. Camera access is limited to capture operations.

The [laptop test guide](docs/LAPTOP_TESTING.md) explains enrollment, test cases and face threshold calibration.

Both laptop and Raspberry Pi profiles use **YuNet + SFace** for landmark alignment and recognition. `setup-face-models` downloads about 39 MB from OpenCV Zoo once and verifies SHA-256 checksums. Enrollment and recognition then work offline. Existing grayscale face archives load automatically; recapture is not required to upgrade a profile.

## Where enrollment data lives

With `config/laptop.toml`, the project stores:

- `data/laptop/attendance.sqlite3`: students, enrollment flags, fingerprint mappings, sessions and attendance.
- `data/laptop/faces/<student ID>.npz`: the captured face images, one compressed archive per enrolled student. The ID is the database ID, not the roll number.
- `data/laptop/credentials.json`: teacher password hash and login session secret.

Paths are resolved relative to the configuration file, so restarting from another working directory with that same config uses the same data. The dashboard shows registered students separately from available face enrollments and displays the full storage paths under **Student registry → Saved data on this computer**. Missing or damaged face files are marked **Unavailable**. The server validates and loads saved faces at startup; it does not depend on the previous process's memory.

## Three explicit operating modes

| Profile | Face | Fingerprint | Feedback | Data directory |
|---|---|---|---|---|
| `config/simulation.toml` | Simulated | Simulated | Dashboard | `data/simulation` |
| `config/laptop.toml` | Real webcam, YuNet + SFace | Simulated | Dashboard | `data/laptop` |
| `config/raspberry_pi.toml` | Real Picamera2, YuNet + SFace | Real R307/R30x UART | Dashboard and optional 16×2 I²C LCD | `data/raspberry_pi` |

The databases and reports identify their mode. Hardware mode rejects simulated input fields and cannot open a simulation/laptop database. **Re-enroll students on the Pi**; do not copy simulated fingerprint mappings into it.

## Move to Raspberry Pi

Follow [the complete Raspberry Pi setup and wiring plan](docs/RASPBERRY_PI_SETUP.md), starting from a fresh 64-bit Raspberry Pi OS. It includes packages, I²C/UART setup, physical pin numbers, camera connection, individual device tests, startup service, troubleshooting and acceptance testing.

After installing the OS packages in that guide, run `bash scripts/setup_pi.sh` on the Pi. It installs the ARM64 OpenCV package while preserving the camera stack's NumPy version, installs the offline face models, and checks both neural networks. The Pi uses the same matching thresholds and enrollment loading as the laptop. The guide includes a before/after-reboot face check that does not record attendance.

**The camera and fingerprint sensor are sufficient for hardware attendance.** The project uses dashboard feedback and an available LCD. LED and buzzer connections and GPIO libraries are unnecessary. With `[lcd].enabled = true` (the default), an absent or failed LCD produces a display status warning and the app continues enrollment and scanning. It retries the configured LCD every five seconds during operations. To add the display later, shut down before wiring, enable I²C, check its address with `i2cdetect -y 1`, set `[lcd].address` and restart. Set `enabled = false` to skip all LCD attempts.

On Pi 5, check the UART device used by the sensor. `/dev/serial0` normally addresses the dedicated debug header. For a sensor on GPIO14/15, the guide explains `uart0-pi5` and the corresponding `[fingerprint].port`, usually `/dev/ttyAMA0`.

Your exact **“RS307” model is pending confirmation**. The supplied fingerprint driver implements the documented R307/R30x `EF01` UART protocol, normally at 57600 baud. Verify your supplier's model, connector and voltages before wiring. The LCD assumes an HD44780-compatible display with a PCF8574-compatible backpack (other supported RPLCD expanders are configurable).

## Daily workflow

1. Register the class roster while no session is open. Enroll face and fingerprint for each student.
2. Select the class and subject; start a session. The session records a snapshot of the roster.
3. Use **Scan once**, or **Start continuous scanning** for a queue of students. Continuous mode runs on the server until Stop, sign-out, session closure, a hardware error or server shutdown.
4. Each student looks at the camera, waits for the finger prompt on the dashboard or LCD, removes any existing finger, then places their enrolled finger. An accepted database write displays **Attendance saved**. An LCD failure falls back to dashboard feedback and allows enrollment and scans to continue.
5. Close the session and download **Excel** or **CSV**. Unmarked students are `Pending` during an open session and `Absent` once closed.

## Verification and maintenance

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml doctor
```

Stop the server before backup or password reset:

```powershell
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml backup backups/laptop-backup-01
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml init --reset-password
```

Backup destinations must be new directories. Backups include the SQLite database, face samples and credentials, **not the fingerprint templates stored inside the sensor**. A process lock prevents maintenance from running against a server using the same data directory. Use only one project process for the physical devices.

## Scope and limits

This is the minor-project prototype described in the supplied proposal, adapted for your components. Both laptop and Pi use offline SFace embeddings with a cosine similarity threshold, a margin from the second candidate, and repeated consistent frames. **It does not provide liveness detection or a measured accuracy guarantee.** Test known and unknown people with your actual camera and lighting before relying on it.

Face crops are stored locally as protected NumPy archives; raw fingerprints remain on the sensor. Database and backups are not encrypted. Get participants' consent, protect the Pi/SD card, and keep the interface on a trusted local network. The teacher login has CSRF protection and basic login throttling. For an untrusted network, put it behind authenticated HTTPS/VPN; do not forward port 8765 onto the internet.

Automated verification does not establish physical wiring, sensor compatibility, biometric accuracy or LCD output. Use the hardware acceptance checklist to verify those on your Pi.

See [architecture and requirement mapping](docs/ARCHITECTURE.md) for code responsibilities, decisions and extension points.

The [verification record](docs/VERIFICATION.md) separates completed software checks from the hardware acceptance work.
