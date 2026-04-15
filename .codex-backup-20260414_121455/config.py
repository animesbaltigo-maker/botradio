from __future__ import annotations

import os


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


BOT_TOKEN="8727643136:AAFCDRfuXhT5ok2yNiwuqqr6dEP8GQOrIOk"
ANIMETHEMES_BASE_URL = os.getenv("ANIMETHEMES_BASE_URL", "https://api.animethemes.moe").strip()
ANIMETHEMES_REQUEST_TIMEOUT = _env_float("ANIMETHEMES_REQUEST_TIMEOUT", 25.0)
