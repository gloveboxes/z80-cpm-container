#!/usr/bin/env python3
"""Serve the SBC terminal and run the emulator inside the container."""

import asyncio
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading

from aiohttp import web, WSMsgType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import run as runner
from terminal_bridge import publish_endpoint


class Controller:
    def __init__(self):
        self.active = True
        self.error = None

    def poll(self):
        return None if self.active else 0


async def start_machine(app):
    app = app["machine"]
    directory = tempfile.TemporaryDirectory(prefix="altair-container-")
    app["temporary"] = directory
    app["controller"] = Controller()
    app["ws"] = None
    app["browser_reserved"] = False
    app["console"] = None
    app["reader"] = None
    app["writer"] = None
    app["connected"] = asyncio.Event()
    app["started"] = False
    app["token"] = secrets.token_hex(16)
    app["endpoint"] = Path(directory.name) / "endpoint"

    async def accept(reader, writer):
        owns_connection = False
        try:
            token = await asyncio.wait_for(reader.readline(), 5)
            if token.strip() != app["token"].encode("ascii") or app["writer"]:
                return
            app["reader"], app["writer"] = reader, writer
            owns_connection = True
            app["connected"].set()
            while data := await reader.read(4096):
                ws = app["ws"]
                if ws is not None and not ws.closed:
                    await ws.send_bytes(data)
                elif app["console"] is not None:
                    app["console"].write(data)
                    await app["console"].drain()
        finally:
            writer.close()
            await writer.wait_closed()
            if owns_connection:
                app["reader"], app["writer"] = None, None
                app["connected"].clear()
                ws = app["ws"]
                if ws is not None and not ws.closed:
                    await ws.close(code=1011, message=b"emulator terminal disconnected")
                if app["console"] is not None:
                    app["console"].close()

    app["server"] = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = app["server"].sockets[0].getsockname()[1]
    publish_endpoint(app["endpoint"], port, app["token"])

    def controller():
        try:
            runner.run_controller(
                runner.BUILD / "bin/z80-debug-host",
                runner.BUILD / "bin/libz80sbc-io-adapter.so",
                app["image"], app["endpoint"], app["controller"])
        except Exception as error:
            app["controller"].error = error
            print(f"emulator controller failed: {error}", file=sys.stderr, flush=True)

    app["thread"] = threading.Thread(target=controller)

    def start_controller():
        if not app["started"]:
            app["started"] = True
            app["thread"].start()

    app["start_controller"] = start_controller

    async def console(reader, writer):
        if app["console"] is not None:
            writer.close()
            await writer.wait_closed()
            return
        app["console"] = writer
        try:
            start_controller()
            await wait_for_target(app)
            if app["browser_reserved"]:
                writer.write(b"\r\n[Browser has control.]\r\n")
                await writer.drain()
            while data := await reader.read(4096):
                if app["ws"] is None:
                    if app["writer"] is None:
                        raise RuntimeError("emulator terminal disconnected")
                    app["writer"].write(data)
                    await app["writer"].drain()
        except (RuntimeError, OSError) as error:
            print(f"console error: {error}", file=sys.stderr, flush=True)
        finally:
            if app["console"] is writer:
                app["console"] = None
            writer.close()
            await writer.wait_closed()

    app["console_server"] = await asyncio.start_server(
        console, "0.0.0.0", int(os.environ.get("ALTAIR_CONSOLE_PORT", "8767")))


async def wait_for_target(app):
    deadline = asyncio.get_running_loop().time() + 30
    while not app["connected"].is_set():
        if not app["thread"].is_alive():
            raise RuntimeError(f"emulator controller stopped: {app['controller'].error}")
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("emulator terminal connection timed out")
        try:
            await asyncio.wait_for(app["connected"].wait(), 0.5)
        except asyncio.TimeoutError:
            pass


async def stop_machine(app):
    app = app["machine"]
    app["controller"].active = False
    if app["writer"]:
        app["writer"].close()
        await app["writer"].wait_closed()
    app["server"].close()
    await app["server"].wait_closed()
    app["console_server"].close()
    await app["console_server"].wait_closed()
    if app["console"]:
        app["console"].close()
        await app["console"].wait_closed()
    if app["thread"].ident:
        await asyncio.to_thread(app["thread"].join, 10)
        if app["thread"].is_alive():
            raise RuntimeError("emulator controller did not stop")
    app["temporary"].cleanup()


async def index(request):
    return web.FileResponse(ROOT / "terminal/index.html")


async def terminal(request):
    app = request.app["machine"]
    if app["ws"] is not None:
        raise web.HTTPConflict(text="terminal already connected")
    if app["started"] and not app["thread"].is_alive():
        raise web.HTTPServiceUnavailable(text="emulator controller stopped")
    origin = request.headers.get("Origin")
    if origin and origin not in (f"http://{request.host}", f"https://{request.host}"):
        raise web.HTTPForbidden(text="unexpected terminal origin")
    ws = web.WebSocketResponse(max_msg_size=65536, heartbeat=30)
    app["browser_reserved"] = True
    try:
        await ws.prepare(request)
        app["ws"] = ws
        if app["console"]:
            app["console"].write(b"\r\n[Browser has control.]\r\n")
            await app["console"].drain()
            await ws.send_bytes(b"\r\n[Browser has control. Press Enter to continue.]\r\n")
        app["start_controller"]()
        await wait_for_target(app)
        async for message in ws:
            if message.type in (WSMsgType.BINARY, WSMsgType.TEXT):
                data = message.data if isinstance(message.data, bytes) else message.data.encode()
                if app["writer"] is None:
                    raise RuntimeError("emulator terminal disconnected")
                app["writer"].write(data)
                await app["writer"].drain()
    except (RuntimeError, OSError) as error:
        print(f"terminal error: {error}", file=sys.stderr, flush=True)
        await ws.close(code=1011, message=str(error).encode()[:120])
    finally:
        app["ws"] = None
        app["browser_reserved"] = False
        if app["console"]:
            app["console"].write(b"\r\n[Console has control. Press Enter to continue.]\r\n")
            await app["console"].drain()
    return ws


def main():
    runner.DISKS = Path(os.environ.get("ALTAIR_DISKS_DIR", ROOT / "disks")).resolve()
    runner.BUILD = Path(os.environ.get("ALTAIR_SBC_BUILD_DIR", ROOT / "build"))
    app = web.Application()
    app["machine"] = {"image": runner.prepare_images()}
    app.on_startup.append(start_machine)
    app.on_cleanup.append(stop_machine)
    app.router.add_get("/", index)
    app.router.add_get("/ws", terminal)
    web.run_app(app, host="0.0.0.0", port=int(os.environ.get("ALTAIR_WEB_PORT", "8080")))


if __name__ == "__main__":
    main()
