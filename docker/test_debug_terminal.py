#!/usr/bin/env python3
"""Exercise the debugger's target socket and browser routing inside the image."""

import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from aiohttp import ClientSession, WSServerHandshakeError

sys.path.insert(0, "/opt/altair/runtime")
from debug_terminal import DebugTerminal, MAX_BUFFER


class DebugTerminalTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.terminal = DebugTerminal(Path(self.directory.name) / "endpoint")
        await self.terminal.start(0)
        site = next(iter(self.terminal.runner.sites))
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
        self.session = ClientSession()
        host, port, token = self.terminal.endpoint.read_text().split()
        self.reader, self.writer = await asyncio.open_connection(host, int(port))
        self.writer.write((token + "\n").encode())
        await self.writer.drain()
        await self.wait_until(lambda: self.terminal.target is not None)

    async def asyncTearDown(self):
        self.writer.close()
        await self.writer.wait_closed()
        await self.session.close()
        await self.terminal.close()
        self.directory.cleanup()

    async def wait_until(self, predicate):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.01)
        await asyncio.wait_for(wait(), 5)

    async def test_buffer_reconnect_input_and_exclusive_browser(self):
        self.writer.write(b"before browser")
        await self.writer.drain()
        await self.wait_until(lambda: len(self.terminal.pending) == 14)
        first = await self.session.ws_connect(self.url + "/ws")
        self.assertEqual((await first.receive()).data, b"before browser")
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.session.ws_connect(self.url + "/ws")
        self.assertEqual(error.exception.status, 409)
        await first.send_bytes(b"x")
        self.assertEqual(await asyncio.wait_for(self.reader.readexactly(1), 5), b"x")
        await first.close()
        await self.wait_until(lambda: not self.terminal.browser_reserved)
        self.writer.write(b"while disconnected")
        await self.writer.drain()
        await self.wait_until(lambda: len(self.terminal.pending) == 18)
        second = await self.session.ws_connect(self.url + "/ws")
        self.assertEqual((await second.receive()).data, b"while disconnected")
        await second.send_str("y")
        self.assertEqual(await asyncio.wait_for(self.reader.readexactly(1), 5), b"y")
        await second.close()

    async def test_buffer_is_bounded_and_reports_truncation(self):
        self.writer.write(b"a" * (MAX_BUFFER + 10))
        await self.writer.drain()
        await self.wait_until(lambda: self.terminal.dropped)
        browser = await self.session.ws_connect(self.url + "/ws")
        self.assertIn(b"truncated", (await browser.receive()).data)
        self.assertEqual((await browser.receive()).data, b"a" * MAX_BUFFER)
        await browser.close()

    async def test_rejects_untrusted_browser_and_second_target(self):
        with self.assertRaises(WSServerHandshakeError) as error:
            await self.session.ws_connect(
                self.url + "/ws", headers={"Origin": "http://example.invalid"})
        self.assertEqual(error.exception.status, 403)
        host, port, token = self.terminal.endpoint.read_text().split()
        for candidate in ("wrong-token", token):
            reader, writer = await asyncio.open_connection(host, int(port))
            writer.write((candidate + "\n").encode())
            await writer.drain()
            self.assertEqual(await asyncio.wait_for(reader.read(), 5), b"")
            writer.close()
            await writer.wait_closed()
        browser = await self.session.ws_connect(self.url + "/ws")
        await browser.send_bytes(b"z")
        self.assertEqual(await asyncio.wait_for(self.reader.readexactly(1), 5), b"z")
        await browser.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
