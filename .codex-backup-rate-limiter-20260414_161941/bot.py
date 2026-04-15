from __future__ import annotations

import logging
import traceback

from telegram import BotCommand, BotCommandScopeAllPrivateChats, Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    InlineQueryHandler,
    PersistenceInput,
    PicklePersistence,
)

from config import (
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
)
from handlers.inline import inline_query
from handlers.language import gate_callback, language_callback, language_command
from handlers.radio import anime_command, ed_command, op_command, radio, radio_callback
from handlers.start import start
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
    app.bot_data["animethemes_client"] = AnimeThemesClient(
        ANIMETHEMES_BASE_URL,
        timeout=ANIMETHEMES_REQUEST_TIMEOUT,
    )
    app.bot_data["media_pipeline"] = MediaPipeline(
        MEDIA_CACHE_DIR,
        ffmpeg_path=FFMPEG_PATH,
    )
    app.bot_data["gatekeeper"] = Gatekeeper(
        RADIO_ANIMES_CHANNEL_CHAT,
        RADIO_ANIMES_CHANNEL_URL,
        membership_ttl_seconds=CHANNEL_MEMBERSHIP_TTL_SECONDS,
    )
    await _set_localized_commands(app)


async def post_shutdown(app: Application) -> None:
    client = app.bot_data.get("animethemes_client")
    if isinstance(client, AnimeThemesClient):
        await client.close()
    pipeline = app.bot_data.get("media_pipeline")
    if isinstance(pipeline, MediaPipeline):
        await pipeline.close()


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

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(APP_CONCURRENT_UPDATES)
        .connection_pool_size(APP_CONNECTION_POOL_SIZE)
        .get_updates_connection_pool_size(APP_GET_UPDATES_CONNECTION_POOL_SIZE)
        .pool_timeout(APP_POOL_TIMEOUT)
        .get_updates_pool_timeout(APP_POOL_TIMEOUT)
        .persistence(persistence)
        .rate_limiter(AIORateLimiter(max_retries=2))
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("radio", radio))
    app.add_handler(CommandHandler("anime", anime_command))
    app.add_handler(CommandHandler("op", op_command))
    app.add_handler(CommandHandler("ed", ed_command))
    app.add_handler(CommandHandler(["idioma", "language", "idioma_bot"], language_command))
    app.add_handler(InlineQueryHandler(inline_query))
    app.add_handler(CallbackQueryHandler(language_callback, pattern=r"^lang:"))
    app.add_handler(CallbackQueryHandler(gate_callback, pattern=r"^gate:"))
    app.add_handler(CallbackQueryHandler(radio_callback, pattern=r"^radio:"))
    app.add_error_handler(error_handler)

    LOGGER.info("Rádio Animes rodando...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

