from __future__ import annotations

from telegram import (
    InlineQueryResultArticle,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InputTextMessageContent,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from services.animethemes_client import AnimeNotFoundError, AnimeThemesClient, AnimeThemesClientError
from services.gatekeeper import Gatekeeper
from services.i18n import strip_html
from services.intents import KIND_ANY, encode_start_parameter, make_slug_action
from services.radio_ui import (
    build_error_text,
    build_inline_gate_text,
    build_inline_no_results_text,
    build_inline_open_keyboard,
    build_inline_prompt_text,
    build_inline_result_caption,
    build_inline_result_description,
)
from services.user_state import get_language


INLINE_PAGE_SIZE = 10


def _client(context: ContextTypes.DEFAULT_TYPE) -> AnimeThemesClient:
    client = context.application.bot_data.get("animethemes_client")
    if not isinstance(client, AnimeThemesClient):
        raise RuntimeError("AnimeThemesClient não inicializado.")
    return client


def _gate(context: ContextTypes.DEFAULT_TYPE) -> Gatekeeper:
    gate = context.application.bot_data.get("gatekeeper")
    if not isinstance(gate, Gatekeeper):
        raise RuntimeError("Gatekeeper não inicializado.")
    return gate


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    return str(getattr(context.bot, "username", "") or "")


def _bot_url(context: ContextTypes.DEFAULT_TYPE, start_parameter: str = "start") -> str:
    username = _bot_username(context).lstrip("@")
    return f"https://t.me/{username}?start={start_parameter}"


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query_obj = update.inline_query
    user = update.effective_user
    if not inline_query_obj or not user:
        return

    gate = _gate(context)
    locale = gate.resolve_locale(update, context)
    query_text = " ".join(inline_query_obj.query.split()).strip()
    offset = int(inline_query_obj.offset or "0")
    open_button = InlineQueryResultsButton(
        text="🎧 Rádio Animes",
        start_parameter="start",
    )

    if not get_language(context):
        result = InlineQueryResultArticle(
            id="gate-language",
            title="Rádio Animes",
            description=strip_html(build_inline_gate_text(locale, "language")),
            input_message_content=InputTextMessageContent(
                build_inline_gate_text(locale, "language"),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=15,
            is_personal=True,
            button=open_button,
        )
        return

    try:
        is_member = await gate.is_channel_member(context, user.id)
    except Exception as exc:
        result = InlineQueryResultArticle(
            id="gate-channel-error",
            title="Rádio Animes",
            description=strip_html(build_error_text(locale, exc)),
            input_message_content=InputTextMessageContent(
                build_error_text(locale, exc),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=10,
            is_personal=True,
            button=open_button,
        )
        return

    if not is_member:
        result = InlineQueryResultArticle(
            id="gate-channel",
            title="Rádio Animes",
            description=strip_html(build_inline_gate_text(locale, "channel")),
            input_message_content=InputTextMessageContent(
                build_inline_gate_text(locale, "channel"),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=15,
            is_personal=True,
            button=open_button,
        )
        return

    if not query_text:
        result = InlineQueryResultArticle(
            id="prompt",
            title="Rádio Animes",
            description=strip_html(build_inline_prompt_text(locale)),
            input_message_content=InputTextMessageContent(
                build_inline_prompt_text(locale),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=30,
            is_personal=True,
            button=open_button,
        )
        return

    try:
        results = await _client(context).search_anime(query_text, None, limit=30)
    except AnimeNotFoundError:
        result = InlineQueryResultArticle(
            id="no-results",
            title="Rádio Animes",
            description=strip_html(build_inline_no_results_text(locale)),
            input_message_content=InputTextMessageContent(
                build_inline_no_results_text(locale),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=20,
            is_personal=True,
            button=open_button,
        )
        return
    except AnimeThemesClientError as exc:
        result = InlineQueryResultArticle(
            id="inline-error",
            title="Rádio Animes",
            description=strip_html(build_error_text(locale, exc)),
            input_message_content=InputTextMessageContent(
                build_error_text(locale, exc),
                parse_mode=ParseMode.HTML,
            ),
            reply_markup=build_inline_open_keyboard(locale, _bot_url(context, "start")),
        )
        await inline_query_obj.answer(
            [result],
            cache_time=10,
            is_personal=True,
            button=open_button,
        )
        return

    page_results = results[offset : offset + INLINE_PAGE_SIZE]
    next_offset = str(offset + INLINE_PAGE_SIZE) if offset + INLINE_PAGE_SIZE < len(results) else ""
    inline_results = []

    for candidate in page_results:
        start_parameter = encode_start_parameter(make_slug_action(candidate.anime_slug, KIND_ANY)) or "start"
        deep_link = _bot_url(context, start_parameter)
        caption = build_inline_result_caption(locale, candidate, _bot_username(context), KIND_ANY)
        description = build_inline_result_description(locale, candidate)
        reply_markup = build_inline_open_keyboard(locale, deep_link)
        result_id = f"{candidate.anime_slug}:{offset}"

        if candidate.image_link:
            inline_results.append(
                InlineQueryResultPhoto(
                    id=result_id,
                    photo_url=candidate.image_link,
                    thumbnail_url=candidate.image_link,
                    title=candidate.anime_name,
                    description=description,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            )
        else:
            inline_results.append(
                InlineQueryResultArticle(
                    id=result_id,
                    title=candidate.anime_name,
                    description=description,
                    input_message_content=InputTextMessageContent(
                        caption,
                        parse_mode=ParseMode.HTML,
                    ),
                    reply_markup=reply_markup,
                )
            )

    await inline_query_obj.answer(
        inline_results,
        cache_time=60,
        is_personal=True,
        next_offset=next_offset,
        button=open_button,
    )
