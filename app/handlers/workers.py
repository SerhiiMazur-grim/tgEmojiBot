import logging
from contextlib import suppress

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramAPIError, TelegramUnauthorizedError
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (CallbackQuery, KeyboardButton, KeyboardButtonRequestManagedBot, ManagedBotUpdated,
                           Message, ReplyKeyboardMarkup, ReplyKeyboardRemove)
from aiogram.utils.token import TokenValidationError

from app.cloner import mtproto
from app.config import Config
from app.crypto import CryptoError, encrypt
from app.db import Db
from app.factory import make_bot
from app.handlers.common import safe_edit
from app.ui import kb, pages, texts

router = Router()
log = logging.getLogger(__name__)


class AddWorker(StatesGroup):
    token = State()


class AddSession(StatesGroup):
    session = State()


def _is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def _parse_id(data: str | None) -> int | None:
    try:
        value = data.split(":")[2]
    except (AttributeError, IndexError):
        return None
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 18 else None


def _back_token():
    return kb.markup(kb.back("adm:backend"))


def _back_session():
    return kb.markup(kb.back("adm:acct"))


async def _backend_view(db: Db, config: Config, uid: int) -> pages.View:
    return await pages.BackendPage().render(pages.Ctx(db, config, None, uid))


@router.callback_query(F.data.startswith("w:del:"))
async def delete(cb: CallbackQuery, db: Db, config: Config):
    if not _is_admin(cb.from_user.id, config):
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    wid = _parse_id(cb.data)
    if wid is None:
        await cb.answer(texts.bad_request_data(), show_alert=True)
        return
    await db.delete_worker(cb.from_user.id, wid)
    if await db.get_setting("create_backend") == f"bot:{wid}":
        await db.set_setting("create_backend", "telethon")
    view = await _backend_view(db, config, cb.from_user.id)
    await safe_edit(cb.message, view.text, view.markup)
    await cb.answer()


@router.callback_query(F.data == "w:add")
async def add_start(cb: CallbackQuery, state: FSMContext, config: Config):
    if not _is_admin(cb.from_user.id, config):
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    await safe_edit(cb.message, texts.add_choice(), kb.add_choice_kb())
    await cb.answer()


@router.callback_query(F.data == "w:token")
async def token_start(cb: CallbackQuery, state: FSMContext, config: Config):
    if not _is_admin(cb.from_user.id, config):
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    await state.set_state(AddWorker.token)
    await safe_edit(cb.message, texts.add_prompt(), _back_token())
    await cb.answer()


@router.callback_query(F.data == "w:create")
async def create_start(cb: CallbackQuery, bot: Bot, config: Config):
    if not _is_admin(cb.from_user.id, config):
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    me = await bot.me()
    markup = ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(
            text="Создать бота", icon_custom_emoji_id=kb.icon("add"), style="success",
            request_managed_bot=KeyboardButtonRequestManagedBot(request_id=1, suggested_name="Клонер эмодзи"))]],
        resize_keyboard=True, one_time_keyboard=True)
    await bot.send_message(cb.from_user.id, texts.create_bot_hint(me.username), reply_markup=markup)
    await cb.answer()


@router.message(F.managed_bot_created)
async def ignore_service(message: Message):
    pass


@router.managed_bot()
async def on_managed_bot(update: ManagedBotUpdated, bot: Bot, db: Db, config: Config):
    creator, child = update.user, update.bot_user
    if not _is_admin(creator.id, config):
        return
    try:
        token = await bot.get_managed_bot_token(user_id=child.id)
    except TelegramAPIError as e:
        log.warning("managed bot token fetch failed: %s", type(e).__name__)
        await bot.send_message(creator.id, texts.managed_failed(), reply_markup=ReplyKeyboardRemove())
        return
    probe = None
    try:
        probe = make_bot(token, config.proxy)
        me = await probe.get_me()
    except (TelegramAPIError, TokenValidationError) as e:
        log.warning("managed bot probe failed: %s", type(e).__name__)
        await bot.send_message(creator.id, texts.managed_failed(), reply_markup=ReplyKeyboardRemove())
        return
    finally:
        if probe is not None:
            await probe.session.close()
    await db.add_worker(creator.id, token, me.username, kind="managed")
    await bot.send_message(creator.id, texts.worker_added(me.username), reply_markup=ReplyKeyboardRemove())
    view = await _backend_view(db, config, creator.id)
    await bot.send_message(creator.id, view.text, reply_markup=view.markup)


@router.message(AddWorker.token, F.text)
async def add_token(message: Message, state: FSMContext, db: Db, config: Config):
    if not _is_admin(message.from_user.id, config):
        await state.clear()
        return
    token = message.text.strip()
    with suppress(TelegramAPIError):
        await message.delete()
    try:
        probe = make_bot(token, config.proxy)
    except TokenValidationError:
        await message.answer(texts.bad_token(), reply_markup=_back_token())
        return
    try:
        me = await probe.get_me()
    except TelegramUnauthorizedError:
        await message.answer(texts.bad_token(), reply_markup=_back_token())
        return
    except TelegramAPIError:
        await message.answer(texts.net_error(), reply_markup=_back_token())
        return
    finally:
        await probe.session.close()
    await db.add_worker(message.from_user.id, token, me.username)
    await state.clear()
    view = await _backend_view(db, config, message.from_user.id)
    await message.answer(texts.worker_added(me.username) + "\n\n" + view.text, reply_markup=view.markup)


def _tt_allowed(user_id: int, config: Config) -> bool:
    return _is_admin(user_id, config) and config.telethon_enabled


@router.callback_query(F.data == "w:tt")
async def tt_start(cb: CallbackQuery, state: FSMContext, config: Config):
    if not _tt_allowed(cb.from_user.id, config):
        await cb.answer(texts.admin_only(), show_alert=True)
        return
    await state.set_state(AddSession.session)
    await safe_edit(cb.message, texts.session_prompt(), _back_session())
    await cb.answer()


@router.message(AddSession.session, F.text)
async def tt_session(message: Message, state: FSMContext, db: Db, config: Config):
    if not _tt_allowed(message.from_user.id, config):
        await state.clear()
        return
    raw = message.text.strip()
    with suppress(TelegramAPIError):
        await message.delete()
    try:
        info = await mtproto.check_session(raw, config)
        blob = encrypt(config.session_key, raw)
    except mtproto.SessionError as e:
        await message.answer(texts.session_bad(str(e)), reply_markup=_back_session())
        return
    except CryptoError:
        await message.answer(texts.session_bad("проверьте SESSION_KEY"), reply_markup=_back_session())
        return
    await db.add_telethon(info.account_id, info.label, blob, message.from_user.id)
    await state.clear()
    view = await pages.AccountPage().render(pages.Ctx(db, config, None, message.from_user.id))
    await message.answer(texts.session_added(info.label) + "\n\n" + view.text, reply_markup=view.markup)
