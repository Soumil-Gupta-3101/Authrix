from dataclasses import dataclass
from pathlib import Path
import tomllib
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Settings:
    mode: str
    data_dir: Path
    timezone: str
    values: dict

    def section(self, name):
        return self.values.get(name, {})


def load_settings(path):
    path = Path(path).resolve()
    with path.open("rb") as stream:
        values = tomllib.load(stream)
    mode = values.get("mode")
    if mode not in {"simulation", "laptop", "raspberry_pi"}:
        raise ValueError("mode must be simulation, laptop or raspberry_pi")
    timezone = values.get("timezone", "Asia/Kolkata")
    ZoneInfo(timezone)
    data = (path.parent / values.get("data_dir", "../data/" + mode)).resolve()
    for key in ("attempt_timeout", "face_timeout", "fingerprint_timeout"):
        if not 1 <= values.get("authentication", {}).get(key, 30) <= 180:
            raise ValueError(f"{key} must be between 1 and 180 seconds")
    face = values.get("face", {})
    if face.get("backend", "lbph") not in {"lbph", "sface"}:
        raise ValueError("face.backend must be lbph or sface")
    if "model_dir" in face:
        face["model_dir"] = str((path.parent / face["model_dir"]).resolve())
    if not 0 < face.get("cosine_threshold", .45) <= 1:
        raise ValueError("face.cosine_threshold must be greater than 0 and at most 1")
    if not 0 <= face.get("similarity_margin", .08) <= 1:
        raise ValueError("face.similarity_margin must be 0..1")
    if not 5 <= face.get("samples", 20) <= 100:
        raise ValueError("face.samples must be between 5 and 100")
    if not 2 <= face.get("consecutive_frames", 3) <= 10:
        raise ValueError("face.consecutive_frames must be between 2 and 10")
    if not 1 <= face.get("distance_threshold", 55) <= 150:
        raise ValueError("face.distance_threshold must be 1..150")
    if not 0 <= face.get("ambiguity_margin", 8) <= 100:
        raise ValueError("face.ambiguity_margin must be 0..100")
    if mode == "raspberry_pi":
        lcd = values.get("lcd", {})
        if lcd.get("columns", 16) != 16 or lcd.get("rows", 2) != 2:
            raise ValueError("This project uses a 16x2 LCD")
        if type(lcd.get("enabled", True)) is not bool:
            raise ValueError("lcd.enabled must be true or false")
    return Settings(mode, data, timezone, values)
