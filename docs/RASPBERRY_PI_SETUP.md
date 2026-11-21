# Raspberry Pi setup and wiring plan

This guide takes a Raspberry Pi 4 or 5 from a fresh **64-bit Raspberry Pi OS** installation to a working attendance prototype. The application uses the Pi camera through Picamera2 and a UART fingerprint sensor. The teacher dashboard displays prompts and results. An optional **16×2 HD44780 I²C LCD** mirrors those messages when available. Camera and fingerprint connections are sufficient for attendance.

The Pi and laptop now use the **same offline YuNet + SFace recognition pipeline**, cosine threshold `0.45`, similarity margin `0.08`, and three consecutive matching frames. The Pi loads saved enrollments at startup and after reboot. The installation below supplies compatible ARM64 OpenCV and the same checksum-verified models used on the laptop.

The exact product sold as **RS307** is still unconfirmed. The UART implementation targets the GROW R307/R30x `EF01` protocol. Verify the seller's model, pin labels, supply voltage and UART signal voltage before connecting that sensor. A successful protocol handshake is necessary but does not by itself establish electrical compatibility.

## Phase 1 — Prepare the Pi

Connect a keyboard/display or SSH into the Pi. Use your actual Linux username; modern Raspberry Pi OS does not always create a user named `pi`. Connect Wi-Fi and set the hostname if desired.

```bash
cat /etc/os-release
uname -m
python3 --version
```

Expect Raspberry Pi OS/Debian and `aarch64`. Use the OS system Python 3.11–3.13 (for example, Bookworm's Python 3.11 or Trixie's Python 3.13). A 32-bit OS is not supported by the pinned ARM64 OpenCV wheel. If you installed Ubuntu or another distribution, use its camera/GPIO installation instructions before these project steps; do not assume apt packages and device permissions are identical.

Use a suitable **5.1 V / 3 A USB-C supply for Pi 4**, or a recommended **5 V / 5 A supply for Pi 5**. A weak phone charger may cause camera resets, serial failures and SD-card corruption. Provide ventilation and cooling for sustained recognition. Shut down and remove power before wiring the header or camera ribbon.

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y python3-venv python3-pip python3-numpy libgomp1 \
  python3-picamera2 rpicam-apps python3-smbus \
  i2c-tools git
sudo timedatectl set-timezone Asia/Kolkata
sudo timedatectl set-ntp true
sudo reboot
```

The Pi 4 has no battery-backed RTC by default. Check `timedatectl status` after connecting to the internet; timestamps require a correct clock. If demonstrating offline, synchronize the clock before disconnecting, or set the correct time manually using `timedatectl` with NTP temporarily disabled. Do not use test attendance as proof that the wall clock is correct.

## Phase 2 — Enable I²C and UART

```bash
sudo raspi-config
```

Under **Interface Options**, enable **I2C** when adding the LCD. Open **Serial Port**, answer **No** to the serial login shell and **Yes** to serial hardware. Enable SSH as needed. Current Pi cameras use the modern camera stack.

For **Pi 5 with the fingerprint sensor on GPIO14/15 (physical pins 8/10)**, enable that UART under `[all]` in `/boot/firmware/config.txt` using `dtoverlay=uart0-pi5`, then reboot. Check `ls -l /dev/serial0 /dev/ttyAMA*` and set `[fingerprint].port` in `config/raspberry_pi.toml` to the GPIO UART device, usually `/dev/ttyAMA0`. Pi 5's default `/dev/serial0` normally points to `/dev/ttyAMA10` on the dedicated debug header. If the sensor already works through a USB UART adapter or another UART, use that device's actual path. Keep any serial console off the sensor's port. The Bluetooth overlay and service steps below apply to Pi 4.

For a stable hardware UART on the Pi 4's GPIO14/15, add the following under `[all]` in `/boot/firmware/config.txt` (older releases use `/boot/config.txt`). Inspect the file first and avoid conflicting duplicate values:

```ini
enable_uart=1
dtoverlay=disable-bt
dtparam=i2c_arm=on
camera_auto_detect=1
```

`disable-bt` disables onboard Bluetooth to free the PL011 UART. Wi-Fi remains available. Disable the Bluetooth UART service if present:

```bash
sudo systemctl disable --now hciuart
```

On both Pi 4 and Pi 5, grant device access and reboot.

```bash
sudo usermod -aG dialout,i2c,video "$USER"
sudo reboot
```

If `hciuart` does not exist on your image, that service step can be skipped. Group membership takes effect after a new login/reboot. Then check:

```bash
groups
ls -l /dev/serial0 /dev/i2c-1
```

On Pi 4, `/dev/serial0` should resolve to the primary hardware UART (typically `ttyAMA0` with this overlay). On Pi 5, use the GPIO UART device checked above. The kernel command line must not contain a console using the sensor's UART. `raspi-config` normally removes serial console entries. If needed, inspect `/boot/firmware/cmdline.txt` and preserve its one-line format when removing the relevant serial console entry. Camera/fingerprint operation works while `/dev/i2c-1` is absent. Display use requires I²C enabled.

## Phase 3 — Gather the small connection parts

In addition to your purchased main components, use:

- A breadboard and jumper wires.
- When adding the LCD, a bidirectional I²C level shifter (for example a BSS138-based module) for a typical 5 V LCD backpack.
- A UART level translator if the confirmed fingerprint TX output is above 3.3 V. Do not assume “5 V supply” also specifies the TX signal voltage.

**The Pi GPIO inputs are 3.3 V only.** Many LCD backpacks pull SDA/SCL to their 5 V supply; connect them through the level shifter. Supplying the whole LCD at 3.3 V is not a reliable replacement unless its own datasheet supports it. Never connect a 5 V UART TX directly to Pi RX.

## Phase 4 — Connect the hardware

All GPIO numbers in code are **BCM** numbers. Physical header positions below are different numbers. Pin 1 is the 3.3 V corner of the 40-pin header; confirm orientation against the Pi board before making connections. All grounds must be common.

### Pi camera module

With power disconnected, connect the camera to the **CSI camera connector** on Pi 4, or either **CAM/DISP connector** on Pi 5. Pi 4 uses a 15-pin connector. Pi 5 uses mini 22-pin connectors, so use a cable appropriate to the Pi and camera board. Open the latch, insert the ribbon squarely with its conductive contacts facing the connector contacts, and close the latch. Confirm orientation against your board and cable. The camera board end must also have the correct orientation.

After boot:

```bash
rpicam-hello --list-cameras
rpicam-still --nopreview -o camera-check.jpg
```

Open the photo to check focus, exposure and ribbon operation. Remove this test photo when finished if it contains a person's face. Some older releases name these commands `libcamera-hello` and `libcamera-still`; use the tools installed for your OS. A Camera Module 3 normally benefits from autofocus; keep framing within its focus range and verify sharpness before enrollment.

### Optional 16×2 LCD with I²C backpack

Default software: bus `1`, address `0x27`, PCF8574 expander, 16 columns, 2 rows. A PCF8574A backpack commonly uses a different address such as `0x3f`; detect the actual address.

You may skip this connection during initial camera/fingerprint setup. `[lcd].enabled = true` automatically attempts to use the configured LCD during operations. An absent or failed display falls back to dashboard messages, with status shown under **Station display**. The app retries after five seconds during subsequent operations. Set `enabled = false` to skip LCD access. To add the LCD later, shut down before wiring, enable I²C, set the detected address and restart the app with `enabled = true`.

| Pi connection | Physical pin | Level shifter / LCD connection |
|---|---:|---|
| 3.3 V | 1 | Level shifter LV reference |
| 5 V | 2 | Level shifter HV reference and LCD VCC |
| GND | 6 | Shifter GND and LCD GND |
| GPIO2 / SDA1 | 3 | LV channel 1; matching HV channel 1 → LCD SDA |
| GPIO3 / SCL1 | 5 | LV channel 2; matching HV channel 2 → LCD SCL |

The shifter needs both reference supplies and a common ground. Do not connect the LCD's high-voltage SDA/SCL directly to the Pi in parallel with the shifter.

```bash
i2cdetect -y 1
```

Set `[lcd].address` in `config/raspberry_pi.toml` to the address found. Turn the backpack's contrast potentiometer gradually until characters are visible. The code writes padded 16-character lines so old characters do not remain after shorter messages. PCF8574A uses the same `PCF8574` expander setting; a different expander or unusual backpack pin mapping may need the corresponding RPLCD setting/driver adjustment.

### Fingerprint sensor

**Only apply the following mapping after confirming your module is a compatible R307/R30x UART model.** Use the sensor's documented labels; wire colors and connector order vary. On a documented R307, the normal main VCC supply is around 5 V; the extra `3.3VT` terminal is associated with finger detection and must not be treated as an interchangeable VCC pin without the module's specification.

| Confirmed sensor terminal | Pi connection | Physical pin |
|---|---|---:|
| Main VCC, if specified for 5 V | 5 V | 4 |
| GND | Common GND | 9 |
| TXD (sensor output) | GPIO15 / RXD0, through translation if needed | 10 |
| RXD (sensor input) | GPIO14 / TXD0, with translation if the sensor requires it | 8 |
| TOUCH / WAKE / 3.3VT / USB pins, if present | Leave unconnected for this UART implementation | — |

Cross TX to RX and RX to TX. This uses **TTL UART**. Default configuration is `/dev/serial0`, `57600`, 8 data bits, no parity, 1 stop bit, address `0xFFFFFFFF`, password `0`. For Pi 5's GPIO14/15, select the actual GPIO UART device as described in Phase 2, usually `/dev/ttyAMA0`. If a previous owner changed the baud/address/password, obtain the actual settings. The application preserves existing sensor settings and templates.

Fingerprint templates live in sensor flash. Enrollment reads the sensor's occupied-slot bitmap, reserves an unused slot, checks for existing fingers, captures the same finger twice, merges it, stores it, and links its slot to the student. Matching searches the sensor independently from the face recognizer. Unknown/weak/unmapped matches are rejected.

## Phase 5 — Copy and install the project

Copy the updated project source to `/home/YOUR_USER/final-smart-attendance-system` by USB, SFTP or Git. Exclude the Windows `.venv`, laptop `data`, backups and `.qa` directories. Preserve an existing Pi's own `data/raspberry_pi` when updating it. **Do not copy the Windows virtual environment or install `requirements-laptop.txt` on the Pi.**

```bash
cd ~/final-smart-attendance-system
bash scripts/setup_pi.sh
.venv/bin/python -m unittest discover -s tests -v
```

The script uses `/usr/bin/python3`, creates a `--system-site-packages` environment, and installs **`opencv-contrib-python-headless==4.11.0.86`** from an ARM64 wheel into that environment. This supplies SFace/YuNet support even when apt OpenCV is too old. It preserves the system NumPy version with a pip constraint, so Picamera2 and its compiled dependencies keep their expected ABI. Picamera2/libcamera and NumPy come from apt. SMBus comes from apt for the optional LCD. The headless wheel supports the image processing and recognition APIs without a desktop display.

The script also installs/verifies the two public face models (about 39 MB, once), then runs `doctor`. Doctor checks camera/serial imports and executes **both neural networks on synthetic images**, without opening the camera or recording attendance. Missing LCD imports are reported as optional. If an old or Windows `.venv` is unsuitable, the script stops with instructions and preserves the environment and enrollment. Use the system camera stack and the virtual environment installation above.

For a manual installation after the apt step, the equivalent commands are:

```bash
/usr/bin/python3 -m venv --system-site-packages .venv
/usr/bin/python3 -c 'import numpy; print("numpy==" + numpy.__version__)' > .venv/pi-numpy-constraint.txt
.venv/bin/python -m pip install -c .venv/pi-numpy-constraint.txt -r requirements-pi.txt
.venv/bin/python -m pip check
.venv/bin/python -m attendance --config config/raspberry_pi.toml setup-face-models
.venv/bin/python -m attendance --config config/raspberry_pi.toml doctor
```

Models are stored in the project's `models/` directory; its path is resolved relative to the profile file. You may copy the complete, already-downloaded `models/` folder from the laptop instead: setup verifies its checksums and reuses valid files. Git excludes the model weights, so a source-only clone needs `setup-face-models`. Recognition never downloads models or sends face images over the network.

If OpenCV/NumPy imports fail, inspect them with `.venv/bin/python -c "import cv2, numpy; print(cv2.__file__, cv2.__version__, numpy.__version__)"`. The Pi venv should use OpenCV 4.11 and the same NumPy version as `/usr/bin/python3`. Avoid multiple pip OpenCV distributions in one venv; keep an old conflicting venv separately and build a fresh one using the script. Do not work around an ABI error by upgrading system NumPy independently of the camera packages.

## Phase 6 — Test each component separately

Stop the dashboard/service first. Run the following from the project root; these scripts do not create attendance:

```bash
.venv/bin/python scripts/hardware_test.py camera
.venv/bin/python scripts/hardware_test.py fingerprint
```

Camera should return a frame shape. Fingerprint should report a successful password/system-parameter handshake, capacity and occupied slots. Enrollment/matching follows the UART query. When the LCD is connected, run `.venv/bin/python scripts/hardware_test.py lcd` to check two readable lines for five seconds. This explicit LCD test reports a connection error if the display is unavailable. Normal enrollment and attendance continue using the dashboard when the LCD is unavailable. Check groups and device access if permissions fail.

## Phase 7 — Enroll and run an actual class

```bash
.venv/bin/python -m attendance --config config/raspberry_pi.toml init
.venv/bin/python -m attendance --config config/raspberry_pi.toml serve --host 0.0.0.0
```

Create your teacher password. Use `hostname -I` to find the Pi's LAN IP. On a laptop/phone on the same trusted Wi-Fi, open `http://PI_LAN_IP:8765`. No internet connection is required after installation. The default profile binds to localhost; the command above deliberately enables LAN access.

1. Run **Test devices** from the dashboard and physically check the outputs. This diagnostic pulses green without recording attendance.
2. Register the real students. Do this while no session is active.
3. Enroll each face using the **Pi camera**. This avoids camera/lighting differences from laptop training.
4. Enroll each finger: remove any finger, place the student's finger, remove it, then place the same finger again. Wait for enrollment to finish. Existing sensor templates are not overwritten.
5. Verify every student has both enrollment badges. Start the correct class and subject.
6. Click **Scan once**. The face must match first. When the dashboard or LCD requests a finger, remove any existing finger and place the corresponding student's finger. Both identities must match.
7. Check the session register and station display. An accepted scan shows **Attendance saved** on the dashboard and available LCD. Duplicate/mismatch/timeout attempts show rejection feedback and preserve attendance counts.
8. Use continuous scanning for a queue. The server repeats attempts after a two-second pause and requires finger removal before every new fingerprint acquisition. Click Stop or close the class to stop. A hardware error also stops the loop.
9. Close the session and download the report. Review the clock and `raspberry_pi` mode column.

Pi data lives in `data/raspberry_pi/attendance.sqlite3` and `data/raspberry_pi/faces/<student ID>.npz`. The dashboard shows the exact paths, saved sample counts and missing/damaged files. New face enrollments save aligned color crops; older Pi grayscale archives are also loaded by SFace without rewriting them. Restarting with the same profile reloads these files; no re-enrollment is needed after a normal reboot. Fingerprint templates remain in sensor flash, and the database retains their student-to-slot mappings. The initial move from laptop to Pi still requires real fingerprint enrollment; do not copy simulated fingerprint mappings.

After enrollment, stop the dashboard/service and test disk loading with the real Pi camera independently of fingerprint and LCD:

```bash
# If the boot service is installed, stop it first:
sudo systemctl stop attendance
.venv/bin/python scripts/hardware_test.py face --roll YOUR_ENROLLED_ROLL
```

Use the student's actual roll number. A successful check prints the loaded face count and the expected roll after three consecutive matches. It saves no images, enrollment or attendance. Reboot the Pi, stop the service if enabled, and run the same command again **without recapturing the face**. Then start the dashboard/service and verify the matching physical fingerprint in a new class session.

Continuous scanning runs in a single background worker. If the browser disconnects, the scan loop remains active until a teacher reconnects and stops/closes it or the server shuts down. A service restart never automatically resumes scanning; the teacher starts it again. There is no manual present button or bypass for a failed biometric.

## Phase 8 — Start automatically after boot

First complete all interactive hardware tests. Copy `deploy/attendance.service.example` to a new `deploy/attendance.service`, edit **all** `YOUR_USER` placeholders and paths, and confirm the chosen account belongs to the device groups. The example exposes the dashboard on the LAN; remove `--host 0.0.0.0` if localhost-only access is wanted.

```bash
cp deploy/attendance.service.example deploy/attendance.service
nano deploy/attendance.service
sudo cp deploy/attendance.service /etc/systemd/system/attendance.service
sudo systemctl daemon-reload
sudo systemctl enable --now attendance
sudo systemctl status attendance
journalctl -u attendance -n 100 --no-pager
```

The service uses an absolute configuration path and runs the same offline model/import check before starting. Complete model installation before enabling it; boot never downloads weights. The server then loads the enrolled faces from the persistent Pi data directory.

Do not start a second manual server while the service is running. To perform maintenance or individual device tests, run `sudo systemctl stop attendance` first. Start it again with `sudo systemctl start attendance`. A stronger systemd sandbox may need explicit device access rules; the provided service runs as your non-root user and uses a restrictive file-creation mask.

## Phase 9 — Acceptance checklist

| Test on the actual Pi | Pass condition |
|---|---|
| Enroll face and finger | Both enrollments persist after reboot |
| Face check before and after reboot, without recapture | `hardware_test.py face --roll ...` identifies the same expected student; no attendance written |
| Enrolled face A + finger A | Exactly one Present record and Attendance saved on the dashboard and available LCD |
| Same student repeats in same session | Duplicate rejected; still one record |
| Face A + finger B | Neither gets new attendance |
| No face / no finger | Timeout or missing input; no attendance |
| Unknown face / unknown finger | Rejected |
| Student from another class | Rejected |
| Close session during capture | In-flight attempt rejected if it has not committed before closure |
| Camera / UART disconnected before a scan | Device error; no fallback to simulated input |
| LCD absent or fault during enrollment/scan | Dashboard reports LCD status; biometric operation proceeds and obeys the same attendance rules |
| LCD added with I²C enabled and correct address | Prompts and results appear on both dashboard and LCD |
| New session for same subject | New attendance permitted |
| Report after session close | Correct Present/Absent roster, timestamp and method |
| Full reboot | Dashboard starts; templates still match; clock checked |

Disconnect/reconnect wiring only with power removed. For failure testing, shut down, disconnect the chosen peripheral, boot, then attempt the operation. Do not hot-pull GPIO power lines. Record face accuracy and timeout timings under real lighting as described in the laptop guide; passing software tests cannot establish these measurements.

## Troubleshooting and recovery

| Symptom | Checks |
|---|---|
| SFace models missing or damaged | Run `setup-face-models` with `config/raspberry_pi.toml`; check the resolved `model_dir` and rerun `doctor` |
| OpenCV too old / NumPy ABI error | Use `scripts/setup_pi.sh` and `requirements-pi.txt`; keep apt NumPy and use the pinned ARM64 OpenCV 4.11 wheel in a fresh Pi venv |
| LCD lights but no characters | Contrast potentiometer; detected address; 16×2 profile; backpack mapping |
| LCD not in `i2cdetect` | I²C enabled; bus 1; ground; SDA/SCL; shifter LV/HV supply and channel pairing |
| Fingerprint UART timeout | Confirm exact module; VCC; crossed TX/RX; correct logic voltages; serial shell disabled; baud/password/address; serial0 link |
| Fingerprint command rejects | Read reported hexadecimal status; verify R30x command support and seller manual; no templates are accepted on protocol error |
| Template index command unsupported | This driver requires `ReadIndexTable 0x1F`; confirm hardware/firmware before adapting the driver |
| Sensor has templates but app says unknown | Slots are not mapped to this database, or sensor was replaced; re-enroll against the intended database |
| Finger already exists during enrollment | Keep the original mapping; do not enroll one finger to two students. An orphan slot requires deliberate sensor maintenance after backing up |
| Camera unavailable | Correct CSI connector, ribbon/cable orientation, camera detection, no competing camera process |
| Face repeatedly times out | Enrollment quality, distance, lighting, focus, one face in frame; calibrate threshold using positive and negative trials |
| Device permission denied | Re-login after group changes; dialout/video/i2c access; only one device process |
| Pi resets or undervoltage | Power supply, short circuits, peripheral current. Check `vcgencmd get_throttled` |
| Wrong report dates | Correct timezone and synchronized system clock before scanning |

For backup, stop the service and run `.venv/bin/python -m attendance --config config/raspberry_pi.toml backup backups/pi-backup-01`. Store the backup privately. To restore on the same system/sensor, stop the service, preserve the current data directory separately, copy the backup's contents to the configured Pi data directory and check owner/group permissions before restarting. The restored database's mode must remain `raspberry_pi`.

The backup contains face crops and student-to-template mappings, not sensor flash. If the fingerprint sensor is lost/replaced, its templates must be re-enrolled. Do not clear sensor flash while its mappings are still being used. For initial prototype recovery from an orphaned template, identify the exact slot with the diagnostic and consult the sensor's documented delete command; the web interface intentionally has no erase-all operation. Have a project maintainer reconcile that slot and the corresponding database before re-enrollment.

## Technical references

- [Raspberry Pi camera software documentation](https://www.raspberrypi.com/documentation/accessories/camera-software.html) and [Picamera2 project](https://github.com/raspberrypi/picamera2) for the modern camera stack.
- [Raspberry Pi interface and UART configuration](https://www.raspberrypi.com/documentation/computers/configuration.html) for serial0, overlays and interface setup.
- [GROW R307 manufacturer manual, mirrored PDF](https://www.openhacks.com/uploadsproductos/r307_fingerprint_module_user_manual.pdf) for the R307 protocol and connector specification; this does not establish the identity of your RS307 unit.
- [RPLCD API](https://rplcd.readthedocs.io/en/latest/api.html) and [RPLCD package](https://pypi.org/project/RPLCD/) for character LCD and I²C expander support.
