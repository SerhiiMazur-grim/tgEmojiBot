import time
from contextlib import suppress

from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, MaybeInaccessibleMessage, Message

from app.ui import texts

PROGRESS_EVERY = 2.0


async def safe_edit(message: MaybeInaccessibleMessage, text: str,
                    reply_markup: InlineKeyboardMarkup | None = None) -> None:
    if not isinstance(message, Message):
        await message.bot.send_message(message.chat.id, text, reply_markup=reply_markup)
        return
    with suppress(TelegramBadRequest):
        await message.edit_text(text, reply_markup=reply_markup)


def make_progress(status, verb: str):
    last = 0.0

    async def progress(done: int, total: int) -> None:
        nonlocal last
        now = time.monotonic()
        if done < total and now - last < PROGRESS_EVERY:
            return
        last = now
        with suppress(TelegramAPIError):
            await safe_edit(status, texts.progress(done, total, verb))

    return progress
