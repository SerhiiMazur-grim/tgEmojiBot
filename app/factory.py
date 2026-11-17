from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode


def make_bot(token: str, proxy: str | None = None, html: bool = False) -> Bot:
    session = AiohttpSession(proxy=proxy) if proxy else None
    kwargs = {"default": DefaultBotProperties(parse_mode=ParseMode.HTML)} if html else {}
    return Bot(token, session=session, **kwargs)
