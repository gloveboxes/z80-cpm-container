"""Browser terminal for an isolated debugger target."""

import asyncio
from pathlib import Path
import secrets
import sys

from aiohttp import web, WSMsgType

from terminal_bridge import publish_endpoint

ROOT = Path(__file__).resolve().parents[1]
MAX_BUFFER = 256 * 1024


class DebugTerminal:
    def __init__(self, endpoint: Path):
        self.endpoint = endpoint
        self.token = secrets.token_hex(16)
        self.target = None
        self.browser = None
        self.browser_reserved = False
        self.pending = bytearray()
        self.dropped = False
        self.output_lock = asyncio.Lock()

    def buffer_output(self, data):
        self.pending.extend(data)
        if len(self.pending) > MAX_BUFFER:
            del self.pending[:-MAX_BUFFER]
            self.dropped = True

    async def accept(self, reader, writer):
        owned = False
        try:
            token = await asyncio.wait_for(reader.readline(), 5)
            if token.strip() != self.token.encode("ascii") or self.target is not None:
                return
            self.target = writer
            owned = True
            while data := await reader.read(4096):
                async with self.output_lock:
                    if self.browser is not None and not self.browser.closed:
                        try:
                            await self.browser.send_bytes(data)
                            continue
                        except (ConnectionError, RuntimeError) as error:
                            print(f"debug browser disconnected: {error}",
                                  file=sys.stderr, flush=True)
                    self.buffer_output(data)
        except (asyncio.TimeoutError, ConnectionError) as error:
            print(f"debug target terminal: {error}", file=sys.stderr, flush=True)
        finally:
            if owned:
                self.target = None
                if self.browser is not None and not self.browser.closed:
                    await self.browser.close(code=1011, message=b"debug target disconnected")
            writer.close()
            await writer.wait_closed()

    async def index(self, _request):
        return web.FileResponse(ROOT / "terminal/index.html")

    async def terminal(self, request):
        if self.browser_reserved:
            raise web.HTTPConflict(text="terminal already connected")
        origin = request.headers.get("Origin")
        if origin and origin not in (f"http://{request.host}", f"https://{request.host}"):
            raise web.HTTPForbidden(text="unexpected terminal origin")
        self.browser_reserved = True
        ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=30)
        try:
            await ws.prepare(request)
            async with self.output_lock:
                self.browser = ws
                if self.dropped:
                    await ws.send_bytes(b"\r\n[Earlier terminal output was truncated.]\r\n")
                    self.dropped = False
                if self.pending:
                    await ws.send_bytes(bytes(self.pending))
                    self.pending.clear()
            async for message in ws:
                if message.type in (WSMsgType.BINARY, WSMsgType.TEXT):
                    if self.target is None:
                        await ws.close(code=1011, message=b"debug target not connected")
                        break
                    data = message.data if isinstance(message.data, bytes) else message.data.encode()
                    self.target.write(data)
                    await self.target.drain()
        except (ConnectionError, RuntimeError) as error:
            print(f"debug browser terminal: {error}", file=sys.stderr, flush=True)
            await ws.close(code=1011, message=b"debug terminal connection error")
        finally:
            self.browser = None
            self.browser_reserved = False
        return ws

    async def start(self, port):
        self.server = await asyncio.start_server(self.accept, "127.0.0.1", 0)
        publish_endpoint(self.endpoint, self.server.sockets[0].getsockname()[1], self.token)
        app = web.Application()
        app.router.add_get("/", self.index)
        app.router.add_get("/ws", self.terminal)
        self.runner = web.AppRunner(app, access_log=None)
        try:
            await self.runner.setup()
            await web.TCPSite(self.runner, "0.0.0.0", port).start()
        except BaseException:
            await self.runner.cleanup()
            self.server.close()
            await self.server.wait_closed()
            raise

    async def close(self):
        await self.runner.cleanup()
        self.server.close()
        await self.server.wait_closed()
        if self.target is not None:
            self.target.close()
            await self.target.wait_closed()
