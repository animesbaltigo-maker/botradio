from __future__ import annotations

import logging
import math
from typing import Any

from telegram import InputMediaAudio, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.animethemes_client import (
    AnimeCandidate,
    AnimeNotFoundError,
    AnimeThemesClient,
    AnimeThemesClientError,
    ThemeTrack,
)
from services.chat_cleanup import delete_message_safely
from services.gatekeeper import Gatekeeper
from services.intents import (
    ACTION_RANDOM,
    ACTION_SELECTOR_QUERY,
    ACTION_SELECTOR_SLUG,
    KIND_ANY,
    KIND_ED,
    KIND_OP,
    make_query_action,
    make_random_action,
    make_slug_action,
)
from services.radio_ui import (
    build_audio_caption,
    build_audio_keyboard,
    build_error_text,
    build_loading_keyboard,
    build_loading_text,
    build_search_results_keyboard,
    build_search_results_text,
    build_theme_selector_caption,
    build_theme_selector_keyboard,
    build_usage_text,
)
from services.media_pipeline import MediaPipeline
LOGGER = logging.getLogger(__name__)

MESSAGE_STATE_KEY = "radio_message_state"
SELECTOR_STATE_KEY = "radio_selector_state"
AUDIO_FILE_ID_CACHE_KEY = "telegram_audio_file_ids"
MESSAGE_BUSY_KEY = "radio_busy_messages"
ANIME_RESULTS_PAGE_SIZE = 5
THEME_CHOICES_PAGE_SIZE = 3


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient não inicializado.")
    return client


def _pipeline(context: ContextTypes.DEFAULT_TYPE) -> MediaPipeline:
    pipeline = context.application.bot_data.get("media_pipeline")
    if not isinstance(pipeline, MediaPipeline):
        raise RuntimeError("MediaPipeline não inicializado.")
    return pipeline


def _gate(context: ContextTypes.DEFAULT_TYPE) -> Gatekeeper:
    gate = context.application.bot_data.get("gatekeeper")
    if not isinstance(gate, Gatekeeper):
        raise RuntimeError("Gatekeeper não inicializado.")
    return gate


def _theme_type_from_token(token: str) -> str | None:
    if token == KIND_ANY:
        return None
    if token in {KIND_OP, KIND_ED}:
        return token
    raise AnimeThemesClientError("errors.invalid_filter")


def _message_key(message: Message) -> str:
    return f"{message.chat_id}:{message.message_id}"


def _message_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    state = context.application.bot_data.setdefault(MESSAGE_STATE_KEY, {})
    if not isinstance(state, dict):
        raise RuntimeError("Estado do rádio inválido.")
    return state


def _selector_state(context: ContextTypes.DEFAULT_TYPE) -> dict[str, dict[str, object]]:
    state = context.application.bot_data.setdefault(SELECTOR_STATE_KEY, {})
    if not isinstance(state, dict):
        raise RuntimeError("Estado do seletor inválido.")
    return state


def _busy_messages(context: ContextTypes.DEFAULT_TYPE) -> set[str]:
    busy = context.application.bot_data.setdefault(MESSAGE_BUSY_KEY, set())
    if not isinstance(busy, set):
        raise RuntimeError("Estado de concorrência inválido.")
    return busy


def _audio_file_id_cache(context: ContextTypes.DEFAULT_TYPE) -> dict[int, str]:
    cache = context.application.bot_data.setdefault(AUDIO_FILE_ID_CACHE_KEY, {})
    if not isinstance(cache, dict):
        raise RuntimeError("Cache de áudio inválido.")
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


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(getattr(context.bot, "username", "") or "")


async def _send_service_message(message: Message, locale: str) -> Message | None:
    try:
        return await message.reply_text(build_loading_text(locale), parse_mode=ParseMode.HTML)
    except Exception:
        LOGGER.exception("Falha ao enviar mensagem de serviço")
        return None


async def _set_loading_markup(
    message: Message,
    locale: str,
    *,
    preparing: bool = False,
) -> None:
    try:
        await message.edit_reply_markup(reply_markup=build_loading_keyboard(locale, preparing=preparing))
    except Exception:
        LOGGER.exception("Falha ao colocar botões de carregamento")


async def _restore_message_markup(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    locale: str,
) -> None:
    audio_state = _load_audio_track_state(context, message)
    if audio_state:
        video_link = audio_state.get("video_link")
        if not isinstance(video_link, str):
            video_link = None
        reply_markup = build_audio_keyboard(locale, video_link)
    else:
        selector_state = _load_selector_message_state(context, message)
        if not selector_state:
            return
        state_type = str(selector_state.get("state_type") or "")
        if state_type == "anime_search":
            results = selector_state.get("results")
            if not isinstance(results, list):
                return
            page = int(selector_state.get("page") or 0)
            reply_markup = build_search_results_keyboard(
                results,
                page,
                locale=locale,
                page_size=ANIME_RESULTS_PAGE_SIZE,
            )
        elif state_type == "theme_selector":
            tracks = selector_state.get("tracks")
            if not isinstance(tracks, list):
                return
            page = int(selector_state.get("page") or 0)
            reply_markup = build_theme_selector_keyboard(
                tracks,
                page,
                locale=locale,
                page_size=THEME_CHOICES_PAGE_SIZE,
            )
        else:
            return

    try:
        await message.edit_reply_markup(reply_markup=reply_markup)
    except Exception:
        LOGGER.exception("Falha ao restaurar botões da mensagem")


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
    locale: str,
) -> None:
    caption = build_audio_caption(locale, track, _bot_username(context))
    reply_markup = build_audio_keyboard(locale, track.video_link)
    performer = "Rádio Animes"
    title = track.display_title
    cached_file_id = _get_cached_audio_file_id(context, track)

    if cached_file_id:
        sent = await message.reply_audio(
            audio=cached_file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            title=title,
            performer=performer,
            reply_markup=reply_markup,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
            pool_timeout=30,
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
            title=title,
            performer=performer,
            reply_markup=reply_markup,
            read_timeout=120,
            write_timeout=120,
            connect_timeout=30,
            pool_timeout=30,
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
    locale: str,
) -> None:
    caption = build_audio_caption(locale, track, _bot_username(context))
    reply_markup = build_audio_keyboard(locale, track.video_link)
    performer = "Rádio Animes"
    title = track.display_title
    cached_file_id = _get_cached_audio_file_id(context, track)

    if cached_file_id:
        edited = await message.edit_media(
            media=InputMediaAudio(
                media=cached_file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
                title=title,
                performer=performer,
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
            title=title,
            performer=performer,
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
    locale: str,
) -> None:
    sent = await message.reply_text(
        build_search_results_text(locale, query_text, kind_token, _bot_username(context)),
        parse_mode=ParseMode.HTML,
        reply_markup=build_search_results_keyboard(
            results,
            0,
            locale=locale,
            page_size=ANIME_RESULTS_PAGE_SIZE,
        ),
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
    locale: str,
) -> Message:
    caption = build_theme_selector_caption(
        locale,
        candidate,
        kind_token,
        len(tracks),
        _bot_username(context),
    )
    reply_markup = build_theme_selector_keyboard(
        tracks,
        0,
        locale=locale,
        page_size=THEME_CHOICES_PAGE_SIZE,
    )

    if candidate.image_link:
        try:
            sent = await message.reply_photo(
                photo=candidate.image_link,
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
                pool_timeout=30,
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


async def _send_client_error(message: Message, exc: Exception, locale: str) -> None:
    await message.reply_text(build_error_text(locale, exc))


async def _answer_callback_error(update: Update, exc: Exception, locale: str) -> None:
    query = update.callback_query
    if not query:
        return
    try:
        await query.answer(build_error_text(locale, exc)[:180], show_alert=True)
    except Exception:
        LOGGER.exception("Falha ao responder callback com erro")


async def _handle_search_page_change(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    page: int,
    locale: str,
) -> None:
    state = _load_selector_message_state(context, message)
    if not state or state.get("state_type") != "anime_search":
        raise AnimeThemesClientError("callbacks.message_expired")

    results = state.get("results")
    if not isinstance(results, list):
        raise AnimeThemesClientError("errors.results_unavailable")

    total_pages = max(1, math.ceil(len(results) / ANIME_RESULTS_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    state["page"] = clamped_page
    await message.edit_reply_markup(
        reply_markup=build_search_results_keyboard(
            results,
            clamped_page,
            locale=locale,
            page_size=ANIME_RESULTS_PAGE_SIZE,
        )
    )


async def _handle_theme_page_change(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    page: int,
    locale: str,
) -> None:
    state = _load_selector_message_state(context, message)
    if not state or state.get("state_type") != "theme_selector":
        raise AnimeThemesClientError("callbacks.message_expired")

    tracks = state.get("tracks")
    if not isinstance(tracks, list):
        raise AnimeThemesClientError("errors.tracks_unavailable")

    total_pages = max(1, math.ceil(len(tracks) / THEME_CHOICES_PAGE_SIZE))
    clamped_page = max(0, min(page, total_pages - 1))
    state["page"] = clamped_page
    await message.edit_reply_markup(
        reply_markup=build_theme_selector_keyboard(
            tracks,
            clamped_page,
            locale=locale,
            page_size=THEME_CHOICES_PAGE_SIZE,
        )
    )


async def _open_selector_for_query(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    query: str,
    kind_token: str,
    locale: str,
) -> None:
    client = _client(context)
    theme_type = _theme_type_from_token(kind_token)
    results = await client.search_anime(query, theme_type)
    if len(results) == 1:
        tracks = await client.get_theme_choices_for_slug(results[0].anime_slug, theme_type)
        await _send_theme_selector_message(context, message, results[0], tracks, kind_token, locale)
        return
    await _send_anime_results_message(context, message, query, results, kind_token, locale)


async def _open_selector_for_slug(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    slug: str,
    kind_token: str,
    locale: str,
) -> None:
    client = _client(context)
    theme_type = _theme_type_from_token(kind_token)
    anime = await client.get_anime_by_slug(slug)
    candidate = AnimeCandidate(
        anime_id=int(anime.get("id", 0)),
        anime_name=str(anime.get("name") or "Anime"),
        anime_slug=str(anime.get("slug") or slug),
        media_format=anime.get("media_format"),
        season=anime.get("season"),
        year=anime.get("year"),
        image_link=client._pick_image_link(anime),
        info_link=client._pick_info_link(anime),
        matching_theme_count=0,
        total_theme_count=len(anime.get("animethemes") or []),
    )
    tracks = await client.get_theme_choices_for_slug(slug, theme_type)
    candidate.matching_theme_count = len(tracks)
    await _send_theme_selector_message(context, message, candidate, tracks, kind_token, locale)


async def _play_random_track(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    kind_token: str,
    locale: str,
) -> None:
    track = await _client(context).get_random_track(_theme_type_from_token(kind_token))
    await _send_audio_message(context, message, track, kind_token, locale)


async def execute_pending_action(
    context: ContextTypes.DEFAULT_TYPE,
    message: Message,
    action: dict[str, Any],
    locale: str,
) -> None:
    action_type = str(action.get("type") or "")
    kind_token = str(action.get("kind_token") or KIND_ANY)

    service_message = await _send_service_message(message, locale)
    try:
        if action_type == ACTION_SELECTOR_QUERY:
            query = str(action.get("query") or "").strip()
            if not query:
                raise AnimeThemesClientError("errors.anime_name_required")
            await _open_selector_for_query(context, message, query, kind_token, locale)
        elif action_type == ACTION_SELECTOR_SLUG:
            slug = str(action.get("slug") or "").strip()
            if not slug:
                raise AnimeThemesClientError("errors.invalid_slug")
            await _open_selector_for_slug(context, message, slug, kind_token, locale)
        elif action_type == ACTION_RANDOM:
            await _play_random_track(context, message, kind_token, locale)
        else:
            raise AnimeThemesClientError("errors.action_unavailable")
    finally:
        await delete_message_safely(service_message)


async def radio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    access = await _gate(context).require_message_access(
        update,
        context,
        pending_action=make_random_action(KIND_ANY),
    )
    if not access:
        return

    try:
        await execute_pending_action(context, message, make_random_action(KIND_ANY), access.locale)
    except Exception as exc:
        await _send_client_error(message, exc, access.locale)
        return
    await delete_message_safely(message)


async def anime_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    access = await _gate(context).require_message_access(update, context)
    if not access:
        return

    query = " ".join(context.args).strip()
    if not query:
        await message.reply_text(build_usage_text(access.locale, "anime"), parse_mode=ParseMode.HTML)
        await delete_message_safely(message)
        return

    action = make_query_action(query, KIND_ANY)

    try:
        await execute_pending_action(context, message, action, access.locale)
    except Exception as exc:
        await _send_client_error(message, exc, access.locale)
        return
    await delete_message_safely(message)


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

    query = " ".join(context.args).strip()
    if kind_token == KIND_OP and not query:
        action = make_random_action(KIND_OP)
    elif kind_token == KIND_ED and not query:
        action = make_random_action(KIND_ED)
    else:
        action = make_query_action(query, kind_token) if query else make_random_action(kind_token)

    access = await _gate(context).require_message_access(update, context, pending_action=action)
    if not access:
        return

    try:
        await execute_pending_action(context, message, action, access.locale)
    except Exception as exc:
        await _send_client_error(message, exc, access.locale)
        return
    await delete_message_safely(message)


async def radio_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.message or not query.data:
        return

    if query.data == "radio:noop":
        locale = get_resolved_language(context, getattr(update.effective_user, "language_code", None))
        try:
            await query.answer(build_error_text(locale, AnimeThemesClientError("status.dj_working"))[:180])
        except Exception:
            pass
        return

    access = await _gate(context).require_callback_access(update, context)
    if not access:
        return
    locale = access.locale

    parts = query.data.split(":")
    action = parts[1] if len(parts) > 1 else ""
    action_arg = parts[2] if len(parts) > 2 else None

    if action in {"searchpage", "themepage"}:
        try:
            page = int(action_arg or "0")
            if action == "searchpage":
                await _handle_search_page_change(context, query.message, page, locale)
            else:
                await _handle_theme_page_change(context, query.message, page, locale)
            await query.answer()
        except Exception as exc:
            await _answer_callback_error(update, exc, locale)
        return

    message_key = _message_key(query.message)
    busy_messages = _busy_messages(context)
    if message_key in busy_messages:
        try:
            await query.answer(build_error_text(locale, AnimeThemesClientError("status.dj_working"))[:180])
        except Exception:
            pass
        return

    busy_messages.add(message_key)
    client = _client(context)

    try:
        if action == "random":
            state = _load_audio_track_state(context, query.message)
            if not state:
                raise AnimeThemesClientError("callbacks.message_expired")

            await query.answer(build_error_text(locale, AnimeThemesClientError("status.switching_track"))[:180])
            await _set_loading_markup(query.message, locale)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            theme_type = _theme_type_from_token(kind_token)
            current_video_id = int(state.get("video_id") or 0) or None
            track = await client.get_random_track(theme_type, exclude_video_id=current_video_id)
            await _edit_audio_message(context, query.message, track, kind_token, locale)
            return

        if action == "next":
            state = _load_audio_track_state(context, query.message)
            if not state:
                raise AnimeThemesClientError("callbacks.message_expired")

            await query.answer(build_error_text(locale, AnimeThemesClientError("status.switching_track"))[:180])
            await _set_loading_markup(query.message, locale)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            theme_type = _theme_type_from_token(kind_token)
            current_video_id = int(state.get("video_id") or 0) or None
            anime_slug = str(state.get("anime_slug") or "")
            track = await client.get_track_for_slug(
                anime_slug,
                theme_type,
                exclude_video_id=current_video_id,
            )
            await _edit_audio_message(context, query.message, track, kind_token, locale)
            return

        if action == "pickanime":
            state = _load_selector_message_state(context, query.message)
            if not state or state.get("state_type") != "anime_search":
                raise AnimeThemesClientError("callbacks.message_expired")

            results = state.get("results")
            if not isinstance(results, list):
                raise AnimeThemesClientError("errors.results_unavailable")

            index = int(action_arg or "-1")
            if index < 0 or index >= len(results):
                raise AnimeThemesClientError("errors.option_gone")

            candidate = results[index]
            if not isinstance(candidate, AnimeCandidate):
                raise AnimeThemesClientError("errors.option_gone")

            await query.answer(build_error_text(locale, AnimeThemesClientError("status.opening_tracks"))[:180])
            await _set_loading_markup(query.message, locale)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            tracks = await client.get_theme_choices_for_slug(candidate.anime_slug, _theme_type_from_token(kind_token))
            await _send_theme_selector_message(context, query.message, candidate, tracks, kind_token, locale)
            _clear_selector_message_state(context, query.message)
            await delete_message_safely(query.message)
            return

        if action == "picktheme":
            state = _load_selector_message_state(context, query.message)
            if not state or state.get("state_type") != "theme_selector":
                raise AnimeThemesClientError("callbacks.message_expired")

            tracks = state.get("tracks")
            if not isinstance(tracks, list):
                raise AnimeThemesClientError("errors.tracks_unavailable")

            index = int(action_arg or "-1")
            if index < 0 or index >= len(tracks):
                raise AnimeThemesClientError("errors.track_gone")

            track = tracks[index]
            if not isinstance(track, ThemeTrack):
                raise AnimeThemesClientError("errors.track_gone")

            await query.answer(build_error_text(locale, AnimeThemesClientError("status.preparing_track"))[:180])
            await _set_loading_markup(query.message, locale, preparing=True)

            kind_token = str(state.get("kind_token") or KIND_ANY)
            await _send_audio_message(context, query.message, track, kind_token, locale)
            await _restore_message_markup(context, query.message, locale)
            return

        raise AnimeThemesClientError("errors.action_unavailable")

    except Exception as exc:
        await _restore_message_markup(context, query.message, locale)
        await _answer_callback_error(update, exc, locale)
    finally:
        busy_messages.discard(message_key)
