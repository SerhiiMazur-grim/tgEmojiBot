import asyncio
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramRetryAfter

MAX_INITIAL = 50
EXT = {"static": "webp", "animated": "tgs", "video": "webm"}
DEFAULT_EMOJI = "🙂"


class CloneError(Exception):
    pass


def pack_link(name: str, sticker_type: str) -> str:
    kind = "addemoji" if sticker_type == "custom_emoji" else "addstickers"
    return f"https://t.me/{kind}/{name}"


def sticker_format(st) -> str:
    return "animated" if st.is_animated else "video" if st.is_video else "static"


async def _retry(fn: Callable[[], Awaitable], attempts: int = 5):
    for _ in range(attempts):
        try:
            return await fn()
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
    return await fn()
