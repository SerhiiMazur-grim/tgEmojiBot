import logging
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace
from typing import Awaitable, Callable

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, CallbackQuery, Message

from app import post
from app.cloner import ops
from app.cloner.backend import PackBackend, PackGone, PartialPack
from app.cloner.items import LIMITS_BY_KIND, TYPE_BY_KIND, collect_message, merge_items
from app.cloner.parse import RangeError, parse_ranges
from app.cloner.service import CloneError, pack_link
from app.config import Config
from app.db import Db, Pack
from app.handlers.common import make_progress, safe_edit
from app.handlers.create import collect_note, not_command
from app.ui import kb, pages, texts

log = logging.getLogger(__name__)
router = Router()
MAX_TITLE = 64
PREVIEW_PAGE = 50


class PackForm(StatesGroup):
    link = State()
    rename = State()
    remove = State()
    add = State()


def _pack_id(data: str | None, index: int = 2) -> int | None:
    try:
        value = (data or "").split(":")[index]
    except IndexError:
        return None
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


async def _pack(cb: CallbackQuery, db: Db) -> Pack | None:
    pack_id = _pack_id(cb.data)
    pack = await db.get_pack(pack_id, cb.from_user.id) if pack_id is not None else None
    if pack is None:
        await cb.answer(texts.pack_missing_alert(), show_alert=True)
    return pack


async def show_pack(message, db: Db, config: Config, bot, uid: int, pack_id: int, note: str = "") -> None:
    view = await pages.PackPage(pack_id).render(pages.Ctx(db, config, bot, uid))
    if view is not None:
        await safe_edit(message, f"{note}\n\n{view.text}" if note else view.text, view.markup)


async def run_op(message, uid: int, bot: Bot, db: Db, config: Config, pack: Pack,
                 action: Callable[[PackBackend], Awaitable[None]], *, refresh: bool = True) -> bool:
    back_kb = kb.pack_back_kb(pack.id)
    try:
        backend = await ops.backend_for_pack(db, config, bot, pack)
    except CloneError as e:
        await safe_edit(message, texts.clone_failed(str(e)), back_kb)
        return False
    if not ops.try_claim(uid, backend):
        await safe_edit(message, texts.op_busy(), back_kb)
        return False
    try:
        await safe_edit(message, texts.reading())
        async with backend:
            try:
                await action(backend)
            except PartialPack as e:
                await ops.refresh_pack(db, backend, pack)
                await safe_edit(message, texts.op_partial(e.done, str(e)), back_kb)
                return False
            if refresh:
                await ops.refresh_pack(db, backend, pack)
        return True
    except PackGone as e:
        await db.delete_pack(pack.id, pack.user_id)
        await safe_edit(message, texts.clone_failed(str(e)), kb.after_delete_kb(pack.kind))
    except CloneError as e:
        await safe_edit(message, texts.clone_failed(str(e)), back_kb)
    except Exception as e:
        log.warning("pack operation failed for user %s: %s", uid, type(e).__name__)
        await safe_edit(message, texts.clone_failed(type(e).__name__), back_kb)
    finally:
        ops.release(uid, backend)
    return False


@router.callback_query(F.data.regexp(r"^pk:ren:\d+$"))
async def ask_rename(cb: CallbackQuery, state: FSMContext, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await state.clear()
    await state.set_state(PackForm.rename)
    await state.update_data(pack_id=pack.id, old_title=pack.title)
    await safe_edit(cb.message, texts.rename_prompt(pack.title), kb.pack_back_kb(pack.id))
    await cb.answer()


@router.message(PackForm.rename, F.text, F.func(not_command))
async def on_rename(message: Message, state: FSMContext):
    data, title = await state.get_data(), message.text.strip()
    if not 1 <= len(title) <= MAX_TITLE:
        await message.answer(texts.title_invalid(MAX_TITLE), reply_markup=kb.pack_back_kb(data["pack_id"]))
        return
    await state.update_data(new_title=title)
    await message.answer(texts.rename_confirm(data.get("old_title", ""), title),
                         reply_markup=kb.confirm_kb(f"pk:renok:{data['pack_id']}", f"menu:pack:{data['pack_id']}"))


@router.callback_query(F.data.regexp(r"^pk:renok:\d+$"))
async def do_rename(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()
    title = (await state.get_data()).get("new_title")
    if not title:
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)
        return

    async def action(backend):
        await backend.rename(pack.short_name, title)

    if await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action):
        await state.clear()
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)


@router.callback_query(F.data.regexp(r"^pk:link:\d+$"))
async def ask_link(cb: CallbackQuery, state: FSMContext, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await state.clear()
    await state.set_state(PackForm.link)
    await state.update_data(pack_id=pack.id)
    await safe_edit(cb.message, texts.link_input_pack(pack.kind, pack.short_name), kb.pack_back_kb(pack.id))
    await cb.answer()


@router.message(PackForm.link, F.text, F.func(not_command))
async def on_link(message: Message, state: FSMContext, bot: Bot, db: Db, config: Config):
    uid = message.from_user.id
    pack = await db.get_pack((await state.get_data())["pack_id"], uid)
    if pack is None:
        await state.clear()
        await message.answer(texts.pick_section())
        return
    back_kb = kb.pack_back_kb(pack.id)
    try:
        backend = await ops.backend_for_pack(db, config, bot, pack)
        name = await ops.validate_name(backend, uid, message.text)
    except CloneError as e:
        await message.answer(texts.clone_failed(str(e)), reply_markup=back_kb)
        return
    await state.update_data(new_name=name)
    await message.answer(texts.link_confirm(pack.kind, pack.short_name, name),
                         reply_markup=kb.confirm_kb(f"pk:linkok:{pack.id}", f"menu:pack:{pack.id}", danger=True))


@router.callback_query(F.data.regexp(r"^pk:linkok:\d+$"))
async def do_link(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()
    new_name = (await state.get_data()).get("new_name")
    if not new_name:
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)
        return
    old_deleted = []

    async def action(backend):
        old_deleted.append(await backend.relink(pack.short_name, new_name, pack.count))
        await db.set_pack_short_name(pack.id, pack.user_id, new_name)
        await ops.refresh_pack(db, backend, replace(pack, short_name=new_name))

    if await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action, refresh=False):
        await state.clear()
        note = "" if old_deleted[0] else texts.link_old_left(pack.kind, pack.short_name)
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id, note)


@router.callback_query(F.data.regexp(r"^pk:del:\d+$"))
async def ask_delete(cb: CallbackQuery, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await safe_edit(cb.message, texts.delete_confirm(pack.title, pack.count),
                    kb.confirm_kb(f"pk:delok:{pack.id}", f"menu:pack:{pack.id}", danger=True))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pk:delok:\d+$"))
async def do_delete(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()

    async def action(backend):
        await backend.delete(pack.short_name)

    if await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action, refresh=False):
        await db.delete_pack(pack.id, pack.user_id)
        await state.clear()
        await safe_edit(cb.message, texts.pack_deleted(pack.title), kb.after_delete_kb(pack.kind))


@router.callback_query(F.data.regexp(r"^pk:rm:\d+:\d+$"))
async def ask_remove(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()
    page = _pack_id(cb.data, 3) or 0
    stickers: list = []

    async def action(backend):
        stickers.extend((await backend.fetch(pack.short_name)).stickers)

    if not await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action, refresh=False):
        return
    page = min(page, max(0, (len(stickers) - 1) // PREVIEW_PAGE))
    start = page * PREVIEW_PAGE
    preview = texts.preview_lines(stickers, start, start + PREVIEW_PAGE)
    await state.clear()
    await state.set_state(PackForm.remove)
    await state.update_data(pack_id=pack.id, preview=[(s.custom_emoji_id, s.emoji) for s in stickers])
    await safe_edit(cb.message, texts.remove_prompt(pack.title, preview),
                    kb.remove_kb(pack.id, page, len(stickers), PREVIEW_PAGE))


@router.message(PackForm.remove, F.text, F.func(not_command))
async def on_remove(message: Message, state: FSMContext, db: Db):
    data = await state.get_data()
    pack = await db.get_pack(data["pack_id"], message.from_user.id)
    if pack is None:
        await state.clear()
        await message.answer(texts.pick_section())
        return
    back_kb = kb.pack_back_kb(pack.id)
    try:
        positions = parse_ranges(message.text)
    except RangeError as e:
        await message.answer(texts.clone_failed(str(e)), reply_markup=back_kb)
        return
    bad = [p for p in positions if p > pack.count]
    if bad:
        await message.answer(texts.remove_out_of_range(bad, pack.count), reply_markup=back_kb)
        return
    if len(positions) >= pack.count:
        await message.answer(texts.remove_all_refused(), reply_markup=back_kb)
        return
    await state.update_data(positions=positions)
    chosen = [SimpleNamespace(custom_emoji_id=c, emoji=e)
              for i, (c, e) in enumerate(data.get("preview") or [], 1) if i in positions and c]
    preview = texts.preview_lines(chosen, 0, PREVIEW_PAGE, numbered=False)
    await message.answer(texts.remove_confirm(positions, preview),
                         reply_markup=kb.confirm_kb(f"pk:rmok:{pack.id}", f"menu:pack:{pack.id}", danger=True))


@router.callback_query(F.data.regexp(r"^pk:rmok:\d+$"))
async def do_remove(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()
    positions = (await state.get_data()).get("positions")
    if not positions:
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)
        return

    async def action(backend):
        await backend.remove(pack.short_name, positions, make_progress(cb.message, "Удаляю"), pack.count)

    if await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action):
        await state.clear()
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)


@router.callback_query(F.data.regexp(r"^pk:add:\d+$"))
async def ask_add(cb: CallbackQuery, state: FSMContext, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await state.clear()
    await state.set_state(PackForm.add)
    await state.update_data(pack_id=pack.id, items=[])
    await safe_edit(cb.message, texts.add_prompt_pack(pack.title), kb.add_kb(pack.id, False))
    await cb.answer()


@router.message(PackForm.add, F.func(not_command))
async def on_add(message: Message, state: FSMContext, bot: Bot, db: Db):
    data = await state.get_data()
    pack = await db.get_pack(data["pack_id"], message.from_user.id)
    if pack is None:
        await state.clear()
        await message.answer(texts.pick_section())
        return
    limit = LIMITS_BY_KIND[pack.kind]
    got = await collect_message(bot, message, pack.kind)
    items, skipped = merge_items(data.get("items") or [], got.items, limit - pack.count)
    await state.update_data(items=items)
    await message.answer(texts.add_confirm(len(items), pack.count + len(items), limit, collect_note(got, skipped)),
                         reply_markup=kb.add_kb(pack.id, bool(items)))


@router.callback_query(F.data.regexp(r"^pk:addclr:\d+$"))
async def clear_add(cb: CallbackQuery, state: FSMContext, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await state.update_data(items=[])
    await safe_edit(cb.message, texts.add_prompt_pack(pack.title), kb.add_kb(pack.id, False))
    await cb.answer()


@router.callback_query(F.data.regexp(r"^pk:addok:\d+$"))
async def do_add(cb: CallbackQuery, state: FSMContext, bot: Bot, db: Db, config: Config):
    pack = await _pack(cb, db)
    if pack is None:
        return
    await cb.answer()
    items = (await state.get_data()).get("items") or []
    if not items:
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)
        return

    async def action(backend):
        await backend.add(pack.short_name, pack.kind, items, make_progress(cb.message, "Добавляю"))

    if await run_op(cb.message, cb.from_user.id, bot, db, config, pack, action):
        await state.clear()
        await show_pack(cb.message, db, config, bot, cb.from_user.id, pack.id)


_posting: set[int] = set()


@router.callback_query(F.data.regexp(r"^pk:post:\d+$"))
async def make_post(cb: CallbackQuery, bot: Bot, db: Db):
    pack = await _pack(cb, db)
    if pack is None:
        return
    uid, chat = cb.from_user.id, cb.message.chat.id
    if uid in _posting:
        await cb.answer(texts.op_busy())
        return
    _posting.add(uid)
    await cb.answer()
    status = await bot.send_message(chat, texts.post_wait())
    try:
        stickers = await post.load_stickers(bot, pack.short_name)
        if not stickers:
            raise CloneError("пак пуст")
        image = await post.build_image(bot, stickers)
        link = pack_link(pack.short_name, TYPE_BY_KIND[pack.kind])
        await bot.send_photo(chat, BufferedInputFile(image, f"{pack.short_name}.png"),
                             caption=post.post_text(link, stickers))
        await bot.send_message(chat, texts.post_ready(), reply_markup=kb.pack_back_kb(pack.id))
        with suppress(TelegramBadRequest):
            await status.delete()
    except CloneError as e:
        await safe_edit(status, texts.post_failed(str(e)), kb.pack_back_kb(pack.id))
    except Exception as e:
        log.warning("post failed for user %s: %s", uid, type(e).__name__)
        await safe_edit(status, texts.post_failed(type(e).__name__), kb.pack_back_kb(pack.id))
    finally:
        _posting.discard(uid)
