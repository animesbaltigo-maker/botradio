from __future__ import annotations

from html import escape
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.animethemes_client import (
    AnimeNotFoundError,
    AnimeThemesClient,
    AnimeThemesClientError,
    ThemeTrack,
)


LOGGER = logging.getLogger(__name__)

KIND_ANY = "ANY"
KIND_OP = "OP"
KIND_ED = "ED"


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient nao inicializado.")
    return client


def _theme_type_from_token(token: str) -> str | None:
    if token == KIND_ANY:
        return None
    if token in {KIND_OP, KIND_ED}:
        return token
    raise AnimeThemesClientError("Filtro de tema invalido.")


def _kind_token(theme_type: str | None) -> str:
    return theme_type or KIND_ANY


def _same_anime_label(kind_token: str) -> str:
    if kind_token == KIND_OP:
        return "Mais OP desse anime"
    if kind_token == KIND_ED:
        return "Mais ED desse anime"
    return "Mais desse anime"


def _build_keyboard(track: ThemeTrack, kind_token: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []

    media_buttons: list[InlineKeyboardButton] = []
    if track.audio_link:
        media_buttons.append(InlineKeyboardButton("Abrir audio", url=track.audio_link))
    if track.video_link:
        media_buttons.append(InlineKeyboardButton("Abrir video", url=track.video_link))
    if media_buttons:
        rows.append(media_buttons)

    play_buttons = [
        InlineKeyboardButton(
            "Outra aleatoria",
            callback_data=f"radio:random:{kind_token}",
        )
    ]
    same_callback = f"radio:same:{track.anime_slug}:{kind_token}"
    if track.anime_slug and len(same_callback) <= 64:
        play_buttons.append(
            InlineKeyboardButton(
                _same_anime_label(kind_token),
                callback_data=same_callback,
            )
        )
    rows.append(play_buttons)

    filter_buttons: list[InlineKeyboardButton] = []
    if kind_token != KIND_OP:
        filter_buttons.append(
            InlineKeyboardButton("So OP", callback_data=f"radio:random:{KIND_OP}")
        )
    if kind_token != KIND_ED:
        filter_buttons.append(
            InlineKeyboardButton("So ED", callback_data=f"radio:random:{KIND_ED}")
        )
    if filter_buttons:
        rows.append(filter_buttons)

    return InlineKeyboardMarkup(rows)


def _build_caption(track: ThemeTrack) -> str:
    lines = [
        f"<b>{escape(track.anime_name)}</b>",
        f"Tema: <code>{escape(track.display_theme)}</code> ({escape(track.theme_type)})",
    ]

    meta = [
        str(track.year) if track.year else None,
        track.season,
        track.media_format,
    ]
    meta_line = " | ".join(part for part in meta if part)
    if meta_line:
        lines.append(escape(meta_line))

    if track.episodes:
        lines.append(f"Episodios: {escape(track.episodes)}")

    source_parts = []
    if track.source:
        source_parts.append(track.source)
    if track.resolution:
        source_parts.append(f"{track.resolution}p")
    if source_parts:
        lines.append("Fonte: " + escape(" | ".join(source_parts)))

    if track.entry_version and track.entry_version > 1:
        lines.append(f"Versao: v{track.entry_version}")
    if track.notes:
        lines.append("Notas: " + escape(track.notes))
    if track.spoiler:
        lines.append("Aviso: esse tema pode ter spoiler.")

    lines.append("via AnimeThemes.moe")
    return "\n".join(lines)


def _build_text_fallback(track: ThemeTrack) -> str:
    lines = [_build_caption(track)]
    if track.audio_link:
        lines.append(f"Audio: {track.audio_link}")
    if track.video_link:
        lines.append(f"Video: {track.video_link}")
    return "\n\n".join(lines)


def _query_from_args(args: list[str]) -> str:
    return " ".join(args).strip()


async def _deliver_track(
    message: Message,
    track: ThemeTrack,
    kind_token: str,
) -> None:
    caption = _build_caption(track)
    reply_markup = _build_keyboard(track, kind_token)

    if track.audio_link:
        try:
            await message.reply_audio(
                audio=track.audio_link,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=track.display_title,
                performer="AnimeThemes Radio",
                reply_markup=reply_markup,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
                pool_timeout=60,
            )
            return
        except Exception:
            LOGGER.exception("Falha ao enviar audio remoto para %s", track.display_title)

    if track.video_link:
        try:
            await message.reply_video(
                video=track.video_link,
                caption=caption,
                parse_mode=ParseMode.HTML,
                supports_streaming=True,
                reply_markup=reply_markup,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
                pool_timeout=60,
            )
            return
        except Exception:
            LOGGER.exception("Falha ao enviar video remoto para %s", track.display_title)

    await message.reply_text(
        _build_text_fallback(track),
        reply_markup=reply_markup,
        disable_web_page_preview=True,
    )


async def _send_client_error(message: Message, exc: Exception) -> None:
    if isinstance(exc, AnimeNotFoundError):
        text = str(exc)
    elif isinstance(exc, AnimeThemesClientError):
        text = str(exc)
    else:
        text = "Nao consegui tocar uma faixa agora."
    await message.reply_text(text)


async def radio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    client = _client(context)
    try:
        track = await client.get_random_track()
        await _deliver_track(message, track, KIND_ANY)
    except Exception as exc:
        await _send_client_error(message, exc)


async def anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    query = _query_from_args(context.args)
    if not query:
        await message.reply_text("Use assim: /anime Naruto")
        return

    client = _client(context)
    try:
        track = await client.get_track_for_query(query)
        await _deliver_track(message, track, KIND_ANY)
    except Exception as exc:
        await _send_client_error(message, exc)


async def op_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _play_by_kind(update, context, KIND_OP)


async def ed_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _play_by_kind(update, context, KIND_ED)


async def _play_by_kind(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    kind_token: str,
) -> None:
    message = update.effective_message
    if not message:
        return

    client = _client(context)
    query = _query_from_args(context.args)

    try:
        if query:
            track = await client.get_track_for_query(query, _theme_type_from_token(kind_token))
        else:
            track = await client.get_random_track(_theme_type_from_token(kind_token))
        await _deliver_track(message, track, kind_token)
    except Exception as exc:
        await _send_client_error(message, exc)


async def radio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    try:
        await query.answer("Escolhendo a proxima faixa...")
    except Exception:
        pass

    client = _client(context)
    parts = query.data.split(":")
    if len(parts) < 3 or parts[0] != "radio":
        return

    action = parts[1]
    kind_token = parts[-1]

    try:
        theme_type = _theme_type_from_token(kind_token)
        if action == "random":
            track = await client.get_random_track(theme_type)
        elif action == "same" and len(parts) >= 4:
            slug = parts[2]
            track = await client.get_track_for_slug(slug, theme_type)
        else:
            return
        await _deliver_track(query.message, track, kind_token)
    except Exception as exc:
        await _send_client_error(query.message, exc)
