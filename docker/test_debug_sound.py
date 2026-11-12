#!/usr/bin/env python3
"""Verify SOUND.C source debugging through the Apple-container MI proxy."""

import argparse
from pathlib import Path
import queue
import subprocess
import threading


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path)
    parser.add_argument("program", type=Path)
    args = parser.parse_args()
    workspace = args.workspace.resolve(strict=True)
    program = args.program.resolve(strict=True)
    proxy = Path(__file__).with_name("debug_proxy.py")
    process = subprocess.Popen(
        [str(proxy), "--workspace", str(workspace), "--program", str(program)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    lines = queue.Queue()
    errors = []
    threading.Thread(target=lambda: [lines.put(line.rstrip()) for line in process.stdout],
                     daemon=True).start()
    threading.Thread(target=lambda: [errors.append(line.rstrip()) for line in process.stderr],
                     daemon=True).start()

    def wait(predicate):
        seen = []
        while True:
            try:
                line = lines.get(timeout=60)
            except queue.Empty as error:
                raise RuntimeError(f"MI timeout: {seen}\nstderr: {errors}") from error
            seen.append(line)
            if line.startswith(("^error",)) or "^error" in line:
                raise RuntimeError(f"MI error: {line}")
            if predicate(line):
                return line

    def command(text, token):
        process.stdin.write(f"{token}{text}\n")
        process.stdin.flush()
        return wait(lambda line: line.startswith(f"{token}^"))

    try:
        wait(lambda line: line == "(gdb)")
        command(f'-file-exec-and-symbols "{program}"', 1)
        command('-break-insert "SOUND.C:231"', 2)
        command("-exec-run", 3)
        stop = wait(lambda line: line.startswith("*stopped"))
        if ('reason="breakpoint-hit"' not in stop or 'line="231"' not in stop
                or 'file="SOUND.C"' not in stop):
            raise RuntimeError(f"unexpected source breakpoint: {stop}")
        command("-exec-step", 4)
        stop = wait(lambda line: line.startswith("*stopped"))
        if ('reason="end-stepping-range"' not in stop
                or 'func="snd_init"' not in stop or 'DXSVC.C' not in stop):
            raise RuntimeError(f"unexpected source step: {stop}")
        command("-gdb-exit", 5)
        if process.wait(timeout=20):
            raise RuntimeError(f"debugger failed: {errors}")
        print("PASS: SOUND.C source breakpoint and step into DXSVC.C")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
