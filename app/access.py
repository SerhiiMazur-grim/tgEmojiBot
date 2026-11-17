import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from app.config import Config
from app.ui import kb, texts

log = logging.getLogger(__name__)


def _is_start(text: str | None) -> bool:
    parts = (text or "").split(maxsplit=1)
    return bool(parts) and parts[0].split("@", 1)[0] == "/start"


class AccessMiddleware(BaseMiddleware):
    async def __call__(self, handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
                       event: TelegramObject, data: dict[str, Any]) -> Any:
        user = data.get("event_from_user")
        if user is None:
            return await handler(event, data)
        db, cfg = data["db"], data["config"]
        if user.id in cfg.admin_ids:
            return await handler(event, data)
        if await db.is_blocked(user.id):
            if isinstance(event, Message):
                if event.chat.type == "private":
                    await event.answer(texts.blocked())
            elif isinstance(event, CallbackQuery):
                await event.answer(texts.blocked_alert(), show_alert=True)
            return None
        if await db.is_allowed(user.id):
            return await handler(event, data)

        if isinstance(event, Message):
            if event.chat.type != "private":
                return None
            if _is_start(event.text):
                await db.upsert_user(user.id, user.full_name, user.username)
                if await db.create_request(user.id, user.full_name):
                    if not await self._notify_admins(data["bot"], cfg, user):
                        await db.delete_request(user.id)
                        await event.answer(texts.admin_unreachable())
                        return None
                status = await db.request_status(user.id)
                await event.answer(texts.request_rejected() if status == "rejected" else texts.request_sent())
            else:
                await event.answer(texts.need_access())
        elif isinstance(event, CallbackQuery):
            await event.answer(texts.no_access_alert(), show_alert=True)
        return None

    @staticmethod
    async def _notify_admins(bot: Bot, cfg: Config, user: User) -> bool:
        delivered = False
        for admin_id in cfg.admin_ids:
            try:
                await bot.send_message(admin_id, texts.admin_request(user.full_name, user.id, user.username),
                                       reply_markup=kb.request_kb(user.id))
                delivered = True
            except TelegramAPIError as e:
                log.warning("cannot notify admin %s about request from %s: %s", admin_id, user.id, e)
        return delivered
