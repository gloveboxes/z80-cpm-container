# Z80 CP/M container

An Apple-container runtime for the Z80 ROMless SBC emulator and its banked
CP/M 2.2 system. It provides a console, browser terminal, host audio client and
an isolated VS Code C debugging session. The source and disk snapshot are
derived from Z80ROMlessSBC commit `27371e7549e89dfd4c980bd39685c7f09e4c1eb6`.
The terminal UI and container integration originate in esp32-altair-8800.

## Requirements

Apple silicon macOS, the Apple `container` CLI and service, Python 3, CMake,
a C compiler, and an audio output device. The first build needs network access
for Debian packages and the pinned miniaudio header.
The image includes `z80asm`, `z80dasm` and `cpmtools` for Z80 and CP/M work.

```sh
container system start
python3 docker/start.py
```

CP/M boots in the console. Open <http://127.0.0.1:8080/> to switch input and
output to the browser; disconnect the browser to return to the console. Press
Enter after a handover to show the current prompt. Ctrl+C stops the launcher,
audio client and container. The audio client is also buildable on Windows and
Linux; container orchestration currently targets macOS.

The image includes four 512 KiB disks. Changes to bundled disks are discarded
with the container. `python3 docker/start.py --disk-dir /absolute/path/to/disks`
uses four writable host images for persistence. Drive A boot tracks and
`AUTORUN.COM` are refreshed on startup. Stop the emulator before modifying
mounted disks. `--web-port`, `--sound-port`, and `--console-port` select
different localhost ports. `--skip-build` reuses the locally built image.
Pass `--sbc-root /absolute/path/to/SBC` to build CP/M and BIOS from a
read-only SBC checkout instead of the image's bundled sources.

## VS Code C debugging

Build a DCC program with debug information and place the `.COM` and `.DBG`
beside its C source. Run `python3 docker/prepare.py` to build the image and
sound client, then use the example [launch configuration](.vscode/launch.json)
with the VS Code C/C++ extension. Open the C file and choose **Z80 CP/M: Debug
DCC program in Apple container**. The pre-launch task builds the image and
audio client; it does not compile the DCC application.

The debugger starts a **separate container** with private copies of the disks.
It installs the target on its private drive B, boots SBC CP/M, and stops at
source breakpoints. The host workspace is mounted read-only at the same path
inside the container. Source paths recorded in `.DBG` must resolve relative
to the `.COM` directory. Debug port 8768 carries sound to the local shim; pass
`--sound-port` in `miDebuggerArgs` to change it. Open
<http://127.0.0.1:8081/> for interactive target output and keyboard input;
`--web-port` changes the host port. Output produced before the browser
connects or while it is disconnected is buffered, up to 256 KiB, for the
duration of the debug session. The browser terminal loads its xterm scripts
from a CDN. Target input and output use the browser, not the VS Code Debug
Console. Input is delivered while the target is running; continue from a
breakpoint before interacting with the program. MI memory operations address
the active bank.

Pass `--disk-dir /absolute/path/to/disks` in `miDebuggerArgs` to mount the four
host disks read-only and snapshot them at debug startup. The target replaces
its namesake only on snapshot drive B. Stop any writer before starting the
debugger so the snapshot is consistent.
Pass `--sbc-root` in `miDebuggerArgs` to use the CP/M and BIOS sources in an
SBC checkout inside the mounted workspace.

Other repositories can use this project as a submodule. Point their VS Code
`miDebuggerPath` to `docker/debug_proxy.py` in the submodule, pass their
workspace and program in `miDebuggerArgs`, and run `docker/prepare.py` from
the submodule before debugging. The `.COM` must be within the mounted workspace.
The image and host audio client are shared build outputs, not modified disks.

## Validation

Run `node docker/test_handover.mjs` against a test container with ports
18080:8080, 18766:8766 and 18767:8767. In a fresh test container,
`node docker/test_container.mjs` checks CP/M and `B:SOUND.COM` PCM.
`docker/test_debug_sound.py` and `docker/test_debug_vscode.py` accept an
isolated workspace containing a debug-built SOUND.COM/DBG; the latter also
requires Node.js and the installed VS Code C++ extension's OpenDebugAD7
executable. Run terminal routing tests inside the built image with:

```sh
container run --rm --mount "type=bind,source=$PWD/docker,target=/test,readonly" \
  --entrypoint python3 z80-cpm:local /test/test_debug_terminal.py
```

## Provenance

Project source retains its applicable MIT notices under `LICENSE`, `sbc/LICENSE`
and the component directories. CP/M's original rights holder's 2022
redistribution clarification is documented by the
[Unofficial CP/M website](http://www.cpm.z80.de/license.html) and
[CP/Mish's copy of the permission](https://github.com/davidgiven/cpmish/blob/master/third_party/dr/COPYING.md).
The disk images contain additional applications whose redistribution rights
are separate from the CP/M operating system and this project's MIT license.
Drive D contains a synthetic ISAM demonstration database.
