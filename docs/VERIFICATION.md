# Verification record

## Fingerprint re-enrollment — 21 November 2026

The dashboard now offers **Re-enroll finger** for an enrolled student while the class session is closed. Replacements use a spare sensor slot, update the student mapping transactionally after storage, and retire the previous template. SQLite records unfinished candidates and pending deletion so cleanup can recover after an interruption. A previous template remains associated with its student while deletion is pending, and the dashboard displays a warning. Cleanup retries during enrollment, scanning and diagnostics.

**86 automated tests passed** with `python -m unittest discover -s tests -q`. Added checks cover recapturing the current finger, rejecting another student's finger, full storage, capture failure, cancellation, failed database writes, uncertain store acknowledgements, pending old-template deletion, retry during scanning, recovery across database reopening, existing-database migration, concurrent session/mapping changes, preservation of other students' templates and the dashboard enrollment API. Sensor operations are mocked, and protocol tests use synthetic UART responses. Physical fingerprint replacement on the Pi remains to be verified.

Dashboard JavaScript syntax checking and `git diff --check` passed.

## Optional LCD and camera/fingerprint operation — 21 November 2026

The Pi now uses dashboard feedback and an available 16×2 I²C LCD. External LED/buzzer controls, dashboard indicators, GPIO tests and GPIO library requirements were removed. LCD access is enabled by default and retried after five seconds during subsequent operations. Missing libraries, absent displays and LCD write/cleanup failures fall back to dashboard feedback.

**70 automated tests passed** with `python -m unittest discover -s tests -q`. The added checks cover connected LCD line output, missing hardware/library fallback, retry and recovery, explicit LCD disabling, cleanup failures, and Pi startup diagnostics with missing optional LCD dependencies. A Pi-profile workflow test uses the actual feedback adapter, camera adapter and enrollment code with mocked physical devices and neural inference, then verifies face enrollment, fingerprint mapping, accepted attendance, duplicate rejection and unknown-fingerprint rejection while the LCD is absent.

Dashboard JavaScript syntax checking and the laptop `doctor` command passed. Doctor verified the public model checksums and ran both YuNet and SFace networks. The Pi setup guide now includes Pi 5 camera/UART details, camera-and-fingerprint setup and later LCD connection. Physical Pi 5, UART sensor compatibility and LCD output still require checks on the user's hardware.

## Face persistence and recognition update — 10 November 2026

The laptop's original four archives were present and readable (20 images each). During a consenting live webcam check, the prior LBPH matcher detected the enrolled user's face but rejected it at distances roughly 67–81 against a threshold of 55. This reproduced a recognition failure rather than missing persistence.

The laptop profile now uses offline YuNet/SFace. Both public models were downloaded from OpenCV Zoo and SHA-256 verified. All 80 legacy grayscale images produced usable aligned features without rewriting the archives. The updated production recognizer correctly identified the consenting user after **two separate recognizer/database loads**, requiring three consecutive matching frames each time. SHA-256 checks confirmed all original archives were unchanged; these checks did not write attendance.

Offline gallery checks matched all 80 saved images to their own identity. When each image's identity was omitted from the gallery, all 80 were rejected. The highest similarity between different stored students was 0.3521, below the configured 0.45 threshold. This is a small existing-gallery check, not a measured real-world false-accept rate or a substitute for trials with new people.

**62 automated tests passed** (the 60-test suite plus two added focused regressions). Added checks cover old/new archive formats, fresh-runtime recognition, fingerprint mismatch rejection, missing/corrupt/restored files, invalid archive shapes/dtypes, failed recapture preserving enrollment, successful recapture invalidating cached models, explicit model setup/checksums, configuration-relative paths, and dashboard file availability versus database flags. JavaScript syntax checking, Python compilation and the laptop `doctor` command passed. Neural inference is mocked in automated SFace restart tests; the live and archive checks above use the actual downloaded models.

Only one enrolled person was verified live in this update. The Pi profile retains LBPH for OS-package compatibility; Pi hardware and broader biometric accuracy remain unverified.

## Original project verification — 29 October 2026

Verified on 29 October 2026 in the Windows project environment using Python 3.12.14. The project virtual environment contains its laptop dependencies.

## Automated checks

**46 tests passed.** The suite runs with `python -m unittest discover -s tests -v` from the project root. It covers:

- Matching dual biometrics, face-only/fingerprint-only rejection, missing and unknown identities, mismatch, wrong class, unenrolled students, closed/changed sessions and no reuse of earlier scan evidence.
- Concurrent duplicate submissions, a single active session, test/hardware database separation, and a server/maintenance process lock.
- UART packet construction, checksums, response address validation, fragmented reads, timeout, capacity, template-slot bitmaps, two-sample enrollment, existing-finger protection and low-score rejection.
- Camera/serial errors, cancellation, serialized jobs and preservation of committed attendance after a feedback failure.
- Teacher login, CSRF, protected exports, registration/enrollment workflow, CSV/XLSX output, roster snapshots, Pending/Absent handling and spreadsheet formula escaping.
- Actual OpenCV model training/loading with synthetic arrays, cascade availability, distance/ambiguity rejection, and mocked Picamera2/GPIO/LCD API contracts including active/passive buzzers.

Python source compilation, JavaScript syntax checking and `pip check` also passed. The laptop `doctor` command passed for Flask, Waitress, openpyxl, NumPy and OpenCV with `cv2.face` available.

## Browser checks

An isolated simulation database was used for UI testing, separate from all three user data profiles. Verified teacher sign-in, session creation, a successful dual check, identity mismatch rejection, duplicate rejection, attendance counts and registration display. The interface was inspected at a narrow in-app viewport and a 1366×900 laptop viewport. The final page reported no browser console warnings/errors. The temporary viewport override was removed afterwards.

`dashboard-preview.jpg` shows the simulation after a duplicate was rejected; the accepted attendance count remains one. All visible people are explicitly named demo records.

## Checks still requiring the actual components

No Raspberry Pi, physical fingerprint sensor, camera enrollment of a person, LCD, LED or buzzer was connected during automated verification. The mock hardware tests confirm software contracts, not electrical operation. Real recognition accuracy and unknown-person rejection require calibration with participants. Exact RS307 protocol/pinout/voltage compatibility and buzzer type remain pending the user's product confirmation. Complete the component tests and acceptance checklist in the Raspberry Pi guide before treating the hardware setup as validated.
