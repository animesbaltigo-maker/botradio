from __future__ import annotations

import logging
import traceback

from telegram import BotCommand, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes

from config import (
    ANIMETHEMES_BASE_URL,
    ANIMETHEMES_REQUEST_TIMEOUT,
    BOT_TOKEN,
    FFMPEG_PATH,
    MEDIA_CACHE_DIR,
)
from handlers.radio import anime_command, ed_command, op_command, radio, radio_callback
from handlers.start import start
from services.animethemes_client import AnimeThemesClient
from services.media_pipeline import MediaPipeline


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)


async def post_init(app: Application) -> None:
    app.bot_data["animethemes_client"] = AnimeThemesClient(
        ANIMETHEMES_BASE_URL,
        timeout=ANIMETHEMES_REQUEST_TIMEOUT,
    )
    app.bot_data["media_pipeline"] = MediaPipeline(
        MEDIA_CACHE_DIR,
        ffmpeg_path=FFMPEG_PATH,
    )
    await app.bot.set_my_commands(
        [
            BotCommand("start", "Mostra como o bot funciona"),
            BotCommand("radio", "Toca um tema aleatorio"),
            BotCommand("anime", "Toca um tema de um anime especifico"),
            BotCommand("op", "Toca uma abertura aleatoria ou do anime informado"),
            BotCommand("ed", "Toca um encerramento aleatorio ou do anime informado"),
        ]
    )


async def post_shutdown(app: Application) -> None:
    client = app.bot_data.get("animethemes_client")
    if isinstance(client, AnimeThemesClient):
        await client.close()
    pipeline = app.bot_data.get("media_pipeline")
    if isinstance(pipeline, MediaPipeline):
        await pipeline.close()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.error("Erro no bot: %r", context.error)
    traceback.print_exception(
        type(context.error),
        context.error,
        context.error.__traceback__,
    )

    try:
        if isinstance(update, Update):
            if update.callback_query:
                await update.callback_query.answer("Ocorreu um erro.", show_alert=True)
            elif update.effective_message:
                await update.effective_message.reply_text("Ocorreu um erro ao processar sua solicitacao.")
    except Exception:
        pass


def main() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("Configure BOT_TOKEN nas variaveis de ambiente.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("radio", radio))
    app.add_handler(CommandHandler("anime", anime_command))
    app.add_handler(CommandHandler("op", op_command))
    app.add_handler(CommandHandler("ed", ed_command))
    app.add_handler(CallbackQueryHandler(radio_callback, pattern=r"^radio:"))
    app.add_error_handler(error_handler)

    logging.info("AnimeThemes Radio Bot rodando...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
