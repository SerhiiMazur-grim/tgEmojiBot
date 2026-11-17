from dataclasses import dataclass, field

from aiogram.exceptions import TelegramBadRequest

from app.cloner.parse import EmojiItem, parse_items
from app.cloner.service import DEFAULT_EMOJI, _retry, sticker_format

LIMITS_BY_KIND = {"emoji": 200, "sticker": 120}
TYPE_BY_KIND = {"emoji": "custom_emoji", "sticker": "regular"}


@dataclass(frozen=True)
class SourceItem:
    file_id: str
    unique_id: str
    emoji: str
    fmt: str
    repaint: bool
    preview_id: str | None
    source_set: str | None = None
    position: int | None = None


@dataclass
class Collected:
    items: list[SourceItem] = field(default_factory=list)
    title: str | None = None
    source: str | None = None
    wrong_kind: int = 0
    not_found: int = 0
    bad_positions: int = 0


def _item(st, source_set: str | None = None, position: int | None = None) -> SourceItem:
    return SourceItem(st.file_id, st.file_unique_id, st.emoji or DEFAULT_EMOJI, sticker_format(st),
                      bool(getattr(st, "needs_repainting", False)), getattr(st, "custom_emoji_id", None),
                      source_set or getattr(st, "set_name", None), position)


async def _set(bot, name):
    try:
        return await _retry(lambda: bot.get_sticker_set(name))
    except TelegramBadRequest:
        return None


def _remember(out: Collected, title: str | None, source: str | None) -> None:
    if out.title is None:
        out.title, out.source = title, source


async def _add_link(bot, link, kind: str, out: Collected) -> None:
    src = await _set(bot, link.name)
    if src is None:
        out.not_found += 1
        return
    if link.kind != kind or src.sticker_type != TYPE_BY_KIND[kind]:
        out.wrong_kind += 1
        return
    stickers = list(src.stickers)
    if link.positions is None:
        chosen = list(range(len(stickers)))
    else:
        chosen = [p - 1 for p in link.positions if p <= len(stickers)]
        out.bad_positions += sum(1 for p in link.positions if p > len(stickers))
    out.items.extend(_item(stickers[i], link.name, i) for i in chosen)
    _remember(out, src.title, link.name)


async def _add_emoji(bot, ids: list[str], kind: str, out: Collected) -> None:
    if kind != "emoji":
        out.wrong_kind += len(ids)
        return
    found = {}
    for i in range(0, len(ids), 200):
        try:
            for st in await _retry(lambda chunk=ids[i:i + 200]: bot.get_custom_emoji_stickers(chunk)):
                found[st.custom_emoji_id] = st
        except TelegramBadRequest:
            pass
    for cid in ids:
        st = found.get(cid)
        if st is None:
            out.not_found += 1
            continue
        out.items.append(_item(st))
        if out.title is None and st.set_name:
            src = await _set(bot, st.set_name)
            if src is not None:
                _remember(out, src.title, st.set_name)


async def collect_message(bot, message, kind: str) -> Collected:
    out = Collected()
    sticker = getattr(message, "sticker", None)
    if sticker is not None:
        if sticker.type != TYPE_BY_KIND[kind]:
            out.wrong_kind += 1
            return out
        position = None
        if sticker.set_name:
            src = await _set(bot, sticker.set_name)
            if src is not None:
                _remember(out, src.title, sticker.set_name)
                position = next((i for i, s in enumerate(src.stickers)
                                 if s.file_unique_id == sticker.file_unique_id), None)
        out.items.append(_item(sticker, sticker.set_name, position))
        return out
    text = message.text or message.caption
    entities = message.entities or message.caption_entities
    pending: list[str] = []
    for parsed in parse_items(text, entities):
        if isinstance(parsed, EmojiItem):
            pending.append(parsed.custom_emoji_id)
            continue
        if pending:
            await _add_emoji(bot, pending, kind, out)
            pending = []
        await _add_link(bot, parsed, kind, out)
    if pending:
        await _add_emoji(bot, pending, kind, out)
    return out


def merge_items(existing: list[SourceItem], new: list[SourceItem], limit: int) -> tuple[list[SourceItem], int]:
    merged = list(existing)
    seen = {i.unique_id for i in merged}
    skipped = 0
    for item in new:
        if item.unique_id in seen:
            continue
        if len(merged) >= limit:
            skipped += 1
            continue
        merged.append(item)
        seen.add(item.unique_id)
    return merged, skipped
