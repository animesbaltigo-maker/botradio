from __future__ import annotations

from html import escape
import logging

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaAudio,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.animethemes_client import (
    AnimeNotFoundError,
    AnimeThemesClient,
    AnimeThemesClientError,
    ThemeTrack,
)
from services.media_pipeline import MediaPipeline


LOGGER = logging.getLogger(__name__)

KIND_ANY = "ANY"
KIND_OP = "OP"
KIND_ED = "ED"
MESSAGE_STATE_KEY = "radio_message_state"
AUDIO_FILE_ID_CACHE_KEY = "telegram_audio_file_ids"
MESSAGE_BUSY_KEY = "radio_busy_messages"
SERVICE_TEXT = "🎶 Buscando com o DJ...\nAguarde um instante."


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient nao inicializado.")
    return client


def _pipeline(context: ContextTypes.DEFAULT_TYPE) -> MediaPipeline:
    pipeline = context.application.bot_data.get("media_pipeline")
    if not isinstance(pipeline, MediaPipeline):
        raise RuntimeError("MediaPipeline nao inicializado.")
    return pipeline


def _theme_type_from_token(token: str) -> str | None:
    if token == KIND_ANY:
        return None
    if token in {KIND_OP, KIND_ED}:
        return token
    raise AnimeThemesClientError("Filtro de tema invalido.")


def _message_key(message: Message) -> str:
    return f"{message.chat_id}:{message.message_id}"


def _message_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    state = context.application.bot_data.setdefault(MESSAGE_STATE_KEY, {})
    if not isinstance(state, dict):
        raise RuntimeError("Estado do radio invalido.")
    return state


def _busy_messages(context: ContextTypes.DEFAULT_TYPE) -> set[str]:
    busy = context.application.bot_data.setdefault(MESSAGE_BUSY_KEY, set())
    if not isinstance(busy, set):
        raise RuntimeError("Estado de busy invalido.")
    return busy


def _audio_file_id_cache(context: ContextTypes.DEFAULT_TYPE) -> dict[int, str]:
    cache = context.application.bot_data.setdefault(AUDIO_FILE_ID_CACHE_KEY, {})
    if not isinstance(cache, dict):
        raise RuntimeError("Cache de audio invalido.")
    return cache


def _store_track_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    track: ThemeTrack,
    kind_token: str,
) -> None:
    _message_state(context)[_message_key(message)] = {
        "anime_slug": track.anime_slug,
        "kind_token": kind_token,
        "video_id": track.video_id,
        "video_link": track.video_link,
    }


def _load_track_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> dict[str, object] | None:
    return _message_state(context).get(_message_key(message))


def _build_keyboard(track: ThemeTrack) -> InlineKeyboardMarkup:
    return _build_keyboard_from_video_link(track.video_link)


def _build_keyboard_from_video_link(video_link: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if video_link:
        rows.append([InlineKeyboardButton("▶️ Abrir video", url=video_link)])
    rows.append(
        [
            InlineKeyboardButton("🎵 Outra aleatoria", callback_data="radio:random"),
            InlineKeyboardButton("🎶 Proxima", callback_data="radio:next"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def _build_loading_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("⏳ Carregando...", callback_data="radio:noop")]]
    )


def _build_caption(track: ThemeTrack, bot_username: str) -> str:
    tag_parts = [
        str(track.year) if track.year else None,
        track.season,
        track.media_format,
    ]
    tags_line = " | ".join(part for part in tag_parts if part) or "desconhecido"

    source_parts = []
    if track.source:
        source_parts.append(track.source)
    if track.resolution:
        source_parts.append(f"{track.resolution}p")
    source_line = " | ".join(source_parts) or "desconhecida"

    info_html = (
        f'<a href="{escape(track.info_link, quote=True)}">Info</a>'
        if track.info_link
        else "Info"
    )
    username = bot_username.lstrip("@") if bot_username else "RadioAnimes_Bot"

    return (
        f"🗂️ <b>{escape(track.anime_name)}</b>\n\n"
        f"<blockquote>"
        f"Tema: {escape(track.display_theme)} ({escape(track.theme_type)})\n"
        f"Tags: {escape(tags_line)}\n"
        f"Fonte: {escape(source_line)}"
        f"</blockquote>\n\n"
        f"@{escape(username)} | {info_html}"
    )


def _build_text_fallback(track: ThemeTrack) -> str:
    lines = [f"📻 {track.anime_name}"]
    if track.audio_link:
        lines.append(f"Audio: {track.audio_link}")
    if track.video_link:
        lines.append(f"Video: {track.video_link}")
    return "\n\n".join(lines)


def _query_from_args(args: list[str]) -> str:
    return " ".join(args).strip()


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(getattr(context.bot, "username", "") or "RadioAnimes_Bot")


async def _send_service_message(message: Message) -> Message | None:
    try:
        return await message.reply_text(SERVICE_TEXT)
    except Exception:
        LOGGER.exception("Falha ao enviar mensagem de servico")
        return None


async def _delete_message_safely(message: Message | None) -> None:
    if not message:
        return
    try:
        await message.delete()
    except Exception:
        LOGGER.exception("Falha ao apagar mensagem temporaria")


async def _set_loading_markup(message: Message) -> None:
    try:
        await message.edit_reply_markup(reply_markup=_build_loading_keyboard())
    except Exception:
        LOGGER.exception("Falha ao colocar markup de carregando")


async def _restore_message_markup(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> None:
    state = _load_track_state(context, message)
    video_link = None
    if state:
        video_link = state.get("video_link")
        if not isinstance(video_link, str):
            video_link = None
    try:
        await message.edit_reply_markup(reply_markup=_build_keyboard_from_video_link(video_link))
    except Exception:
        LOGGER.exception("Falha ao restaurar botoes da mensagem")


def _track_cache_key(track: ThemeTrack) -> int:
    return int(track.audio_id or track.video_id)


def _get_cached_audio_file_id(
    context: ContextTypes.DEFAULT_TYPE,
    track: ThemeTrack,
) -> str | None:
    return _audio_file_id_cache(context).get(_track_cache_key(track))


def _remember_audio_file_id(
    context: ContextTypes.DEFAULT_TYPE,
    track: ThemeTrack,
    message: Message | None,
) -> None:
    if not message or not getattr(message, "audio", None):
        return
    file_id = getattr(message.audio, "file_id", None)
    if file_id:
        _audio_file_id_cache(context)[_track_cache_key(track)] = str(file_id)


async def _send_audio_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    track: ThemeTrack,
    kind_token: str,
) -> None:
    caption = _build_caption(track, _bot_username(context))
    reply_markup = _build_keyboard(track)
    cached_file_id = _get_cached_audio_file_id(context, track)

    if cached_file_id:
        sent = await message.reply_audio(
            audio=cached_file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            title=f"📻 {track.anime_name}",
            performer="AnimeThemes",
            reply_markup=reply_markup,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=60,
            pool_timeout=60,
        )
        _remember_audio_file_id(context, track, sent)
        _store_track_state(context, sent, track, kind_token)
        return

    prepared = await _pipeline(context).prepare_track(track)

    audio_file = prepared.mp3_path.open("rb")
    thumb_file = prepared.thumbnail_path.open("rb") if prepared.thumbnail_path else None
    try:
        sent = await message.reply_audio(
            audio=audio_file,
            thumbnail=thumb_file,
            caption=caption,
            parse_mode=ParseMode.HTML,
            title=f"📻 {track.anime_name}",
            performer="AnimeThemes",
            reply_markup=reply_markup,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=60,
            pool_timeout=60,
        )
    finally:
        audio_file.close()
        if thumb_file:
            thumb_file.close()

    _remember_audio_file_id(context, track, sent)
    _store_track_state(context, sent, track, kind_token)


async def _edit_audio_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    track: ThemeTrack,
    kind_token: str,
) -> None:
    caption = _build_caption(track, _bot_username(context))
    reply_markup = _build_keyboard(track)
    cached_file_id = _get_cached_audio_file_id(context, track)

    if cached_file_id:
        edited = await message.edit_media(
            media=InputMediaAudio(
                media=cached_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=f"📻 {track.anime_name}",
                performer="AnimeThemes",
            ),
            reply_markup=reply_markup,
        )
        if isinstance(edited, Message):
            _remember_audio_file_id(context, track, edited)
        _store_track_state(context, message, track, kind_token)
        return

    prepared = await _pipeline(context).prepare_track(track)

    audio_file = prepared.mp3_path.open("rb")
    thumb_file = prepared.thumbnail_path.open("rb") if prepared.thumbnail_path else None
    try:
        media = InputMediaAudio(
            media=audio_file,
            filename=prepared.mp3_path.name,
            thumbnail=thumb_file if thumb_file and prepared.thumbnail_path else None,
            caption=caption,
            parse_mode=ParseMode.HTML,
            title=f"📻 {track.anime_name}",
            performer="AnimeThemes",
        )
        edited = await message.edit_media(media=media, reply_markup=reply_markup)
    finally:
        audio_file.close()
        if thumb_file:
            thumb_file.close()

    if isinstance(edited, Message):
        _remember_audio_file_id(context, track, edited)
    _store_track_state(context, message, track, kind_token)


async def _send_client_error(message: Message, exc: Exception) -> None:
    if isinstance(exc, AnimeNotFoundError):
        text = str(exc)
    elif isinstance(exc, AnimeThemesClientError):
        text = str(exc)
    else:
        LOGGER.exception("Falha inesperada ao tocar faixa", exc_info=exc)
        text = "Nao consegui tocar uma faixa agora."
    await message.reply_text(text)


async def _answer_callback_error(update: Update, exc: Exception) -> None:
    query = update.callback_query
    if not query:
        return
    if isinstance(exc, (AnimeNotFoundError, AnimeThemesClientError)):
        text = str(exc)
    else:
        LOGGER.exception("Falha inesperada no callback", exc_info=exc)
        text = "Nao consegui trocar a faixa agora."
    try:
        await query.answer(text[:180], show_alert=True)
    except Exception:
        LOGGER.exception("Falha ao responder callback")


async def radio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return
    client = _client(context)
    service_message = await _send_service_message(message)
    try:
        track = await client.get_random_track()
        await _send_audio_message(context, message, track, KIND_ANY)
    except Exception as exc:
        await _delete_message_safely(service_message)
        await _send_client_error(message, exc)
        return
    await _delete_message_safely(service_message)


async def anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    query = _query_from_args(context.args)
    if not query:
        await message.reply_text("Use assim: /anime Naruto")
        return

    client = _client(context)
    service_message = await _send_service_message(message)
    try:
        track = await client.get_track_for_query(query)
        await _send_audio_message(context, message, track, KIND_ANY)
    except Exception as exc:
        await _delete_message_safely(service_message)
        await _send_client_error(message, exc)
        return
    await _delete_message_safely(service_message)


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
    service_message = await _send_service_message(message)

    try:
        if query:
            track = await client.get_track_for_query(query, _theme_type_from_token(kind_token))
        else:
            track = await client.get_random_track(_theme_type_from_token(kind_token))
        await _send_audio_message(context, message, track, kind_token)
    except Exception as exc:
        await _delete_message_safely(service_message)
        await _send_client_error(message, exc)
        return
    await _delete_message_safely(service_message)


async def radio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    action = query.data.split(":", 1)[1]
    if action == "noop":
        try:
            await query.answer("O DJ ainda esta buscando...")
        except Exception:
            pass
        return

    state = _load_track_state(context, query.message)
    if not state:
        await _answer_callback_error(update, AnimeThemesClientError("Essa mensagem ja expirou."))
        return

    message_key = _message_key(query.message)
    busy_messages = _busy_messages(context)
    if message_key in busy_messages:
        try:
            await query.answer("O DJ ja esta trocando essa faixa...")
        except Exception:
            pass
        return

    busy_messages.add(message_key)
    try:
        await query.answer("Trocando a faixa...")
    except Exception:
        pass
    await _set_loading_markup(query.message)

    client = _client(context)

    try:
        kind_token = str(state.get("kind_token") or KIND_ANY)
        theme_type = _theme_type_from_token(kind_token)
        current_video_id = int(state.get("video_id") or 0) or None

        if action == "random":
            track = await client.get_random_track(theme_type, exclude_video_id=current_video_id)
        elif action == "next":
            anime_slug = str(state.get("anime_slug") or "")
            track = await client.get_track_for_slug(
                anime_slug,
                theme_type,
                exclude_video_id=current_video_id,
            )
        else:
            return

        await _edit_audio_message(context, query.message, track, kind_token)
    except Exception as exc:
        await _restore_message_markup(context, query.message)
        await _answer_callback_error(update, exc)
    finally:
        busy_messages.discard(message_key)
