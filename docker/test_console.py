#!/usr/bin/env python3
"""Exercise guest control keys and local exit using a real pseudo-terminal."""

from contextlib import ExitStack
import os
import pty
import socket
import tempfile
import termios
import threading
import time
import unittest
from unittest import mock

import start as launcher


class ConsoleKeysTests(unittest.TestCase):
    def setUp(self):
        self.master, self.slave = pty.openpty()
        self.saved = termios.tcgetattr(self.slave)
        self.input = os.fdopen(os.dup(self.slave), "rb", buffering=0)
        self.output = tempfile.TemporaryFile()
        self.client, self.guest = socket.socketpair()
        self.guest.settimeout(1)
        self.errors = []
        self.stack = ExitStack()
        self.stack.enter_context(mock.patch.object(launcher.sys, "stdin", self.input))
        self.stack.enter_context(mock.patch.object(launcher.sys, "stdout", self.output))
        self.stack.enter_context(mock.patch.object(launcher, "container_state", return_value="running"))
        self.shim = mock.Mock()
        self.shim.poll.return_value = None
        self.thread = threading.Thread(target=self.run_console)
        self.addCleanup(self.close_console)
        self.thread.start()
        deadline = time.monotonic() + 1
        while termios.tcgetattr(self.slave)[3] & termios.ICANON:
            self.assertTrue(self.thread.is_alive(), self.errors)
            self.assertLess(time.monotonic(), deadline, "console did not enter raw mode")
            time.sleep(0.005)

    def run_console(self):
        try:
            launcher.run_console(self.client, self.shim, "test-console")
        except Exception as error:
            self.errors.append(error)

    def close_console(self):
        self.guest.close()
        self.thread.join(timeout=2)
        self.client.close()
        self.stack.close()
        self.input.close()
        self.output.close()
        os.close(self.master)
        os.close(self.slave)
        self.assertFalse(self.thread.is_alive(), "console thread did not stop")

    def receive(self, size):
        data = b""
        while len(data) < size:
            received = self.guest.recv(size - len(data))
            self.assertTrue(received, "guest connection closed")
            data += received
        return data

    def assert_stopped(self):
        self.thread.join(timeout=1)
        self.assertFalse(self.thread.is_alive(), "Ctrl+] did not stop the console")
        self.assertEqual(self.errors, [])
        restored = termios.tcgetattr(self.slave)
        expected = self.saved.copy()
        # Restoring canonical mode can set the kernel's pending-input flag.
        restored[3] &= ~termios.PENDIN
        expected[3] &= ~termios.PENDIN
        self.assertEqual(restored, expected)
        self.guest.settimeout(0.1)
        with self.assertRaises(socket.timeout):
            self.guest.recv(4096)

    def test_ctrl_c_reaches_guest_and_escape_stops_console(self):
        os.write(self.master, b"RUN\x03")
        self.assertEqual(self.receive(4), b"RUN\x03")
        self.assertTrue(self.thread.is_alive())
        os.write(self.master, b"END\x1dignored")
        self.assertEqual(self.receive(3), b"END")
        self.assert_stopped()

    def test_escape_alone_is_not_sent_to_guest(self):
        os.write(self.master, b"\x1d")
        self.assert_stopped()


if __name__ == "__main__":
    unittest.main()
