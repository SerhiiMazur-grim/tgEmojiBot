from abc import ABC, abstractmethod
from dataclasses import dataclass

from aiogram.types import InlineKeyboardMarkup

from app.config import Config
from app.db import Db
from app.ui import kb, texts

PACKS_PAGE = 8
PANEL_PAGE = 8
KINDS = ("emoji", "sticker")


@dataclass(frozen=True)
class View:
    text: str
    markup: InlineKeyboardMarkup


@dataclass(frozen=True)
class Ctx:
    db: Db
    config: Config
    bot: object
    user_id: int

    @property
    def is_admin(self) -> bool:
        return self.user_id in self.config.admin_ids


class Page(ABC):
    @property
    @abstractmethod
    def key(self) -> str: ...

    @abstractmethod
    async def render(self, ctx: Ctx) -> View | None:
        pass


class HomePage(Page):
    key = "home"

    async def render(self, ctx: Ctx) -> View:
        return View(texts.home_text(), kb.main_menu(ctx.is_admin))


class PacksPage(Page):
    def __init__(self, kind: str, page: int):
        self.kind, self.page = kind, page

    @property
    def key(self) -> str:
        return f"packs:{self.kind}:{self.page}"

    async def render(self, ctx: Ctx) -> View:
        _, total = await ctx.db.list_packs(ctx.user_id, self.kind, 0, 1)
        page = min(self.page, max(0, (total - 1) // PACKS_PAGE))
        items, total = await ctx.db.list_packs(ctx.user_id, self.kind, page, PACKS_PAGE)
        return View(texts.packs_text(self.kind, total), kb.packs_kb(items, self.kind, page, total, PACKS_PAGE))


class PackPage(Page):
    def __init__(self, pack_id: int):
        self.pack_id = pack_id

    @property
    def key(self) -> str:
        return f"pack:{self.pack_id}"

    async def render(self, ctx: Ctx) -> View | None:
        pack = await ctx.db.get_pack(self.pack_id, ctx.user_id)
        return View(texts.pack_text(pack), kb.pack_kb(pack)) if pack else None


class PanelPage(Page):
    key = "panel"

    async def render(self, ctx: Ctx) -> View:
        return View(texts.panel_text(), kb.panel_kb(await ctx.db.count_pending()))


class AccountPage(Page):
    key = "account"

    def __init__(self, status: str = ""):
        self.status = status

    async def render(self, ctx: Ctx) -> View:
        session = await ctx.db.shared_session()
        return View(texts.account_text(session, self.status), kb.account_kb(session.id if session else None))


class BackendPage(Page):
    key = "backend"

    async def render(self, ctx: Ctx) -> View:
        choice = await ctx.db.get_setting("create_backend") or "telethon"
        workers = await ctx.db.list_workers(ctx.user_id)
        return View(texts.backend_text(), kb.backend_kb(workers, choice, await ctx.db.shared_session() is not None))


class StatsPage(Page):
    key = "stats"

    async def render(self, ctx: Ctx) -> View:
        return View(texts.stats_text(await ctx.db.stats()), kb.stats_kb())


class RequestsPage(Page):
    def __init__(self, page: int):
        self.page = page

    @property
    def key(self) -> str:
        return f"requests:{self.page}"

    async def render(self, ctx: Ctx) -> View:
        total = await ctx.db.count_pending()
        page = min(self.page, max(0, (total - 1) // PANEL_PAGE))
        items, total = await ctx.db.list_pending(page, PANEL_PAGE)
        return View(texts.requests_text(items, total), kb.requests_kb(items, page, total, PANEL_PAGE))


class UsersPage(Page):
    def __init__(self, page: int):
        self.page = page

    @property
    def key(self) -> str:
        return f"users:{self.page}"

    async def render(self, ctx: Ctx) -> View:
        _, total = await ctx.db.list_users(0, 1)
        page = min(self.page, max(0, (total - 1) // PANEL_PAGE))
        items, total = await ctx.db.list_users(page, PANEL_PAGE)
        return View(texts.users_text(items, total), kb.users_kb(items, page, total, PANEL_PAGE))


class UserPage(Page):
    def __init__(self, uid: int):
        self.uid = uid

    @property
    def key(self) -> str:
        return f"user:{self.uid}"

    async def render(self, ctx: Ctx) -> View | None:
        info = await ctx.db.get_user(self.uid)
        if info is None:
            return None
        blocked, allowed = await ctx.db.is_blocked(self.uid), await ctx.db.is_allowed(self.uid)
        return View(texts.user_card(info, await ctx.db.user_pack_count(self.uid), blocked, allowed),
                    kb.user_card_kb(self.uid, blocked, allowed))


def _int(value: str) -> int | None:
    return int(value) if value.isascii() and value.isdigit() and len(value) <= 12 else None


def resolve_page(key: str) -> Page | None:
    parts = key.split(":")
    if parts == ["home"]:
        return HomePage()
    if len(parts) == 3 and parts[0] == "packs" and parts[1] in KINDS and _int(parts[2]) is not None:
        return PacksPage(parts[1], _int(parts[2]))
    if len(parts) == 2 and parts[0] == "pack" and _int(parts[1]) is not None:
        return PackPage(_int(parts[1]))
    return None
