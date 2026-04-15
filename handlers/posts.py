from __future__ import annotations

import html

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, POST_SEPARATOR_STICKER, RADIO_ANIMES_CHANNEL_CHAT
from services.animethemes_client import AnimeNotFoundError, AnimeThemesClient
from services.chat_cleanup import delete_message_safely
from services.i18n import t
from services.intents import KIND_ANY, encode_start_parameter, make_slug_action
from services.radio_ui import build_inline_open_keyboard, build_inline_result_caption
from services.user_state import get_resolved_language


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient não inicializado.")
    return client


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(getattr(context.bot, "username", "") or "")


def _bot_url(context: ContextTypes.DEFAULT_TYPE, start_parameter: str) -> str:
    username = _bot_username(context).lstrip("@")
    return f"https://t.me/{username}?start={start_parameter}"


async def _send_separator(bot, destination: str) -> None:
    if not POST_SEPARATOR_STICKER:
        return
    try:
        await bot.send_sticker(chat_id=destination, sticker=POST_SEPARATOR_STICKER)
    except Exception:
        pass


async def postanime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    if not _is_admin(user.id):
        await message.reply_text(t(locale, "errors.admin_only"), parse_mode=ParseMode.HTML)
        return

    query = " ".join(context.args).strip()
    if not query:
        await message.reply_text(t(locale, "postanime.usage"), parse_mode=ParseMode.HTML)
        await delete_message_safely(message)
        return

    status_message = await message.reply_text(
        t(locale, "postanime.loading"),
        parse_mode=ParseMode.HTML,
    )
    await delete_message_safely(message)

    try:
        results = await _client(context).search_anime(query, None, limit=8)
        if not results:
            await status_message.edit_text(t(locale, "postanime.not_found"), parse_mode=ParseMode.HTML)
            return

        candidate = results[0]
        if not candidate.anime_slug:
            await status_message.edit_text(t(locale, "postanime.resolve_error"), parse_mode=ParseMode.HTML)
            return

        start_parameter = encode_start_parameter(make_slug_action(candidate.anime_slug, KIND_ANY)) or "start"
        deep_link = _bot_url(context, start_parameter)
        caption = build_inline_result_caption(locale, candidate, _bot_username(context), KIND_ANY)
        keyboard = build_inline_open_keyboard(locale, deep_link)
        destination = RADIO_ANIMES_CHANNEL_CHAT

        if candidate.image_link:
            try:
                await context.bot.send_photo(
                    chat_id=destination,
                    photo=candidate.image_link,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                    pool_timeout=30,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=destination,
                    text=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
        else:
            await context.bot.send_message(
                chat_id=destination,
                text=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )

        await _send_separator(context.bot, destination)
        await status_message.edit_text(
            t(locale, "postanime.success", title=html.escape(candidate.anime_name)),
            parse_mode=ParseMode.HTML,
        )
    except AnimeNotFoundError:
        await status_message.edit_text(t(locale, "postanime.not_found"), parse_mode=ParseMode.HTML)
    except Exception as exc:
        await status_message.edit_text(
            t(
                locale,
                "postanime.post_error",
                error=html.escape(str(exc) or t(locale, "errors.generic_action")),
            ),
            parse_mode=ParseMode.HTML,
        )
