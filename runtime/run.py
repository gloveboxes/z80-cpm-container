#!/usr/bin/env python3
"""Build and run the banked Z80 ROMless SBC CP/M machine."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SBC = Path(os.environ.get("Z80_CPM_SBC_ROOT", ROOT / "sbc"))
CPM = SBC / "src" / "cpm"
DISKS = ROOT / "disks"
BUILD = Path(os.environ.get("ALTAIR_SBC_BUILD_DIR", ROOT / "build"))

if not (CPM / "build_images.py").is_file():
    raise SystemExit("The SBC runtime sources are missing")
sys.path.insert(0, str(CPM))
import build_images
from test_z80_debug_host import MISession, write_memory


def command(*args: str) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def build_host(audio: bool) -> tuple[Path, Path]:
    output = BUILD / "bin"
    output.mkdir(parents=True, exist_ok=True)
    output_options = [
        f"-DCMAKE_RUNTIME_OUTPUT_DIRECTORY={output}",
        f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY={output}",
        f"-DCMAKE_RUNTIME_OUTPUT_DIRECTORY_RELEASE={output}",
        f"-DCMAKE_LIBRARY_OUTPUT_DIRECTORY_RELEASE={output}",
    ]
    host_build = BUILD / "host"
    adapter_build = BUILD / "adapter"
    command("cmake", "-S", str(CPM / "z80_debug_host"), "-B", str(host_build),
            "-DZ80_DEBUG_HOST_BUILD_EXAMPLES=OFF", *output_options)
    command("cmake", "--build", str(host_build), "--config", "Release")
    command("cmake", "-S", str(CPM / "dcc_io_adapter"), "-B", str(adapter_build),
            "-DBUILD_TESTING=OFF", f"-DZ80SBC_SPEAKER_AUDIO={'ON' if audio else 'OFF'}",
            *output_options)
    command("cmake", "--build", str(adapter_build), "--config", "Release")
    host = output / ("z80-debug-host.exe" if os.name == "nt" else "z80-debug-host")
    names = (["z80sbc-io-adapter.dll", "libz80sbc-io-adapter.dll"]
             if os.name == "nt" else
             ["libz80sbc-io-adapter.dylib" if sys.platform == "darwin" else
              "libz80sbc-io-adapter.so"])
    adapters = [output / name for name in names if (output / name).is_file()]
    if not host.is_file() or len(adapters) != 1:
        raise RuntimeError(f"Expected {host} and exactly one adapter in {output}; found {adapters}")
    return host, adapters[0]


def prepare_images() -> bytes:
    assembler = shutil.which("z80asm")
    cpmcp = shutil.which("cpmcp")
    if not assembler or not cpmcp:
        raise RuntimeError("z80asm and cpmtools (cpmcp and cpmrm) must be on PATH")
    paths = [DISKS / name for name in build_images.SOURCE_DISK_NAMES]
    disks = [path.read_bytes() for path in paths]
    if any(len(disk) != build_images.DISK_IMAGE_BYTES for disk in disks):
        raise ValueError("all local disk images must be exactly 512 KiB")
    cpm = build_images.build_banked_cpm_system(assembler)
    bios = build_images.build_bios(assembler)
    image = build_images.build_z80_image(cpm, bios)
    drive_a = build_images.build_native_drive(disks[0], cpm, bios)
    # Keep the user's autorun configuration while refreshing the boot code.
    offset = build_images.AUTORUN_OFFSET
    old_record = disks[0][offset:offset + build_images.RECORD_BYTES]
    if old_record.startswith(build_images.AUTORUN_MAGIC):
        drive_a = drive_a[:offset] + old_record + drive_a[offset + len(old_record):]
    drive_a = build_images.install_autorun(
        drive_a, build_images.build_autorun(assembler), cpmcp)
    with tempfile.NamedTemporaryFile(dir=DISKS, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            temporary.write(drive_a)
            temporary.close()
            os.chmod(temporary_path, paths[0].stat().st_mode)
            os.replace(temporary_path, paths[0])
        finally:
            temporary_path.unlink(missing_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)
    (BUILD / "z80boot.pkg").write_bytes(build_images.build_boot_package(image))
    return image


def run_controller(host: Path, adapter: Path, image: bytes, endpoint: Path,
                   bridge: subprocess.Popen) -> None:
    with tempfile.TemporaryDirectory(prefix="altair-sbc-") as name:
        directory = Path(name)
        bank1 = directory / "bank1.img"
        bank1.write_bytes(build_images.build_bank1_image(image))
        program = directory / "loader.COM"
        program.write_bytes(bytes((0xC3, 0x00, 0x01)))
        program.with_suffix(".dbg").write_text(
            'DCCDBG 2\nfunction-begin 0100 "_main" "main"\n'
            'line 0100 1 "loader.c"\nfunction-end 0103 "_main" "main"\n',
            encoding="ascii",
        )
        env_file = ROOT / "runtime" / "altair_env.txt"
        text = "".join(
            f"DRIVE_{letter}={(DISKS / filename).as_posix()}\n"
            for letter, filename in zip("ABCD", build_images.SOURCE_DISK_NAMES)
        )
        if env_file.is_file():
            text += env_file.read_text(encoding="ascii")
        environment = directory / "session.env"
        environment.write_text(text, encoding="ascii")
        session = MISession(host, [
            "--direct-loader", "--bank1-image", str(bank1),
            "--io-adapter", str(adapter), "--env-file", str(environment),
            "--terminal-endpoint-file", str(endpoint),
        ])
        try:
            session.command(f"-file-exec-and-symbols {json.dumps(program.as_posix())}")
            session.command("-break-insert -t *0x0100")
            _, stopped = session.command("-exec-run", stop=True)
            if 'reason="breakpoint-hit"' not in stopped:
                raise RuntimeError(f"loader did not stop at 0x0100: {stopped}")
            write_memory(session, image)
            bios = int.from_bytes(image[1:3], byteorder="little")
            trampoline = bytes((0x3E, 0xA5, 0xD3, 0x15, 0xC3, bios & 255, bios >> 8))
            session.command(f"-data-write-memory-bytes 0x0100 {trampoline.hex()}")
            session.command("-exec-continue")
            while session.process.poll() is None and bridge.poll() is None:
                try:
                    line = session.lines.get(timeout=1.0)
                except queue.Empty:
                    continue
                if line.startswith("*stopped") and 'reason="exited-normally"' in line:
                    session.command("-exec-continue")
            if session.process.poll() not in (None, 0):
                raise RuntimeError(f"emulator exited with status {session.process.returncode}")
            if bridge.poll() not in (None, 0):
                raise RuntimeError(f"terminal bridge exited: {bridge.returncode}")
        finally:
            session.close()


def run(host: Path, adapter: Path, image: bytes) -> None:
    with tempfile.TemporaryDirectory(prefix="altair-terminal-") as name:
        endpoint = Path(name) / "endpoint"
        bridge = subprocess.Popen(
            [sys.executable, str(ROOT / "runtime" / "terminal_bridge.py"),
             "--endpoint-file", str(endpoint)], cwd=ROOT)
        try:
            deadline = time.monotonic() + 10
            while not endpoint.exists():
                if bridge.poll() is not None:
                    raise RuntimeError(f"terminal bridge exited: {bridge.returncode}")
                if time.monotonic() >= deadline:
                    raise RuntimeError("terminal bridge did not publish its endpoint")
                time.sleep(0.05)
            run_controller(host, adapter, image, endpoint, bridge)
        finally:
            if bridge.poll() is None:
                bridge.terminate()
            bridge.wait()


def main() -> None:
    global BUILD, DISKS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--no-audio", action="store_true")
    parser.add_argument("--disk-dir", type=Path, default=DISKS,
                        help="directory containing the four 512 KiB disk images")
    parser.add_argument("--build-dir", type=Path, default=BUILD,
                        help="directory for generated host binaries and boot package")
    args = parser.parse_args()
    DISKS = args.disk_dir.resolve()
    BUILD = args.build_dir.resolve()
    host, adapter = build_host(not args.no_audio)
    image = prepare_images()
    if not args.prepare_only:
        run(host, adapter, image)


if __name__ == "__main__":
    main()
