from __future__ import annotations

import os
from pathlib import Path


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


BASE_DIR = Path(__file__).resolve().parent
BOT_TOKEN="8727643136:AAFCDRfuXhT5ok2yNiwuqqr6dEP8GQOrIOk"
ANIMETHEMES_BASE_URL = os.getenv("ANIMETHEMES_BASE_URL", "https://api.animethemes.moe").strip()
ANIMETHEMES_REQUEST_TIMEOUT = _env_float("ANIMETHEMES_REQUEST_TIMEOUT", 25.0)
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()
MEDIA_CACHE_DIR = os.getenv("MEDIA_CACHE_DIR", str(BASE_DIR / "cache")).strip()
