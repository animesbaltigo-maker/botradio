from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from handlers.radio import execute_pending_action
from services.gatekeeper import Gatekeeper
from services.i18n import LANGUAGE_OPTIONS, resolve_locale, strip_html, t
from services.radio_ui import build_error_text, build_language_picker_text, build_usage_text
from services.user_state import (
    clear_pending_action,
    get_language,
    get_pending_action,
    get_resolved_language,
    has_selected_language,
    set_language,
)


LOGGER = logging.getLogger(__name__)


def _gate(context: ContextTypes.DEFAULT_TYPE) -> Gatekeeper:
    gate = context.application.bot_data.get("gatekeeper")
    if not isinstance(gate, Gatekeeper):
        raise RuntimeError("Gatekeeper não inicializado.")
    return gate


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    locale = get_resolved_language(context, getattr(update.effective_user, "language_code", None))
    current_locale = get_language(context)
    await message.reply_text(
        build_language_picker_text(locale, current_locale=current_locale),
        parse_mode=ParseMode.HTML,
        reply_markup=build_language_keyboard(),
    )


def build_language_keyboard():
    from services.radio_ui import build_language_keyboard as _build_language_keyboard

    return _build_language_keyboard()


async def language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    locale_guess = resolve_locale(get_language(context), getattr(update.effective_user, "language_code", None))

    try:
        if query.data == "lang:open":
            await query.answer()
            await query.message.reply_text(
                build_language_picker_text(locale_guess, current_locale=get_language(context)),
                parse_mode=ParseMode.HTML,
                reply_markup=build_language_keyboard(),
            )
            return

        _, action, locale_code = query.data.split(":", 2)
        if action != "set":
            return
    except Exception:
        await query.answer(build_error_text(locale_guess, RuntimeError())[:180], show_alert=True)
        return

    chosen_locale = set_language(context, locale_code)
    chosen_label = next(
        (option.label for option in LANGUAGE_OPTIONS if option.code == chosen_locale),
        chosen_locale,
    )

    try:
        await query.answer(t(chosen_locale, "status.language_saved", language=chosen_label)[:180])
    except Exception:
        LOGGER.exception("Falha ao responder seleção de idioma")

    pending_action = get_pending_action(context)

    try:
        is_member = await _gate(context).is_channel_member(
            context,
            update.effective_user.id,
            force_refresh=True,
        )
    except Exception as exc:
        await query.message.reply_text(build_error_text(chosen_locale, exc))
        return

    if not is_member:
        await query.message.reply_text(
            t(chosen_locale, "status.language_saved", language=chosen_label),
            parse_mode=ParseMode.HTML,
        )
        await _gate(context).send_channel_gate(query.message, chosen_locale)
        return

    if pending_action:
        clear_pending_action(context)
        await _gate(context).send_ready_message(query.message, chosen_locale)
        try:
            await execute_pending_action(context, query.message, pending_action, chosen_locale)
        except Exception as exc:
            await query.message.reply_text(build_error_text(chosen_locale, exc))
        return

    await query.message.reply_text(
        t(chosen_locale, "status.language_saved", language=chosen_label),
        parse_mode=ParseMode.HTML,
    )


async def gate_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    locale = get_resolved_language(context, getattr(update.effective_user, "language_code", None))
    if not has_selected_language(context):
        await query.answer(strip_html(t(locale, "gate.language.body"))[:180], show_alert=True)
        await _gate(context).send_language_picker(query.message, locale, current_locale=get_language(context))
        return

    try:
        is_member = await _gate(context).is_channel_member(
            context,
            update.effective_user.id,
            force_refresh=True,
        )
    except Exception as exc:
        await query.answer(build_error_text(locale, exc)[:180], show_alert=True)
        return

    if not is_member:
        await query.answer(strip_html(t(locale, "gate.channel.body"))[:180], show_alert=True)
        await _gate(context).send_channel_gate(query.message, locale)
        return

    pending_action = get_pending_action(context)
    try:
        await query.answer(t(locale, "status.access_released")[:180])
    except Exception:
        LOGGER.exception("Falha ao responder liberação de acesso")

    if pending_action:
        clear_pending_action(context)
        await _gate(context).send_ready_message(query.message, locale)
        try:
            await execute_pending_action(context, query.message, pending_action, locale)
        except Exception as exc:
            await query.message.reply_text(build_error_text(locale, exc))
        return

    await _gate(context).send_ready_message(query.message, locale)


async def language_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    locale = get_resolved_language(context, getattr(update.effective_user, "language_code", None))
    await message.reply_text(build_usage_text(locale, "language"), parse_mode=ParseMode.HTML)
