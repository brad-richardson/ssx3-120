# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TRICKY_ENV key audit: every key build_tricky.py stages in tricky.env
must be read by the runtime (vendored ps2xRuntime sources) or recorded in
the knob registry/matrix. Fails on any unread key (dead knob).

Synthetic only: parses key names from source, no game data, no ISOs.
"""
import re
import unittest
from pathlib import Path

TRICKY = Path(__file__).resolve().parents[1] / 'tricky'
ROOT = Path(__file__).resolve().parents[3]

KEY_RE = re.compile(r'^(PS2X_[A-Z0-9_]+)=')


def env_keys():
    keys = []
    in_env = False
    for line in (TRICKY / 'build_tricky.py').read_text().splitlines():
        if line.startswith('TRICKY_ENV'):
            in_env = True
            continue
        if in_env:
            if line.startswith('"""'):
                break
            m = KEY_RE.match(line)
            if m:
                keys.append(m.group(1))
    return keys


def registry_names():
    names = set()
    for rel in ('tests/det/knob_registry.tsv', 'tests/det/knob_matrix.py',
                'tools/ssx3/knobs.tsv'):
        p = ROOT / rel
        if p.exists():
            names |= set(re.findall(r'PS2X_[A-Z0-9_]+', p.read_text()))
    return names


def runtime_hits():
    rt = ROOT / 'deps' / 'PS2Recomp' / 'ps2xRuntime'
    hits = set()
    if not rt.is_dir():
        return hits
    for p in list(rt.rglob('*.h')) + list(rt.rglob('*.cpp')):
        try:
            hits |= set(re.findall(r'PS2X_[A-Z0-9_]+', p.read_text()))
        except (OSError, UnicodeDecodeError):
            continue
    return hits


class TrickyEnvKeysTest(unittest.TestCase):
    def test_every_key_runtime_read_or_registered(self):
        keys = env_keys()
        self.assertGreater(len(keys), 10, 'TRICKY_ENV parse found no keys')
        known = registry_names() | runtime_hits()
        unread = [k for k in keys if k not in known]
        self.assertEqual(unread, [], f'unread TRICKY_ENV keys: {unread}')

    def test_no_pre_pb51_dead_keys(self):
        keys = set(env_keys())
        dead = {'PS2X_SSX3_TRICKY_COURSES', 'PS2X_SSX3_GEM_TSV',
                'PS2X_ATRAC_NATIVE', 'PS2X_VAG_Q4', 'PS2X_CDVD_IOP_ASYNC',
                'PS2X_CDVD_EXTRA_OPEN_SLACK'}
        self.assertEqual(keys & dead, set(), f'dead keys back: {keys & dead}')


if __name__ == '__main__':
    unittest.main()
