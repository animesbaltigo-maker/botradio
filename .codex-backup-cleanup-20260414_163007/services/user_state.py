from __future__ import annotations

from typing import Any

from telegram.ext import ContextTypes

from services.i18n import DEFAULT_LOCALE, normalize_locale


STATE_KEY = "radio_animes"
LANGUAGE_KEY = "language"
PENDING_ACTION_KEY = "pending_action"


def _root(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any]:
    root = context.user_data.setdefault(STATE_KEY, {})
    if not isinstance(root, dict):
        root = {}
        context.user_data[STATE_KEY] = root
    return root


def get_language(context: ContextTypes.DEFAULT_TYPE) -> str | None:
    value = _root(context).get(LANGUAGE_KEY)
    if isinstance(value, str) and value.strip():
        return normalize_locale(value)
    return None


def set_language(context: ContextTypes.DEFAULT_TYPE, locale: str) -> str:
    normalized = normalize_locale(locale)
    _root(context)[LANGUAGE_KEY] = normalized
    return normalized


def get_resolved_language(
    context: ContextTypes.DEFAULT_TYPE,
    telegram_language_code: str | None = None,
) -> str:
    return normalize_locale(get_language(context) or telegram_language_code or DEFAULT_LOCALE)


def has_selected_language(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return get_language(context) is not None


def get_pending_action(context: ContextTypes.DEFAULT_TYPE) -> dict[str, Any] | None:
    value = _root(context).get(PENDING_ACTION_KEY)
    if isinstance(value, dict):
        return value
    return None


def set_pending_action(
    context: ContextTypes.DEFAULT_TYPE,
    action: dict[str, Any] | None,
) -> None:
    root = _root(context)
    if action is None:
        root.pop(PENDING_ACTION_KEY, None)
        return
    root[PENDING_ACTION_KEY] = action


def clear_pending_action(context: ContextTypes.DEFAULT_TYPE) -> None:
    _root(context).pop(PENDING_ACTION_KEY, None)

