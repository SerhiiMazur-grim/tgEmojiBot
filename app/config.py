import os
from dataclasses import dataclass, field
from typing import Mapping

from cryptography.fernet import Fernet


@dataclass(frozen=True)
class Config:
    bot_token: str = field(repr=False)
    admin_ids: frozenset[int]
    db_path: str
    proxy: str | None
    tg_api_id: int | None = None
    tg_api_hash: str | None = field(default=None, repr=False)
    session_key: str | None = field(default=None, repr=False)
    tg_session: str | None = field(default=None, repr=False)

    @property
    def telethon_enabled(self) -> bool:
        return bool(self.tg_api_id and self.tg_api_hash and self.session_key)


def load_config(env: Mapping[str, str] = os.environ) -> Config:
    token = env.get("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN не задан")
    admins = frozenset(int(x) for x in env.get("ADMIN_IDS", "").replace(" ", "").split(",") if x)
    if not admins:
        raise RuntimeError("ADMIN_IDS не задан")
    proxy = env.get("PROXY_URL", "").strip() or None
    raw_id = env.get("TG_API_ID", "").strip()
    try:
        api_id = int(raw_id) if raw_id else None
    except ValueError:
        raise RuntimeError("TG_API_ID должен быть числом") from None
    api_hash = env.get("TG_API_HASH", "").strip() or None
    session_key = env.get("SESSION_KEY", "").strip() or None
    if session_key:
        try:
            Fernet(session_key.encode())
        except ValueError:
            raise RuntimeError("SESSION_KEY: неверный ключ Fernet") from None
    tg_session = env.get("TG_SESSION", "").strip() or None
    return Config(token, admins, env.get("DB_PATH", "bot.db"), proxy, api_id, api_hash, session_key, tg_session)
