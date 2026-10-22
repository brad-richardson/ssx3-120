# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Synthetic tests for the shipped RefPack decisions (tk-speed-1).

No game data, no discs: every test builds its inputs in memory. The
in-lane byte-identity gate (replay vs oracle finals) is documented in
the lane REPORT, not here.
"""
import importlib.util
import sys
import unittest
from pathlib import Path

SHARED = Path(__file__).resolve().parents[1] / 'tricky' / 'shared'


def load(name):
    path = SHARED / (name + '.py')
    spec = importlib.util.spec_from_file_location(
        f'tk_speed_{name}', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f'tk_speed_{name}'] = mod
    spec.loader.exec_module(mod)
    return mod


optimal = load('refpack_optimal')
dec = load('refpack_decisions')


# Frozen reference: find_matches/_extend as of tk-speed-1 cf4fea9 (bytes
# slicing), kept as the lock on match-candidate identity: any future
# search optimization must reproduce these candidates exactly
# (identical candidates + untouched DP + untouched emitter = identical
# bytes), plus the replay gate.
def _extend_ref(data, a, b, limit):
    n = 0
    while n < limit:
        k = min(64, limit - n)
        if data[a + n:a + n + k] == data[b + n:b + n + k]:
            n += k
            continue
        for t in range(k):
            if data[a + n + t] != data[b + n + t]:
                return n + t
        return n + k
    return n


def find_matches_ref(data, candidates=128, skip_threshold=64):
    from collections import defaultdict, deque
    n = len(data)
    chains = defaultdict(lambda: deque(maxlen=candidates))
    result = [()] * n
    pos = 0
    carry = None
    while pos < n - 2:
        key = data[pos:pos + 3]
        if carry and carry[0] >= 3:
            best = {}
            L, D = carry
            cls = 0 if D <= 1024 else 1 if D <= 16384 else 2
            best[cls] = (min(L, 1028), D)
            result[pos] = tuple(best.values())
            carry = (L - 1, D)
            chains[key].append(pos)
            pos += 1
            continue
        best = {}
        limit = min(1028, n - pos)
        for prev in reversed(chains.get(key, ())):
            dist = pos - prev
            if dist > 131072:
                break
            cls = 0 if dist <= 1024 else 1 if dist <= 16384 else 2
            if cls in best and best[cls][0] >= limit:
                continue
            length = _extend_ref(data, prev, pos, limit)
            minimum = (3, 4, 5)[cls]
            if length >= minimum and (cls not in best or length > best[cls][0]):
                best[cls] = (length, dist)
        result[pos] = tuple(best.values())
        chains[key].append(pos)
        longest = max((v for v in best.values()), default=None)
        if longest and longest[0] >= skip_threshold:
            carry = (longest[0] - 1, longest[1])
        pos += 1
    return result


class MatchIdentityTest(unittest.TestCase):
    def corpus(self):
        out = [b'', b'a', b'abc',
               pseudo(300, seed=3), pseudo(3000, seed=7),
               pseudo(20000, seed=11)]
        rep = pseudo(5000, seed=5)
        out.append(rep + rep[:3000])                    # near class-0 match
        out.append(bytes(200000))                       # long literal run
        out.append((b'ABCDEFGH' * 9000)[:70000])         # period 8, all classes
        far = pseudo(4000, seed=9)
        out.append(far + pseudo(1000, seed=13) + far)    # class-2 distance
        out.append(pseudo(132100, seed=17))              # past MAX_DIST window
        return out

    def test_extend_known_answers(self):
        # The memoryview rewrite was reverted (measured 0.85x: bytes ==
        # uses optimized memcmp); these pin _extend's semantics for any
        # future attempt. Reference copies above stay as the lock.
        cases = [
            (b'abcabc', 0, 3, 3, 3),
            (b'aaaa', 0, 1, 3, 3),
            (b'aaaa', 0, 1, 2, 2),
            (b'abcdef', 0, 3, 3, 0),
            (b'A' * 200 + b'B', 0, 1, 200, 199),
            (b'', 0, 0, 0, 0),
        ]
        for data, a, b, limit, want in cases:
            with self.subTest(a=a, b=b, limit=limit):
                self.assertEqual(optimal._extend(data, a, b, limit), want)
                self.assertEqual(_extend_ref(data, a, b, limit), want)

    def test_find_matches_identity(self):
        for raw in self.corpus():
            with self.subTest(n=len(raw)):
                self.assertEqual(optimal.find_matches(raw),
                                 find_matches_ref(bytes(raw)))

    def test_plan_identity(self):
        # plan() is a pure function of (data, find_matches(data));
        # the reverted rewrite leaves candidates identical, and the DP
        # and emitter are untouched, so picks and bytes are identical.
        for raw in self.corpus():
            with self.subTest(n=len(raw)):
                picks = optimal.plan(raw)
                self.assertEqual(optimal.emit_commands(raw, picks),
                                 optimal.encode(raw))
                for p in picks:
                    if p[0] == 'match':
                        self.assertGreaterEqual(p[3], 1)


def pseudo(n, seed=1):
    out = bytearray()
    x = seed
    for _ in range(n):
        x = (1103515245 * x + 12345) & 0x7fffffff
        out.append((x >> 16) & 0xff)
    return bytes(out)


class PlanEmitTest(unittest.TestCase):
    def test_identity_random(self):
        raw = pseudo(5000) + bytes((i * 7) % 251 for i in range(5000))
        self.assertEqual(optimal.emit_commands(raw, optimal.plan(raw)),
                         optimal.encode(raw))

    def test_identity_edges(self):
        for raw in (b'', b'a', b'ab', b'abc', b'abcd',
                    bytes(112), bytes(113), pseudo(300)):
            with self.subTest(n=len(raw)):
                self.assertEqual(
                    optimal.emit_commands(raw, optimal.plan(raw)),
                    optimal.encode(raw))

    def test_emit_rejects_malformed(self):
        raw = pseudo(100)
        good = optimal.plan(raw)
        self.assertTrue(good)
        with self.assertRaises(ValueError):
            optimal.emit_commands(raw, [('bogus', 1)])
        with self.assertRaises(ValueError):
            optimal.emit_commands(raw, [('lit', 3)])
        with self.assertRaises(ValueError):
            optimal.emit_commands(raw, good[:-1])
        with self.assertRaises(ValueError):
            optimal.emit_commands(raw + b'x', good)


class SerializeTest(unittest.TestCase):
    def test_roundtrip(self):
        picks = [('lit', 8), ('match', 0, 45, 1234), ('match', 3, 5, 70000),
                 ('stop', 2)]
        back = [dec.parse_pick(dec.serialize_pick(p)) for p in picks]
        self.assertEqual(back, picks)

    def test_rejects(self):
        for bad in ('', 'X1', 'M1.2', 'M1', 'S', 'L', None, 42):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    dec.parse_pick(bad)

    def test_range_checks_live_in_emit(self):
        # Syntax-valid but out-of-range picks parse, then fail at emit.
        self.assertEqual(dec.parse_pick('L3'), ('lit', 3))
        self.assertEqual(dec.parse_pick('S4'), ('stop', 4))
        with self.assertRaises(ValueError):
            optimal.emit_commands(pseudo(16), [('lit', 3)])
        with self.assertRaises(ValueError):
            optimal.emit_commands(b'', [('stop', 4)])


class ReplayTest(unittest.TestCase):
    def _record(self, raw):
        cuts, plans, payloads = [len(raw)], None, None
        plans = [optimal.plan(raw)]
        payloads = [optimal.encode(raw)]
        import hashlib
        rec = {
            'sha256': dec.group_sha(raw),
            'params': dict(dec.PARAMS),
            'cuts': cuts,
            'blocks': [{'cmds': [dec.serialize_pick(p) for p in plans[0]],
                        'sha': hashlib.sha256(payloads[0]).hexdigest()}],
        }
        return rec

    def test_emit_group_matches_search(self):
        sys.path.insert(0, str(SHARED))
        try:
            import probe_worlds  # noqa: F401
            import relayout_stream  # noqa: F401
        finally:
            sys.path.remove(str(SHARED))
        raw = pseudo(3000, seed=7) + bytes((i * 13) % 251 for i in range(3000))
        rec = self._record(raw)
        data, n = dec.emit_group(raw, rec)
        self.assertEqual(n, 1)
        self.assertEqual(data, self._search_bytes(raw))

    def _search_bytes(self, raw):
        import struct
        from relayout_stream import BLOCK, CAPACITY
        payload = optimal.encode(raw)
        tag = b'CEND'
        return tag + struct.pack('<I', BLOCK) + payload + bytes(CAPACITY - len(payload))

    def test_emit_group_cuts_only_matches_search(self):
        sys.path.insert(0, str(SHARED))
        try:
            import probe_worlds  # noqa: F401
            import relayout_stream  # noqa: F401
        finally:
            sys.path.remove(str(SHARED))
        raw = pseudo(3000, seed=7) + bytes((i * 13) % 251 for i in range(3000))
        rec = self._record(raw)
        for b in rec['blocks']:
            b['cmds'] = None
        data, n = dec.emit_group(raw, rec)
        self.assertEqual(n, 1)
        self.assertEqual(data, self._search_bytes(raw))

    def test_emit_group_rejects_wrong_input(self):
        raw = pseudo(500, seed=9)
        rec = self._record(raw)
        with self.assertRaises(ValueError):
            dec.emit_group(raw + b'x', rec)
        rec2 = dict(rec, params=dict(rec['params'], margin=0))
        with self.assertRaises(ValueError):
            dec.emit_group(raw, rec2)


if __name__ == '__main__':
    unittest.main()
