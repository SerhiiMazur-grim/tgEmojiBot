import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.cloner import ops
from app.cloner.backend import PartialPack
from app.cloner.items import LIMITS_BY_KIND, TYPE_BY_KIND, Collected, collect_message, merge_items
from app.cloner.service import CloneError, pack_link
from app.config import Config
from app.db import Db
from app.handlers.common import make_progress, safe_edit
from app.ui import kb, texts

log = logging.getLogger(__name__)
router = Router()
MAX_TITLE = 64


class NewPack(StatesGroup):
    collect = State()
    title = State()
    link = State()


def not_command(message) -> bool:
    return not (message.text or "").startswith("/")


def collect_note(c: Collected, skipped: int) -> str:
    parts = []
    if skipped:
        parts.append(f"Не поместилось: {skipped}")
    if c.wrong_kind:
        parts.append(f"Пропущено другого типа: {c.wrong_kind}")
    if c.not_found:
        parts.append(f"Не найдено: {c.not_found}")
    if c.bad_positions:
        parts.append(f"Номеров вне набора: {c.bad_positions}")
    return "\n".join(parts)


def _cart(data: dict, note: str = "") -> tuple[str, object]:
    items = data.get("items") or []
    text = texts.cart_text(data["kind"], len(items), data.get("title"), note, data.get("short_name"))
    return text, kb.cart_kb(bool(items))


@router.callback_query(F.data.in_({"new:emoji", "new:sticker"}))
async def start_new(cb: CallbackQuery, state: FSMContext):
    kind = cb.data.split(":")[1]
    await state.clear()
    await state.set_state(NewPack.collect)
    await state.update_data(kind=kind, items=[], title=None, source=None)
    await safe_edit(cb.message, texts.new_prompt(kind), kb.cart_kb(False))
    await cb.answer()


@router.callback_query(NewPack.collect, F.data == "new:link")
async def ask_link(cb: CallbackQuery, state: FSMContext):
    await state.set_state(NewPack.link)
    await safe_edit(cb.message, texts.link_input((await state.get_data())["kind"]), kb.title_back_kb())
    await cb.answer()


@router.message(NewPack.link, F.text, F.func(not_command))
async def on_link(message: Message, state: FSMContext, bot: Bot, db: Db, config: Config):
    uid = message.from_user.id
    try:
        backend = await ops.backend_for_new(db, config, bot, uid)
        name = await ops.validate_name(backend, uid, message.text)
    except CloneError as e:
        await message.answer(texts.clone_failed(str(e)), reply_markup=kb.title_back_kb())
        return
    await state.update_data(short_name=name)
    await state.set_state(NewPack.collect)
    text, markup = _cart(await state.get_data())
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == "new:cart")
async def back_to_cart(cb: CallbackQuery, state: FSMContext, config: Config):
    data = await state.get_data()
    if not data.get("kind"):
        await safe_edit(cb.message, texts.pick_section(), kb.main_menu(cb.from_user.id in config.admin_ids))
    else:
        await state.set_state(NewPack.collect)
        await safe_edit(cb.message, *_cart(data))
    await cb.answer()


@router.message(NewPack.collect, F.func(not_command))
async def on_input(message: Message, state: FSMContext, bot: Bot):
    data = await state.get_data()
    got = await collect_message(bot, message, data["kind"])
    items, skipped = merge_items(data["items"], got.items, LIMITS_BY_KIND[data["kind"]])
    await state.update_data(items=items, title=data.get("title") or got.title,
                            source=data.get("source") or got.source)
    text, markup = _cart(await state.get_data(), collect_note(got, skipped))
    await message.answer(text, reply_markup=markup)


@router.callback_query(NewPack.collect, F.data == "new:title")
async def ask_title(cb: CallbackQuery, state: FSMContext):
    await state.set_state(NewPack.title)
    await safe_edit(cb.message, texts.title_input(), kb.title_back_kb())
    await cb.answer()


@router.message(NewPack.title, F.text, F.func(not_command))
async def on_title(message: Message, state: FSMContext):
    title = message.text.strip()
    if not 1 <= len(title) <= MAX_TITLE:
        await message.answer(texts.title_invalid(MAX_TITLE), reply_markup=kb.title_back_kb())
        return
    await state.update_data(title=title)
    await state.set_state(NewPack.collect)
    text, markup = _cart(await state.get_data())
    await message.answer(text, reply_markup=markup)


@router.callback_query(NewPack.collect, F.data == "new:clear")
async def clear(cb: CallbackQuery, state: FSMContext):
    await state.update_data(items=[], title=None, source=None, short_name=None)
    await safe_edit(cb.message, *_cart(await state.get_data()))
    await cb.answer()


@router.callback_query(F.data == "new:go")
async def do_create(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    await cb.answer()
    uid, data = cb.from_user.id, await state.get_data()
    items, kind = data.get("items") or [], data.get("kind")
    if not items:
        await safe_edit(cb.message, texts.pick_section(), kb.main_menu(uid in config.admin_ids))
        return
    try:
        backend = await ops.backend_for_new(db, config, bot, uid)
    except CloneError as e:
        await safe_edit(cb.message, texts.clone_failed(str(e)), kb.cart_kb(True))
        return
    if not ops.try_claim(uid, backend):
        await safe_edit(cb.message, texts.op_busy(), kb.cart_kb(True))
        return
    title = (data.get("title") or data.get("source") or "Emoji")[:MAX_TITLE]
    try:
        await safe_edit(cb.message, texts.reading())
        note = None
        async with backend:
            try:
                short_name = await backend.create(title, kind, items, data.get("source"),
                                                  make_progress(cb.message, "Создаю пак"), data.get("short_name"))
            except PartialPack as e:
                short_name, note = e.short_name, e
            remote = await backend.fetch(short_name)
        first = remote.stickers[0].custom_emoji_id if remote.stickers else None
        pack_id = await db.add_pack(uid, kind, short_name, remote.title, first, len(remote.stickers),
                                    backend.name, backend.ref)
        await db.log_clone(uid, data.get("source") or "", short_name, len(remote.stickers),
                           "telethon" if backend.name == "telethon" else "token")
        await state.clear()
        link = pack_link(short_name, TYPE_BY_KIND[kind])
        text = texts.pack_partial(link, note.done, str(note)) if note else texts.pack_done(remote.title, 0)
        await safe_edit(cb.message, text, kb.created_kb(link, kind, pack_id))
    except CloneError as e:
        await safe_edit(cb.message, texts.clone_failed(str(e)), kb.cart_kb(True))
    except Exception as e:
        log.warning("pack create failed for user %s: %s", uid, type(e).__name__)
        await safe_edit(cb.message, texts.clone_failed(type(e).__name__), kb.cart_kb(True))
    finally:
        ops.release(uid, backend)


@router.message(F.text, F.func(not_command))
async def fallback(message: Message, config: Config):
    await message.answer(texts.pick_section(), reply_markup=kb.main_menu(message.from_user.id in config.admin_ids))
