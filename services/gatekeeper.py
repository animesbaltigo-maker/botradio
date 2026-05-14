from __future__ import annotations

from dataclasses import dataclass
import logging

from telegram import Message, Update
from telegram.ext import ContextTypes

from services.cache import AsyncTTLCache
from services.chat_cleanup import delete_message_safely
from services.errors import LocalizedError
from services.i18n import resolve_locale, strip_html, t
from services.radio_ui import (
    build_access_ready_text,
    build_channel_gate_keyboard,
    build_channel_gate_text,
    build_language_keyboard,
    build_language_picker_text,
)
from services.user_state import get_language, has_selected_language, set_pending_action


LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class AccessContext:
    locale: str
    selected_language: bool
    channel_member: bool


class Gatekeeper:
    def __init__(
        self,
        channel_chat_id: str | tuple[str, ...],
        channel_url: str,
        *,
        membership_ttl_seconds: float = 300.0,
    ) -> None:
        if isinstance(channel_chat_id, str):
            self._channel_chat_ids = (channel_chat_id,) if channel_chat_id else ()
        else:
            self._channel_chat_ids = tuple(channel for channel in channel_chat_id if channel)
        self._channel_url = channel_url
        self._membership_ttl_seconds = membership_ttl_seconds
        self._membership_cache: AsyncTTLCache[bool] = AsyncTTLCache()

    def resolve_locale(
        self,
        update: Update | None,
        context: ContextTypes.DEFAULT_TYPE,
    ) -> str:
        telegram_language = getattr(update.effective_user, "language_code", None) if update else None
        return resolve_locale(get_language(context), telegram_language)

    async def require_message_access(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        pending_action: dict[str, object] | None = None,
    ) -> AccessContext | None:
        message = update.effective_message
        user = update.effective_user
        if not message or not user:
            return None

        locale = self.resolve_locale(update, context)
        if not has_selected_language(context):
            if pending_action is not None:
                set_pending_action(context, pending_action)
            await self.send_language_picker(message, locale, current_locale=get_language(context))
            await delete_message_safely(message)
            return None

        try:
            is_member = await self.is_channel_member(context, user.id, force_refresh=True)
        except LocalizedError as exc:
            await message.reply_text(t(locale, exc.key, **exc.params))
            await delete_message_safely(message)
            return None

        if not is_member:
            if pending_action is not None:
                set_pending_action(context, pending_action)
            await self.send_channel_gate(message, locale)
            await delete_message_safely(message)
            return None

        return AccessContext(locale=locale, selected_language=True, channel_member=True)

    async def require_callback_access(
        self,
        update: Update,
        context: ContextTypes.DEFAULT_TYPE,
        *,
        pending_action: dict[str, object] | None = None,
    ) -> AccessContext | None:
        query = update.callback_query
        user = update.effective_user
        if not query or not query.message or not user:
            return None

        locale = self.resolve_locale(update, context)
        if not has_selected_language(context):
            if pending_action is not None:
                set_pending_action(context, pending_action)
            try:
                await query.answer(strip_html(t(locale, "gate.language.body"))[:180], show_alert=True)
            except Exception:
                LOGGER.exception("Falha ao responder callback de idioma")
            await self.send_language_picker(query.message, locale, current_locale=get_language(context))
            return None

        try:
            is_member = await self.is_channel_member(context, user.id, force_refresh=True)
        except LocalizedError as exc:
            try:
                await query.answer(t(locale, exc.key, **exc.params)[:180], show_alert=True)
            except Exception:
                LOGGER.exception("Falha ao responder callback de gate")
            return None

        if not is_member:
            if pending_action is not None:
                set_pending_action(context, pending_action)
            try:
                await query.answer(strip_html(t(locale, "gate.channel.body"))[:180], show_alert=True)
            except Exception:
                LOGGER.exception("Falha ao responder callback de canal")
            await self.send_channel_gate(query.message, locale)
            return None

        return AccessContext(locale=locale, selected_language=True, channel_member=True)

    async def is_channel_member(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        *,
        force_refresh: bool = False,
    ) -> bool:
        key = f"channel:{user_id}"
        stale_value = await self._membership_cache.peek(key)
        if force_refresh:
            await self._membership_cache.delete(key)
        else:
            cached_value = await self._membership_cache.get(key)
            if cached_value is not None:
                return cached_value

        try:
            return await self._membership_cache.get_or_create(
                key,
                ttl_seconds=self._membership_ttl_seconds,
                loader=lambda: self._fetch_channel_membership(context, user_id),
            )
        except Exception as exc:
            LOGGER.exception("Falha ao validar membro do canal", exc_info=exc)
            if stale_value is False:
                return stale_value
            raise LocalizedError("gate.channel_check_error") from exc

    async def send_language_picker(
        self,
        message: Message,
        locale: str,
        *,
        current_locale: str | None = None,
        replace: bool = False,
    ) -> Message:
        text = build_language_picker_text(locale, current_locale=current_locale)
        reply_markup = build_language_keyboard()
        if replace:
            try:
                await message.edit_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                return message
            except Exception:
                LOGGER.debug("Falha ao editar mensagem para seletor de idioma", exc_info=True)
        return await message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    async def send_channel_gate(
        self,
        message: Message,
        locale: str,
        *,
        replace: bool = False,
    ) -> Message:
        text = build_channel_gate_text(locale)
        reply_markup = build_channel_gate_keyboard(locale, self._channel_chat_ids)
        if replace:
            try:
                await message.edit_text(
                    text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                )
                return message
            except Exception:
                LOGGER.debug("Falha ao editar mensagem para gate de canal", exc_info=True)
        return await message.reply_text(text, reply_markup=reply_markup, parse_mode="HTML")

    async def send_ready_message(
        self,
        message: Message,
        locale: str,
        *,
        replace: bool = False,
    ) -> Message:
        text = build_access_ready_text(locale)
        if replace:
            try:
                await message.edit_text(text, parse_mode="HTML")
                return message
            except Exception:
                LOGGER.debug("Falha ao editar mensagem de acesso liberado", exc_info=True)
        return await message.reply_text(text, parse_mode="HTML")

    async def _fetch_channel_membership(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
    ) -> bool:
        for channel_chat_id in self._channel_chat_ids:
            member = await context.bot.get_chat_member(channel_chat_id, user_id)
            status = str(getattr(member, "status", "") or "").lower()
            allowed = status in {"member", "administrator", "creator"}
            if status == "restricted":
                allowed = bool(getattr(member, "is_member", False))
            if not allowed:
                return False
        return True
