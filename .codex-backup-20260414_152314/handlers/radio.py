from __future__ import annotations

from html import escape
import logging
import math

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
    AnimeCandidate,
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
SELECTOR_STATE_KEY = "radio_selector_state"
AUDIO_FILE_ID_CACHE_KEY = "telegram_audio_file_ids"
MESSAGE_BUSY_KEY = "radio_busy_messages"
SERVICE_TEXT = "🎶 <b>Buscando com o DJ...</b>\nAguarde um instante."
ANIME_RESULTS_PAGE_SIZE = 5
THEME_CHOICES_PAGE_SIZE = 3


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


def _kind_label(token: str, *, plural: bool = False) -> str:
    if token == KIND_OP:
        return "aberturas" if plural else "abertura"
    if token == KIND_ED:
        return "encerramentos" if plural else "encerramento"
    return "faixas" if plural else "faixa"


def _kind_short_label(token: str) -> str:
    if token == KIND_OP:
        return "OP"
    if token == KIND_ED:
        return "ED"
    return "tema"


def _message_key(message: Message) -> str:
    return f"{message.chat_id}:{message.message_id}"


def _message_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    state = context.application.bot_data.setdefault(MESSAGE_STATE_KEY, {})
    if not isinstance(state, dict):
        raise RuntimeError("Estado do radio invalido.")
    return state


def _selector_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    state = context.application.bot_data.setdefault(SELECTOR_STATE_KEY, {})
    if not isinstance(state, dict):
        raise RuntimeError("Estado do seletor invalido.")
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


def _store_audio_track_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    track: ThemeTrack,
    kind_token: str,
) -> None:
    _message_state(context)[_message_key(message)] = {
        "state_type": "audio",
        "anime_slug": track.anime_slug,
        "kind_token": kind_token,
        "video_id": track.video_id,
        "video_link": track.video_link,
    }


def _load_audio_track_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> dict[str, object] | None:
    state = _message_state(context).get(_message_key(message))
    if not isinstance(state, dict):
        return None
    if state.get("state_type") != "audio":
        return None
    return state


def _store_selector_message_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    state: dict[str, object],
) -> None:
    _selector_state(context)[_message_key(message)] = state


def _load_selector_message_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> dict[str, object] | None:
    state = _selector_state(context).get(_message_key(message))
    if isinstance(state, dict):
        return state
    return None


def _clear_selector_message_state(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> None:
    _selector_state(context).pop(_message_key(message), None)


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


def _build_loading_keyboard(label: str = "⏳ Carregando...") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(label, callback_data="radio:noop")]])


def _truncate_button_text(text: str, limit: int = 60) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def _build_search_results_keyboard(
    results: list[AnimeCandidate],
    kind_token: str,
    page: int,
) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / ANIME_RESULTS_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    start = clamped_page * ANIME_RESULTS_PAGE_SIZE
    end = start + ANIME_RESULTS_PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []

    for index, candidate in enumerate(results[start:end], start=start):
        meta: list[str] = []
        if candidate.media_format:
            meta.append(candidate.media_format)
        if candidate.year:
            meta.append(str(candidate.year))
        count = candidate.matching_theme_count or candidate.total_theme_count
        if count:
            meta.append(f"{count} {_kind_short_label(kind_token)}")
        label = candidate.anime_name
        if meta:
            label = f"{label} | {' | '.join(meta)}"
        rows.append(
            [
                InlineKeyboardButton(
                    _truncate_button_text(label),
                    callback_data=f"radio:pickanime:{index}",
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if clamped_page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Anterior",
                    callback_data=f"radio:searchpage:{clamped_page - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                f"{clamped_page + 1}/{total_pages}",
                callback_data="radio:noop",
            )
        )
        if clamped_page + 1 < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    "➡️ Proxima",
                    callback_data=f"radio:searchpage:{clamped_page + 1}",
                )
            )
        rows.append(nav_row)

    return InlineKeyboardMarkup(rows)


def _build_theme_selector_keyboard(
    tracks: list[ThemeTrack],
    page: int,
) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(tracks) / THEME_CHOICES_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    start = clamped_page * THEME_CHOICES_PAGE_SIZE
    end = start + THEME_CHOICES_PAGE_SIZE
    rows: list[list[InlineKeyboardButton]] = []

    for index, track in enumerate(tracks[start:end], start=start):
        rows.append(
            [
                InlineKeyboardButton(
                    _truncate_button_text(track.selection_label),
                    callback_data=f"radio:picktheme:{index}",
                )
            ]
        )

    if total_pages > 1:
        nav_row: list[InlineKeyboardButton] = []
        if clamped_page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    "⬅️ Anterior",
                    callback_data=f"radio:themepage:{clamped_page - 1}",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                f"{clamped_page + 1}/{total_pages}",
                callback_data="radio:noop",
            )
        )
        if clamped_page + 1 < total_pages:
            nav_row.append(
                InlineKeyboardButton(
                    "➡️ Proxima",
                    callback_data=f"radio:themepage:{clamped_page + 1}",
                )
            )
        rows.append(nav_row)

    return InlineKeyboardMarkup(rows)


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
        f"🎶 <b>{escape(track.anime_name)}</b>\n\n"
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


def _build_search_results_text(query: str, kind_token: str) -> str:
    return (
        "🔎 <b>Escolha o anime</b>\n"
        f"Busca: <b>{escape(query)}</b>\n"
        f"Filtro: {escape(_kind_label(kind_token))}"
    )


def _build_theme_selector_caption(candidate: AnimeCandidate, kind_token: str) -> str:
    tag_parts = [
        candidate.media_format,
        candidate.season,
        str(candidate.year) if candidate.year else None,
    ]
    tags_line = " | ".join(part for part in tag_parts if part) or "@RadioAnimes"
    info_html = (
        f'<a href="{escape(candidate.info_link, quote=True)}">Info</a>'
        if candidate.info_link
        else "Info"
    )
    return (
        f"🎶 <b>{escape(candidate.anime_name)}</b>\n"
        f"🎚️ Escolha uma {_kind_label(kind_token)}.\n"
        f"🏷️ {escape(tags_line)}\n\n"
        f"{info_html}"
    )


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


async def _set_loading_markup(message: Message, label: str = "⏳ Carregando...") -> None:
    try:
        await message.edit_reply_markup(reply_markup=_build_loading_keyboard(label))
    except Exception:
        LOGGER.exception("Falha ao colocar markup de carregando")


async def _restore_message_markup(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
) -> None:
    audio_state = _load_audio_track_state(context, message)
    if audio_state:
        video_link = audio_state.get("video_link")
        if not isinstance(video_link, str):
            video_link = None
        reply_markup = _build_keyboard_from_video_link(video_link)
    else:
        selector_state = _load_selector_message_state(context, message)
        if not selector_state:
            return
        state_type = str(selector_state.get("state_type") or "")
        if state_type == "anime_search":
            results = selector_state.get("results")
            if not isinstance(results, list):
                return
            kind_token = str(selector_state.get("kind_token") or KIND_ANY)
            page = int(selector_state.get("page") or 0)
            reply_markup = _build_search_results_keyboard(results, kind_token, page)
        elif state_type == "theme_selector":
            tracks = selector_state.get("tracks")
            if not isinstance(tracks, list):
                return
            page = int(selector_state.get("page") or 0)
            reply_markup = _build_theme_selector_keyboard(tracks, page)
        else:
            return

    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
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
            title=f"📻 {track.anime_name} @RadioAnimes",
            performer="@RadioAnimes",
            reply_markup=reply_markup,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=60,
            pool_timeout=60,
        )
        _remember_audio_file_id(context, track, sent)
        _store_audio_track_state(context, sent, track, kind_token)
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
            title=f"📻 {track.anime_name} @RadioAnimes",
            performer="@RadioAnimes",
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
    _store_audio_track_state(context, sent, track, kind_token)


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
                title=f"📻 {track.anime_name} @RadioAnimes",
                performer="@RadioAnimes",
            ),
            reply_markup=reply_markup,
        )
        if isinstance(edited, Message):
            _remember_audio_file_id(context, track, edited)
        _store_audio_track_state(context, message, track, kind_token)
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
            title=f"📻 {track.anime_name} @RadioAnimes",
            performer="@RadioAnimes",
        )
        edited = await message.edit_media(media=media, reply_markup=reply_markup)
    finally:
        audio_file.close()
        if thumb_file:
            thumb_file.close()

    if isinstance(edited, Message):
        _remember_audio_file_id(context, track, edited)
    _store_audio_track_state(context, message, track, kind_token)


async def _send_anime_results_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    query_text: str,
    results: list[AnimeCandidate],
    kind_token: str,
) -> None:
    sent = await message.reply_text(
        _build_search_results_text(query_text, kind_token),
        parse_mode=ParseMode.HTML,
        reply_markup=_build_search_results_keyboard(results, kind_token, 0),
    )
    _store_selector_message_state(
        context,
        sent,
        {
            "state_type": "anime_search",
            "query": query_text,
            "kind_token": kind_token,
            "results": results,
            "page": 0,
        },
    )


async def _send_theme_selector_message(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    candidate: AnimeCandidate,
    tracks: list[ThemeTrack],
    kind_token: str,
) -> Message:
    caption = _build_theme_selector_caption(candidate, kind_token)
    reply_markup = _build_theme_selector_keyboard(tracks, 0)
    sent: Message

    if candidate.image_link:
        try:
            sent = await message.reply_photo(
                photo=candidate.image_link,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=60,
                pool_timeout=60,
            )
        except Exception:
            LOGGER.exception("Falha ao enviar banner do anime, usando texto")
            sent = await message.reply_text(
                caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
            )
    else:
        sent = await message.reply_text(
            caption,
            parse_mode=ParseMode.HTML,
            reply_markup=reply_markup,
        )

    _store_selector_message_state(
        context,
        sent,
        {
            "state_type": "theme_selector",
            "kind_token": kind_token,
            "anime": candidate,
            "tracks": tracks,
            "page": 0,
        },
    )
    return sent


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
        text = "Nao consegui concluir essa acao agora."
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
        theme_type = _theme_type_from_token(kind_token)
        if query:
            results = await client.search_anime(query, theme_type)
            if len(results) == 1:
                tracks = await client.get_theme_choices_for_slug(results[0].anime_slug, theme_type)
                await _send_theme_selector_message(context, message, results[0], tracks, kind_token)
            else:
                await _send_anime_results_message(context, message, query, results, kind_token)
        else:
            track = await client.get_random_track(theme_type)
            await _send_audio_message(context, message, track, kind_token)
    except Exception as exc:
        await _delete_message_safely(service_message)
        await _send_client_error(message, exc)
        return
    await _delete_message_safely(service_message)


async def _handle_search_page_change(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    page: int,
) -> None:
    state = _load_selector_message_state(context, message)
    if not state or state.get("state_type") != "anime_search":
        raise AnimeThemesClientError("Essa selecao de anime ja expirou.")

    results = state.get("results")
    if not isinstance(results, list):
        raise AnimeThemesClientError("Nao consegui ler os resultados dessa busca.")

    kind_token = str(state.get("kind_token") or KIND_ANY)
    total_pages = max(1, math.ceil(len(results) / ANIME_RESULTS_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    state["page"] = clamped_page
    await message.edit_reply_markup(
        reply_markup=_build_search_results_keyboard(results, kind_token, clamped_page)
    )


async def _handle_theme_page_change(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    page: int,
) -> None:
    state = _load_selector_message_state(context, message)
    if not state or state.get("state_type") != "theme_selector":
        raise AnimeThemesClientError("Essa lista de faixas ja expirou.")

    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        raise AnimeThemesClientError("Nao consegui ler as faixas desse anime.")

    total_pages = max(1, math.ceil(len(tracks) / THEME_CHOICES_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    state["page"] = clamped_page
    await message.edit_reply_markup(
        reply_markup=_build_theme_selector_keyboard(tracks, clamped_page)
    )


async def radio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    action_arg = parts[2] if len(parts) > 2 else None

    if action == "noop":
        try:
            await query.answer("O DJ ainda esta trabalhando...")
        except Exception:
            pass
        return

    if action in {"searchpage", "themepage"}:
        try:
            page = int(action_arg or "0")
            if action == "searchpage":
                await _handle_search_page_change(context, query.message, page)
            else:
                await _handle_theme_page_change(context, query.message, page)
            await query.answer()
        except Exception as exc:
            await _answer_callback_error(update, exc)
        return

    message_key = _message_key(query.message)
    busy_messages = _busy_messages(context)
    if message_key in busy_messages:
        try:
            await query.answer("O DJ ja esta trabalhando nessa mensagem...")
        except Exception:
            pass
        return

    busy_messages.add(message_key)
    client = _client(context)

    try:
        if action == "random":
            state = _load_audio_track_state(context, query.message)
            if not state:
                raise AnimeThemesClientError("Essa mensagem ja expirou.")

            await query.answer("Trocando a faixa...")
            await _set_loading_markup(query.message)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            theme_type = _theme_type_from_token(kind_token)
            current_video_id = int(state.get("video_id") or 0) or None
            track = await client.get_random_track(theme_type, exclude_video_id=current_video_id)
            await _edit_audio_message(context, query.message, track, kind_token)
            return

        if action == "next":
            state = _load_audio_track_state(context, query.message)
            if not state:
                raise AnimeThemesClientError("Essa mensagem ja expirou.")

            await query.answer("Trocando a faixa...")
            await _set_loading_markup(query.message)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            theme_type = _theme_type_from_token(kind_token)
            current_video_id = int(state.get("video_id") or 0) or None
            anime_slug = str(state.get("anime_slug") or "")
            track = await client.get_track_for_slug(
                anime_slug,
                theme_type,
                exclude_video_id=current_video_id,
            )
            await _edit_audio_message(context, query.message, track, kind_token)
            return

        if action == "pickanime":
            state = _load_selector_message_state(context, query.message)
            if not state or state.get("state_type") != "anime_search":
                raise AnimeThemesClientError("Essa selecao de anime ja expirou.")

            results = state.get("results")
            if not isinstance(results, list):
                raise AnimeThemesClientError("Nao consegui ler os resultados dessa busca.")

            index = int(action_arg or "-1")
            if index < 0 or index >= len(results):
                raise AnimeThemesClientError("Essa opcao de anime nao existe mais.")

            candidate = results[index]
            if not isinstance(candidate, AnimeCandidate):
                raise AnimeThemesClientError("Nao consegui abrir esse anime agora.")

            await query.answer("Abrindo as faixas...")
            await _set_loading_markup(query.message)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            theme_type = _theme_type_from_token(kind_token)
            tracks = await client.get_theme_choices_for_slug(candidate.anime_slug, theme_type)
            await _send_theme_selector_message(context, query.message, candidate, tracks, kind_token)
            _clear_selector_message_state(context, query.message)
            await _delete_message_safely(query.message)
            return

        if action == "picktheme":
            state = _load_selector_message_state(context, query.message)
            if not state or state.get("state_type") != "theme_selector":
                raise AnimeThemesClientError("Essa lista de faixas ja expirou.")

            tracks = state.get("tracks")
            if not isinstance(tracks, list):
                raise AnimeThemesClientError("Nao consegui ler as faixas desse anime.")

            index = int(action_arg or "-1")
            if index < 0 or index >= len(tracks):
                raise AnimeThemesClientError("Essa faixa nao existe mais.")

            track = tracks[index]
            if not isinstance(track, ThemeTrack):
                raise AnimeThemesClientError("Nao consegui preparar essa faixa agora.")

            await query.answer("Preparando a faixa...")
            await _set_loading_markup(query.message, "⏳ Preparando...")

            kind_token = str(state.get("kind_token") or KIND_ANY)
            await _send_audio_message(context, query.message, track, kind_token)
            await _restore_message_markup(context, query.message)
            return

        raise AnimeThemesClientError("Essa acao do bot nao esta mais disponivel.")

    except Exception as exc:
        await _restore_message_markup(context, query.message)
        await _answer_callback_error(update, exc)
    finally:
        busy_messages.discard(message_key)
