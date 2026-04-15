from __future__ import annotations

import logging

from telegram import Message
from telegram.ext import ContextTypes

from services.user_state import pop_cleanup_messages, remember_cleanup_message


LOGGER = logging.getLogger(__name__)


async def delete_message_safely(message: Message | None) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        LOGGER.debug(
            "Falha ao apagar mensagem %s:%s",
            getattr(message, "chat_id", "?"),
            getattr(message, "message_id", "?"),
            exc_info=True,
        )


def register_cleanup_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message | None,
) -> None:
    if not message:
        return
    remember_cleanup_message(
        context,
        chat_id=int(message.chat_id),
        message_id=int(message.message_id),
    )


async def delete_registered_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    for item in pop_cleanup_messages(context):
        try:
            await context.bot.delete_message(
                chat_id=item["chat_id"],
                message_id=item["message_id"],
            )
        except Exception:
            LOGGER.debug(
                "Falha ao apagar mensagem registrada %s:%s",
                item["chat_id"],
                item["message_id"],
                exc_info=True,
            )


async def cleanup_anchor_messages(
    context: ContextTypes.DEFAULT_TYPE,
    anchor_message: Message | None,
    *,
    include_reply_to: bool = False,
    include_registered: bool = False,
) -> None:
    targets: list[Message] = []
    if anchor_message:
        targets.append(anchor_message)
        if include_reply_to and anchor_message.reply_to_message:
            targets.append(anchor_message.reply_to_message)

    seen: set[tuple[int, int]] = set()
    for message in targets:
        key = (int(message.chat_id), int(message.message_id))
        if key in seen:
            continue
        seen.add(key)
        await delete_message_safely(message)

    if include_registered:
        await delete_registered_messages(context)
