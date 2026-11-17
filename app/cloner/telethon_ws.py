import asyncio

import aiohttp
from aiohttp_socks import ProxyConnector
from telethon.network.connection import ConnectionTcpObfuscated

WS_URL = "wss://kws{dc}.web.telegram.org:443/apiws"
CONNECT_TIMEOUT = 15


class _Reader:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._ws = ws
        self._buffer = bytearray()

    async def readexactly(self, count: int) -> bytes:
        while len(self._buffer) < count:
            message = await self._ws.receive()
            if message.type == aiohttp.WSMsgType.BINARY:
                self._buffer.extend(message.data)
            elif message.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.ERROR):
                raise asyncio.IncompleteReadError(bytes(self._buffer), count)
        data = bytes(self._buffer[:count])
        del self._buffer[:count]
        return data


class _Writer:
    def __init__(self, ws: aiohttp.ClientWebSocketResponse, session: aiohttp.ClientSession) -> None:
        self._ws = ws
        self._session = session
        self._pending = bytearray()

    def write(self, data: bytes) -> None:
        self._pending.extend(data)

    async def drain(self) -> None:
        if self._pending:
            data, self._pending = bytes(self._pending), bytearray()
            await self._ws.send_bytes(data)

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        await self._ws.close()
        await self._session.close()


def ws_connection(proxy_url: str | None) -> type[ConnectionTcpObfuscated]:
    class ConnectionWebSocket(ConnectionTcpObfuscated):
        async def _connect(self, timeout=None, ssl=None):
            connector = ProxyConnector.from_url(proxy_url, rdns=True) if proxy_url else None
            session = aiohttp.ClientSession(connector=connector)
            try:
                ws = await asyncio.wait_for(
                    session.ws_connect(WS_URL.format(dc=self._dc_id), protocols=("binary",), max_msg_size=0),
                    timeout or CONNECT_TIMEOUT,
                )
            except BaseException:
                await session.close()
                raise
            self._reader = _Reader(ws)
            self._writer = _Writer(ws, session)
            self._codec = self.packet_codec(self)
            self._init_conn()
            await self._writer.drain()

    return ConnectionWebSocket


def telethon_kwargs(proxy_url: str | None) -> dict:
    return {"connection": ws_connection(proxy_url)} if proxy_url else {}
