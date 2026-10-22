# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Native RefPack search: byte-identity vs the Python reference [native-refpack-1].

Synthetic buffers only (no game data, no discs): a seeded fuzz mix of at
least 10k cases plus structured edges. Each case checks native vs Python
for find_matches candidates, optimal plan picks, optimal bytes and greedy
bytes. The Python side runs with the native hook disabled, so this file
tests the exact production wiring (refpack_optimal.plan falls back to the
same code path when the library is missing).

Corpus checks on real game bytes (decision captures + full course groups)
run locally and are reported in the lane REPORT, not here.
"""
import importlib.util
import os
import sys
import unittest
from pathlib import Path

SHARED = Path(__file__).resolve().parents[1] / 'tricky' / 'shared'


def load(name):
    path = SHARED / (name + '.py')
    spec = importlib.util.spec_from_file_location(f'nrp_{name}', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f'nrp_{name}'] = mod
    spec.loader.exec_module(mod)
    return mod


native = load('refpack_native')
optimal = load('refpack_optimal')
greedy_mod = load('refpack_encode')

# Force the pure-Python reference paths for the oracle side.
optimal._native = None
greedy_mod._native = None

NATIVE_OK, NATIVE_DETAIL = native.available()
if not NATIVE_OK:
    print(f'test_refpack_native: native unavailable, '
          f'differential tests will skip ({NATIVE_DETAIL})', flush=True)

requires_native = unittest.skipIf(
    not NATIVE_OK, f'native unavailable: {NATIVE_DETAIL}')


def lcg(n, seed=1):
    out = bytearray()
    x = seed & 0x7fffffff or 1
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7fffffff
        out.append((x >> 16) & 0xff)
    return bytes(out)


def check_case(tc, raw, cand=128, skip=64, gcand=64):
    m_py = optimal.find_matches(raw, cand, skip)
    m_na = native.find_matches(raw, cand, skip)
    tc.assertEqual(m_na, m_py, f'find_matches n={len(raw)}')
    p_py = optimal.plan(raw, cand, skip)
    p_na = native.plan(raw, cand, skip)
    tc.assertEqual(p_na, p_py, f'plan n={len(raw)}')
    tc.assertEqual(native.encode_optimal(raw, cand, skip),
                   optimal.encode(raw, cand, skip), f'optimal n={len(raw)}')
    tc.assertEqual(native.encode_greedy(raw, gcand),
                   greedy_mod.encode(raw, gcand), f'greedy n={len(raw)}')


class NativeIdentityTest(unittest.TestCase):
    @requires_native
    def test_edges(self):
        cases = [b'', b'a', b'ab', b'abc', b'abcd',
                 bytes(111), bytes(112), bytes(113), bytes(114),
                 bytes(1027), bytes(1028), bytes(1029),
                 b'abcabc', b'aaaa', b'A' * 200 + b'B']
        for raw in cases:
            with self.subTest(n=len(raw)):
                check_case(self, bytes(raw))

    @requires_native
    def test_structured(self):
        rep = lcg(5000, seed=5)
        far = lcg(4000, seed=9)
        cases = [
            rep + rep[:3000],
            bytes(70000),
            (b'ABCDEFGH' * 9000)[:70000],
            far + lcg(1000, seed=13) + far,
            lcg(132100, seed=17),
            (b'the quick brown fox jumps over the lazy dog. ' * 2000)[:80000],
            lcg(5000) + bytes((i * 7) % 251 for i in range(5000)),
        ]
        for raw in cases:
            with self.subTest(n=len(raw)):
                check_case(self, bytes(raw))

    @requires_native
    def test_param_variants(self):
        raw = lcg(3000, seed=7) + bytes((i * 13) % 251 for i in range(3000))
        for cand, skip in ((1, 64), (16, 64), (64, 1), (128, 1),
                           (128, 1028), (32, 32)):
            with self.subTest(cand=cand, skip=skip):
                check_case(self, raw, cand=cand, skip=skip)
        for gcand in (1, 7, 64):
            with self.subTest(gcand=gcand):
                self.assertEqual(native.encode_greedy(raw, gcand),
                                 greedy_mod.encode(raw, gcand))

    @requires_native
    def test_fuzz_10k(self):
        # Seeded mix, deterministic: tiny (exhaustive-ish shapes), small,
        # medium, plus boundary lengths around the 10/16/67/112/1028 forms
        # and all three distance classes.
        n_cases = 0
        # 1. tiny: every length 0..64, three seeds each
        for n in range(65):
            for seed in (1, 2, 3):
                with self.subTest(kind='tiny', n=n, seed=seed):
                    check_case(self, lcg(n, seed))
                n_cases += 1
        # 2. small structured: 9000 seeded cases, 8..600 bytes
        x = 0x12345678
        for t in range(9000):
            x = (1103515245 * x + 12345) & 0x7fffffff
            n = 8 + (x % 593)
            x = (1103515245 * x + 12345) & 0x7fffffff
            seed = x or 1
            style = t % 5
            if style == 0:
                raw = lcg(n, seed)
            elif style == 1:
                raw = bytes(((i * 31 + seed) % 7) for i in range(n))
            elif style == 2:
                unit = lcg(1 + seed % 40, seed)
                raw = (unit * (n // len(unit) + 1))[:n]
            elif style == 3:
                a = lcg(n // 2 + 1, seed)
                raw = (a + a[:n - len(a) + 1])[:n] if n else b''
            else:
                raw = bytes((seed + i // (1 + seed % 17)) % 256
                            for i in range(n))
            with self.subTest(kind='small', t=t):
                check_case(self, raw)
            n_cases += 1
        # 3. medium: 800 cases, 601..5200 bytes (hits longer forms)
        for t in range(800):
            x = (1103515245 * (0x9e3779b9 ^ t) + 12345) & 0x7fffffff
            n = 601 + (x % 4600)
            base = lcg(n, 1000 + t)
            if t % 4 == 0:  # plant a far (class-1/2) repeat
                cut = n // 3
                base = base[:cut] + base[:n - cut]
            with self.subTest(kind='medium', t=t):
                check_case(self, base)
            n_cases += 1
        # 4. boundary lengths around each command form
        for n in (9, 10, 11, 15, 16, 17, 18, 66, 67, 68, 69, 111, 112,
                  113, 114, 224, 1026, 1027, 1028, 1030, 2048, 4096):
            for seed in (11, 12):
                with self.subTest(kind='boundary', n=n, seed=seed):
                    check_case(self, lcg(n, seed) + lcg(n, seed)[:n // 2])
                n_cases += 1
        self.assertGreaterEqual(n_cases, 10000, n_cases)

    @requires_native
    def test_large_window(self):
        # Distances past each class edge and past MAX_DIST, all in one buffer
        a = lcg(2000, seed=21)
        gap1 = lcg(2000, seed=22)
        buf = a + gap1 + a  # dist ~4000: class 1
        b = lcg(3000, seed=23)
        gap2 = lcg(20000, seed=24)
        buf += b + gap2 + b  # dist ~23000: class 2
        c = lcg(1500, seed=25)
        buf += c + lcg(132000, seed=26) + c  # dist past MAX_DIST: no match
        check_case(self, buf)


class NativeErrorTest(unittest.TestCase):
    def test_limits_match_reference(self):
        big = b'x' * (0xffffff + 1)
        with self.assertRaises(ValueError):
            optimal.encode(big)
        with self.assertRaises(ValueError):
            greedy_mod.encode(big)
        with self.assertRaises(ValueError):
            greedy_mod.encode(b'abc', 0)
        if NATIVE_OK:
            with self.assertRaises(ValueError):
                native.encode_optimal(big)
            with self.assertRaises(ValueError):
                native.encode_greedy(big)
            with self.assertRaises(ValueError):
                native.encode_greedy(b'abc', 0)
            with self.assertRaises(ValueError):
                native.plan(big)

    @requires_native
    def test_deterministic(self):
        raw = lcg(20000, seed=11) + lcg(20000, seed=11)[:5000]
        self.assertEqual(native.plan(raw), native.plan(raw))
        self.assertEqual(native.encode_optimal(raw),
                         native.encode_optimal(raw))
        self.assertEqual(native.encode_greedy(raw),
                         native.encode_greedy(raw))

    def test_order_table_matches_generator(self):
        # The embedded set-iteration table must equal freshly computed
        # Python-set order for every length (provenance lock).
        import re
        hdr = (SHARED / 'refpack_cand_order.h').read_text()
        rows = re.findall(r'\{([^}]*)\}, /\* (\d+) \*/', hdr)
        self.assertEqual(len(rows), 1012)
        by_len = {int(n): [int(v) for v in body.split(',')] for body, n in rows}
        for length in range(17, 1029):
            want = list({length, length - 1, length - 2, length - 3,
                         10, 67, 16} - set(range(0, 5)))
            got = [v for v in by_len[length] if v != 0]
            self.assertEqual(got, want, f'order row {length}')

    def test_fallback_without_native(self):
        # REFPACK_NATIVE=off in a fresh interpreter: production entry points
        # still return correct bytes via the Python reference.
        import subprocess
        code = (
            'import sys; sys.path.insert(0, %r);'
            'import refpack_optimal as O, refpack_encode as G;'
            'raw = bytes((i * 7) %% 251 for i in range(3000));'
            'assert O.encode(raw) == O.emit_commands(raw, O.plan(raw));'
            'assert G.encode(raw);'
            'print("fallback-ok")'
        ) % str(SHARED)
        env = dict(os.environ, REFPACK_NATIVE='off')
        p = subprocess.run([sys.executable, '-c', code], capture_output=True,
                           text=True, timeout=300, env=env)
        self.assertEqual(p.returncode, 0, p.stderr[-2000:])
        self.assertIn('fallback-ok', p.stdout)


if __name__ == '__main__':
    unittest.main()
