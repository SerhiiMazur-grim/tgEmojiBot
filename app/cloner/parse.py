import re
from dataclasses import dataclass

MAX_POSITION = 1000
_KINDS = {"addemoji": "emoji", "addstickers": "sticker"}
_LINK = re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/(addemoji|addstickers)/([A-Za-z0-9_]+)"
                   r"|tg://(addemoji|addstickers)\?set=([A-Za-z0-9_]+)", re.I)
_LIST = r"\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*"
_AFTER_LINK = re.compile(r"[ \t]*(" + _LIST + r")(?![\w/.])", re.A)
_FULL = re.compile(r"^" + _LIST + r"$", re.A)


class RangeError(Exception):
    pass


@dataclass(frozen=True)
class LinkItem:
    kind: str
    name: str
    positions: list[int] | None


@dataclass(frozen=True)
class EmojiItem:
    custom_emoji_id: str


def parse_ranges(text: str) -> list[int]:
    value = (text or "").strip()
    if not _FULL.match(value):
        raise RangeError("Не понял номера. Пример: 20-30 или 4, 6-8")
    out: set[int] = set()
    for part in value.split(","):
        bounds = [int(x) for x in part.split("-")]
        low, high = bounds[0], bounds[-1]
        if low < 1 or high < low or high > MAX_POSITION:
            raise RangeError(f"Неверный диапазон: {part.strip()}")
        out.update(range(low, high + 1))
    return sorted(out)


def _py_offset(text: str, utf16_offset: int) -> int:
    return len(text.encode("utf-16-le")[: utf16_offset * 2].decode("utf-16-le", "ignore"))


def _link(match: re.Match, positions) -> LinkItem:
    kind = (match.group(1) or match.group(3)).lower()
    return LinkItem(_KINDS[kind], match.group(2) or match.group(4), positions)


def parse_items(text: str | None, entities) -> list[LinkItem | EmojiItem]:
    text = text or ""
    found: list[tuple[int, LinkItem | EmojiItem]] = []
    for m in _LINK.finditer(text):
        after = _AFTER_LINK.match(text, m.end())
        positions = None
        if after:
            try:
                positions = parse_ranges(after.group(1))
            except RangeError:
                positions = None
        found.append((m.start(), _link(m, positions)))
    for e in entities or []:
        if e.type == "custom_emoji":
            found.append((_py_offset(text, e.offset), EmojiItem(e.custom_emoji_id)))
        elif e.type == "text_link" and e.url:
            m = _LINK.search(e.url)
            if m:
                found.append((_py_offset(text, e.offset), _link(m, None)))
    found.sort(key=lambda pair: pair[0])
    return [item for _, item in found]
