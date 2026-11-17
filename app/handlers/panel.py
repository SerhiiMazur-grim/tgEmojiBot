from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.cloner import mtproto
from app.config import Config
from app.crypto import CryptoError, decrypt
from app.db import Db
from app.handlers.admin import apply_decision
from app.handlers.common import safe_edit
from app.ui import kb, pages, texts

router = Router()
PAGE = pages.PANEL_PAGE


class AdminForm(StatesGroup):
    query = State()


def is_admin(user_id: int, config: Config) -> bool:
    return user_id in config.admin_ids


def parse_page(data: str | None) -> int:
    try:
        return max(0, int((data or "").rsplit(":", 1)[1]))
    except (IndexError, ValueError):
        return 0


def _ctx(db: Db, config: Config, uid: int) -> pages.Ctx:
    return pages.Ctx(db, config, None, uid)


async def _show(cb: CallbackQuery, db: Db, config: Config, page: pages.Page) -> None:
    view = await page.render(_ctx(db, config, cb.from_user.id))
    await safe_edit(cb.message, view.text, view.markup)


async def _deny(cb: CallbackQuery) -> None:
    await cb.answer(texts.admin_only(), show_alert=True)


@router.callback_query(F.data == "adm:panel")
async def show_panel(cb: CallbackQuery, state: FSMContext, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await state.clear()
    await _show(cb, db, config, pages.PanelPage())
    await cb.answer()


@router.callback_query(F.data.startswith("adm:req:"))
async def show_requests(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await _show(cb, db, config, pages.RequestsPage(parse_page(cb.data)))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:(ok|no):\d{1,12}:\d{1,4}$"))
async def decide_from_panel(cb: CallbackQuery, db: Db, config: Config, bot: Bot):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    _, action, uid, page = cb.data.split(":")
    outcome = await apply_decision(db, bot, cb.from_user.id, int(uid), action == "ok")
    if outcome == "missing":
        await cb.answer(texts.request_missing(), show_alert=True)
    elif outcome == "already":
        await cb.answer(texts.already_decided(), show_alert=True)
    else:
        await cb.answer()
    await _show(cb, db, config, pages.RequestsPage(int(page)))


@router.callback_query(F.data == "adm:stats")
async def show_stats(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await _show(cb, db, config, pages.StatsPage())
    await cb.answer()


def _uid(data: str | None) -> int | None:
    try:
        value = (data or "").split(":")[2]
    except IndexError:
        return None
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 18 else None


@router.callback_query(F.data.startswith("adm:users:"))
async def show_users(cb: CallbackQuery, state: FSMContext, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await state.clear()
    await _show(cb, db, config, pages.UsersPage(parse_page(cb.data)))
    await cb.answer()


@router.callback_query(F.data.startswith("adm:user:"))
async def show_card(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    uid = _uid(cb.data)
    view = await pages.UserPage(uid).render(_ctx(db, config, cb.from_user.id)) if uid is not None else None
    if view is None:
        await cb.answer(texts.user_not_found_alert(), show_alert=True)
        return
    await safe_edit(cb.message, view.text, view.markup)
    await cb.answer()


async def _mutate(cb: CallbackQuery, db: Db, config: Config, action) -> None:
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    uid = _uid(cb.data)
    if uid is None:
        await cb.answer(texts.bad_request_data(), show_alert=True)
        return
    if uid in config.admin_ids:
        await cb.answer(texts.cannot_touch_admin(), show_alert=True)
        return
    await action(db, uid)
    view = await pages.UserPage(uid).render(_ctx(db, config, cb.from_user.id))
    if view is not None:
        await safe_edit(cb.message, view.text, view.markup)
    await cb.answer()


@router.callback_query(F.data.startswith("adm:revoke:"))
async def revoke(cb: CallbackQuery, db: Db, config: Config):
    await _mutate(cb, db, config, lambda d, u: d.revoke_access(u))


@router.callback_query(F.data.startswith("adm:block:"))
async def block(cb: CallbackQuery, db: Db, config: Config):
    await _mutate(cb, db, config, lambda d, u: d.block(u))


@router.callback_query(F.data.startswith("adm:unblock:"))
async def unblock(cb: CallbackQuery, db: Db, config: Config):
    await _mutate(cb, db, config, lambda d, u: d.unblock(u))


@router.callback_query(F.data == "adm:search")
async def search_start(cb: CallbackQuery, state: FSMContext, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await state.set_state(AdminForm.query)
    await safe_edit(cb.message, texts.search_prompt(), kb.search_cancel_kb())
    await cb.answer()


@router.message(AdminForm.query, F.text, ~F.text.startswith("/"))
async def search_query(message: Message, state: FSMContext, db: Db, config: Config):
    if not is_admin(message.from_user.id, config):
        await state.clear()
        return
    info = await db.find_user(message.text)
    view = await pages.UserPage(info.user_id).render(_ctx(db, config, message.from_user.id)) if info else None
    if view is None:
        await message.answer(texts.user_not_found(), reply_markup=kb.search_cancel_kb())
        return
    await state.clear()
    await message.answer(view.text, reply_markup=view.markup)


@router.callback_query(F.data == "adm:acct")
async def show_account(cb: CallbackQuery, state: FSMContext, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await state.clear()
    await _show(cb, db, config, pages.AccountPage())
    await cb.answer()


@router.callback_query(F.data == "adm:acctchk")
async def check_account(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await cb.answer()
    session = await db.shared_session()
    status = "сессия не добавлена"
    if session is not None:
        try:
            await mtproto.check_session(decrypt(config.session_key, session.session_enc), config)
            status = "работает"
        except CryptoError:
            status = "ошибка: проверьте SESSION_KEY"
        except mtproto.SessionError as e:
            status = f"ошибка: {e}"
    await _show(cb, db, config, pages.AccountPage(status))


@router.callback_query(F.data.regexp(r"^adm:acctdel:\d{1,12}$"))
async def ask_delete_account(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    sid = cb.data.split(":")[2]
    await safe_edit(cb.message, texts.account_delete_confirm(),
                    kb.confirm_kb(f"adm:acctdelok:{sid}", "adm:acct", danger=True))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:acctdelok:\d{1,12}$"))
async def delete_account(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await db.delete_telethon(int(cb.data.split(":")[2]))
    await _show(cb, db, config, pages.AccountPage())
    await cb.answer()


@router.callback_query(F.data == "adm:backend")
async def show_backend(cb: CallbackQuery, state: FSMContext, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    await state.clear()
    await _show(cb, db, config, pages.BackendPage())
    await cb.answer()


@router.callback_query(F.data.regexp(r"^adm:bk:(tt|\d{1,12})$"))
async def pick_backend(cb: CallbackQuery, db: Db, config: Config):
    if not is_admin(cb.from_user.id, config):
        await _deny(cb)
        return
    value = cb.data.split(":")[2]
    if value == "tt":
        await db.set_setting("create_backend", "telethon")
    elif any(str(w.id) == value for w in await db.list_workers(cb.from_user.id)):
        await db.set_setting("create_backend", f"bot:{value}")
    await _show(cb, db, config, pages.BackendPage())
    await cb.answer()
