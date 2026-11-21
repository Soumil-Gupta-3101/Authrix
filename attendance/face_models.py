"""Explicit, checksum-verified installation of OpenCV Zoo face models."""
import hashlib
import os
from pathlib import Path
import urllib.request


DEFAULT_DIRECTORY = Path(__file__).resolve().parents[1] / "models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": (
        "face_detection_yunet", "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    "face_recognition_sface_2021dec.onnx": (
        "face_recognition_sface", "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}


def model_directory(options):
    return Path(options.get("model_dir", DEFAULT_DIRECTORY)).resolve()


def verify_model(path, expected):
    with path.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
            raise ValueError(f"Face model checksum failed: {path.name}; run setup-face-models again")


def download_models(directory):
    directory.mkdir(parents=True, exist_ok=True)
    for filename, (folder, checksum) in MODELS.items():
        path = directory / filename
        valid = False
        if path.is_file():
            try:
                verify_model(path, checksum)
                valid = True
            except ValueError:
                pass
        if not valid:
            temporary = path.with_suffix(".download")
            try:
                url = f"https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/{folder}/{filename}"
                with urllib.request.urlopen(url, timeout=60) as source, temporary.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                verify_model(temporary, checksum)
                os.replace(temporary, path)
            finally:
                temporary.unlink(missing_ok=True)
        license_path = directory / f"{folder}.LICENSE"
        if not license_path.is_file():
            url = f"https://raw.githubusercontent.com/opencv/opencv_zoo/main/models/{folder}/LICENSE"
            with urllib.request.urlopen(url, timeout=30) as source:
                license_path.write_bytes(source.read())
        print(f"OK {path.name} (SHA-256 verified)", flush=True)
