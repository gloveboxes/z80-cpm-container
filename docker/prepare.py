#!/usr/bin/env python3
"""Build the shared Apple-container image and host audio client."""

from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

if sys.platform != "darwin":
    raise SystemExit("Apple containers require macOS")
for command in (
    ["container", "build", "-f", "docker/Dockerfile", "-t", "z80-cpm:local", "."],
    ["cmake", "-S", "docker/sound_shim", "-B", "build/sound"],
    ["cmake", "--build", "build/sound", "--config", "Release"],
):
    print("+", " ".join(command), flush=True)
    subprocess.run(command, cwd=ROOT, check=True)
