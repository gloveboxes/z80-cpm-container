#!/usr/bin/env python3
"""Check startup disk validation without starting CP/M or changing host disks."""

import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))
import run as runner


@unittest.skipUnless(shutil.which("fsck.cpm") and shutil.which("z80asm")
                     and shutil.which("cpmcp"), "container Z80 tools required")
class DiskIntegrityTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="disk-integrity-test-")
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.paths = []
        for name in runner.build_images.SOURCE_DISK_NAMES:
            path = self.directory / name
            shutil.copyfile(ROOT / "disks" / name, path)
            self.paths.append(path)
        self.build = self.directory / "build"
        self.disks_patch = mock.patch.object(runner, "DISKS", self.directory)
        self.build_patch = mock.patch.object(runner, "BUILD", self.build)
        self.disks_patch.start()
        self.build_patch.start()
        self.addCleanup(self.disks_patch.stop)
        self.addCleanup(self.build_patch.stop)

    def test_valid_disks_are_checked_before_preparation(self):
        other_disks = [path.read_bytes() for path in self.paths[1:]]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            runner.prepare_images()
        self.assertEqual(output.getvalue().splitlines(), [
            f"filesystem validated: {path.name}" for path in self.paths
        ])
        self.assertEqual(other_disks, [path.read_bytes() for path in self.paths[1:]])
        self.assertTrue((self.build / "z80boot.pkg").is_file())

    def test_duplicate_extent_aborts_before_any_disk_is_updated(self):
        path = self.paths[3]
        disk = bytearray(path.read_bytes())
        offsets = range(runner.build_images.SYSTEM_BYTES,
                        runner.build_images.SYSTEM_BYTES
                        + (runner.build_images.DPB_DRM + 1) * 32, 32)
        occupied = next(offset for offset in offsets if disk[offset] <= 15)
        unused = next(offset for offset in offsets if disk[offset] == 0xE5)
        disk[unused:unused + 32] = disk[occupied:occupied + 32]
        path.write_bytes(disk)
        before = [image.read_bytes() for image in self.paths]
        with mock.patch.object(runner.build_images, "build_banked_cpm_system") as assemble:
            with self.assertRaisesRegex(RuntimeError, path.name):
                runner.prepare_images()
        assemble.assert_not_called()
        self.assertEqual(before, [image.read_bytes() for image in self.paths])
        self.assertFalse(self.build.exists())

    def test_wrong_size_aborts_before_preparation(self):
        path = self.paths[1]
        path.write_bytes(path.read_bytes()[:-1])
        before = [image.read_bytes() for image in self.paths]
        with mock.patch.object(runner.build_images, "build_banked_cpm_system") as assemble:
            with self.assertRaisesRegex(ValueError, path.name):
                runner.prepare_images()
        assemble.assert_not_called()
        self.assertEqual(before, [image.read_bytes() for image in self.paths])
        self.assertFalse(self.build.exists())


if __name__ == "__main__":
    unittest.main()
