from __future__ import annotations

import asyncio
import html
import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import ADMIN_IDS, POST_SEPARATOR_STICKER, RADIO_ANIMES_CHANNEL_CHAT
from services.animethemes_client import AnimeCandidate, AnimeNotFoundError, AnimeThemesClient
from services.chat_cleanup import delete_message_safely
from services.i18n import t
from services.intents import KIND_ANY, encode_start_parameter, make_slug_action
from services.radio_ui import build_inline_open_keyboard, build_inline_result_caption
from services.user_state import get_resolved_language


LOGGER = logging.getLogger(__name__)

POST_BULK_DELAY_SECONDS = 1.25
POST_BULK_MAX_COUNT = 100
POST_BULK_RUNNING_KEY = "postanime_bulk_running"
POST_BULK_TASK_KEY = "postanime_bulk_task"


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient nao inicializado.")
    return client


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(getattr(context.bot, "username", "") or "")


def _bot_url(context: ContextTypes.DEFAULT_TYPE, start_parameter: str) -> str:
    username = _bot_username(context).lstrip("@")
    return f"https://t.me/{username}?start={start_parameter}"


def _bulk_running(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get(POST_BULK_RUNNING_KEY, False))


def _set_bulk_running(context: ContextTypes.DEFAULT_TYPE, value: bool) -> None:
    context.application.bot_data[POST_BULK_RUNNING_KEY] = value


def _set_bulk_task(context: ContextTypes.DEFAULT_TYPE, task: object | None) -> None:
    if task is None:
        context.application.bot_data.pop(POST_BULK_TASK_KEY, None)
        return
    context.application.bot_data[POST_BULK_TASK_KEY] = task


def _parse_bulk_count(args: list[str]) -> int | None:
    if len(args) != 1:
        return None
    raw = args[0].strip()
    if not raw.isdigit():
        return None
    return int(raw)


async def _send_separator(bot, destination: str) -> None:
    if not POST_SEPARATOR_STICKER:
        return
    try:
        await bot.send_sticker(chat_id=destination, sticker=POST_SEPARATOR_STICKER)
    except Exception:
        LOGGER.exception("Falha ao enviar sticker divisor do /postanime")


async def _safe_edit_status(message, text: str) -> None:
    try:
        await message.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception:
        LOGGER.exception("Falha ao atualizar status do /postanime")


async def _send_anime_post(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    candidate: AnimeCandidate,
) -> None:
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


async def _run_bulk_postanime(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    count: int,
    status_message,
) -> None:
    try:
        await _safe_edit_status(
            status_message,
            t(locale, "postanime.bulk_fetching", count=count),
        )

        candidates = await _client(context).get_random_anime_candidates(count)
        if not candidates:
            await _safe_edit_status(status_message, t(locale, "postanime.bulk_empty"))
            return

        total = len(candidates)
        sent = 0
        failed = 0

        for index, candidate in enumerate(candidates, start=1):
            current_title = html.escape(candidate.anime_name or "Anime")
            try:
                await _send_anime_post(context, locale, candidate)
                sent += 1
            except Exception as exc:
                failed += 1
                LOGGER.exception("Falha ao postar anime em lote: %s", candidate.anime_slug, exc_info=exc)

            await _safe_edit_status(
                status_message,
                t(
                    locale,
                    "postanime.bulk_progress",
                    sent=sent,
                    failed=failed,
                    processed=index,
                    total=total,
                    current=current_title,
                ),
            )

            if index < total:
                await asyncio.sleep(POST_BULK_DELAY_SECONDS)

        await _safe_edit_status(
            status_message,
            t(
                locale,
                "postanime.bulk_finished",
                sent=sent,
                failed=failed,
                total=total,
            ),
        )
    finally:
        _set_bulk_running(context, False)
        _set_bulk_task(context, None)


async def postanime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if not message or not user:
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    if not _is_admin(user.id):
        await message.reply_text(t(locale, "errors.admin_only"), parse_mode=ParseMode.HTML)
        return

    if not context.args:
        await message.reply_text(t(locale, "postanime.usage"), parse_mode=ParseMode.HTML)
        await delete_message_safely(message)
        return

    bulk_count = _parse_bulk_count(context.args)
    if bulk_count is not None:
        if bulk_count < 1 or bulk_count > POST_BULK_MAX_COUNT:
            await message.reply_text(
                t(locale, "postanime.bulk_invalid_count", max_count=POST_BULK_MAX_COUNT),
                parse_mode=ParseMode.HTML,
            )
            await delete_message_safely(message)
            return

        if _bulk_running(context):
            await message.reply_text(
                t(locale, "postanime.bulk_already_running"),
                parse_mode=ParseMode.HTML,
            )
            await delete_message_safely(message)
            return

        status_message = await message.reply_text(
            t(
                locale,
                "postanime.bulk_started",
                count=bulk_count,
                delay=f"{POST_BULK_DELAY_SECONDS:.2f}s",
            ),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        await delete_message_safely(message)

        _set_bulk_running(context, True)
        task = context.application.create_task(
            _run_bulk_postanime(context, locale, bulk_count, status_message)
        )
        _set_bulk_task(context, task)
        return

    query = " ".join(context.args).strip()
    status_message = await message.reply_text(
        t(locale, "postanime.loading"),
        parse_mode=ParseMode.HTML,
    )
    await delete_message_safely(message)

    try:
        results = await _client(context).search_anime(query, None, limit=8)
        if not results:
            await _safe_edit_status(status_message, t(locale, "postanime.not_found"))
            return

        candidate = results[0]
        if not candidate.anime_slug:
            await _safe_edit_status(status_message, t(locale, "postanime.resolve_error"))
            return

        await _send_anime_post(context, locale, candidate)
        await _safe_edit_status(
            status_message,
            t(locale, "postanime.success", title=html.escape(candidate.anime_name)),
        )
    except AnimeNotFoundError:
        await _safe_edit_status(status_message, t(locale, "postanime.not_found"))
    except Exception as exc:
        await _safe_edit_status(
            status_message,
            t(
                locale,
                "postanime.post_error",
                error=html.escape(str(exc) or t(locale, "errors.generic_action")),
            ),
        )
