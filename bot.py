from __future__ import annotations

import logging
import traceback

from telegram import BotCommand, BotCommandScopeAllPrivateChats, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationHandlerStop,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    MessageHandler,
    PersistenceInput,
    PicklePersistence,
    TypeHandler,
    filters,
)

from config import (
    ADMIN_IDS,
    ANIMETHEMES_BASE_URL,
    ANIMETHEMES_REQUEST_TIMEOUT,
    APP_CONCURRENT_UPDATES,
    APP_CONNECTION_POOL_SIZE,
    APP_GET_UPDATES_CONNECTION_POOL_SIZE,
    APP_POOL_TIMEOUT,
    BOT_TOKEN,
    CHANNEL_MEMBERSHIP_TTL_SECONDS,
    FFMPEG_PATH,
    MEDIA_CACHE_DIR,
    PERSISTENCE_FILE,
    RADIO_ANIMES_CHANNEL_CHAT,
    RADIO_ANIMES_CHANNEL_URL,
    RADIO_ANIMES_REQUIRED_CHANNELS,
)
from handlers.broadcast import broadcast_callbacks, broadcast_command, broadcast_message_router
from handlers.control_block import control_block_callback_guard, control_block_message_guard
from services.control_agent import start_control_agent, stop_control_agent
from handlers.inline import inline_query
from handlers.language import gate_callback, language_callback, language_command
from handlers.posts import postanime_command
from handlers.radio import anime_command, ed_command, op_command, radio, radio_callback
from handlers.start import start
from handlers.tracking import track_user_update
from services.animethemes_client import AnimeThemesClient
from services.gatekeeper import Gatekeeper
from services.i18n import SUPPORTED_LOCALES, resolve_locale, t
from services.media_pipeline import MediaPipeline
from services.user_state import get_language


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)

LOGGER = logging.getLogger(__name__)


def _build_commands(locale: str) -> list[BotCommand]:
    return [
        BotCommand("start", t(locale, "commands.start")),
        BotCommand("radio", t(locale, "commands.radio")),
        BotCommand("anime", t(locale, "commands.anime")),
        BotCommand("op", t(locale, "commands.op")),
        BotCommand("ed", t(locale, "commands.ed")),
        BotCommand("idioma", t(locale, "commands.language")),
    ]


async def _set_localized_commands(app: Application) -> None:
    scope = BotCommandScopeAllPrivateChats()
    await app.bot.set_my_commands(_build_commands("pt"), scope=scope)
    for locale in SUPPORTED_LOCALES:
        await app.bot.set_my_commands(_build_commands(locale), scope=scope, language_code=locale)


async def post_init(app: Application) -> None:
    await start_control_agent(app)
    app.bot_data["animethemes_client"] = AnimeThemesClient(
        ANIMETHEMES_BASE_URL,
        timeout=ANIMETHEMES_REQUEST_TIMEOUT,
    )
    app.bot_data["media_pipeline"] = MediaPipeline(
        MEDIA_CACHE_DIR,
        ffmpeg_path=FFMPEG_PATH,
    )
    app.bot_data["gatekeeper"] = Gatekeeper(
        RADIO_ANIMES_REQUIRED_CHANNELS or (RADIO_ANIMES_CHANNEL_CHAT,),
        RADIO_ANIMES_CHANNEL_URL,
        membership_ttl_seconds=CHANNEL_MEMBERSHIP_TTL_SECONDS,
    )
    await _set_localized_commands(app)


async def post_shutdown(app: Application) -> None:
    await stop_control_agent(app)
    client = app.bot_data.get("animethemes_client")
    if isinstance(client, AnimeThemesClient):
        await client.close()
    pipeline = app.bot_data.get("media_pipeline")
    if isinstance(pipeline, MediaPipeline):
        await pipeline.close()


async def required_channel_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    gate = context.application.bot_data.get("gatekeeper")
    if not message or not user or not isinstance(gate, Gatekeeper):
        raise ApplicationHandlerStop

    locale = gate.resolve_locale(update, context)
    try:
        is_member = await gate.is_channel_member(context, user.id, force_refresh=True)
    except Exception as exc:
        await message.reply_text(t(locale, "gate.channel_check_error"))
        raise ApplicationHandlerStop from exc

    if not is_member:
        missing_channels = await gate.missing_channel_memberships(context, user.id, force_refresh=True)
        await gate.send_channel_gate(message, locale, channels=missing_channels)
        raise ApplicationHandlerStop


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    LOGGER.error("Erro no bot: %r", context.error)
    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__,
    )

    locale = "pt"
    if isinstance(update, Update):
        locale = resolve_locale(
            get_language(context),
            getattr(update.effective_user, "language_code", None),
        )

    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer(t(locale, "errors.generic_action")[:180], show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text(t(locale, "errors.generic_action"))
    except Exception:
        pass


def _attach_optional_rate_limiter(builder: Application.builder) -> Application.builder:
    try:
        return builder.rate_limiter(AIORateLimiter(max_retries=2))
    except RuntimeError as exc:
        LOGGER.warning("AIORateLimiter indisponivel no ambiente atual: %s", exc)
        return builder


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Configure BOT_TOKEN nas variáveis de ambiente.")

    persistence = PicklePersistence(
        filepath=PERSISTENCE_FILE,
        store_data=PersistenceInput(
            user_data=True,
            chat_data=False,
            bot_data=False,
            callback_data=False,
        ),
        single_file=True,
        update_interval=30,
    )

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(APP_CONCURRENT_UPDATES)
        .connection_pool_size(APP_CONNECTION_POOL_SIZE)
        .get_updates_connection_pool_size(APP_GET_UPDATES_CONNECTION_POOL_SIZE)
        .pool_timeout(APP_POOL_TIMEOUT)
        .get_updates_pool_timeout(APP_POOL_TIMEOUT)
        .persistence(persistence)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
    )
    builder = _attach_optional_rate_limiter(builder)
    app = builder.build()

    app.add_handler(CallbackQueryHandler(control_block_callback_guard, pattern=r".*"), group=-100)
    app.add_handler(MessageHandler(filters.ALL, control_block_message_guard), group=-100)

    app.add_handler(TypeHandler(Update, track_user_update), group=-1)
    app.add_handler(MessageHandler(filters.COMMAND, required_channel_guard), group=-90)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("radio", radio))
    app.add_handler(CommandHandler("anime", anime_command))
    app.add_handler(CommandHandler("op", op_command))
    app.add_handler(CommandHandler("ed", ed_command))
    app.add_handler(CommandHandler(["idioma", "language", "idioma_bot"], language_command))
    if ADMIN_IDS:
        app.add_handler(CommandHandler(["broadcast", "bc"], broadcast_command))
        app.add_handler(CommandHandler("postanime", postanime_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, broadcast_message_router))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(gate_callback, pattern=r"^gate:"))
    app.add_handler(CallbackQueryHandler(radio_callback, pattern=r"^radio:"))
    app.add_handler(CallbackQueryHandler(broadcast_callbacks, pattern=r"^bc\|"))
    app.add_error_handler(error_handler)

    LOGGER.info("Rádio Animes rodando...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
