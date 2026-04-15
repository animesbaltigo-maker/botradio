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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ANIMETHEMES_BASE_URL = os.getenv("ANIMETHEMES_BASE_URL", "https://api.animethemes.moe").strip()
ANIMETHEMES_REQUEST_TIMEOUT = _env_float("ANIMETHEMES_REQUEST_TIMEOUT", 25.0)
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "").strip()
MEDIA_CACHE_DIR = os.getenv("MEDIA_CACHE_DIR", str(BASE_DIR / "cache")).strip()
PERSISTENCE_FILE = os.getenv("PERSISTENCE_FILE", str(DATA_DIR / "radio_animes_state.pkl")).strip()
RADIO_ANIMES_CHANNEL_CHAT = os.getenv("RADIO_ANIMES_CHANNEL_CHAT", "@RadioAnimes").strip()
RADIO_ANIMES_CHANNEL_URL = os.getenv("RADIO_ANIMES_CHANNEL_URL", "https://t.me/RadioAnimes").strip()
CHANNEL_MEMBERSHIP_TTL_SECONDS = _env_float("CHANNEL_MEMBERSHIP_TTL_SECONDS", 300.0)
APP_CONCURRENT_UPDATES = _env_int("APP_CONCURRENT_UPDATES", 256)
APP_CONNECTION_POOL_SIZE = _env_int("APP_CONNECTION_POOL_SIZE", 256)
APP_GET_UPDATES_CONNECTION_POOL_SIZE = _env_int("APP_GET_UPDATES_CONNECTION_POOL_SIZE", 32)
APP_POOL_TIMEOUT = _env_float("APP_POOL_TIMEOUT", 30.0)
