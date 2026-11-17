import random
import re
import string

MAX_LEN = 64
_LINK_PREFIX = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:addemoji|addstickers)/", re.I)
_SHORT_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$", re.A)


def clean_short_name(raw: str, tail: str = "") -> str | None:
    name = _LINK_PREFIX.sub("", (raw or "").strip()).strip("/")
    if tail and not name.lower().endswith(tail.lower()):
        name += tail
    if not _SHORT_NAME.match(name) or "__" in name or name.endswith("_") or len(name) > MAX_LEN:
        return None
    return name


_HANDLE_RE = re.compile(r"(?:@\w+|(?:https?://)?t\.me/\S+)$")


def build_title(original: str, worker_username: str) -> str:
    suffix = f" by @{worker_username}"
    room = MAX_LEN - len(suffix)
    base = original.strip()
    if len(base) <= room:
        return base + suffix
    m = _HANDLE_RE.search(base)
    if m:
        token = m.group()
        head = base[:m.start()].rstrip()
        if len(token) <= room:
            head_room = room - len(token) - 1
            head = head[:max(head_room, 0)].rstrip()
            return (head + " " + token if head else token) + suffix
        base = head
    return base[:room].rstrip() + suffix


def build_short_name(original_name: str, worker_username: str,
                     taken: set[str] | frozenset[str] = frozenset()) -> str:
    tail = f"_by_{worker_username}"
    base = original_name
    i = base.lower().rfind("_by_")
    if i > 0:
        base = base[:i]
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if not base:
        base = "emoji"
    if not base[0].isalpha():
        base = "e" + base

    def make(suffix: str = "") -> str:
        room = MAX_LEN - len(tail) - len(suffix)
        return base[:room].rstrip("_") + suffix + tail

    candidate = make()
    n = 2
    while candidate in taken:
        candidate = make(f"_{n}")
        n += 1
        if n > 1000:
            raise RuntimeError("не удалось подобрать свободное имя набора")
    return candidate


def random_suffix(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def build_user_short_name(original_name: str, suffix=random_suffix) -> str:
    base = original_name
    i = base.lower().rfind("_by_")
    if i > 0:
        base = base[:i]
    base = re.sub(r"[^A-Za-z0-9_]", "_", base)
    base = re.sub(r"_+", "_", base).strip("_") or "emoji"
    if not base[0].isalpha():
        base = "e" + base
    tail = "_" + suffix()
    return base[:MAX_LEN - len(tail)].rstrip("_") + tail
