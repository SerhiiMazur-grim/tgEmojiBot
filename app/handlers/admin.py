from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery, Message

from app.config import Config
from app.db import Db
from app.handlers.common import safe_edit
from app.ui import texts

router = Router()


def _parse(data: str | None) -> tuple[bool, int] | None:
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[1] not in ("ok", "no"):
        return None
    try:
        return parts[1] == "ok", int(parts[2])
    except ValueError:
        return None


async def apply_decision(db: Db, bot: Bot, admin_id: int, uid: int, ok: bool) -> str:
    status = await db.request_status(uid)
    if status is None:
        return "missing"
    if status != "pending":
        return "already"
    if not await db.resolve_pending(uid, "approved" if ok else "rejected"):
        return "already"
    if ok:
        await db.allow(uid, admin_id)
    try:
        await bot.send_message(uid, texts.approved() if ok else texts.rejected())
    except TelegramAPIError:
        pass
    return "applied"


@router.callback_query(F.data.startswith("acc:"))
async def decide(cb: CallbackQuery, db: Db, config: Config, bot: Bot):
    if cb.from_user.id not in config.admin_ids:
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    parsed = _parse(cb.data)
    if parsed is None:
        await cb.answer(texts.bad_request_data(), show_alert=True)
        return
    ok, uid = parsed
    outcome = await apply_decision(db, bot, cb.from_user.id, uid, ok)
    if outcome == "missing":
        await cb.answer(texts.request_missing(), show_alert=True)
        return
    if outcome == "already":
        await cb.answer(texts.already_decided(), show_alert=True)
        return
    original = cb.message.html_text if isinstance(cb.message, Message) else ""
    await safe_edit(cb.message, texts.admin_decided(original, ok))
    await cb.answer()
