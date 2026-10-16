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


class ResetsRungTest(unittest.TestCase):
    """The ov-s rung sits between race and scenery (regression cover).

    Gari scenery once ran directly on the race world and missed its oracle;
    the resets rung routes through ov-s wherever GC reset data exists.
    """

    def test_order_runs_resets_between_race_and_scenery(self):
        order = run_chain.RUN_ORDER
        self.assertLess(order.index('race'), order.index('resets'))
        self.assertLess(order.index('resets'), order.index('scenery'))

    def test_resets_tool_present_where_needed(self):
        tool = TRICKY / 'tk12' / 'make_resets.py'
        for course, recipe in run_chain.COURSES.items():
            if recipe.get('resets_nbd'):
                self.assertTrue(tool.exists(), course)

    def test_passthrough_without_reset_data(self):
        import types
        with tempfile.TemporaryDirectory(prefix='tk-chain-') as temp:
            args = types.SimpleNamespace(workdir=temp, lane_dir=temp)
            chain = run_chain.Chain(args)
            for course, recipe in run_chain.COURSES.items():
                if not recipe.get('resets_nbd'):
                    sentinel = Path(temp) / 'race.BIG'
                    self.assertEqual(
                        chain.rung_resets(course, recipe, sentinel,
                                          Path(temp), Path(temp)),
                        sentinel)


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
