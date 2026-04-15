from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from services.i18n import escape_text, t


def _yes_no(locale: str, value: bool) -> str:
    return t(locale, "common.value.yes") if value else t(locale, "common.value.no")


def _mode_label(locale: str, mode: str | None) -> str:
    if mode == "all":
        return t(locale, "broadcast.mode.all")
    if mode == "single":
        return t(locale, "broadcast.mode.single")
    return t(locale, "broadcast.mode.none")


def _format_line(locale: str, label: str, value: str) -> str:
    return f"<b>{escape_text(label)}:</b> {value}"


def build_broadcast_menu_text(
    locale: str,
    data: dict[str, object],
    *,
    total_users: int,
    running: bool,
    note: str | None = None,
) -> str:
    mode = str(data.get("mode") or "")
    target_user_id = data.get("target_user_id")
    photo = data.get("photo")
    text = str(data.get("text") or "")
    button_ready = bool(str(data.get("button_text") or "").strip() and str(data.get("button_url") or "").strip())
    pin = bool(data.get("pin"))

    lines = [
        t(locale, "broadcast.title"),
        "",
        t(locale, "broadcast.running" if running else "broadcast.idle"),
        "",
        t(locale, "broadcast.subtitle"),
        "",
    ]
    if note:
        lines.extend([f"<blockquote>{note}</blockquote>", ""])

    details = [
        _format_line(locale, t(locale, "common.field.mode"), f"<code>{escape_text(_mode_label(locale, mode))}</code>"),
    ]
    if mode == "single" and target_user_id:
        details.append(
            _format_line(
                locale,
                t(locale, "broadcast.target_user"),
                f"<code>{escape_text(target_user_id)}</code>",
            )
        )
    details.extend(
        [
            _format_line(locale, t(locale, "buttons.broadcast_media"), f"<code>{_yes_no(locale, bool(photo))}</code>"),
            _format_line(locale, t(locale, "buttons.broadcast_text"), f"<code>{_yes_no(locale, bool(text.strip()))}</code>"),
            _format_line(locale, t(locale, "buttons.broadcast_button"), f"<code>{_yes_no(locale, button_ready)}</code>"),
            _format_line(locale, t(locale, "common.field.pin"), f"<code>{_yes_no(locale, pin)}</code>"),
            _format_line(locale, t(locale, "broadcast.total_users"), f"<code>{total_users}</code>"),
        ]
    )
    lines.append("<blockquote>" + "\n".join(details) + "</blockquote>")
    lines.extend(["", t(locale, "broadcast.choose_option")])
    return "\n".join(lines)


def build_broadcast_menu_keyboard(
    locale: str,
    data: dict[str, object],
    *,
    running: bool,
) -> InlineKeyboardMarkup:
    mode = str(data.get("mode") or "")
    pin = bool(data.get("pin"))

    if mode == "all":
        mode_label = t(locale, "buttons.broadcast_to_all")
    elif mode == "single":
        mode_label = t(locale, "buttons.broadcast_to_user")
    else:
        mode_label = t(locale, "buttons.broadcast_mode")

    pin_label = f"📌 {_yes_no(locale, pin)}"
    send_label = t(locale, "buttons.broadcast_running") if running else t(locale, "buttons.broadcast_send")

    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(mode_label, callback_data="bc|set_mode"),
                InlineKeyboardButton(t(locale, "buttons.broadcast_media"), callback_data="bc|set_media"),
            ],
            [
                InlineKeyboardButton(t(locale, "buttons.broadcast_text"), callback_data="bc|set_text"),
                InlineKeyboardButton(t(locale, "buttons.broadcast_button"), callback_data="bc|set_button"),
            ],
            [
                InlineKeyboardButton(pin_label, callback_data="bc|toggle_pin"),
                InlineKeyboardButton(t(locale, "buttons.broadcast_preview"), callback_data="bc|preview"),
            ],
            [
                InlineKeyboardButton(send_label, callback_data="bc|send"),
                InlineKeyboardButton(t(locale, "buttons.broadcast_reset"), callback_data="bc|reset"),
            ],
            [
                InlineKeyboardButton(t(locale, "buttons.broadcast_close"), callback_data="bc|close"),
            ],
        ]
    )


def build_broadcast_message_keyboard(data: dict[str, object]) -> InlineKeyboardMarkup | None:
    button_text = str(data.get("button_text") or "").strip()
    button_url = str(data.get("button_url") or "").strip()
    if not button_text or not button_url:
        return None
    return InlineKeyboardMarkup([[InlineKeyboardButton(button_text, url=button_url)]])


def build_broadcast_preview_text(locale: str, data: dict[str, object]) -> str:
    mode = str(data.get("mode") or "")
    target_user_id = data.get("target_user_id")
    text = str(data.get("text") or "").strip()
    pin = bool(data.get("pin"))

    header_lines = [
        t(locale, "broadcast.preview_title"),
        "",
        _format_line(locale, t(locale, "common.field.mode"), f"<code>{escape_text(_mode_label(locale, mode))}</code>"),
    ]
    if mode == "single" and target_user_id:
        header_lines.append(
            _format_line(
                locale,
                t(locale, "broadcast.target_user"),
                f"<code>{escape_text(target_user_id)}</code>",
            )
        )
    header_lines.append(_format_line(locale, t(locale, "common.field.pin"), f"<code>{_yes_no(locale, pin)}</code>"))
    header = "\n".join(header_lines)
    return header + "\n\n" + (text or "<i>—</i>")


def build_broadcast_preview_keyboard(
    locale: str,
    data: dict[str, object],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    message_keyboard = build_broadcast_message_keyboard(data)
    if message_keyboard:
        rows.extend(message_keyboard.inline_keyboard)
    rows.append([InlineKeyboardButton(t(locale, "buttons.broadcast_confirm_send"), callback_data="bc|send")])
    rows.append([InlineKeyboardButton(t(locale, "buttons.broadcast_back"), callback_data="bc|menu")])
    return InlineKeyboardMarkup(rows)


def build_broadcast_mode_keyboard(locale: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(t(locale, "buttons.broadcast_to_all"), callback_data="bc|mode_all")],
            [InlineKeyboardButton(t(locale, "buttons.broadcast_to_user"), callback_data="bc|mode_single")],
            [InlineKeyboardButton(t(locale, "buttons.broadcast_back"), callback_data="bc|menu")],
        ]
    )


def build_broadcast_prompt_keyboard(
    locale: str,
    *,
    remove_callback: str | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if remove_callback:
        label_map = {
            "bc|remove_media": t(locale, "buttons.broadcast_remove_media"),
            "bc|remove_text": t(locale, "buttons.broadcast_remove_text"),
            "bc|remove_button": t(locale, "buttons.broadcast_remove_button"),
        }
        rows.append([InlineKeyboardButton(label_map.get(remove_callback, t(locale, "buttons.broadcast_reset")), callback_data=remove_callback)])
    rows.append([InlineKeyboardButton(t(locale, "buttons.broadcast_back"), callback_data="bc|menu")])
    return InlineKeyboardMarkup(rows)


def build_broadcast_starting_text(locale: str, total: int, *, pin_disabled: bool, pin_limit: int) -> str:
    pin_warning = ""
    if pin_disabled:
        pin_warning = t(locale, "broadcast.status.pin_warning", limit=pin_limit)
    return t(locale, "broadcast.status.starting", total=total, pin_warning=pin_warning)


def build_broadcast_progress_text(locale: str, *, sent: int, failed: int, processed: int, total: int) -> str:
    return t(
        locale,
        "broadcast.status.progress",
        sent=sent,
        failed=failed,
        processed=processed,
        total=total,
    )


def build_broadcast_finished_text(locale: str, *, sent: int, failed: int, removed: int, processed: int) -> str:
    return t(
        locale,
        "broadcast.status.finished",
        sent=sent,
        failed=failed,
        removed=removed,
        processed=processed,
    )


def build_closed_text(locale: str) -> str:
    return "✅ " + escape(t(locale, "broadcast.closed"))
