from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.radio import execute_pending_action
from services.chat_cleanup import delete_message_safely, register_cleanup_message
from services.gatekeeper import Gatekeeper
from services.intents import decode_start_parameter
from services.radio_ui import build_error_text, build_start_caption
from services.user_state import (
    get_language,
    get_resolved_language,
    has_selected_language,
    set_pending_action,
)


BANNER_URL = "https://photo.chelpbot.me/AgACAgEAAxkBajdq32nec9iWglPIl5GfbuGfphLP6VoyAAIcDGsbjonxRu9aubnT6Bk0AQADAgADdwADOwQ/photo.jpg"


def _gate(context: ContextTypes.DEFAULT_TYPE) -> Gatekeeper:
    gate = context.application.bot_data.get("gatekeeper")
    if not isinstance(gate, Gatekeeper):
        raise RuntimeError("Gatekeeper não inicializado.")
    return gate


async def _send_start_banner(message, locale: str):
    return await message.reply_photo(
        photo=BANNER_URL,
        caption=build_start_caption(locale),
        parse_mode=ParseMode.HTML,
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    deep_link_action = decode_start_parameter(context.args[0]) if context.args else None
    if deep_link_action:
        set_pending_action(context, deep_link_action)

    if not has_selected_language(context):
        banner = await _send_start_banner(message, locale)
        register_cleanup_message(context, banner)
        await _gate(context).send_language_picker(message, locale, current_locale=get_language(context))
        await delete_message_safely(message)
        return

    try:
        is_member = await _gate(context).is_channel_member(context, user.id)
    except Exception as exc:
        await message.reply_text(build_error_text(locale, exc))
        return

    if not is_member:
        banner = await _send_start_banner(message, locale)
        register_cleanup_message(context, banner)
        await _gate(context).send_channel_gate(message, locale)
        await delete_message_safely(message)
        return

    if deep_link_action:
        await execute_pending_action(context, message, deep_link_action, locale)
        await delete_message_safely(message)
        return

    await _send_start_banner(message, locale)
    await delete_message_safely(message)
