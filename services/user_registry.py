from __future__ import annotations

import json
import os
from pathlib import Path
import threading
import time
from typing import Iterable

from telegram import Update, User

from config import USER_REGISTRY_FILE


REGISTRY_PATH = Path(USER_REGISTRY_FILE)
REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
_LOCK = threading.RLock()


def _load_registry() -> dict[str, dict[str, object]]:
    if not REGISTRY_PATH.exists():
        return {}
    try:
        raw = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    result: dict[str, dict[str, object]] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, dict):
            continue
        result[key] = value
    return result


def _save_registry(data: dict[str, dict[str, object]]) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = REGISTRY_PATH.with_suffix(REGISTRY_PATH.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    os.replace(temp_path, REGISTRY_PATH)


def remember_user(user: User | None) -> bool:
    if not user or getattr(user, "is_bot", False):
        return False

    now = int(time.time())
    key = str(int(user.id))

    with _LOCK:
        data = _load_registry()
        existing = data.get(key, {})
        created_at = existing.get("created_at", now)
        data[key] = {
            "user_id": int(user.id),
            "username": str(getattr(user, "username", "") or ""),
            "first_name": str(getattr(user, "first_name", "") or ""),
            "last_name": str(getattr(user, "last_name", "") or ""),
            "full_name": str(user.full_name or ""),
            "language_code": str(getattr(user, "language_code", "") or ""),
            "created_at": int(created_at) if isinstance(created_at, int) else now,
            "updated_at": now,
        }
        _save_registry(data)
    return True


def remember_effective_user(update: Update | None) -> bool:
    if not update:
        return False
    return remember_user(getattr(update, "effective_user", None))


def get_all_users() -> list[int]:
    with _LOCK:
        data = _load_registry()
    users: list[int] = []
    for key in data:
        try:
            users.append(int(key))
        except ValueError:
            continue
    users.sort()
    return users


def get_total_users() -> int:
    return len(get_all_users())


def remove_user(user_id: int) -> bool:
    key = str(int(user_id))
    with _LOCK:
        data = _load_registry()
        if key not in data:
            return False
        data.pop(key, None)
        _save_registry(data)
    return True


def remove_users(user_ids: Iterable[int]) -> int:
    keys = {str(int(user_id)) for user_id in user_ids}
    if not keys:
        return 0

    removed = 0
    with _LOCK:
        data = _load_registry()
        for key in keys:
            if key in data:
                data.pop(key, None)
                removed += 1
        if removed:
            _save_registry(data)
    return removed
