#!/usr/bin/env python3
"""Run one isolated SBC GDB/MI debug session inside the container."""

import asyncio
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

import run
import build_images
from debug_terminal import DebugTerminal


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("program", type=Path)
    parser.add_argument("--disk-dir", type=Path, default=run.DISKS)
    args = parser.parse_args()
    program = args.program
    if not program.is_file() or program.suffix.lower() != ".com":
        raise SystemExit(f"debug target is not a COM file: {program}")
    if not re.fullmatch(r"[A-Za-z0-9_]{1,8}", program.stem):
        raise SystemExit(f"debug target must have an 8.3 CP/M name: {program.name}")
    with tempfile.TemporaryDirectory(prefix="z80-cpm-debug-") as name:
        directory = Path(name)
        disks = directory / "disks"
        shutil.copytree(args.disk_dir, disks)
        run.DISKS = disks
        image = run.prepare_images()
        bank0 = directory / "bank0.img"
        bank1 = directory / "bank1.img"
        bank0.write_bytes(image)
        bank1.write_bytes(build_images.build_bank1_image(image))
        diskdefs = directory / "diskdefs"
        diskdefs.write_text(build_images.cpm_diskdefs(), encoding="ascii")
        environment = {**os.environ, "DISKDEFS": str(diskdefs)}
        drive_b = disks / build_images.SOURCE_DISK_NAMES[1]
        stem = program.stem.upper()
        name = f"{stem}.COM"
        disk = drive_b.read_bytes()
        name_field = stem.ljust(8) + "COM"
        if any(disk[offset] == 0 and
               bytes(value & 0x7f for value in disk[offset + 1:offset + 12])
               == name_field.encode("ascii")
               for offset in range(build_images.SYSTEM_BYTES,
                                   build_images.SYSTEM_BYTES + (build_images.DPB_DRM + 1) * 32, 32)):
            subprocess.run(["cpmrm", "-f", "z80sbc", str(drive_b), f"0:{name}"],
                           cwd=directory, env=environment, check=True, stdout=sys.stderr)
        subprocess.run(["cpmcp", "-f", "z80sbc", str(drive_b), str(program),
                        f"0:{name}"],
                       cwd=directory, env=environment, check=True, stdout=sys.stderr)
        env_file = directory / "adapter.env"
        env_file.write_text(
            "".join(f"DRIVE_{letter}={disks / filename}\n"
                    for letter, filename in zip("ABCD", build_images.SOURCE_DISK_NAMES)),
            encoding="ascii",
        )
        command = [
            str(run.BUILD / "bin/z80-debug-host"), "--interpreter=mi",
            "--sbc-image", str(bank0), "--bank1-image", str(bank1),
            "--io-adapter", str(run.BUILD / "bin/libz80sbc-io-adapter.so"),
            "--env-file", str(env_file),
            "--terminal-endpoint-file", str(directory / "terminal.endpoint"),
        ]
        terminal = DebugTerminal(directory / "terminal.endpoint")
        await terminal.start(8081)
        try:
            process = await asyncio.create_subprocess_exec(*command)
            return await process.wait()
        finally:
            await terminal.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
