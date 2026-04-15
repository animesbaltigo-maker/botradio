from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


BANNER_URL = "https://photo.chelpbot.me/AgACAgEAAxkBajdq32nec9iWglPIl5GfbuGfphLP6VoyAAIcDGsbjonxRu9aubnT6Bk0AQADAgADdwADOwQ/photo.jpg"


START_TEXT = (
    "🎶 <b>Rádio Animes está no ar!</b>\n"
    "Escute aberturas, encerramentos e temas icônicos de anime direto no Telegram.\n\n"
    "<i>Com este bot, você pode:</i>\n"
    "<blockquote>• Tocar um tema aleatório\n"
    "• Ouvir uma abertura aleatória\n"
    "• Ouvir um encerramento aleatório\n"
    "• Buscar músicas de um anime específico</blockquote>\n\n"
    "<b>A trilha sonora dos animes, direto no Telegram.</b>\n\n"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if not message:
        return

    await message.reply_photo(
        photo=BANNER_URL,
        caption=START_TEXT,
        parse_mode=ParseMode.HTML,
    )