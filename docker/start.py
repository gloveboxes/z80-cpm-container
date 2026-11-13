#!/usr/bin/env python3
"""Build and run the SBC image with Apple containers and a local sound shim."""

import argparse
import json
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build/sound"
IMAGE = "z80-cpm:local"


def command(*args):
    print("+", " ".join(map(str, args)), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def container_state(name):
    result = subprocess.run(["container", "inspect", name], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"cannot inspect container {name}: {result.stderr.strip()}")
    return json.loads(result.stdout)[0]["status"]["state"]


def run_console(client, shim, name):
    import termios
    import tty

    input_fd = sys.stdin.fileno()
    output_fd = sys.stdout.fileno()
    saved = termios.tcgetattr(input_fd) if os.isatty(input_fd) else None
    watching_input = True
    try:
        if saved is not None:
            tty.setraw(input_fd)
            settings = termios.tcgetattr(input_fd)
            settings[3] |= termios.ISIG
            termios.tcsetattr(input_fd, termios.TCSANOW, settings)
        next_check = time.monotonic() + 2
        while True:
            readable, _, _ = select.select(
                [client, input_fd] if watching_input else [client], [], [], 0.2)
            if client in readable:
                data = client.recv(4096)
                if not data:
                    raise RuntimeError("container console disconnected")
                os.write(output_fd, data)
            if watching_input and input_fd in readable:
                data = os.read(input_fd, 4096)
                if data:
                    client.sendall(data)
                else:
                    watching_input = False
            if shim.poll() is not None:
                raise RuntimeError(f"sound shim exited with status {shim.returncode}")
            if time.monotonic() >= next_check:
                state = container_state(name)
                if state != "running":
                    command("container", "logs", name)
                    raise RuntimeError(f"container stopped (state: {state})")
                next_check = time.monotonic() + 2
    finally:
        if saved is not None:
            termios.tcsetattr(input_fd, termios.TCSADRAIN, saved)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disk-dir", type=Path, help="directory of writable host 512 KiB disks")
    parser.add_argument("--sbc-root", type=Path, help="use CP/M sources from this SBC checkout")
    parser.add_argument("--web-port", type=int, default=8080)
    parser.add_argument("--sound-port", type=int, default=8766)
    parser.add_argument("--console-port", type=int, default=8767)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("Apple containers require macOS")
    ports = (args.web_port, args.sound_port, args.console_port)
    if any(not 1 <= port <= 65535 for port in ports):
        parser.error("ports must be in the range 1-65535")
    if len(set(ports)) != len(ports):
        parser.error("web, sound and console ports must differ")
    if args.disk_dir:
        disks = args.disk_dir.resolve(strict=True)
        if not disks.is_dir():
            parser.error("--disk-dir must name a directory")
        for filename in ("drive_a_cpm63k.img", "drive_b_bdsc.img",
                         "drive_c_escape.img", "drive_d_blank.img"):
            path = disks / filename
            if not path.is_file() or path.stat().st_size != 524288:
                parser.error(f"{path} must be a 512 KiB image")
        if not os.access(disks, os.W_OK):
            parser.error(f"{disks} must be writable")
    else:
        disks = None
    sbc_root = args.sbc_root.resolve(strict=True) if args.sbc_root else None
    if sbc_root is not None and ("," in str(sbc_root) or
                                 not (sbc_root / "src/cpm/build_images.py").is_file()):
        parser.error("--sbc-root must name an SBC checkout without commas in its path")

    command("container", "system", "status")
    if not args.skip_build:
        command("container", "build", "-f", "docker/Dockerfile", "-t", IMAGE, ".")
    command("cmake", "-S", "docker/sound_shim", "-B", BUILD)
    command("cmake", "--build", BUILD, "--config", "Release")
    launch_id = uuid.uuid4().hex
    name = f"altair-sbc-{launch_id}"
    options = [
        "container", "run", "-d", "--name", name,
        "--label", f"com.altair.launch-id={launch_id}",
        "-p", f"127.0.0.1:{args.web_port}:8080",
        "-p", f"127.0.0.1:{args.sound_port}:8766",
        "-p", f"127.0.0.1:{args.console_port}:8767",
    ]
    if disks:
        options += ["--mount", f"type=bind,source={disks},target=/opt/altair/disks"]
    if sbc_root is not None:
        options += ["--mount", f"type=bind,source={sbc_root},target=/opt/sbc-source,readonly",
                    "--env", "Z80_CPM_SBC_ROOT=/opt/sbc-source"]
    options.append(IMAGE)
    shim = None
    started = False
    try:
        command(*options)
        started = True
        url = f"http://127.0.0.1:{args.web_port}/"
        deadline = time.monotonic() + 30
        while True:
            if container_state(name) != "running":
                command("container", "logs", name)
                raise RuntimeError("container stopped during startup")
            try:
                with urllib.request.urlopen(url, timeout=1) as response:
                    if response.status == 200:
                        break
            except OSError:
                if time.monotonic() >= deadline:
                    command("container", "logs", name)
                    raise RuntimeError(f"container did not become ready at {url}")
                time.sleep(0.2)
        command("container", "logs", name)
        executable = BUILD / "altair-sound"
        shim = subprocess.Popen([str(executable), str(args.sound_port)], cwd=ROOT)
        time.sleep(0.5)
        if shim.poll() is not None:
            raise RuntimeError(f"sound shim exited with status {shim.returncode}")
        print(f"Terminal: {url} (Ctrl+C stops the container and sound shim)", flush=True)
        with socket.create_connection(("127.0.0.1", args.console_port), timeout=10) as client:
            client.settimeout(None)
            run_console(client, shim, name)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup_errors = []
        if shim and shim.poll() is None:
            shim.terminate()
            try:
                shim.wait(timeout=10)
            except subprocess.TimeoutExpired:
                shim.kill()
                shim.wait()
                cleanup_errors.append("sound shim did not terminate gracefully")
        if not started:
            result = subprocess.run(["container", "inspect", name],
                                    capture_output=True, text=True)
            if result.returncode == 0:
                details = json.loads(result.stdout)[0]
                started = details["configuration"]["labels"].get("com.altair.launch-id") == launch_id
        if started:
            for action in ("stop", "delete"):
                result = subprocess.run(["container", action, name], cwd=ROOT,
                                        capture_output=True, text=True)
                if result.returncode:
                    cleanup_errors.append(f"container {action} {name}: {result.stderr.strip()}")
        if cleanup_errors:
            for error in cleanup_errors:
                print(f"cleanup error: {error}", file=sys.stderr)
            if sys.exc_info()[0] is None:
                raise RuntimeError("container cleanup failed")


if __name__ == "__main__":
    main()
