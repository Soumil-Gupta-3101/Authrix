#!/usr/bin/env bash
# Run after the apt installation in docs/RASPBERRY_PI_SETUP.md.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."

if [[ "$(uname -s)" != "Linux" || "$(uname -m)" != "aarch64" ]]; then
    echo "Use this installer on 64-bit Raspberry Pi OS (aarch64)." >&2
    exit 1
fi

pi_python=/usr/bin/python3
"$pi_python" - <<'PY'
import sys
if not (3, 11) <= sys.version_info[:2] <= (3, 13):
    raise SystemExit("Use the Raspberry Pi OS system Python 3.11-3.13.")
import numpy, picamera2, libcamera
print("System camera imports OK; NumPy", numpy.__version__)
PY

if [[ ! -d .venv ]]; then
    "$pi_python" -m venv --system-site-packages .venv
fi
if [[ ! -x .venv/bin/python ]] || ! grep -Eiq '^include-system-site-packages *= *true' .venv/pyvenv.cfg; then
    echo "The existing .venv is unsuitable. Keep it separately and create a fresh --system-site-packages Pi venv." >&2
    exit 1
fi
if [[ "$("$pi_python" -c 'import sys; print(sys.version_info[:2])')" != "$(.venv/bin/python -c 'import sys; print(sys.version_info[:2])')" ]]; then
    echo "The existing .venv uses a different Python. Keep it separately and recreate it with the system python3." >&2
    exit 1
fi

# Prevent pip from replacing NumPy with a different ABI than the OS camera stack.
"$pi_python" -c 'import numpy; print("numpy==" + numpy.__version__)' > .venv/pi-numpy-constraint.txt
.venv/bin/python -m pip install -c .venv/pi-numpy-constraint.txt -r requirements-pi.txt
.venv/bin/python -m pip check
.venv/bin/python -m attendance --config config/raspberry_pi.toml setup-face-models
.venv/bin/python -m attendance --config config/raspberry_pi.toml doctor
echo "Pi software and offline face models are ready. Continue with the individual hardware tests in the Pi guide."
