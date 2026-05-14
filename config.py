from __future__ import annotations

import os
from pathlib import Path


def _env_str(name: str, default: str = "", *, allow_blank: bool = False) -> str:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value and not allow_blank:
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _env_int_tuple(name: str) -> tuple[int, ...]:
    raw = _env_str(name, "", allow_blank=True)
    if not raw:
        return ()
    values: list[int] = []
    for chunk in raw.split(","):
        cleaned = chunk.strip()
        if not cleaned:
            continue
        try:
            values.append(int(cleaned))
        except ValueError:
            continue
    return tuple(dict.fromkeys(values))


def _env_str_tuple(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, "").strip() or default
    values = [chunk.strip() for chunk in raw.replace(";", ",").split(",") if chunk.strip()]
    return tuple(dict.fromkeys(values))


def _load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


BASE_DIR = Path(__file__).resolve().parent
_load_env_file(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

BOT_TOKEN = _env_str("BOT_TOKEN", "", allow_blank=True)
ANIMETHEMES_BASE_URL = _env_str("ANIMETHEMES_BASE_URL", "https://api.animethemes.moe")
ANIMETHEMES_REQUEST_TIMEOUT = _env_float("ANIMETHEMES_REQUEST_TIMEOUT", 25.0)
FFMPEG_PATH = _env_str("FFMPEG_PATH", "", allow_blank=True)
MEDIA_CACHE_DIR = _env_str("MEDIA_CACHE_DIR", str(BASE_DIR / "cache"))
PERSISTENCE_FILE = _env_str("PERSISTENCE_FILE", str(DATA_DIR / "radio_animes_state.pkl"))
RADIO_ANIMES_CHANNEL_CHAT = _env_str("RADIO_ANIMES_CHANNEL_CHAT", "@RadioAnimes")
RADIO_ANIMES_REQUIRED_CHANNELS = _env_str_tuple(
    "RADIO_ANIMES_REQUIRED_CHANNELS",
    "@RadioAnimes,@QG_BALTIGO",
)
RADIO_ANIMES_CHANNEL_URL = _env_str("RADIO_ANIMES_CHANNEL_FOLDER_URL", "https://t.me/addlist/F7In7PWb4s1iMWMx")
CHANNEL_MEMBERSHIP_TTL_SECONDS = _env_float("CHANNEL_MEMBERSHIP_TTL_SECONDS", 300.0)
APP_CONCURRENT_UPDATES = _env_int("APP_CONCURRENT_UPDATES", 256)
APP_CONNECTION_POOL_SIZE = _env_int("APP_CONNECTION_POOL_SIZE", 256)
APP_GET_UPDATES_CONNECTION_POOL_SIZE = _env_int("APP_GET_UPDATES_CONNECTION_POOL_SIZE", 32)
APP_POOL_TIMEOUT = _env_float("APP_POOL_TIMEOUT", 30.0)
ADMIN_IDS = _env_int_tuple("ADMIN_IDS")
USER_REGISTRY_FILE = _env_str("USER_REGISTRY_FILE", str(DATA_DIR / "radio_animes_users.json"))
POST_SEPARATOR_STICKER = _env_str("POST_SEPARATOR_STICKER", "", allow_blank=True)
