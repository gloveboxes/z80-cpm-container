#!/usr/bin/env python3
"""Exercise SOUND.C using the installed VS Code C++ debug adapter."""

import argparse
import json
from pathlib import Path
import queue
import subprocess
import threading
import time
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("adapter", type=Path)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("program", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("--proxy", type=Path, default=Path(__file__).with_name("debug_proxy.py"))
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    program = args.program.resolve(strict=True)
    source = args.source.resolve(strict=True)
    proxy = args.proxy.resolve(strict=True)
    process = subprocess.Popen([str(args.adapter)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    browser = None
    messages = queue.Queue()
    errors = []

    def read_messages():
        while True:
            headers = {}
            while True:
                line = process.stdout.readline()
                if not line:
                    return
                if line == b"\r\n":
                    break
                key, value = line.decode("ascii").split(":", 1)
                headers[key.lower()] = value.strip()
            messages.put(json.loads(process.stdout.read(int(headers["content-length"]))))

    threading.Thread(target=read_messages, daemon=True).start()
    threading.Thread(target=lambda: [errors.append(line.decode(errors="replace"))
                                     for line in process.stderr], daemon=True).start()
    sequence = 0

    def send(command, arguments):
        nonlocal sequence
        sequence += 1
        data = json.dumps({"seq": sequence, "type": "request", "command": command,
                           "arguments": arguments}).encode()
        process.stdin.write(f"Content-Length: {len(data)}\r\n\r\n".encode() + data)
        process.stdin.flush()
        return sequence

    def wait(predicate):
        seen = []
        while True:
            try:
                message = messages.get(timeout=90)
            except queue.Empty as error:
                raise RuntimeError(f"debug adapter timeout: {seen}\nstderr: {errors}") from error
            seen.append(message)
            if message.get("type") == "response" and not message.get("success", True):
                raise RuntimeError(f"debug adapter error: {message}")
            if predicate(message):
                return message

    def response(number):
        return wait(lambda message: message.get("request_seq") == number)

    try:
        response(send("initialize", {"adapterID": "cppdbg", "linesStartAt1": True,
                                      "columnsStartAt1": True, "pathFormat": "path"}))
        send("launch", {
            "name": "SOUND", "type": "cppdbg", "request": "launch",
            "program": str(program), "cwd": str(program.parent), "MIMode": "gdb",
            "miDebuggerPath": str(proxy),
            "miDebuggerArgs": f'--workspace "{workspace}" --program "{program}"',
            "targetArchitecture": "x86", "stopAtEntry": False,
            "launchCompleteCommand": "exec-continue", "externalConsole": False,
        })
        wait(lambda message: message.get("event") == "initialized")
        result = response(send("setBreakpoints", {
            "source": {"path": str(source)}, "breakpoints": [{"line": 231}],
        }))
        if not result["body"]["breakpoints"][0]["verified"]:
            raise RuntimeError(f"unverified breakpoint: {result}")
        response(send("configurationDone", {}))
        stopped = wait(lambda message: message.get("event") == "stopped")
        if stopped["body"].get("line") != 231:
            raise RuntimeError(f"unexpected stop: {stopped}")
        thread = stopped["body"]["threadId"]
        response(send("stepIn", {"threadId": thread}))
        wait(lambda message: message.get("event") == "stopped")
        stack = response(send("stackTrace", {"threadId": thread}))
        if "DXSVC.C" not in stack["body"]["stackFrames"][0]["source"]["path"]:
            raise RuntimeError(f"unexpected stepped frame: {stack}")
        deadline = time.monotonic() + 30
        while True:
            try:
                with urllib.request.urlopen("http://127.0.0.1:8081/", timeout=1) as page:
                    if page.status == 200:
                        break
            except urllib.error.URLError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("debug terminal did not start")
                time.sleep(0.1)
        terminal_events = queue.Queue()

        def connect_browser():
            client = subprocess.Popen(
                ["node", str(Path(__file__).with_name("test_debug_terminal.mjs")), "8081"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            threading.Thread(target=lambda: [terminal_events.put(json.loads(line))
                                             for line in client.stdout], daemon=True).start()
            return client

        def terminal_wait(*texts):
            output = ""
            while not all(text in output for text in texts):
                try:
                    event = terminal_events.get(timeout=60)
                except queue.Empty as error:
                    raise RuntimeError(f"debug terminal timeout: {output}") from error
                if event["event"] in ("close", "error"):
                    raise RuntimeError(f"debug terminal closed: {event}; output: {output}")
                output += event.get("data", "")
            return output

        response(send("continue", {"threadId": thread}))
        time.sleep(2)
        browser = connect_browser()
        terminal_wait("Speaker engine ready", "Timed tone.")
        browser.stdin.write(json.dumps({"close": True}) + "\n")
        browser.stdin.flush()
        browser.wait(timeout=10)
        while not terminal_events.empty():
            terminal_events.get_nowait()
        time.sleep(1)
        browser = connect_browser()
        event = terminal_events.get(timeout=15)
        if event["event"] != "open":
            raise RuntimeError(f"debug terminal reconnect failed: {event}")
        browser.stdin.write(json.dumps({"send": "x"}) + "\n")
        browser.stdin.flush()
        terminal_wait("Stopped by keypress.")
        wait(lambda message: message.get("event") in ("exited", "terminated"))
        response(send("disconnect", {"terminateDebuggee": True}))
        print("PASS: VS Code SOUND.C breakpoint, step and browser keyboard input")
    finally:
        if browser is not None and browser.poll() is None:
            browser.terminate()
            browser.wait(timeout=10)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
