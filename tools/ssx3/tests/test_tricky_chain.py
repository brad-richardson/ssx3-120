"""Tricky world-chain validation: recipes, helpers, and shared BIGF reader.

Synthetic only: no game data, no ISOs. Golden-SHA gates on real chain
outputs live in run_chain.py itself (check() per rung); the workdir-golden
test below runs them against a finished workdir when present, else skips.
"""
import importlib.util
import struct
import tempfile
import unittest
from pathlib import Path

TRICKY = Path(__file__).resolve().parents[1] / 'tricky'
DATA = TRICKY / 'data'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


run_chain = load('tk_run_chain', TRICKY / 'run_chain.py')


def load_no_main(name, path):
    """Load a converter that calls main() at import (e.g. big.py)."""
    import types
    source = Path(path).read_text()
    source = source.split('\ndef main')[0]
    mod = types.ModuleType(name)
    exec(compile(source, str(path), 'exec'), mod.__dict__)
    return mod


big = load_no_main('tk_big', TRICKY / 'tk25' / 'big.py')


def make_big(members):
    """Minimal synthetic BIGF: {name: bytes} -> archive bytes.

    Member offsets are absolute file offsets (data follows the header),
    matching what big.members() seeks.
    """
    names = [n.encode('latin1') + b'\0' for n in members]
    header_len = sum(8 + len(n) for n in names)
    base = 16 + header_len
    header = b''
    blob = b''
    for name, payload in members.items():
        header += struct.pack('>II', base + len(blob), len(payload))
        header += name.encode('latin1') + b'\0'
        blob += payload
    return (b'BIGF' + b'\0\0\0\0' + struct.pack('>II', len(members),
                                               header_len) + header + blob)


class BigReaderTest(unittest.TestCase):
    def test_members_lists_synthetic_archive(self):
        archive = make_big({'A.BIG': b'hello', 'B.BIG': b'w' * 3000})
        with tempfile.TemporaryDirectory(prefix='tk-big-') as temp:
            p = Path(temp) / 'SYN.BIG'
            p.write_bytes(archive)
            with open(p, 'rb') as f:
                ms = big.members(f, 0)
        self.assertEqual([m[0] for m in ms], ['A.BIG', 'B.BIG'])
        self.assertEqual([m[2] for m in ms], [5, 3000])

    def test_getf_extracts_member_bytes(self):
        archive = make_big({'A.BIG': b'hello', 'B.BIG': b'w' * 3000})
        with tempfile.TemporaryDirectory(prefix='tk-big-') as temp:
            p = Path(temp) / 'SYN.BIG'
            p.write_bytes(archive)
            out = Path(temp) / 'B.BIG'
            with open(p, 'rb') as f:
                base = 0
                for name, off, size in big.members(f, base):
                    if name == 'B.BIG':
                        f.seek(base + off)
                        out.write_bytes(f.read(size))
            self.assertEqual(out.read_bytes(), b'w' * 3000)

    def test_members_rejects_non_archive(self):
        with tempfile.TemporaryDirectory(prefix='tk-big-') as temp:
            p = Path(temp) / 'NOT.BIG'
            p.write_bytes(b'JUNK' + b'\0' * 64)
            with open(p, 'rb') as f:
                with self.assertRaises(AssertionError):
                    big.members(f, 0)


class CheckHelperTest(unittest.TestCase):
    def test_full_and_prefix_matches_pass(self):
        with tempfile.TemporaryDirectory(prefix='tk-check-') as temp:
            import hashlib
            p = Path(temp) / 'X.BIG'
            p.write_bytes(b'test-payload')
            digest = hashlib.sha256(b'test-payload').hexdigest()
            run_chain.check(p, digest, 'full')
            run_chain.check(p, digest[:8], 'prefix')

    def test_mismatch_raises(self):
        with tempfile.TemporaryDirectory(prefix='tk-check-') as temp:
            p = Path(temp) / 'X.BIG'
            p.write_bytes(b'test-payload')
            with self.assertRaises(SystemExit):
                run_chain.check(p, '0' * 64, 'mismatch')

    def test_none_oracle_skips(self):
        with tempfile.TemporaryDirectory(prefix='tk-check-') as temp:
            p = Path(temp) / 'X.BIG'
            p.write_bytes(b'test-payload')
            run_chain.check(p, None, 'skipped')


class RecipeTableTest(unittest.TestCase):
    REQUIRED = {'big', 'donor', 'ps2_tool', 'build_args', 'race_tool',
                'race_args', 'scenery_tool', 'rails_tool'}

    def test_ten_courses_present(self):
        self.assertEqual(len(run_chain.COURSES), 10)

    def test_required_keys_and_tools_exist(self):
        for course, recipe in run_chain.COURSES.items():
            self.assertTrue(self.REQUIRED <= set(recipe), course)
            for key in ('ps2_tool', 'scenery_tool', 'rails_tool'):
                tool = recipe.get(key)
                if tool:
                    self.assertTrue((TRICKY / tool).exists(),
                                    f'{course} {key}={tool}')
            terrain = recipe.get('terrain_tool')
            if terrain:
                self.assertTrue((TRICKY / terrain).exists(),
                                f'{course} terrain_tool={terrain}')

    def test_fence_files_exist_and_parse(self):
        for course, recipe in run_chain.COURSES.items():
            fence = (recipe.get('fence_file') or '').format(c=course)
            if not fence:
                continue
            p = DATA / fence
            self.assertTrue(p.exists(), f'{course} {fence}')
            ids = p.read_text().split()
            self.assertTrue(ids, f'{course} {fence} empty')
            self.assertTrue(all(n.isdigit() for n in ids),
                            f'{course} {fence} non-numeric entry')

    def test_no_duplicate_recipe_keys(self):
        import re
        source = (TRICKY / 'run_chain.py').read_text()
        for course in run_chain.COURSES:
            block = source.split(f"'{course}': dict(")[1].split('\n    ),')[0]
            keys = re.findall(r'^ {8}(\w+)=', block, re.M)
            self.assertEqual(len(keys), len(set(keys)),
                             f'{course} duplicates: {keys}')

    def test_oracles_are_hex(self):
        for course, recipe in run_chain.COURSES.items():
            for key, value in recipe.items():
                if key.startswith('oracle_') and value is not None:
                    self.assertRegex(value, r'^[0-9a-f]+$',
                                     f'{course} {key}={value}')

    def test_data_placeholders_resolve(self):
        fmt = dict(donor=Path('/nonexistent'), course='X',
                   data=DATA, lane=Path('/nonexistent'))
        for course, recipe in run_chain.COURSES.items():
            for key in ('scenery_extra', 'terrain_extra'):
                for arg in recipe.get(key) or []:
                    rendered = arg.format(**fmt)
                    if rendered.startswith(str(DATA)):
                        self.assertTrue(
                            Path(rendered).exists(),
                            f'{course} {key} missing file {rendered}')


class RoutesRungTest(unittest.TestCase):
    """The tk-routes-2 follow-up rung sits after s5 (post-golden commit).

    Aloha covers3, merq wall2819 and mega tube94s2 apply route_step.py to
    the s5-final play world (mega: recorded reference, input-SHA-gated,
    until the mega-h/u2/c tail lands).
    """
    COURSES = {'aloha': 22, 'merq': 148, 'mega': 94}

    def test_routes_wired_only_on_routes_courses(self):
        wired = {c for c, r in run_chain.COURSES.items()
                 if r.get('routes_args')}
        self.assertEqual(wired, set(self.COURSES))

    def test_routes_groups_and_tool(self):
        tool = TRICKY / 'tkroutes' / 'route_step.py'
        self.assertTrue(tool.exists())
        for course, group in self.COURSES.items():
            recipe = run_chain.COURSES[course]
            self.assertEqual(recipe.get('routes_group'), group, course)
            self.assertTrue(recipe.get('routes_args'), course)
            oracle = recipe.get('oracle_routes')
            self.assertRegex(oracle, r'^[0-9a-f]{64}$', course)

    def test_no_routes_reference_anywhere(self):
        # Megaplex ships skipped-with-record until its tail lands; no lab
        # reference file may feed any course (tk-nogc-1).
        for course, recipe in run_chain.COURSES.items():
            self.assertNotIn('routes_ref', recipe, course)
            self.assertNotIn('routes_ref_sha', recipe, course)

    def test_routes_rung_skips_without_chain_base(self):
        import types
        with tempfile.TemporaryDirectory(prefix='tk-chain-') as temp:
            args = types.SimpleNamespace(workdir=temp, lane_dir=temp)
            chain = run_chain.Chain(args)
            recipe = dict(run_chain.COURSES['mega'])
            self.assertIsNone(chain.rung_routes('mega', recipe, None))

    def test_routes_rung_after_s5(self):
        order = run_chain.RUN_ORDER
        self.assertLess(order.index('s5'), order.index('routes'))


class ResetsRungTest(unittest.TestCase):
    """The ov-s rung sits between race and scenery (regression cover).

    Gari scenery once ran directly on the race world and missed its oracle;
    the resets rung routes through ov-s wherever the PS2 donor carries
    authored reset surfaces (tk-nogc-1: .pbd patch tail, no GC reader).
    """

    def test_order_runs_resets_between_race_and_scenery(self):
        order = run_chain.RUN_ORDER
        self.assertLess(order.index('race'), order.index('resets'))
        self.assertLess(order.index('resets'), order.index('scenery'))

    def test_resets_tool_present_where_needed(self):
        tool = TRICKY / 'tk12' / 'make_resets.py'
        for course, recipe in run_chain.COURSES.items():
            if recipe.get('resets_pbd'):
                self.assertTrue(tool.exists(), course)

    def test_passthrough_without_reset_data(self):
        import types
        with tempfile.TemporaryDirectory(prefix='tk-chain-') as temp:
            args = types.SimpleNamespace(workdir=temp, lane_dir=temp)
            chain = run_chain.Chain(args)
            for course, recipe in run_chain.COURSES.items():
                if not recipe.get('resets_pbd'):
                    sentinel = Path(temp) / 'race.BIG'
                    self.assertEqual(
                        chain.rung_resets(course, recipe, sentinel,
                                          Path(temp), Path(temp)),
                        sentinel)

    def test_resets_courses_match_driver_manifest(self):
        # Every manifest alias resolves to a course key; the PS2-surface
        # resets courses are the same four (tk-nogc-1: resets_pbd).
        driver = load('tk_build_tricky', TRICKY / 'build_tricky.py')
        for _event, alias, _name in driver.MANIFEST_ROWS:
            self.assertIn(alias, driver.MANIFEST_COURSE, alias)
        resets = {c for c, r in run_chain.COURSES.items()
                  if r.get('resets_pbd')}
        self.assertEqual(resets, {'snow', 'gari', 'mesa', 'aloha'})


class SceneryTmpTest(unittest.TestCase):
    """Scenery scratch stays in the run's workdir (regression cover).

    All scenery tools default --tmp to a lab home path; the driver must
    pass a per-course dir under --workdir (read-only HOME safe, unique
    across concurrent builds, inside disk accounting). Covers the b
    rung and the alternate sg rung.
    """

    def _capture_scenery_argv(self, rung, course='merq'):
        import types
        from unittest.mock import patch
        with tempfile.TemporaryDirectory(prefix='tk-chain-') as temp:
            args = types.SimpleNamespace(workdir=temp, lane_dir=temp)
            chain = run_chain.Chain(args)
            seen = []

            def stop(step, *cmd, **kw):
                seen.append((step, list(cmd)))
                raise SystemExit('stop-after-capture')

            recipe = run_chain.COURSES[course]
            with patch.object(run_chain, 'run', side_effect=stop):
                with self.assertRaises(SystemExit) as ctx:
                    if rung == 'b':
                        chain.rung_scenery(course, recipe,
                                           Path(temp) / 'race.BIG',
                                           Path(temp), Path(temp))
                    else:
                        chain.rung_sg(course, recipe,
                                      Path(temp) / 's.BIG',
                                      Path(temp), Path(temp))
                self.assertIn('stop-after-capture', str(ctx.exception))
            return temp, seen

    def test_scenery_b_rung_passes_workdir_tmp(self):
        temp, seen = self._capture_scenery_argv('b')
        step, cmd = seen[0]
        self.assertEqual(step, 'scenery')
        self.assertIn('--tmp', cmd)
        tmp = cmd[cmd.index('--tmp') + 1]
        self.assertEqual(Path(tmp), Path(temp) / 'w-merq-b' / 'tmp')

    def test_scenery_sg_rung_passes_workdir_tmp(self):
        temp, seen = self._capture_scenery_argv('sg')
        step, cmd = seen[0]
        self.assertEqual(step, 'sg-scenery')
        self.assertIn('--tmp', cmd)
        tmp = cmd[cmd.index('--tmp') + 1]
        self.assertEqual(Path(tmp), Path(temp) / 'w-merq-sg' / 'tmp')


def load_make_resets():
    """Load make_resets with the chain's import layout (tk12 + shared)."""
    import sys
    for sub in ('tk12', 'shared'):
        p = str(TRICKY / sub)
        if p not in sys.path:
            sys.path.insert(0, p)
    return load('tk_make_resets', TRICKY / 'tk12' / 'make_resets.py')


def synthetic_pbd(surfaces):
    """Minimal .pbd patch table: 448-byte patches, coeffs + tail surface."""
    n = len(surfaces)
    start, inst = 144, 144 + 448 * n
    raw = bytearray(inst + 64)
    struct.pack_into('<I', raw, 8, n)
    struct.pack_into('<I', raw, 68, start)
    struct.pack_into('<I', raw, 72, inst)
    for i, surface in enumerate(surfaces):
        at = start + 448 * i
        for j in range(16):
            struct.pack_into('<4f', raw, at + 80 + 16 * j,
                             float(i), float(j), 0.5, 1.0)
        struct.pack_into('<I4h', raw, at + 424, surface, 0, 0, 1, 2)
    return bytes(raw)


class PbdSurfacesTest(unittest.TestCase):
    """The PS2 surface reader (tk-nogc-1 resets source)."""

    def test_surfaces_come_from_the_pbd_tail(self):
        make_resets = load_make_resets()
        src = make_resets.pbd_surfaces(synthetic_pbd([0, 5, 0]))
        self.assertEqual([s for _, s in src], [0, 5, 0])
        self.assertEqual(len(src[0][0]), 16)

    def test_garbage_and_stub_tables_refused(self):
        make_resets = load_make_resets()
        with self.assertRaises(ValueError):
            make_resets.pbd_surfaces(b'\0' * 200)
        # ps2_as_gc stub shape: valid GC magic, no terrain section
        # (count word stays 0) — never a resets source.
        stub = bytearray(160)
        struct.pack_into('>I', stub, 0, 0x00161b03)
        with self.assertRaises(ValueError):
            make_resets.pbd_surfaces(bytes(stub))

    def test_tool_takes_pbd_not_nbd(self):
        make_resets = load_make_resets()
        self.assertIn('DONOR.pbd', make_resets.__doc__)
        source = (TRICKY / 'tk12' / 'make_resets.py').read_text()
        self.assertNotIn('gamecube_surfaces', source)


class PublicInputsTest(unittest.TestCase):
    """Pins are an optional comparison input; the workdir wins by default."""

    def test_pins_absent_falls_back_fail_closed(self):
        import types
        with tempfile.TemporaryDirectory(prefix='tk-chain-') as temp:
            args = types.SimpleNamespace(workdir=temp, lane_dir=temp,
                                         pins=None)
            chain = run_chain.Chain(args)
            self.assertIsNone(chain.pin_file('donor', 'mesa', 'mesa.pbd'))
            with self.assertRaises(SystemExit):
                chain.need(None, 'test input')


class NoGameCubeTest(unittest.TestCase):
    """No GameCube code path stays reachable from the public chain."""

    def test_no_gc_keys_in_recipes(self):
        for course, recipe in run_chain.COURSES.items():
            for key in ('race_aip', 'resets_nbd', 'routes_ref',
                        'routes_ref_sha'):
                self.assertNotIn(key, recipe, course)
            for value in recipe.values():
                if isinstance(value, str):
                    self.assertFalse(value.startswith('gc/'), course)

    def test_no_gc_plumbing_in_driver(self):
        source = (TRICKY / 'run_chain.py').read_text()
        for token in ('self.a.gc_dir', 'self.a.routes_ref_dir',
                      'gamecube_surfaces', '--gc-dir', '--routes-ref-dir'):
            self.assertNotIn(token, source)


class WorkdirGoldenTest(unittest.TestCase):
    """Per-rung golden SHAs against a finished --workdir (skips if absent)."""

    def test_finished_workdir_matches_oracles(self):
        import os
        workdir = os.environ.get('PS2X_TK_WORKDIR')
        if not workdir:
            self.skipTest('PS2X_TK_WORKDIR not set')
        work = Path(workdir)
        checked = 0
        for course, recipe in run_chain.COURSES.items():
            cands = {
                'oracle_f': work / f'w-{course}-b' / f'{course.upper()}.BIG',
                'oracle_c': work / f'w-{course}-c' / f'{course.upper()}.BIG',
            }
            for key, path in cands.items():
                oracle = recipe.get(key)
                if oracle and path.exists():
                    run_chain.check(path, oracle, f'{course} {key}')
                    checked += 1
        if not checked:
            self.skipTest(f'no rung outputs in {workdir}')


if __name__ == '__main__':
    unittest.main()
