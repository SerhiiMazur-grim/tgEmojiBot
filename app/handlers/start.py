from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.config import Config
from app.db import Db
from app.handlers.common import safe_edit
from app.ui import kb, texts

router = Router()


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, db: Db, config: Config):
    await state.clear()
    user = message.from_user
    await db.upsert_user(user.id, user.full_name, user.username)
    await message.answer(texts.home_text(), reply_markup=kb.main_menu(user.id in config.admin_ids))


@router.message(Command("help"))
async def help_cmd(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(texts.help_text(), reply_markup=kb.back_home())


@router.callback_query(F.data == "home")
async def home(cb: CallbackQuery, state: FSMContext, config: Config):
    await state.clear()
    await safe_edit(cb.message, texts.home_text(), kb.main_menu(cb.from_user.id in config.admin_ids))
    await cb.answer()
