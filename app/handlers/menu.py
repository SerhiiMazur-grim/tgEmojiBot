from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery

from app.config import Config
from app.db import Db
from app.handlers.common import safe_edit
from app.ui import pages, texts

router = Router()


@router.callback_query(F.data.startswith("menu:"))
async def navigate(cb: CallbackQuery, state: FSMContext, db: Db, config: Config, bot: Bot):
    page = pages.resolve_page(cb.data[len("menu:"):])
    view = await page.render(pages.Ctx(db, config, bot, cb.from_user.id)) if page else None
    if view is None:
        await cb.answer(texts.pack_missing_alert(), show_alert=True)
        return
    await state.clear()
    await safe_edit(cb.message, view.text, view.markup)
    await cb.answer()
