#!/usr/bin/env python3
"""Launch an isolated Apple-container GDB/MI debugger for one DCC program."""

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--program", required=True, type=Path)
    parser.add_argument("--workspace", required=True, type=Path)
    parser.add_argument("--disk-dir", type=Path,
                        help="snapshot these 512 KiB disks for this debug session")
    parser.add_argument("--image", default="z80-cpm:local")
    parser.add_argument("--sound-port", type=int, default=8768)
    parser.add_argument("--web-port", type=int, default=8081)
    parser.add_argument("--interpreter", default="mi", choices=["mi"])
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Apple containers require macOS")
    workspace = args.workspace.resolve(strict=True)
    program = args.program.resolve(strict=True)
    if not workspace.is_dir() or not program.is_file():
        parser.error("workspace must be a directory and program must be a file")
    if not program.is_relative_to(workspace):
        parser.error("program must be inside the workspace")
    if "," in str(workspace):
        parser.error("workspace paths containing commas cannot be bind mounted")
    disks = None
    if args.disk_dir:
        disks = args.disk_dir.resolve(strict=True)
        if not disks.is_dir() or "," in str(disks):
            parser.error("--disk-dir must name a directory without commas in its path")
        for filename in ("drive_a_cpm63k.img", "drive_b_bdsc.img",
                         "drive_c_escape.img", "drive_d_blank.img"):
            path = disks / filename
            if not path.is_file() or path.stat().st_size != 524288:
                parser.error(f"{path} must be a 512 KiB image")
    if not 1 <= args.sound_port <= 65535:
        parser.error("sound port must be in the range 1-65535")
    if not 1 <= args.web_port <= 65535 or args.web_port == args.sound_port:
        parser.error("web port must be in the range 1-65535 and differ from sound port")
    shim = Path(__file__).resolve().parents[1] / "build/sound/altair-sound"
    if not shim.is_file():
        parser.error("sound shim not built; run python3 docker/prepare.py")
    command = [
        "container", "run", "--rm", "-i", "--name", f"z80-debug-{uuid.uuid4().hex}",
        "-p", f"127.0.0.1:{args.sound_port}:8766",
        "-p", f"127.0.0.1:{args.web_port}:8081",
        "--mount", f"type=bind,source={workspace},target={workspace},readonly",
    ]
    if disks is not None:
        command.extend(["--mount", f"type=bind,source={disks},target=/opt/debug-disks,readonly"])
    command.extend([
        "--entrypoint", "python3", args.image,
        "/opt/altair/runtime/debug_session.py", str(program),
    ])
    if disks is not None:
        command.extend(["--disk-dir", "/opt/debug-disks"])
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupt)
    sound = None
    process = None
    try:
        print(f"Debug terminal: http://127.0.0.1:{args.web_port}/",
              file=sys.stderr, flush=True)
        sound = subprocess.Popen([str(shim), str(args.sound_port)], stdin=subprocess.DEVNULL,
                                 stdout=sys.stderr)
        process = subprocess.Popen(command)
        while process.poll() is None:
            if sound.poll() is not None:
                raise RuntimeError(f"sound shim exited with status {sound.returncode}")
            try:
                process.wait(timeout=0.25)
            except subprocess.TimeoutExpired:
                pass
        return process.returncode
    except KeyboardInterrupt:
        return 130
    finally:
        for child in (process, sound):
            if child is not None and child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()


if __name__ == "__main__":
    raise SystemExit(main())
