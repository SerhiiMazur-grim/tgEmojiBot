from contextlib import suppress
from dataclasses import dataclass
from io import BytesIO

from telethon import TelegramClient, functions, types
from telethon.errors import ShortNameOccupiedError
from telethon.sessions import StringSession

from app.cloner.naming import build_user_short_name, random_suffix
from app.cloner.service import EXT, CloneError
from app.cloner.telethon_ws import telethon_kwargs
from app.config import Config


class SessionError(Exception):
    pass


@dataclass(frozen=True)
class SessionInfo:
    account_id: int
    label: str


def make_client(session_str: str, cfg: Config) -> TelegramClient:
    return TelegramClient(StringSession(session_str), cfg.tg_api_id, cfg.tg_api_hash,
                          device_model="Emoji cloner", app_version="1.0", receive_updates=False,
                          **telethon_kwargs(cfg.proxy))


async def check_session(session_str: str, cfg: Config) -> SessionInfo:
    client = None
    try:
        client = make_client(session_str, cfg)
        await client.connect()
        if not await client.is_user_authorized():
            raise SessionError("сессия не авторизована")
        me = await client.get_me()
        label = f"@{me.username}" if me.username else (me.first_name or str(me.id))
        return SessionInfo(me.id, label)
    except SessionError:
        raise
    except Exception as e:
        raise SessionError(type(e).__name__) from None
    finally:
        if client is not None:
            with suppress(Exception):
                await client.disconnect()


MAX_NAME_TRIES = 5
MIME = {"static": "image/webp", "animated": "application/x-tgsticker", "video": "video/webm"}


def _attributes(fmt: str, emoji: str, sticker_type: str, repaint: bool) -> list:
    attrs = [types.DocumentAttributeFilename(file_name=f"s.{EXT[fmt]}")]
    if sticker_type == "custom_emoji":
        attrs.append(types.DocumentAttributeCustomEmoji(
            alt=emoji, stickerset=types.InputStickerSetEmpty(), free=False, text_color=repaint or None))
    return attrs


async def _upload_item(client, data: bytes, fmt: str, emoji: str, sticker_type: str, repaint: bool):
    file = await client.upload_file(BytesIO(data), file_name=f"s.{EXT[fmt]}")
    media = await client(functions.messages.UploadMediaRequest(
        peer=types.InputPeerSelf(),
        media=types.InputMediaUploadedDocument(
            file=file, mime_type=MIME[fmt], attributes=_attributes(fmt, emoji, sticker_type, repaint))))
    doc = media.document
    return types.InputStickerSetItem(
        document=types.InputDocument(id=doc.id, access_hash=doc.access_hash, file_reference=doc.file_reference),
        emoji=emoji)


async def _free_short_name(client, set_name: str, suffix) -> str:
    for _ in range(MAX_NAME_TRIES):
        name = build_user_short_name(set_name, suffix)
        try:
            if await client(functions.stickers.CheckShortNameRequest(short_name=name)):
                return name
        except ShortNameOccupiedError:
            continue
    raise CloneError("Не удалось подобрать свободное имя набора.")
