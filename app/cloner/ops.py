import logging

from app.cloner import mtproto
from app.cloner.backend import BotBackend, PackBackend, TelethonBackend
from app.cloner.service import CloneError
from app.config import Config
from app.crypto import encrypt
from app.db import Db, Pack

log = logging.getLogger(__name__)

SETTING = "create_backend"
NOT_CONNECTED = "Сервис создания паков сейчас не подключён. Сообщите администратору."
BAD_NAME = ("Ссылка не подходит. Нужны латинские буквы, цифры и _, начало с буквы, "
            "без двух _ подряд и без _ в конце. Обычно не короче 5 символов.")
_busy_users: set[int] = set()
_busy_accounts: set[tuple[str, int]] = set()


class NoBackend(CloneError):
    pass


async def _shared(db: Db, config: Config, bot) -> PackBackend:
    session = await db.shared_session() if config.telethon_enabled else None
    if session is None:
        raise NoBackend(NOT_CONNECTED)
    return TelethonBackend(bot, session, config)


async def backend_for_new(db: Db, config: Config, bot, uid: int) -> PackBackend:
    if uid in config.admin_ids:
        choice = await db.get_setting(SETTING) or "telethon"
        if choice.startswith("bot:"):
            for w in await db.list_workers(uid):
                if str(w.id) == choice[4:]:
                    return BotBackend(bot, w, uid, config)
    return await _shared(db, config, bot)


async def backend_for_pack(db: Db, config: Config, bot, pack: Pack) -> PackBackend:
    if pack.backend == "bot":
        for w in await db.list_workers(pack.user_id):
            if w.id == pack.backend_ref:
                return BotBackend(bot, w, pack.user_id, config)
        raise NoBackend("Бот, создавший этот пак, отключён.")
    session = await db.get_telethon(pack.backend_ref) if config.telethon_enabled else None
    if session is None:
        raise NoBackend(NOT_CONNECTED)
    return TelethonBackend(bot, session, config)


def try_claim(uid: int, backend: PackBackend) -> bool:
    account = (backend.name, backend.ref)
    if uid in _busy_users or account in _busy_accounts:
        return False
    _busy_users.add(uid)
    _busy_accounts.add(account)
    return True


def release(uid: int, backend: PackBackend) -> None:
    _busy_users.discard(uid)
    _busy_accounts.discard((backend.name, backend.ref))


async def validate_name(backend: PackBackend, uid: int, raw: str) -> str:
    name = backend.normalize_name(raw)
    if name is None:
        raise CloneError(BAD_NAME)
    if not try_claim(uid, backend):
        raise CloneError("Операция уже идёт. Попробуйте через пару минут.")
    try:
        async with backend:
            status = await backend.name_status(name)
    finally:
        release(uid, backend)
    if status == "taken":
        raise CloneError("Эта ссылка уже занята. Пришлите другую.")
    if status == "invalid":
        raise CloneError(BAD_NAME)
    return name


async def refresh_pack(db: Db, backend: PackBackend, pack: Pack) -> None:
    remote = await backend.fetch(pack.short_name)
    first = remote.stickers[0].custom_emoji_id if remote.stickers else None
    await db.update_pack(pack.id, pack.user_id, title=remote.title, first_emoji_id=first, count=len(remote.stickers))


async def sync_env_session(db: Db, config: Config) -> None:
    if not (config.tg_session and config.telethon_enabled):
        return
    try:
        info = await mtproto.check_session(config.tg_session, config)
    except mtproto.SessionError as e:
        log.warning("TG_SESSION не подошла: %s", e)
        return
    keep = await db.add_telethon(info.account_id, info.label, encrypt(config.session_key, config.tg_session),
                                 min(config.admin_ids))
    for session in await db.list_telethon():
        if session.id != keep:
            await db.delete_telethon(session.id)
