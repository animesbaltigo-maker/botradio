from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from telegram import InputMediaPhoto, Message, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden, NetworkError, RetryAfter, TimedOut
from telegram.ext import ContextTypes

from config import ADMIN_IDS
from services.broadcast_ui import (
    build_broadcast_finished_text,
    build_broadcast_menu_keyboard,
    build_broadcast_menu_text,
    build_broadcast_message_keyboard,
    build_broadcast_mode_keyboard,
    build_broadcast_preview_keyboard,
    build_broadcast_preview_text,
    build_broadcast_progress_text,
    build_broadcast_prompt_keyboard,
    build_broadcast_starting_text,
)
from services.chat_cleanup import delete_message_safely
from services.i18n import t
from services.user_registry import get_all_users, get_total_users, remove_user, remove_users
from services.user_state import get_resolved_language


LOGGER = logging.getLogger(__name__)

BROADCAST_STATE_KEY = "broadcast_state"
BROADCAST_DATA_KEY = "broadcast_data"
BROADCAST_LOCK_KEY = "broadcast_lock"
BROADCAST_LAST_KEY = "broadcast_last_action"
BROADCAST_PANEL_KEY = "broadcast_panel"

BROADCAST_COOLDOWN = 1.0

SEND_WORKERS = 4
PER_MESSAGE_DELAY = 0.05
STATUS_EVERY = 100
STATUS_MIN_INTERVAL = 3.0
PIN_MAX_USERS = 100

GLOBAL_BROADCAST_RUNNING_KEY = "broadcast_running"
GLOBAL_BROADCAST_TASK_KEY = "broadcast_task"


def _is_admin(user_id: int | None) -> bool:
    return user_id is not None and user_id in ADMIN_IDS


def _blank_data() -> dict[str, object]:
    return {
        "mode": None,
        "target_user_id": None,
        "text": "",
        "photo": None,
        "button_text": "",
        "button_url": "",
        "draft_button_text": "",
        "pin": False,
    }


def _panel_data(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object]:
    data = context.user_data.get(BROADCAST_DATA_KEY)
    if not isinstance(data, dict):
        data = _blank_data()
        context.user_data[BROADCAST_DATA_KEY] = data
    return data


def _set_state(context: ContextTypes.DEFAULT_TYPE, state: str) -> None:
    context.user_data[BROADCAST_STATE_KEY] = state


def _get_state(context: ContextTypes.DEFAULT_TYPE) -> str:
    value = context.user_data.get(BROADCAST_STATE_KEY, "")
    return str(value or "")


def _clear_transient_fields(data: dict[str, object]) -> None:
    data["draft_button_text"] = ""


def _reset_broadcast(context: ContextTypes.DEFAULT_TYPE, *, keep_panel: bool = False) -> None:
    context.user_data.pop(BROADCAST_STATE_KEY, None)
    context.user_data.pop(BROADCAST_DATA_KEY, None)
    context.user_data.pop(BROADCAST_LOCK_KEY, None)
    context.user_data.pop(BROADCAST_LAST_KEY, None)
    if not keep_panel:
        context.user_data.pop(BROADCAST_PANEL_KEY, None)


def _remember_panel_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    *,
    kind: str,
) -> None:
    context.user_data[BROADCAST_PANEL_KEY] = {
        "chat_id": int(message.chat_id),
        "message_id": int(message.message_id),
        "kind": kind,
    }


def _panel_ref(context: ContextTypes.DEFAULT_TYPE) -> dict[str, object] | None:
    ref = context.user_data.get(BROADCAST_PANEL_KEY)
    if isinstance(ref, dict):
        return ref
    return None


async def _delete_panel_message(context: ContextTypes.DEFAULT_TYPE) -> None:
    ref = _panel_ref(context)
    if not ref:
        return
    try:
        await context.bot.delete_message(
            chat_id=int(ref["chat_id"]),
            message_id=int(ref["message_id"]),
        )
    except Exception:
        LOGGER.debug("Falha ao apagar painel de broadcast", exc_info=True)
    finally:
        context.user_data.pop(BROADCAST_PANEL_KEY, None)


async def _guard_action(context: ContextTypes.DEFAULT_TYPE) -> str:
    now = time.monotonic()

    if context.user_data.get(BROADCAST_LOCK_KEY):
        return "locked"

    last = float(context.user_data.get(BROADCAST_LAST_KEY, 0.0) or 0.0)
    if now - last < BROADCAST_COOLDOWN:
        return "cooldown"

    context.user_data[BROADCAST_LOCK_KEY] = True
    context.user_data[BROADCAST_LAST_KEY] = now
    return "ok"


def _release_guard(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.pop(BROADCAST_LOCK_KEY, None)


def _broadcast_is_running(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.application.bot_data.get(GLOBAL_BROADCAST_RUNNING_KEY, False))


def _set_broadcast_running(context: ContextTypes.DEFAULT_TYPE, value: bool) -> None:
    context.application.bot_data[GLOBAL_BROADCAST_RUNNING_KEY] = value


def _set_broadcast_task(context: ContextTypes.DEFAULT_TYPE, task: asyncio.Task[object] | None) -> None:
    if task is None:
        context.application.bot_data.pop(GLOBAL_BROADCAST_TASK_KEY, None)
        return
    context.application.bot_data[GLOBAL_BROADCAST_TASK_KEY] = task


async def _send_panel_text(
    context: ContextTypes.DEFAULT_TYPE,
    *,
    chat_id: int,
    reply_to_message_id: int | None,
    text: str,
    reply_markup,
) -> Message:
    sent = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
        reply_to_message_id=reply_to_message_id,
    )
    _remember_panel_message(context, sent, kind="text")
    return sent


async def _render_panel_text(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    text: str,
    reply_markup,
    *,
    query_message: Message | None = None,
    source_message: Message | None = None,
) -> Message | None:
    if query_message:
        if query_message.photo:
            sent = await _send_panel_text(
                context,
                chat_id=int(query_message.chat_id),
                reply_to_message_id=None,
                text=text,
                reply_markup=reply_markup,
            )
            await delete_message_safely(query_message)
            return sent
        try:
            await query_message.edit_text(
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            _remember_panel_message(context, query_message, kind="text")
            return query_message
        except Exception:
            LOGGER.debug("Falha ao editar painel via callback", exc_info=True)

    panel = _panel_ref(context)
    if panel:
        try:
            kind = str(panel.get("kind") or "text")
            if kind == "photo":
                sent = await _send_panel_text(
                    context,
                    chat_id=int(panel["chat_id"]),
                    reply_to_message_id=None,
                    text=text,
                    reply_markup=reply_markup,
                )
                try:
                    await context.bot.delete_message(
                        chat_id=int(panel["chat_id"]),
                        message_id=int(panel["message_id"]),
                    )
                except Exception:
                    LOGGER.debug("Falha ao apagar painel antigo em foto", exc_info=True)
                return sent

            await context.bot.edit_message_text(
                chat_id=int(panel["chat_id"]),
                message_id=int(panel["message_id"]),
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return None
        except Exception:
            LOGGER.debug("Falha ao editar painel salvo", exc_info=True)

    if source_message:
        return await _send_panel_text(
            context,
            chat_id=int(source_message.chat_id),
            reply_to_message_id=None,
            text=text,
            reply_markup=reply_markup,
        )
    return None


def _with_note(base_text: str, note: str | None = None) -> str:
    if not note:
        return base_text
    return f"{base_text}\n\n<blockquote>{note}</blockquote>"


async def _show_main_menu(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    *,
    query_message: Message | None = None,
    source_message: Message | None = None,
    note: str | None = None,
) -> Message | None:
    data = _panel_data(context)
    text = build_broadcast_menu_text(
        locale,
        data,
        total_users=get_total_users(),
        running=_broadcast_is_running(context),
        note=note,
    )
    keyboard = build_broadcast_menu_keyboard(locale, data, running=_broadcast_is_running(context))
    _set_state(context, "")
    _clear_transient_fields(data)
    return await _render_panel_text(
        context,
        locale,
        text,
        keyboard,
        query_message=query_message,
        source_message=source_message,
    )


async def _show_prompt(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    prompt_text: str,
    *,
    remove_callback: str | None = None,
    query_message: Message | None = None,
    source_message: Message | None = None,
    note: str | None = None,
) -> Message | None:
    return await _render_panel_text(
        context,
        locale,
        _with_note(prompt_text, note),
        build_broadcast_prompt_keyboard(locale, remove_callback=remove_callback),
        query_message=query_message,
        source_message=source_message,
    )


async def _show_preview(
    context: ContextTypes.DEFAULT_TYPE,
    locale: str,
    *,
    query_message: Message,
) -> None:
    data = _panel_data(context)
    caption = build_broadcast_preview_text(locale, data)
    keyboard = build_broadcast_preview_keyboard(locale, data)
    photo = data.get("photo")

    if photo:
        try:
            await query_message.edit_media(
                media=InputMediaPhoto(
                    media=str(photo),
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=keyboard,
            )
            _remember_panel_message(context, query_message, kind="photo")
            return
        except Exception:
            LOGGER.debug("Falha ao editar preview com foto", exc_info=True)

    await _render_panel_text(
        context,
        locale,
        caption,
        keyboard,
        query_message=query_message,
    )


def _build_admin_reply_markup(data: dict[str, object]):
    return build_broadcast_message_keyboard(data)


async def _send_broadcast_message(bot, chat_id: int, data: dict[str, object]) -> Message:
    text = str(data.get("text") or "").strip()
    photo = data.get("photo")
    reply_markup = _build_admin_reply_markup(data)

    if photo:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=str(photo),
            caption=text or None,
            parse_mode=ParseMode.HTML if text else None,
            reply_markup=reply_markup,
        )

    return await bot.send_message(
        chat_id=chat_id,
        text=text or "📢",
        parse_mode=ParseMode.HTML,
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def _maybe_pin_message(bot, chat_id: int, message: Message | None, should_pin: bool) -> None:
    if not should_pin or not message:
        return
    try:
        await bot.pin_chat_message(
            chat_id=chat_id,
            message_id=message.message_id,
            disable_notification=True,
        )
    except Exception:
        LOGGER.debug("Falha ao fixar mensagem do broadcast", exc_info=True)


def _should_remove_user_on_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return (
        "blocked" in text
        or "chat not found" in text
        or "user is deactivated" in text
        or "bot was blocked by the user" in text
        or "forbidden" in text
    )


async def _safe_send_one(bot, user_id: int, data: dict[str, object], should_pin: bool) -> tuple[bool, bool]:
    try:
        message = await _send_broadcast_message(bot, user_id, data)
        await _maybe_pin_message(bot, user_id, message, should_pin)
        return True, False
    except RetryAfter as exc:
        await asyncio.sleep(float(exc.retry_after) + 1.0)
        try:
            message = await _send_broadcast_message(bot, user_id, data)
            await _maybe_pin_message(bot, user_id, message, should_pin)
            return True, False
        except Exception as retry_exc:
            return False, _should_remove_user_on_error(retry_exc)
    except (Forbidden, BadRequest) as exc:
        return False, _should_remove_user_on_error(exc)
    except (TimedOut, NetworkError):
        return False, False
    except Exception as exc:
        return False, _should_remove_user_on_error(exc)


async def _update_status_message(locale: str, status_msg: Message, counters: dict[str, Any], total: int) -> None:
    try:
        await status_msg.edit_text(
            build_broadcast_progress_text(
                locale,
                sent=int(counters["sent"]),
                failed=int(counters["failed"]),
                processed=int(counters["processed"]),
                total=total,
            ),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        LOGGER.debug("Falha ao atualizar status do broadcast", exc_info=True)


async def _broadcast_worker(
    queue: asyncio.Queue[int | None],
    *,
    bot,
    data: dict[str, object],
    should_pin: bool,
    counters: dict[str, Any],
    status_msg: Message,
    total: int,
    locale: str,
) -> None:
    while True:
        user_id = await queue.get()
        try:
            if user_id is None:
                return

            ok, should_remove = await _safe_send_one(bot, int(user_id), data, should_pin)
            if ok:
                counters["sent"] += 1
            else:
                counters["failed"] += 1
                if should_remove:
                    counters["remove_ids"].add(int(user_id))

            counters["processed"] += 1
            now = time.monotonic()
            should_update = (
                counters["processed"] % STATUS_EVERY == 0
                or now - float(counters["last_status_at"]) >= STATUS_MIN_INTERVAL
            )
            if should_update:
                async with counters["status_lock"]:
                    now = time.monotonic()
                    if (
                        counters["processed"] % STATUS_EVERY == 0
                        or now - float(counters["last_status_at"]) >= STATUS_MIN_INTERVAL
                    ):
                        counters["last_status_at"] = now
                        await _update_status_message(locale, status_msg, counters, total)

            await asyncio.sleep(PER_MESSAGE_DELAY)
        finally:
            queue.task_done()


async def _execute_broadcast_background(
    *,
    application,
    bot,
    admin_chat_id: int,
    reply_to_message_id: int | None,
    data: dict[str, object],
    locale: str,
) -> None:
    try:
        mode = str(data.get("mode") or "")
        text = str(data.get("text") or "").strip()
        photo = data.get("photo")
        target_user_id = data.get("target_user_id")
        requested_pin = bool(data.get("pin"))

        if mode not in {"all", "single"}:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=t(locale, "broadcast.error.define_destination"),
                reply_to_message_id=reply_to_message_id,
            )
            return

        if not text and not photo:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=t(locale, "broadcast.error.define_content"),
                reply_to_message_id=reply_to_message_id,
            )
            return

        if mode == "single":
            if not isinstance(target_user_id, int):
                await bot.send_message(
                    chat_id=admin_chat_id,
                    text=t(locale, "broadcast.error.invalid_user_id"),
                    reply_to_message_id=reply_to_message_id,
                )
                return

            ok, should_remove = await _safe_send_one(bot, target_user_id, data, requested_pin)
            if should_remove:
                remove_user(target_user_id)
            await bot.send_message(
                chat_id=admin_chat_id,
                text=t(locale, "broadcast.single_done", sent=1 if ok else 0, failed=0 if ok else 1),
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to_message_id,
            )
            return

        users = get_all_users()
        if not users:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=t(locale, "broadcast.error.no_users"),
                reply_to_message_id=reply_to_message_id,
            )
            return

        total = len(users)
        should_pin = requested_pin and total <= PIN_MAX_USERS
        status_msg = await bot.send_message(
            chat_id=admin_chat_id,
            text=build_broadcast_starting_text(
                locale,
                total,
                pin_disabled=requested_pin and not should_pin,
                pin_limit=PIN_MAX_USERS,
            ),
            parse_mode=ParseMode.HTML,
            reply_to_message_id=reply_to_message_id,
        )

        queue: asyncio.Queue[int | None] = asyncio.Queue()
        counters: dict[str, Any] = {
            "sent": 0,
            "failed": 0,
            "processed": 0,
            "remove_ids": set(),
            "last_status_at": time.monotonic(),
            "status_lock": asyncio.Lock(),
        }

        for user_id in users:
            await queue.put(int(user_id))
        for _ in range(SEND_WORKERS):
            await queue.put(None)

        workers = [
            asyncio.create_task(
                _broadcast_worker(
                    queue,
                    bot=bot,
                    data=data,
                    should_pin=should_pin,
                    counters=counters,
                    status_msg=status_msg,
                    total=total,
                    locale=locale,
                )
            )
            for _ in range(SEND_WORKERS)
        ]

        await queue.join()
        await asyncio.gather(*workers, return_exceptions=True)

        removed = remove_users(counters["remove_ids"])
        final_text = build_broadcast_finished_text(
            locale,
            sent=int(counters["sent"]),
            failed=int(counters["failed"]),
            removed=removed,
            processed=int(counters["processed"]),
        )
        try:
            await status_msg.edit_text(final_text, parse_mode=ParseMode.HTML)
        except Exception:
            await bot.send_message(
                chat_id=admin_chat_id,
                text=final_text,
                parse_mode=ParseMode.HTML,
                reply_to_message_id=reply_to_message_id,
            )
    finally:
        application.bot_data[GLOBAL_BROADCAST_RUNNING_KEY] = False
        application.bot_data.pop(GLOBAL_BROADCAST_TASK_KEY, None)


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not _is_admin(user.id):
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    _set_state(context, "")
    _panel_data(context)
    await _show_main_menu(context, locale, source_message=message)
    await delete_message_safely(message)


async def broadcast_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user = update.effective_user
    if not query or not query.message or not user or not _is_admin(user.id):
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    try:
        await query.answer()
    except Exception:
        LOGGER.debug("Falha ao responder callback do broadcast", exc_info=True)

    guard = await _guard_action(context)
    if guard != "ok":
        return

    try:
        data = _panel_data(context)
        action = str(query.data or "")
        if not action.startswith("bc|"):
            return
        action = action.split("|", 1)[1]

        if action == "menu":
            await _show_main_menu(context, locale, query_message=query.message)
            return

        if action == "close":
            _reset_broadcast(context)
            await delete_message_safely(query.message)
            return

        if action == "reset":
            context.user_data[BROADCAST_DATA_KEY] = _blank_data()
            await _show_main_menu(context, locale, query_message=query.message)
            return

        if action == "set_mode":
            _set_state(context, "")
            await _render_panel_text(
                context,
                locale,
                t(locale, "broadcast.prompt.mode"),
                build_broadcast_mode_keyboard(locale),
                query_message=query.message,
            )
            return

        if action == "mode_all":
            data["mode"] = "all"
            data["target_user_id"] = None
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, "broadcast.saved.mode_all"),
            )
            return

        if action == "mode_single":
            data["mode"] = "single"
            _set_state(context, "awaiting_target_user_id")
            await _show_prompt(
                context,
                locale,
                t(locale, "broadcast.prompt.target_user"),
                query_message=query.message,
            )
            return

        if action == "set_media":
            _set_state(context, "awaiting_media")
            await _show_prompt(
                context,
                locale,
                t(locale, "broadcast.prompt.media"),
                query_message=query.message,
                remove_callback="bc|remove_media",
            )
            return

        if action == "set_text":
            _set_state(context, "awaiting_text")
            await _show_prompt(
                context,
                locale,
                t(locale, "broadcast.prompt.text"),
                query_message=query.message,
                remove_callback="bc|remove_text",
            )
            return

        if action == "set_button":
            _set_state(context, "awaiting_button_text")
            data["draft_button_text"] = ""
            await _show_prompt(
                context,
                locale,
                t(locale, "broadcast.prompt.button_text"),
                query_message=query.message,
                remove_callback="bc|remove_button",
            )
            return

        if action == "remove_media":
            data["photo"] = None
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, "broadcast.removed.media"),
            )
            return

        if action == "remove_text":
            data["text"] = ""
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, "broadcast.removed.text"),
            )
            return

        if action == "remove_button":
            data["button_text"] = ""
            data["button_url"] = ""
            data["draft_button_text"] = ""
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, "broadcast.removed.button"),
            )
            return

        if action == "toggle_pin":
            data["pin"] = not bool(data.get("pin"))
            note_key = "broadcast.saved.pin_on" if bool(data.get("pin")) else "broadcast.saved.pin_off"
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, note_key),
            )
            return

        if action == "preview":
            await _show_preview(context, locale, query_message=query.message)
            return

        if action == "send":
            if _broadcast_is_running(context):
                await query.answer(t(locale, "broadcast.error.already_running")[:180], show_alert=True)
                return

            mode = str(data.get("mode") or "")
            if mode not in {"all", "single"}:
                await query.answer(t(locale, "broadcast.error.define_destination")[:180], show_alert=True)
                return

            text = str(data.get("text") or "").strip()
            photo = data.get("photo")
            if not text and not photo:
                await query.answer(t(locale, "broadcast.error.define_content")[:180], show_alert=True)
                return

            safe_payload = dict(data)
            _set_state(context, "")
            task = context.application.create_task(
                _execute_broadcast_background(
                    application=context.application,
                    bot=context.bot,
                    admin_chat_id=int(update.effective_chat.id),
                    reply_to_message_id=int(query.message.message_id),
                    data=safe_payload,
                    locale=locale,
                )
            )
            _set_broadcast_task(context, task)
            _set_broadcast_running(context, True)
            await _show_main_menu(
                context,
                locale,
                query_message=query.message,
                note=t(locale, "broadcast.started"),
            )
            return
    finally:
        _release_guard(context)


async def broadcast_message_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    message = update.effective_message
    if not user or not message or not _is_admin(user.id):
        return

    state = _get_state(context)
    if not state:
        return

    locale = get_resolved_language(context, getattr(user, "language_code", None))
    data = _panel_data(context)

    try:
        if state == "awaiting_target_user_id":
            raw = str(message.text or "").strip()
            if not raw.isdigit():
                await _show_prompt(
                    context,
                    locale,
                    t(locale, "broadcast.prompt.target_user"),
                    source_message=message,
                    note=t(locale, "broadcast.error.invalid_user_id"),
                )
                return

            data["mode"] = "single"
            data["target_user_id"] = int(raw)
            await _show_main_menu(
                context,
                locale,
                source_message=message,
                note=t(locale, "broadcast.saved.target_user", user_id=raw),
            )
            return

        if state == "awaiting_media":
            photo = message.photo[-1] if message.photo else None
            if not photo:
                await _show_prompt(
                    context,
                    locale,
                    t(locale, "broadcast.prompt.media"),
                    source_message=message,
                    remove_callback="bc|remove_media",
                    note=t(locale, "broadcast.error.photo_required"),
                )
                return

            data["photo"] = photo.file_id
            await _show_main_menu(
                context,
                locale,
                source_message=message,
                note=t(locale, "broadcast.saved.media"),
            )
            return

        if state == "awaiting_text":
            raw = str(message.text or "").strip()
            if not raw:
                await _show_prompt(
                    context,
                    locale,
                    t(locale, "broadcast.prompt.text"),
                    source_message=message,
                    remove_callback="bc|remove_text",
                    note=t(locale, "broadcast.error.text_required"),
                )
                return

            data["text"] = raw
            await _show_main_menu(
                context,
                locale,
                source_message=message,
                note=t(locale, "broadcast.saved.text"),
            )
            return

        if state == "awaiting_button_text":
            raw = str(message.text or "").strip()
            if not raw:
                await _show_prompt(
                    context,
                    locale,
                    t(locale, "broadcast.prompt.button_text"),
                    source_message=message,
                    remove_callback="bc|remove_button",
                    note=t(locale, "broadcast.error.text_required"),
                )
                return

            data["draft_button_text"] = raw
            _set_state(context, "awaiting_button_url")
            await _show_prompt(
                context,
                locale,
                t(locale, "broadcast.prompt.button_url"),
                source_message=message,
                remove_callback="bc|remove_button",
            )
            return

        if state == "awaiting_button_url":
            raw = str(message.text or "").strip()
            if not (
                raw.startswith("http://")
                or raw.startswith("https://")
                or raw.startswith("tg://")
            ):
                await _show_prompt(
                    context,
                    locale,
                    t(locale, "broadcast.prompt.button_url"),
                    source_message=message,
                    remove_callback="bc|remove_button",
                    note=t(locale, "broadcast.error.invalid_url"),
                )
                return

            data["button_text"] = str(data.get("draft_button_text") or "").strip()
            data["button_url"] = raw
            data["draft_button_text"] = ""
            await _show_main_menu(
                context,
                locale,
                source_message=message,
                note=t(locale, "broadcast.saved.button"),
            )
            return
    finally:
        await delete_message_safely(message)
