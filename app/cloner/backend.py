import asyncio
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO
from typing import Awaitable, Callable

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile, InputSticker
from telethon import functions, types
from telethon.errors import (FloodWaitError, RPCError, ShortNameInvalidError, ShortNameOccupiedError,
                             StickersetInvalidError)

from app.cloner import mtproto
from app.cloner.items import TYPE_BY_KIND, SourceItem
from app.cloner.naming import build_short_name, build_title, clean_short_name
from app.cloner.service import DEFAULT_EMOJI, EXT, MAX_INITIAL, CloneError, _retry, sticker_format
from app.config import Config
from app.crypto import CryptoError, decrypt
from app.db import TelethonSession, Worker
from app.factory import make_bot

Progress = Callable[[int, int], Awaitable[None]] | None
GONE_TEXT = "Пак больше не существует."
SETTLE_TRIES = 8
SETTLE_DELAY = 2.0


class PackGone(CloneError):
    pass


class PartialPack(CloneError):
    def __init__(self, short_name: str, done: int, reason: str):
        super().__init__(reason)
        self.short_name, self.done = short_name, done


@dataclass(frozen=True)
class RemoteSticker:
    ref: object
    custom_emoji_id: str | None
    emoji: str


@dataclass(frozen=True)
class RemotePack:
    title: str
    stickers: list[RemoteSticker]


class PackBackend(ABC):
    name: str
    ref: int

    @abstractmethod
    async def __aenter__(self) -> "PackBackend": ...

    @abstractmethod
    async def __aexit__(self, *exc) -> None: ...

    @abstractmethod
    def normalize_name(self, raw: str) -> str | None:
        pass

    @abstractmethod
    async def name_status(self, name: str) -> str:
        pass

    @abstractmethod
    async def relink(self, short_name: str, new_name: str, expected: int | None = None) -> bool:
        pass

    @abstractmethod
    async def create(self, title: str, kind: str, items: list[SourceItem], source: str | None,
                     progress: Progress = None, short_name: str | None = None) -> str: ...

    @abstractmethod
    async def add(self, short_name: str, kind: str, items: list[SourceItem], progress: Progress = None) -> None: ...

    @abstractmethod
    async def remove(self, short_name: str, positions: list[int], progress: Progress = None,
                     expected: int | None = None) -> None: ...

    @abstractmethod
    async def rename(self, short_name: str, title: str) -> None: ...

    @abstractmethod
    async def delete(self, short_name: str) -> None: ...

    @abstractmethod
    async def fetch(self, short_name: str) -> RemotePack: ...


async def _download(bot, file_id: str) -> bytes:
    buf = BytesIO()
    await _retry(lambda: bot.download(file_id, destination=buf))
    return buf.getvalue()


def _reason(e: Exception) -> str:
    if isinstance(e, FloodWaitError):
        return f"Telegram просит подождать {e.seconds} с"
    return type(e).__name__


async def _settled(read: Callable[[], Awaitable], size: Callable[[object], int], expected: int | None):
    for attempt in range(SETTLE_TRIES):
        res = await read()
        if expected is None or size(res) == expected:
            return res
        if attempt < SETTLE_TRIES - 1:
            await asyncio.sleep(SETTLE_DELAY)
    raise CloneError("Telegram ещё обновляет пак. Повторите через минуту.")


def _input_doc(d) -> types.InputDocument:
    return types.InputDocument(id=d.id, access_hash=d.access_hash, file_reference=d.file_reference)


def _picked(stickers: list[RemoteSticker], positions: list[int]) -> list[RemoteSticker]:
    return [stickers[p - 1] for p in positions if 1 <= p <= len(stickers)]


class TelethonBackend(PackBackend):
    name = "telethon"

    def __init__(self, bot, session: TelethonSession, config: Config):
        self.bot, self.session, self.config = bot, session, config
        self.ref = session.id
        self.client = None
        self._fresh: dict[str, RemotePack] = {}

    async def __aenter__(self) -> "TelethonBackend":
        try:
            session_str = decrypt(self.config.session_key, self.session.session_enc)
        except CryptoError:
            raise CloneError("Не удалось открыть общий аккаунт. Сообщите администратору.") from None
        try:
            self.client = mtproto.make_client(session_str, self.config)
            await self.client.connect()
            authorized = await self.client.is_user_authorized()
        except Exception as e:
            await self.__aexit__()
            raise CloneError(f"Общий аккаунт недоступен: {type(e).__name__}") from None
        if not authorized:
            await self.__aexit__()
            raise CloneError("Общий аккаунт не подключён. Сообщите администратору.")
        return self

    async def __aexit__(self, *exc) -> None:
        if self.client is not None:
            with suppress(Exception):
                await self.client.disconnect()

    def _remember(self, short_name: str, res) -> None:
        if getattr(res, "documents", None) is not None and getattr(res, "set", None) is not None:
            self._fresh[short_name] = self._parse(res)

    @staticmethod
    def _parse(res) -> RemotePack:
        stickers = []
        for d in res.documents:
            alt = next((a.alt for a in d.attributes if hasattr(a, "alt")), "")
            stickers.append(RemoteSticker(_input_doc(d), str(d.id) if res.set.emojis else None, alt))
        return RemotePack(res.set.title, stickers)

    async def _prepare(self, items: list[SourceItem], kind: str) -> list:
        sets: dict[str, list] = {}
        out = []
        for it in items:
            doc = await self._source_doc(it, sets)
            if doc is not None:
                out.append(types.InputStickerSetItem(document=_input_doc(doc), emoji=it.emoji))
                continue
            data = await _download(self.bot, it.file_id)
            out.append(await mtproto._upload_item(self.client, data, it.fmt, it.emoji, TYPE_BY_KIND[kind], it.repaint))
        return out

    async def _source_doc(self, it: SourceItem, sets: dict[str, list]):
        if not it.source_set:
            return None
        if it.source_set not in sets:
            try:
                res = await self.client(functions.messages.GetStickerSetRequest(
                    stickerset=types.InputStickerSetShortName(short_name=it.source_set), hash=0))
                sets[it.source_set] = list(res.documents)
            except RPCError:
                sets[it.source_set] = []
        docs = sets[it.source_set]
        if it.preview_id:
            return next((d for d in docs if str(d.id) == it.preview_id), None)
        if it.position is not None and it.position < len(docs):
            doc = docs[it.position]
            alt = next((a.alt for a in doc.attributes if hasattr(a, "alt")), None)
            return doc if alt == it.emoji else None
        return None

    def normalize_name(self, raw: str) -> str | None:
        return clean_short_name(raw)

    async def name_status(self, name: str) -> str:
        try:
            free = await self.client(functions.stickers.CheckShortNameRequest(short_name=name))
        except ShortNameOccupiedError:
            return "taken"
        except ShortNameInvalidError:
            return "invalid"
        except Exception as e:
            raise CloneError(_reason(e)) from None
        return "free" if free else "taken"

    async def relink(self, short_name: str, new_name: str, expected: int | None = None) -> bool:
        ref = types.InputStickerSetShortName(short_name=short_name)
        old = await _settled(lambda: self._simple(functions.messages.GetStickerSetRequest(stickerset=ref, hash=0)),
                             lambda r: len(r.documents), expected)
        items = []
        for d in old.documents:
            alt = next((a.alt for a in d.attributes if hasattr(a, "alt")), "") or DEFAULT_EMOJI
            items.append(types.InputStickerSetItem(document=_input_doc(d), emoji=alt))
        new_ref = types.InputStickerSetShortName(short_name=new_name)
        created = False
        try:
            self._remember(new_name, await self.client(functions.stickers.CreateStickerSetRequest(
                user_id=types.InputUserSelf(), title=old.set.title, short_name=new_name, stickers=items[:MAX_INITIAL],
                emojis=True if old.set.emojis else None,
                text_color=True if getattr(old.set, "text_color", None) else None)))
            created = True
            for it in items[MAX_INITIAL:]:
                self._remember(new_name, await self.client(functions.stickers.AddStickerToSetRequest(
                    stickerset=new_ref, sticker=it)))
        except Exception as e:
            if created:
                with suppress(Exception):
                    await self.client(functions.stickers.DeleteStickerSetRequest(stickerset=new_ref))
            self._fresh.pop(new_name, None)
            raise CloneError(_reason(e)) from None
        try:
            await self.client(functions.stickers.DeleteStickerSetRequest(stickerset=ref))
        except Exception:
            return False
        return True

    async def create(self, title, kind, items, source, progress=None, short_name=None) -> str:
        name, done = None, 0
        try:
            prepared = await self._prepare(items, kind)
            name = short_name or await mtproto._free_short_name(self.client, source or "emoji", mtproto.random_suffix)
            first, rest = prepared[:MAX_INITIAL], prepared[MAX_INITIAL:]
            repaint = kind == "emoji" and any(i.repaint for i in items)
            self._remember(name, await self.client(functions.stickers.CreateStickerSetRequest(
                user_id=types.InputUserSelf(), title=title, short_name=name, stickers=first,
                emojis=True if kind == "emoji" else None, text_color=True if repaint else None)))
            done = len(first)
            if progress:
                await progress(done, len(prepared))
            ref = types.InputStickerSetShortName(short_name=name)
            for it in rest:
                self._remember(name, await self.client(functions.stickers.AddStickerToSetRequest(
                    stickerset=ref, sticker=it)))
                done += 1
                if progress:
                    await progress(done, len(prepared))
        except CloneError:
            raise
        except Exception as e:
            if done:
                raise PartialPack(name, done, _reason(e)) from None
            raise CloneError(_reason(e)) from None
        return name

    async def add(self, short_name, kind, items, progress=None) -> None:
        done = 0
        try:
            prepared = await self._prepare(items, kind)
            ref = types.InputStickerSetShortName(short_name=short_name)
            for it in prepared:
                self._remember(short_name, await self.client(functions.stickers.AddStickerToSetRequest(
                    stickerset=ref, sticker=it)))
                done += 1
                if progress:
                    await progress(done, len(prepared))
        except CloneError:
            raise
        except StickersetInvalidError:
            raise PackGone(GONE_TEXT) from None
        except Exception as e:
            raise PartialPack(short_name, done, _reason(e)) from None

    async def remove(self, short_name, positions, progress=None, expected=None) -> None:
        remote = await _settled(lambda: self.fetch(short_name), lambda r: len(r.stickers), expected)
        picked = _picked(remote.stickers, positions)
        done = 0
        try:
            for s in picked:
                self._remember(short_name, await self.client(functions.stickers.RemoveStickerFromSetRequest(
                    sticker=s.ref)))
                done += 1
                if progress:
                    await progress(done, len(picked))
        except Exception as e:
            raise PartialPack(short_name, done, _reason(e)) from None

    async def rename(self, short_name, title) -> None:
        self._remember(short_name, await self._simple(functions.stickers.RenameStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name=short_name), title=title)))

    async def delete(self, short_name) -> None:
        await self._simple(functions.stickers.DeleteStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name=short_name)))

    async def _simple(self, request):
        try:
            return await self.client(request)
        except StickersetInvalidError:
            raise PackGone(GONE_TEXT) from None
        except Exception as e:
            raise CloneError(_reason(e)) from None

    async def fetch(self, short_name) -> RemotePack:
        if short_name in self._fresh:
            return self._fresh[short_name]
        return self._parse(await self._simple(functions.messages.GetStickerSetRequest(
            stickerset=types.InputStickerSetShortName(short_name=short_name), hash=0)))


class BotBackend(PackBackend):
    name = "bot"

    def __init__(self, bot, worker: Worker, owner_id: int, config: Config):
        self.bot, self.worker, self.owner_id, self.config = bot, worker, owner_id, config
        self.ref = worker.id
        self.w = None

    async def __aenter__(self) -> "BotBackend":
        self.w = make_bot(self.worker.token, self.config.proxy)
        return self

    async def __aexit__(self, *exc) -> None:
        if self.w is not None:
            with suppress(Exception):
                await self.w.session.close()

    async def _upload(self, items, progress) -> list[InputSticker]:
        out = []
        for i, it in enumerate(items, 1):
            data = await _download(self.bot, it.file_id)
            up = await _retry(lambda data=data, it=it: self.w.upload_sticker_file(
                user_id=self.owner_id, sticker=BufferedInputFile(data, filename=f"s.{EXT[it.fmt]}"),
                sticker_format=it.fmt))
            out.append(InputSticker(sticker=up.file_id, format=it.fmt, emoji_list=[it.emoji]))
            if progress:
                await progress(i, len(items))
        return out

    async def _exists(self, name: str) -> bool:
        try:
            await self.w.get_sticker_set(name=name)
            return True
        except TelegramBadRequest:
            return False

    def normalize_name(self, raw: str) -> str | None:
        return clean_short_name(raw, f"_by_{self.worker.username}")

    async def name_status(self, name: str) -> str:
        try:
            return "taken" if await self._exists(name) else "free"
        except Exception as e:
            raise CloneError(type(e).__name__) from None

    async def relink(self, short_name: str, new_name: str, expected: int | None = None) -> bool:
        old = await _settled(lambda: self._simple(self.w.get_sticker_set(name=short_name)),
                             lambda r: len(r.stickers), expected)
        items = [InputSticker(sticker=s.file_id, format=sticker_format(s), emoji_list=[s.emoji or DEFAULT_EMOJI])
                 for s in old.stickers]
        created = False
        try:
            await _retry(lambda: self.w.create_new_sticker_set(
                user_id=self.owner_id, name=new_name, title=old.title, stickers=items[:MAX_INITIAL],
                sticker_type=old.sticker_type,
                needs_repainting=(any(bool(getattr(s, "needs_repainting", False)) for s in old.stickers)
                                  if old.sticker_type == "custom_emoji" else None)))
            created = True
            for it in items[MAX_INITIAL:]:
                await _retry(lambda it=it: self.w.add_sticker_to_set(
                    user_id=self.owner_id, name=new_name, sticker=it))
        except Exception as e:
            if created:
                with suppress(Exception):
                    await self.w.delete_sticker_set(name=new_name)
            raise CloneError(type(e).__name__) from None
        try:
            await self.w.delete_sticker_set(name=short_name)
        except Exception:
            return False
        return True

    async def create(self, title, kind, items, source, progress=None, short_name=None) -> str:
        name, done = None, 0
        try:
            uploaded = await self._upload(items, progress)
            taken: set[str] = set()
            name = short_name
            for _ in range(50):
                if short_name:
                    break
                name = build_short_name(source or "emoji", self.worker.username, taken)
                if not await self._exists(name):
                    break
                taken.add(name)
            first, rest = uploaded[:MAX_INITIAL], uploaded[MAX_INITIAL:]
            await _retry(lambda: self.w.create_new_sticker_set(
                user_id=self.owner_id, name=name, title=build_title(title, self.worker.username), stickers=first,
                sticker_type=TYPE_BY_KIND[kind],
                needs_repainting=any(i.repaint for i in items) if kind == "emoji" else None))
            done = len(first)
            for it in rest:
                await _retry(lambda it=it: self.w.add_sticker_to_set(user_id=self.owner_id, name=name, sticker=it))
                done += 1
        except CloneError:
            raise
        except Exception as e:
            if done:
                raise PartialPack(name, done, type(e).__name__) from None
            raise CloneError(type(e).__name__) from None
        return name

    async def add(self, short_name, kind, items, progress=None) -> None:
        done = 0
        try:
            for it in await self._upload(items, progress):
                await _retry(lambda it=it: self.w.add_sticker_to_set(
                    user_id=self.owner_id, name=short_name, sticker=it))
                done += 1
        except Exception as e:
            raise PartialPack(short_name, done, type(e).__name__) from None

    async def remove(self, short_name, positions, progress=None, expected=None) -> None:
        remote = await _settled(lambda: self.fetch(short_name), lambda r: len(r.stickers), expected)
        picked = _picked(remote.stickers, positions)
        done = 0
        try:
            for s in picked:
                await _retry(lambda s=s: self.w.delete_sticker_from_set(sticker=s.ref))
                done += 1
                if progress:
                    await progress(done, len(picked))
        except Exception as e:
            raise PartialPack(short_name, done, type(e).__name__) from None

    async def rename(self, short_name, title) -> None:
        await self._simple(self.w.set_sticker_set_title(name=short_name, title=title))

    async def delete(self, short_name) -> None:
        await self._simple(self.w.delete_sticker_set(name=short_name))

    async def _simple(self, call):
        try:
            return await call
        except TelegramBadRequest as e:
            if "STICKERSET_INVALID" in (e.message or "").upper():
                raise PackGone(GONE_TEXT) from None
            raise CloneError(type(e).__name__) from None

    async def fetch(self, short_name) -> RemotePack:
        src = await self._simple(self.w.get_sticker_set(name=short_name))
        return RemotePack(src.title, [RemoteSticker(s.file_id, getattr(s, "custom_emoji_id", None), s.emoji or "")
                                      for s in src.stickers])
