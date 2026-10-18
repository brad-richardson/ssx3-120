"""bake_gems pairing tests (tk-speed-1-gems). No game data, no discs.

Locks the fix for 'GARI: kind-3 3334 != scenery 3332': show-hidden
lights appended downstream of scenery (gari sg_show 136/138, mesa
finalw_show 63/64) have no scenery mapping and must be ignored (never
baked), while missing mapped records still fail closed.
"""
import importlib.util
import struct
import sys
import unittest
from pathlib import Path

TRICKY = Path(__file__).resolve().parents[1] / 'tricky'


def load_bake_gems():
    for d in ('tk12', 'tk13', 'tk30', 'shared'):
        sys.path.insert(0, str(TRICKY / d))
    sys.path.insert(0, str(TRICKY))
    try:
        path = TRICKY / 'bake_gems.py'
        spec = importlib.util.spec_from_file_location(
            'tk_speed_bake_gems', path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules['tk_speed_bake_gems'] = mod
        spec.loader.exec_module(mod)
        return mod
    finally:
        sys.path.remove(str(TRICKY))
        for d in ('tk12', 'tk13', 'tk30', 'shared'):
            sys.path.remove(str(TRICKY / d))


bg = load_bake_gems()


def payload(x, y, z, rad):
    p = bytearray(108)
    struct.pack_into('<16f', p, 16, *((0.0,) * 12 + (x, y, z, 0.0)))
    struct.pack_into('<f', p, 92, rad)
    return bytes(p)


def names_for(n, gems):
    names = {}
    for i in range(n):
        if i in gems:
            names[i] = f'Gem_TrickMultiplier_Yellow_{i}'
        else:
            names[i] = f'Mdl_Other_{i}'
    return names


class PairCourseTest(unittest.TestCase):
    def test_exact_pair(self):
        k3 = {i: payload(float(i), 0.0, 0.0, 10.0) for i in range(10)}
        src = list(range(10))
        gems, ignored = bg.pair_course('T', k3, src, names_for(10, {3, 7}))
        self.assertEqual([g[0] for g in gems], [3, 7])
        self.assertEqual(ignored, [])

    def test_gari_shape_extras_ignored(self):
        # r13b gari: 3332 mapped + appended show lights 3332/3333.
        k3 = {i: payload(float(i), 0.0, 0.0, 10.0) for i in range(3334)}
        src = list(range(3332))
        gems, ignored = bg.pair_course('GARI', k3, src,
                                       names_for(3332, {100, 2000}))
        self.assertEqual([g[0] for g in gems], [100, 2000])
        self.assertEqual(ignored, [3332, 3333])

    def test_missing_mapped_fails(self):
        k3 = {i: payload(0.0, 0.0, 0.0, 10.0) for i in range(9)}
        src = list(range(10))
        with self.assertRaises(SystemExit):
            bg.pair_course('T', k3, src, names_for(10, {3}))

    def test_missing_gem_rid_fails(self):
        k3 = {i: payload(0.0, 0.0, 0.0, 10.0) for i in range(10)
              if i != 3}
        k3[10] = payload(0.0, 0.0, 0.0, 10.0)
        src = list(range(10))
        with self.assertRaises(SystemExit):
            bg.pair_course('T', k3, src, names_for(10, {3}))

    def test_unknown_colour_fails(self):
        k3 = {0: payload(1.0, 2.0, 3.0, 10.0)}
        with self.assertRaises(SystemExit):
            bg.pair_course('T', k3, [0], {0: 'Gem_TrickMultiplier_Purple_0'})

    def test_bad_sphere_fails(self):
        k3 = {0: payload(1.0, 2.0, 3.0, -1.0)}
        with self.assertRaises(SystemExit):
            bg.pair_course('T', k3, [0], names_for(1, {0}))


if __name__ == '__main__':
    unittest.main()
