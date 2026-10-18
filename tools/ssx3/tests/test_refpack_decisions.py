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
