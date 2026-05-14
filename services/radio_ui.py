from __future__ import annotations

from html import escape
import math
from typing import TYPE_CHECKING

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.errors import LocalizedError
from services.i18n import LANGUAGE_OPTIONS, escape_text, t


if TYPE_CHECKING:
    from services.animethemes_client import AnimeCandidate, ThemeTrack


def build_loading_text(locale: str) -> str:
    return (
        f"<b>{t(locale, 'status.loading_title')}</b>\n"
        f"{t(locale, 'status.loading_wait')}"
    )


def build_usage_text(locale: str, command_name: str) -> str:
    key_prefix = {
        "anime": "usage.anime",
        "op": "usage.op",
        "ed": "usage.ed",
        "language": "usage.language",
    }.get(command_name, "usage.anime")
    return (
        f"{t(locale, key_prefix + '.title')}\n\n"
        f"<blockquote>"
        f"<b>{t(locale, 'common.field.how_to_use')}:</b> {t(locale, key_prefix + '.body')}\n"
        f"<b>{t(locale, 'common.field.example')}:</b> <code>{escape_text(t(locale, key_prefix + '.example'))}</code>\n"
        f"<b>{t(locale, 'common.field.result')}:</b> {t(locale, key_prefix + '.result')}"
        f"</blockquote>"
    )


def build_start_caption(locale: str) -> str:
    return (
        f"{t(locale, 'start.title')}\n"
        f"{t(locale, 'start.description')}\n\n"
        f"<blockquote>{t(locale, 'start.features')}</blockquote>\n\n"
        f"{t(locale, 'start.footer')}"
    )


def build_language_picker_text(locale: str, current_locale: str | None = None) -> str:
    current_line = ""
    if current_locale:
        current_label = next(
            (item.label for item in LANGUAGE_OPTIONS if item.code == current_locale),
            current_locale,
        )
        current_line = (
            f"\n\n<b>{t(locale, 'common.field.language')}:</b> {escape_text(current_label)}"
        )
    return (
        f"{t(locale, 'gate.language.title')}\n\n"
        f"{t(locale, 'gate.language.body')}\n"
        f"{t(locale, 'gate.language.footer')}"
        f"{current_line}"
    )


def build_channel_gate_text(locale: str) -> str:
    return (
        f"{t(locale, 'gate.channel.title')}\n\n"
        f"{t(locale, 'gate.channel.body')}\n"
        f"{t(locale, 'gate.channel.footer')}"
    )


def build_access_ready_text(locale: str) -> str:
    return (
        f"{t(locale, 'gate.ready.title')}\n\n"
        f"{t(locale, 'gate.ready.body')}\n"
        f"{t(locale, 'gate.resume.body')}"
    )


def build_search_results_text(
    locale: str,
    query: str,
    kind_token: str,
    bot_username: str,
) -> str:
    return (
        f"{t(locale, 'search.choose_anime_title')}\n\n"
        f"<blockquote>"
        f"<b>{t(locale, 'common.field.search')}:</b> {escape_text(query)}\n"
        f"<b>{t(locale, 'common.field.filter')}:</b> {escape_text(filter_value(locale, kind_token))}\n"
        f"<b>{t(locale, 'common.field.result')}:</b> {t(locale, 'search.result_hint')}"
        f"</blockquote>\n\n"
        f"{build_footer(bot_username, None, locale, use_brand_as_info=True)}"
    )


def build_theme_selector_caption(
    locale: str,
    candidate: AnimeCandidate,
    kind_token: str,
    track_count: int,
    bot_username: str,
) -> str:
    return (
        f"🗂️ <b>{escape_text(candidate.anime_name)}</b>\n\n"
        f"<blockquote>"
        f"{format_line(locale, 'common.field.theme', selector_prompt(locale, kind_token))}\n"
        f"{format_line(locale, 'common.field.tags', build_tags_line(candidate.year, candidate.season, candidate.media_format))}\n"
        f"{format_line(locale, 'common.field.source', t(locale, 'search.selector.source', count=track_count))}"
        f"</blockquote>\n\n"
        f"{build_footer(bot_username, candidate.info_link, locale)}"
    )


def build_audio_caption(
    locale: str,
    track: ThemeTrack,
    bot_username: str,
) -> str:
    source_parts = []
    if track.source:
        source_parts.append(str(track.source))
    if track.resolution:
        source_parts.append(f"{track.resolution}p")
    source_line = " | ".join(source_parts) or "—"

    return (
        f"🗂️ <b>{escape_text(track.anime_name)}</b>\n\n"
        f"<blockquote>"
        f"{format_line(locale, 'common.field.theme', f'{escape_text(track.display_theme)} ({escape_text(track.theme_type)})')}\n"
        f"{format_line(locale, 'common.field.tags', build_tags_line(track.year, track.season, track.media_format))}\n"
        f"{format_line(locale, 'common.field.source', source_line)}"
        f"</blockquote>\n\n"
        f"{build_footer(bot_username, track.info_link, locale)}"
    )


def build_text_fallback(locale: str, track: ThemeTrack, bot_username: str) -> str:
    audio_value = track.audio_link or "—"
    video_value = track.video_link or "—"
    return (
        f"🗂️ <b>{escape_text(track.anime_name)}</b>\n\n"
        f"<blockquote>"
        f"{format_line(locale, 'common.field.theme', escape_text(track.display_theme))}\n"
        f"{format_line(locale, 'common.field.source', audio_value)}\n"
        f"{format_line(locale, 'common.field.result', video_value)}"
        f"</blockquote>\n\n"
        f"{build_footer(bot_username, track.info_link, locale)}"
    )


def build_inline_prompt_text(locale: str) -> str:
    return (
        f"{t(locale, 'inline.query_prompt_title')}\n\n"
        f"{t(locale, 'inline.query_prompt_body')}"
    )


def build_inline_no_results_text(locale: str) -> str:
    return (
        f"{t(locale, 'inline.no_results_title')}\n\n"
        f"{t(locale, 'inline.no_results_body')}"
    )


def build_inline_gate_text(locale: str, reason: str) -> str:
    title_key = "inline.gate_language_title" if reason == "language" else "inline.gate_channel_title"
    body_key = "inline.gate_language_body" if reason == "language" else "inline.gate_channel_body"
    return (
        f"<b>{t(locale, title_key)}</b>\n\n"
        f"{t(locale, body_key)}"
    )


def build_inline_result_caption(
    locale: str,
    candidate: AnimeCandidate,
    bot_username: str,
    kind_token: str,
) -> str:
    return (
        f"🗂️ <b>{escape_text(candidate.anime_name)}</b>\n\n"
        f"<blockquote>"
        f"{format_line(locale, 'common.field.theme', filter_value(locale, kind_token))}\n"
        f"{format_line(locale, 'common.field.tags', build_tags_line(candidate.year, candidate.season, candidate.media_format))}\n"
        f"{format_line(locale, 'common.field.source', t(locale, 'search.inline_hint'))}"
        f"</blockquote>\n\n"
        f"{build_footer(bot_username, candidate.info_link, locale)}"
    )


def build_inline_result_description(
    locale: str,
    candidate: AnimeCandidate,
) -> str:
    tags = build_tags_line(candidate.year, candidate.season, candidate.media_format)
    count = candidate.matching_theme_count or candidate.total_theme_count
    return t(locale, "inline.result_description", tags=tags, count=count)


def build_error_text(locale: str, error: Exception) -> str:
    if isinstance(error, LocalizedError):
        return t(locale, error.key, **error.params)
    return t(locale, "errors.generic_action")


def build_language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(option.label, callback_data=f"lang:set:{option.code}")] for option in LANGUAGE_OPTIONS]
    )


_CHANNEL_LABELS = {
    "@RadioAnimes": "🎧 Rádio Animes",
    "@QG_BALTIGO": "🏠 QG Baltigo",
}


def _channel_url(channel: str) -> str:
    channel = str(channel or "").strip()
    if channel.startswith("@"):
        return f"https://t.me/{channel[1:]}"
    if channel.startswith("http://") or channel.startswith("https://"):
        return channel
    return f"https://t.me/{channel.lstrip('@')}"


def _channel_label(channel: str) -> str:
    channel = str(channel or "").strip()
    return _CHANNEL_LABELS.get(channel, f"📢 {channel.lstrip('@').replace('_', ' ').title() or 'Canal'}")


def build_channel_gate_keyboard(locale: str, channels: tuple[str, ...]) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(_channel_label(channel), url=_channel_url(channel))
        for channel in channels
    ]
    rows = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
    rows.append([InlineKeyboardButton(t(locale, "buttons.recheck_access"), callback_data="gate:recheck")])
    rows.append([InlineKeyboardButton(t(locale, "buttons.change_language"), callback_data="lang:open")])
    return InlineKeyboardMarkup(
        rows
    )


def build_audio_keyboard(locale: str, video_link: str | None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if video_link:
        rows.append([InlineKeyboardButton(t(locale, "buttons.open_video"), url=video_link)])
    rows.append(
        [
            InlineKeyboardButton(t(locale, "buttons.random_other"), callback_data="radio:random"),
            InlineKeyboardButton(t(locale, "buttons.next_track"), callback_data="radio:next"),
        ]
    )
    return InlineKeyboardMarkup(rows)


def build_loading_keyboard(locale: str, *, preparing: bool = False) -> InlineKeyboardMarkup:
    label_key = "buttons.preparing" if preparing else "buttons.loading"
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(locale, label_key), callback_data="radio:noop")]]
    )


def build_search_results_keyboard(
    results: list[AnimeCandidate],
    page: int,
    *,
    locale: str,
    page_size: int,
) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(results) / page_size))
    clamped_page = max(0, min(page, total_pages - 1))
    start = clamped_page * page_size
    end = start + page_size
    rows: list[list[InlineKeyboardButton]] = []

    for index, candidate in enumerate(results[start:end], start=start):
        rows.append(
            [
                InlineKeyboardButton(
                    truncate_button_text(candidate.anime_name, 58),
                    callback_data=f"radio:pickanime:{index}",
                )
            ]
        )

    rows.extend(build_pagination_rows(locale, clamped_page, total_pages, "radio:searchpage"))
    return InlineKeyboardMarkup(rows)


def build_theme_selector_keyboard(
    tracks: list[ThemeTrack],
    page: int,
    *,
    locale: str,
    page_size: int,
) -> InlineKeyboardMarkup:
    total_pages = max(1, math.ceil(len(tracks) / page_size))
    clamped_page = max(0, min(page, total_pages - 1))
    start = clamped_page * page_size
    end = start + page_size
    rows: list[list[InlineKeyboardButton]] = []

    for index, track in enumerate(tracks[start:end], start=start):
        rows.append(
            [
                InlineKeyboardButton(
                    truncate_button_text(build_track_button_label(locale, track), 60),
                    callback_data=f"radio:picktheme:{index}",
                )
            ]
        )

    rows.extend(build_pagination_rows(locale, clamped_page, total_pages, "radio:themepage"))
    return InlineKeyboardMarkup(rows)


def build_inline_open_keyboard(locale: str, deep_link_url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(t(locale, "buttons.listen_in_bot"), url=deep_link_url)]]
    )


def build_pagination_rows(
    locale: str,
    page: int,
    total_pages: int,
    prefix: str,
) -> list[list[InlineKeyboardButton]]:
    if total_pages <= 1:
        return []

    row: list[InlineKeyboardButton] = []
    if page > 0:
        row.append(
            InlineKeyboardButton(
                t(locale, "buttons.prev_page"),
                callback_data=f"{prefix}:{page - 1}",
            )
        )
    row.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data="radio:noop"))
    if page + 1 < total_pages:
        row.append(
            InlineKeyboardButton(
                t(locale, "buttons.next_page"),
                callback_data=f"{prefix}:{page + 1}",
            )
        )
    return [row]


def build_track_button_label(locale: str, track: ThemeTrack) -> str:
    text = f"{track.display_theme} {track.song_title or track.display_theme}".strip()
    artist_names = ", ".join(name for name in track.artist_names if name)
    if artist_names:
        return f"{text} {t(locale, 'common.by')} {artist_names}"
    return text


def truncate_button_text(text: str, limit: int) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "…"


def filter_value(locale: str, kind_token: str) -> str:
    if kind_token == "OP":
        return t(locale, "common.filter.op")
    if kind_token == "ED":
        return t(locale, "common.filter.ed")
    return t(locale, "common.filter.any")


def selector_prompt(locale: str, kind_token: str) -> str:
    if kind_token == "OP":
        return f"{t(locale, 'search.selector.op')} ({t(locale, 'common.filter.op')})"
    if kind_token == "ED":
        return f"{t(locale, 'search.selector.ed')} ({t(locale, 'common.filter.ed')})"
    return f"{t(locale, 'search.selector.any')} ({t(locale, 'common.filter.any')})"


def build_tags_line(
    year: int | None,
    season: str | None,
    media_format: str | None,
) -> str:
    parts = [
        str(year) if year else None,
        season,
        media_format,
    ]
    return " | ".join(part for part in parts if part) or "—"


def format_line(locale: str, label_key: str, value: str) -> str:
    return f"<b>{escape_text(t(locale, label_key))}:</b> {value}"


def build_footer(
    bot_username: str,
    info_link: str | None,
    locale: str,
    *,
    use_brand_as_info: bool = False,
) -> str:
    info_value = escape_text(t(locale, "brand.name")) if use_brand_as_info else t(locale, "common.info")
    if info_link and not use_brand_as_info:
        info_part = f'<a href="{escape(info_link, quote=True)}">{escape_text(info_value)}</a>'
    else:
        info_part = escape_text(info_value)

    username = bot_username.lstrip("@") if bot_username else t(locale, "brand.name")
    prefix = f"@{escape_text(username)}" if bot_username else escape_text(username)
    return f"{prefix} | {info_part}"

