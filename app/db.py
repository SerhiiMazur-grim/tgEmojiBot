import asyncio
from dataclasses import dataclass, field

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS whitelist(
    user_id INTEGER PRIMARY KEY,
    approved_by INTEGER NOT NULL,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS access_requests(
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS worker_bots(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id INTEGER NOT NULL,
    token TEXT NOT NULL,
    username TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 0,
    UNIQUE(owner_id, username)
);
CREATE TABLE IF NOT EXISTS users(
    user_id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    username TEXT,
    first_seen TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS blocked_users(
    user_id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS clone_log(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    source_set TEXT NOT NULL,
    new_name TEXT NOT NULL,
    count INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS telethon_sessions(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id INTEGER NOT NULL UNIQUE,
    label TEXT NOT NULL,
    session_enc BLOB NOT NULL,
    added_by INTEGER NOT NULL,
    ts TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS user_prefs(
    user_id INTEGER PRIMARY KEY,
    active_telethon_id INTEGER
);
CREATE TABLE IF NOT EXISTS packs(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    short_name TEXT NOT NULL,
    title TEXT NOT NULL,
    first_emoji_id TEXT,
    count INTEGER NOT NULL,
    backend TEXT NOT NULL,
    backend_ref INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS app_settings(
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True)
class Worker:
    id: int
    owner_id: int
    token: str = field(repr=False)
    username: str
    is_active: bool
    kind: str = "token"


def _worker(row: aiosqlite.Row) -> Worker:
    return Worker(row["id"], row["owner_id"], row["token"], row["username"], bool(row["is_active"]), row["kind"])


def _session(row: aiosqlite.Row) -> "TelethonSession":
    return TelethonSession(row["id"], row["account_id"], row["label"], bytes(row["session_enc"]), row["added_by"])


@dataclass(frozen=True)
class UserInfo:
    user_id: int
    name: str
    username: str | None
    first_seen: str


@dataclass(frozen=True)
class PendingRequest:
    user_id: int
    name: str
    ts: str


@dataclass(frozen=True)
class TelethonSession:
    id: int
    account_id: int
    label: str
    session_enc: bytes = field(repr=False)
    added_by: int


@dataclass(frozen=True)
class Stats:
    allowed: int
    pending: int
    blocked: int
    workers_token: int
    workers_managed: int
    telethon: int
    clones_total: int
    clones_7d: int
    clones_by_kind: dict[str, int]


@dataclass(frozen=True)
class Pack:
    id: int
    user_id: int
    kind: str
    short_name: str
    title: str
    first_emoji_id: str | None
    count: int
    backend: str
    backend_ref: int
    created_at: str
    updated_at: str


def _pack(row: aiosqlite.Row) -> Pack:
    return Pack(row["id"], row["user_id"], row["kind"], row["short_name"], row["title"], row["first_emoji_id"],
                row["count"], row["backend"], row["backend_ref"], row["created_at"], row["updated_at"])


async def _ensure_column(conn: aiosqlite.Connection, table: str, column: str, ddl: str) -> None:
    async with conn.execute(f"PRAGMA table_info({table})") as cur:
        cols = {r["name"] for r in await cur.fetchall()}
    if column not in cols:
        await conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


class Db:
    def __init__(self, path: str):
        self._path = path
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def c(self) -> aiosqlite.Connection:
        assert self._conn is not None, "Db.open() не вызван"
        return self._conn

    async def open(self) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await _ensure_column(self._conn, "worker_bots", "kind", "kind TEXT NOT NULL DEFAULT 'token'")
        await self._conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def add_pack(self, user_id: int, kind: str, short_name: str, title: str, first_emoji_id: str | None,
                       count: int, backend: str, backend_ref: int) -> int:
        cur = await self.c.execute(
            "INSERT INTO packs(user_id, kind, short_name, title, first_emoji_id, count, backend, backend_ref) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (user_id, kind, short_name, title, first_emoji_id, count, backend, backend_ref))
        await self.c.commit()
        return cur.lastrowid

    async def get_pack(self, pack_id: int, user_id: int) -> Pack | None:
        async with self.c.execute("SELECT * FROM packs WHERE id=? AND user_id=?", (pack_id, user_id)) as cur:
            row = await cur.fetchone()
        return _pack(row) if row else None

    async def list_packs(self, user_id: int, kind: str, page: int, size: int) -> tuple[list[Pack], int]:
        async with self.c.execute("SELECT COUNT(*) FROM packs WHERE user_id=? AND kind=?", (user_id, kind)) as cur:
            total = (await cur.fetchone())[0]
        page, size = max(page, 0), max(size, 1)
        async with self.c.execute(
                "SELECT * FROM packs WHERE user_id=? AND kind=? ORDER BY updated_at DESC, id DESC LIMIT ? OFFSET ?",
                (user_id, kind, size, page * size)) as cur:
            return [_pack(r) for r in await cur.fetchall()], total

    async def update_pack(self, pack_id: int, user_id: int, *, title: str, first_emoji_id: str | None,
                          count: int) -> None:
        await self.c.execute(
            "UPDATE packs SET title=?, first_emoji_id=?, count=?, updated_at=CURRENT_TIMESTAMP "
            "WHERE id=? AND user_id=?", (title, first_emoji_id, count, pack_id, user_id))
        await self.c.commit()

    async def set_pack_short_name(self, pack_id: int, user_id: int, short_name: str) -> None:
        await self.c.execute("UPDATE packs SET short_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
                             (short_name, pack_id, user_id))
        await self.c.commit()

    async def delete_pack(self, pack_id: int, user_id: int) -> None:
        await self.c.execute("DELETE FROM packs WHERE id=? AND user_id=?", (pack_id, user_id))
        await self.c.commit()

    async def get_setting(self, key: str) -> str | None:
        async with self.c.execute("SELECT value FROM app_settings WHERE key=?", (key,)) as cur:
            row = await cur.fetchone()
        return row["value"] if row else None

    async def set_setting(self, key: str, value: str) -> None:
        await self.c.execute("INSERT OR REPLACE INTO app_settings(key, value) VALUES(?,?)", (key, value))
        await self.c.commit()

    async def shared_session(self) -> TelethonSession | None:
        async with self.c.execute("SELECT * FROM telethon_sessions ORDER BY id LIMIT 1") as cur:
            row = await cur.fetchone()
        return _session(row) if row else None

    async def is_allowed(self, user_id: int) -> bool:
        async with self.c.execute("SELECT 1 FROM whitelist WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone() is not None

    async def allow(self, user_id: int, approved_by: int) -> None:
        await self.c.execute("INSERT OR REPLACE INTO whitelist(user_id, approved_by) VALUES(?,?)",
                             (user_id, approved_by))
        await self.c.commit()

    async def create_request(self, user_id: int, name: str) -> bool:
        cur = await self.c.execute("INSERT OR IGNORE INTO access_requests(user_id, name) VALUES(?,?)",
                                   (user_id, name))
        await self.c.commit()
        return cur.rowcount == 1

    async def request_status(self, user_id: int) -> str | None:
        async with self.c.execute("SELECT status FROM access_requests WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
            return row["status"] if row else None

    async def resolve_request(self, user_id: int, status: str) -> None:
        await self.c.execute("UPDATE access_requests SET status=? WHERE user_id=?", (status, user_id))
        await self.c.commit()

    async def resolve_pending(self, user_id: int, status: str) -> bool:
        async with self._lock:
            cur = await self.c.execute("UPDATE access_requests SET status=? WHERE user_id=? AND status='pending'",
                                       (status, user_id))
            await self.c.commit()
            return cur.rowcount > 0

    async def delete_request(self, user_id: int) -> None:
        await self.c.execute("DELETE FROM access_requests WHERE user_id=?", (user_id,))
        await self.c.commit()

    async def upsert_user(self, user_id: int, name: str, username: str | None) -> None:
        await self.c.execute(
            "INSERT INTO users(user_id, name, username) VALUES(?,?,?) "
            "ON CONFLICT(user_id) DO UPDATE SET name=excluded.name, username=excluded.username",
            (user_id, name, username))
        await self.c.commit()

    async def get_user(self, user_id: int) -> UserInfo | None:
        sql = (
            "SELECT x.user_id AS user_id, COALESCE(u.name, r.name, CAST(x.user_id AS TEXT)) AS name, "
            "u.username AS username, COALESCE(u.first_seen, w.ts, r.ts) AS first_seen "
            "FROM (SELECT user_id FROM users UNION SELECT user_id FROM whitelist "
            "UNION SELECT user_id FROM access_requests) x "
            "LEFT JOIN users u ON u.user_id=x.user_id "
            "LEFT JOIN whitelist w ON w.user_id=x.user_id "
            "LEFT JOIN access_requests r ON r.user_id=x.user_id WHERE x.user_id=?")
        async with self.c.execute(sql, (user_id,)) as cur:
            row = await cur.fetchone()
        return UserInfo(row["user_id"], row["name"], row["username"], row["first_seen"]) if row else None

    async def find_user(self, query: str) -> UserInfo | None:
        q = query.strip()
        if q.isascii() and q.isdigit() and len(q) <= 18:
            return await self.get_user(int(q))
        if q.startswith("@") and len(q) > 1:
            async with self.c.execute(
                    "SELECT user_id FROM users WHERE lower(username)=lower(?) "
                    "ORDER BY first_seen DESC, user_id DESC LIMIT 1", (q[1:],)) as cur:
                row = await cur.fetchone()
            return await self.get_user(row["user_id"]) if row else None
        return None

    async def list_users(self, page: int, size: int) -> tuple[list[UserInfo], int]:
        async with self.c.execute("SELECT COUNT(*) FROM whitelist") as cur:
            total = (await cur.fetchone())[0]
        sql = (
            "SELECT w.user_id AS user_id, COALESCE(u.name, r.name, CAST(w.user_id AS TEXT)) AS name, "
            "u.username AS username, COALESCE(u.first_seen, w.ts) AS first_seen "
            "FROM whitelist w LEFT JOIN users u ON u.user_id=w.user_id "
            "LEFT JOIN access_requests r ON r.user_id=w.user_id "
            "ORDER BY w.ts DESC, w.user_id LIMIT ? OFFSET ?")
        page, size = max(page, 0), max(size, 1)
        async with self.c.execute(sql, (size, page * size)) as cur:
            rows = await cur.fetchall()
        return [UserInfo(r["user_id"], r["name"], r["username"], r["first_seen"]) for r in rows], total

    async def revoke_access(self, user_id: int) -> None:
        async with self._lock:
            await self.c.execute("DELETE FROM whitelist WHERE user_id=?", (user_id,))
            await self.c.execute("DELETE FROM access_requests WHERE user_id=?", (user_id,))
            await self.c.commit()

    async def block(self, user_id: int) -> None:
        await self.c.execute("INSERT OR IGNORE INTO blocked_users(user_id) VALUES(?)", (user_id,))
        await self.c.commit()

    async def unblock(self, user_id: int) -> None:
        await self.c.execute("DELETE FROM blocked_users WHERE user_id=?", (user_id,))
        await self.c.commit()

    async def is_blocked(self, user_id: int) -> bool:
        async with self.c.execute("SELECT 1 FROM blocked_users WHERE user_id=?", (user_id,)) as cur:
            return await cur.fetchone() is not None

    async def count_pending(self) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM access_requests WHERE status='pending'") as cur:
            return (await cur.fetchone())[0]

    async def list_pending(self, page: int, size: int) -> tuple[list[PendingRequest], int]:
        total = await self.count_pending()
        page, size = max(page, 0), max(size, 1)
        async with self.c.execute(
                "SELECT user_id, name, ts FROM access_requests WHERE status='pending' "
                "ORDER BY ts, user_id LIMIT ? OFFSET ?", (size, page * size)) as cur:
            rows = await cur.fetchall()
        return [PendingRequest(r["user_id"], r["name"], r["ts"]) for r in rows], total

    async def log_clone(self, user_id: int, source_set: str, new_name: str, count: int, kind: str) -> None:
        await self.c.execute(
            "INSERT INTO clone_log(user_id, source_set, new_name, count, kind) VALUES(?,?,?,?,?)",
            (user_id, source_set, new_name, count, kind))
        await self.c.commit()

    async def user_pack_count(self, user_id: int) -> int:
        async with self.c.execute("SELECT COUNT(*) FROM clone_log WHERE user_id=?", (user_id,)) as cur:
            return (await cur.fetchone())[0]

    async def add_worker(self, owner_id: int, token: str, username: str, kind: str = "token") -> None:
        async with self._lock:
            async with self.c.execute("SELECT COUNT(*) FROM worker_bots WHERE owner_id=?", (owner_id,)) as cur:
                first = (await cur.fetchone())[0] == 0
            await self.c.execute(
                "INSERT INTO worker_bots(owner_id, token, username, is_active, kind) VALUES(?,?,?,?,?) "
                "ON CONFLICT(owner_id, username) DO UPDATE SET token=excluded.token, kind=excluded.kind",
                (owner_id, token, username, 1 if first else 0, kind))
            await self.c.commit()

    async def list_workers(self, owner_id: int) -> list[Worker]:
        async with self.c.execute("SELECT * FROM worker_bots WHERE owner_id=? ORDER BY id", (owner_id,)) as cur:
            return [_worker(r) for r in await cur.fetchall()]

    async def get_active(self, owner_id: int) -> Worker | None:
        async with self.c.execute("SELECT * FROM worker_bots WHERE owner_id=? AND is_active=1", (owner_id,)) as cur:
            row = await cur.fetchone()
            return _worker(row) if row else None

    async def set_active(self, owner_id: int, worker_id: int) -> bool:
        async with self._lock:
            async with self.c.execute("SELECT 1 FROM worker_bots WHERE id=? AND owner_id=?",
                                      (worker_id, owner_id)) as cur:
                if await cur.fetchone() is None:
                    return False
            await self.c.execute("UPDATE worker_bots SET is_active=(id=?) WHERE owner_id=?", (worker_id, owner_id))
            await self.c.execute("UPDATE user_prefs SET active_telethon_id=NULL WHERE user_id=?", (owner_id,))
            await self.c.commit()
            return True

    async def add_telethon(self, account_id: int, label: str, session_enc: bytes, added_by: int) -> int:
        async with self._lock:
            await self.c.execute(
                "INSERT INTO telethon_sessions(account_id, label, session_enc, added_by) VALUES(?,?,?,?) "
                "ON CONFLICT(account_id) DO UPDATE SET label=excluded.label, "
                "session_enc=excluded.session_enc, added_by=excluded.added_by",
                (account_id, label, session_enc, added_by))
            await self.c.commit()
            async with self.c.execute("SELECT id FROM telethon_sessions WHERE account_id=?", (account_id,)) as cur:
                return (await cur.fetchone())["id"]

    async def list_telethon(self) -> list[TelethonSession]:
        async with self.c.execute("SELECT * FROM telethon_sessions ORDER BY id") as cur:
            return [_session(r) for r in await cur.fetchall()]

    async def get_telethon(self, session_id: int) -> TelethonSession | None:
        async with self.c.execute("SELECT * FROM telethon_sessions WHERE id=?", (session_id,)) as cur:
            row = await cur.fetchone()
        return _session(row) if row else None

    async def delete_telethon(self, session_id: int) -> None:
        async with self._lock:
            await self.c.execute("DELETE FROM telethon_sessions WHERE id=?", (session_id,))
            await self.c.execute("UPDATE user_prefs SET active_telethon_id=NULL WHERE active_telethon_id=?",
                                 (session_id,))
            await self.c.commit()

    async def set_active_telethon(self, user_id: int, session_id: int | None) -> bool:
        async with self._lock:
            if session_id is not None and await self.get_telethon(session_id) is None:
                return False
            await self.c.execute(
                "INSERT INTO user_prefs(user_id, active_telethon_id) VALUES(?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET active_telethon_id=excluded.active_telethon_id",
                (user_id, session_id))
            await self.c.commit()
            return True

    async def get_active_telethon_id(self, user_id: int) -> int | None:
        async with self.c.execute("SELECT active_telethon_id FROM user_prefs WHERE user_id=?", (user_id,)) as cur:
            row = await cur.fetchone()
        return row["active_telethon_id"] if row else None

    async def get_active_target(self, user_id: int, telethon_enabled: bool = True) -> "Worker | TelethonSession | None":
        sid = await self.get_active_telethon_id(user_id) if telethon_enabled else None
        if sid is not None:
            session = await self.get_telethon(sid)
            if session is not None:
                return session
        return await self.get_active(user_id)

    async def stats(self) -> Stats:
        async def one(sql: str) -> int:
            async with self.c.execute(sql) as cur:
                return (await cur.fetchone())[0]

        async with self.c.execute("SELECT kind, COUNT(*) AS n FROM clone_log GROUP BY kind") as cur:
            by_kind = {r["kind"]: r["n"] for r in await cur.fetchall()}
        return Stats(
            allowed=await one("SELECT COUNT(*) FROM whitelist"),
            pending=await self.count_pending(),
            blocked=await one("SELECT COUNT(*) FROM blocked_users"),
            workers_token=await one("SELECT COUNT(*) FROM worker_bots WHERE kind='token'"),
            workers_managed=await one("SELECT COUNT(*) FROM worker_bots WHERE kind='managed'"),
            telethon=await one("SELECT COUNT(*) FROM telethon_sessions"),
            clones_total=await one("SELECT COUNT(*) FROM clone_log"),
            clones_7d=await one("SELECT COUNT(*) FROM clone_log WHERE ts >= datetime('now','-7 days')"),
            clones_by_kind=by_kind,
        )

    async def delete_worker(self, owner_id: int, worker_id: int) -> None:
        async with self._lock:
            was_active = await self.get_active(owner_id)
            await self.c.execute("DELETE FROM worker_bots WHERE id=? AND owner_id=?", (worker_id, owner_id))
            if was_active and was_active.id == worker_id:
                await self.c.execute(
                    "UPDATE worker_bots SET is_active=1 WHERE id=(SELECT MIN(id) FROM worker_bots WHERE owner_id=?)",
                    (owner_id,))
            await self.c.commit()
