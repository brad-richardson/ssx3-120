# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Tests for the APK-path font wiring (stage/install helpers in build.py)."""
import importlib.util
import tempfile
import types
import unittest
from pathlib import Path

BUILD = Path(__file__).resolve().parents[1] / 'build.py'
SPEC = importlib.util.spec_from_file_location('ssx3_build', BUILD)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)

DEVICE = '/storage/emulated/0/Android/data/com.example/files'


def make_args(work, skip_fonts=False, stock_dump=None):
    return types.SimpleNamespace(work=work, skip_fonts=skip_fonts,
                                 stock_dump=stock_dump)


def make_files(files):
    files.mkdir(parents=True, exist_ok=True)
    (files / 'ps2x.env').write_text('PS2X_CD_IMAGE=x\n')


class StageFontsTest(unittest.TestCase):
    def test_build_copies_pack_and_points_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            pack = tmp / 'pack'
            pack.mkdir()
            (pack / 'a.png').write_bytes(b'PNG!')
            files = tmp / 'files'
            make_files(files)
            build.font_pack = lambda a, inputs: pack
            got = build.stage_fonts(make_args(tmp), {'iso': 'i'}, files,
                                    DEVICE)
            self.assertEqual(got, files / 'font-pack')
            self.assertEqual((files / 'font-pack/a.png').read_bytes(),
                             b'PNG!')
            env = (files / 'ps2x.env').read_text()
            self.assertIn('GE1_TEX_REPLACE=' + DEVICE + '/font-pack\n',
                          env)
            self.assertIn('GE1_TEX_PRELOAD=1\n', env)
            # Idempotent: a second stage keeps exactly one pair of lines.
            build.stage_fonts(make_args(tmp), {'iso': 'i'}, files,
                              DEVICE)
            env = (files / 'ps2x.env').read_text()
            self.assertEqual(env.count('GE1_TEX_REPLACE='), 1)
            self.assertEqual(env.count('GE1_TEX_PRELOAD='), 1)

    def test_skip_removes_stale_pack_and_leaves_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = tmp / 'files'
            make_files(files)
            stale = files / 'font-pack'
            stale.mkdir(parents=True)
            (stale / 'old.png').write_bytes(b'OLD')
            build.font_pack = lambda a, inputs: None
            got = build.stage_fonts(make_args(tmp), {'iso': 'i'}, files,
                                    DEVICE)
            self.assertIsNone(got)
            self.assertFalse(stale.exists())
            env = (files / 'ps2x.env').read_text()
            self.assertNotIn('GE1_TEX_REPLACE=', env)
            self.assertNotIn('GE1_TEX_PRELOAD=', env)


class FontPackSourcesTest(unittest.TestCase):
    def test_lists_staged_files_device_relative(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = Path(tmp) / 'files'
            staged = files / 'font-pack'
            staged.mkdir(parents=True)
            (staged / 'b.png').write_bytes(b'B')
            (staged / 'a.png').write_bytes(b'A')
            self.assertEqual(build.font_pack_sources(files),
                             [(staged / 'a.png', 'font-pack/a.png'),
                              (staged / 'b.png', 'font-pack/b.png')])

    def test_empty_without_staged_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                build.font_pack_sources(Path(tmp) / 'files'), [])


class SkipFontsTest(unittest.TestCase):
    def test_skip_fonts_returns_none_without_subprocess(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(
                build.font_pack(make_args(Path(tmp), skip_fonts=True),
                                {}))


if __name__ == '__main__':
    unittest.main()
