# Laptop testing guide

Use this guide before connecting the Raspberry Pi. All commands run from the project root. Close a running server before switching profiles, because both defaults use port 8765.

## 1. Check the installation

On Windows, use the project interpreter explicitly; you do not need to activate the virtual environment:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-laptop.txt
.\.venv\Scripts\python.exe -m attendance --config config/simulation.toml doctor
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

On Linux/macOS replace `.\.venv\Scripts\python.exe` with `.venv/bin/python`. A webcam is not needed for the automated suite. The OpenCV test uses synthetic image arrays to check model training/loading; it does not measure recognition of real people.

## 2. Run the complete simulation

```powershell
.\.venv\Scripts\python.exe -m attendance --config config/simulation.toml init --demo
.\.venv\Scripts\python.exe -m attendance --config config/simulation.toml serve
```

Set a teacher password of at least 12 characters. Sign in at `http://127.0.0.1:8765`. Select `ECE-A`, enter a subject and start a session. `Demo Student One` and `Demo Student Two` have simulated enrollment already. For each row below, select the indicated inputs and click Scan once.

| Case | Face input | Fingerprint input | Expected |
|---|---|---|---|
| Successful dual check | One | One | Present, green success and simulated beep |
| Duplicate | One again | One again | Rejected as duplicate, one attendance row |
| Face only | Two | No finger | Rejected, Two remains Pending |
| Fingerprint only | No face | Two | Rejected, Two remains Pending |
| Mismatch | One | Two | Rejected, no new attendance |
| Unknown person | Unknown | Unknown | Rejected as not enrolled |
| Missing both | No face | No finger | Rejected |
| Wrong class | An enrolled student from another class | Same student | Rejected |
| Closed session | Close session first | — | Scan controls disabled; API rejects a new scan |

To test wrong class, close the session, register another student in `ECE-B`, enroll both simulated biometrics and reopen an `ECE-A` session. Enrollment/roster changes are blocked while a session is active so the recorded roster stays clear.

Close a session leaving one student unmarked. Download Excel and CSV; check name, roll, class, subject, local time, `Face + Fingerprint`, `Present`/`Absent`, and `simulation` mode. A new session for the same subject allows fresh attendance.

**Test devices** is a deliberate diagnostic: it pulses the simulated green/red indicators and buzzer without recording attendance. This is the only diagnostic exception to the usual success-feedback rule.

## 3. Use the laptop camera

Stop the simulation, run `python -m attendance --config config/laptop.toml setup-face-models` once using the project interpreter, then initialize/start `config/laptop.toml`. Model installation downloads about 39 MB of checksum-verified OpenCV Zoo files into `models/`; subsequent use is offline. This profile uses a separate database and password. Allow desktop camera access in Windows Settings → Privacy & security → Camera. Close other programs using the webcam. If the wrong camera opens, change `camera.index` from `0` to `1` in the profile.

Register each participant and press **Enroll face**. Only one face should be visible, reasonably frontal and at least 100 camera pixels wide/high. Face detection must find exactly one face. The image must pass basic lighting/sharpness checks. Capture takes 20 accepted samples with small pose changes; the deadline is 90 seconds. A visible preview appears during capture. Avoid large rotations and extreme expressions.

Press **Enroll finger** to add that student's simulated mapping. This does not enroll a physical finger. Start a session, select the simulated fingerprint, and click Scan once. Look at the webcam for three consistent face matches. A matching real face and simulated finger can create a **laptop test** record only.

Enrollment survives server restarts. Face samples are saved in `data/laptop/faces/<student ID>.npz` and loaded at startup. The laptop now uses YuNet landmarks to align faces and SFace to compute identity features. Older 160×160 grayscale crops are aligned in memory without rewriting them; new enrollments save 112×112 aligned color crops. Features are cached during the process and rebuilt from the archives after restarting. To check this, stop the server with Ctrl+C, start it again with the same config, select the student's simulated fingerprint and scan. Use a new class session if that student's attendance was already marked in the previous one.

The dashboard distinguishes registered students from available face enrollments. **Saved** reports a valid face archive and its sample count; **Unavailable** identifies missing or damaged files and requests restoration or recapture. Expand **Saved data on this computer** in the registry to see the exact database and face paths. `doctor` also reports these counts.

If a scan times out, the dashboard retains the last face-check reason (lighting, blur, no single face, weak match or ambiguous match). The terminal logs the latest match distances, threshold and ambiguity margin. For SFace these are cosine distances (`1 - similarity`), not probabilities. A valid archive does not guarantee that every live image will match.

Test face A with fingerprint B; no attendance should be recorded. Cover the camera or step away to test the face timeout. Stop a scan to test cancellation. Close the session while scanning to test session invalidation. Disconnect the camera while idle, start a scan and check the device error; reconnect and retry. The final automated tests also inject serial, timeout and display failures that cannot be exercised without hardware.

## 4. Calibrate the face matcher

The laptop profile selects `backend = "sface"`, `cosine_threshold = 0.45`, `similarity_margin = 0.08`, and three consecutive matching frames. Higher cosine similarity means more similar features. The closest student's similarity must meet the threshold and exceed the runner-up by the margin. An unknown person remains rejected even when only one student is enrolled. These are starting values, not an accuracy guarantee; raising the similarity threshold makes acceptance stricter.

The Pi profile uses the same SFace matcher and settings, with an ARM64 OpenCV 4.11 installation that preserves the OS NumPy used by Picamera2. Follow `scripts/setup_pi.sh` and the Pi setup guide. The initial move to the Pi still needs its own face and physical fingerprint enrollment; do not copy laptop simulation mappings. Older archives already enrolled on the Pi load automatically after the matcher upgrade.

If known students are rejected, first improve lighting, framing and enrollment. If enrolled students are confused, increase the ambiguity margin. Test unknown people whenever changing the matcher or threshold.

Use consenting participants and record results independently:

| Trial set | Suggested minimum | Record |
|---|---|---|
| Enrolled students under normal light | 10 attempts/person | Correct accept, incorrect identity, reject, elapsed time |
| Enrolled students under changed light/distance | 10/person | Same measurements |
| People never enrolled | 20 total attempts | False accepts and correct rejects |
| Two faces in view | 5 attempts | No attendance |
| Face/fingerprint mismatch | 10 attempts | No attendance |

Report false accept/reject counts, dataset size and test conditions in your project evaluation. Photographs/videos may fool the face matcher; dual authentication reduces simple proxy attendance but does not prove liveness.

## 5. What remains for the hardware stage

The laptop can verify the dashboard, authentication rule, SQLite transactions, exports, camera software and face recognition behavior. It cannot confirm the Pi camera ribbon connection, actual fingerprint enrollment/matching, voltage compatibility, LCD address, LEDs or buzzer type. Follow the Pi guide for those checks and re-enroll the real class on the Pi.
