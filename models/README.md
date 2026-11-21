# Offline face models

Install using the project Python interpreter:

```powershell
.\.venv\Scripts\python.exe -m attendance --config config/laptop.toml setup-face-models
```

This downloads roughly 39 MB once. Runtime is offline. Model weights are ignored by Git; no enrollment data belongs in this directory.

| File | Source | License | SHA-256 |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` | [OpenCV Zoo YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | MIT; see `face_detection_yunet.LICENSE` | `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4` |
| `face_recognition_sface_2021dec.onnx` | [OpenCV Zoo SFace](https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface) | Apache 2.0; see `face_recognition_sface.LICENSE` | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |

Both installation and runtime initialization verify these hashes. The laptop uses OpenCV 4.12; the Pi uses the ARM64 OpenCV 4.11 headless wheel. Both profiles use these same model files. On the Pi, `bash scripts/setup_pi.sh` installs them; alternatively run `.venv/bin/python -m attendance --config config/raspberry_pi.toml setup-face-models` after installing the Pi dependencies. Doctor executes both networks without opening the camera. A copied `models/` folder can be reused after checksum verification.
