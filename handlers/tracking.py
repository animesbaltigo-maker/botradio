from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from services.user_registry import remember_effective_user


async def track_user_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    remember_effective_user(update)
