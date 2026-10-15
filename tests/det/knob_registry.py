#!/usr/bin/env python3
"""Check registered PS2X_/GE1_ runtime names at a selected engine revision.

Direct getenv literals and quoted names passed through tables/helpers are covered.
Generated runner code and tests are excluded. The GE1 adapter is vendored under
ps2xRuntime/third_party. Both ownership roots are scanned: the engine checkout
(via git objects) and this repo's tools/ssx3 + games trees.
"""

import argparse
import csv
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # tests/det -> repo root
DEFAULT_ENGINE = ROOT / "deps" / "PS2Recomp"
NAME = re.compile(r'"((?:PS2X|GE1)_[A-Z0-9_]+)"')
DIRECT = re.compile(r'(?:std::)?getenv\s*\(\s*"((?:PS2X|GE1)_[A-Z0-9_]+)"')
CLASSES = {'play-default', 'user-toggle', 'reference', 'gate', 'diag', 'active-lane', 'dead'}
SOURCE_EXT = {'.c', '.cc', '.cpp', '.h', '.hpp', '.inl', '.mm'}


def git(fork, *args):
    result = subprocess.run(['git', '-C', str(fork), *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f'git {" ".join(args)}: {result.stderr.strip()}')
    return result.stdout


def source_names(source):
    """Return every quoted knob and the subset directly passed to getenv."""
    return set(NAME.findall(source)), set(DIRECT.findall(source))


def names_at_engine(engine, rev):
    paths = git(engine, 'ls-tree', '-r', '--name-only', rev, '--', 'ps2xRuntime').splitlines()
    all_names, direct = {}, set()
    for path in paths:
        if Path(path).suffix not in SOURCE_EXT or path.startswith('ps2xRuntime/src/runner/'):
            continue
        source = git(engine, 'show', f'{rev}:{path}')
        quoted, getenv = source_names(source)
        for name in quoted:
            all_names.setdefault(name, 'engine: ' + path)
        direct.update(getenv)
    return all_names, direct


def names_at_tree(root):
    all_names, direct = {}, set()
    for base in ('tools/ssx3', 'games'):
        for path in sorted((root / base).rglob('*')):
            if not path.is_file() or path.suffix not in SOURCE_EXT:
                continue
            try:
                source = path.read_text()
            except OSError:
                continue
            quoted, getenv = source_names(source)
            for name in quoted:
                all_names.setdefault(name, str(path.relative_to(root)))
            direct.update(getenv)
    return all_names, direct


def load_registry(path):
    with open(path, newline='') as f:
        rows = list(csv.DictReader((line for line in f if not line.startswith('#')), delimiter='\t'))
    duplicates = []
    result = {}
    for row in rows:
        name = row['name']
        if name in result:
            duplicates.append(name)
        result[name] = row
    return result, duplicates


def problems(found, registry, duplicates):
    errors = [f'DUPLICATE {name}' for name in duplicates]
    for name, row in sorted(registry.items()):
        if row['class'] not in CLASSES:
            errors.append(f'BADCLASS {name}: {row["class"]!r}')
        if name in found and row['class'] == 'dead':
            errors.append(f'LIVE_MARKED_DEAD {name}: {found[name]}')
        if name in found and row['owner_lane'] in ('', 'not found'):
            errors.append(f'NO_OWNER {name}: {found[name]}')
    for name in sorted(set(found) - set(registry)):
        errors.append(f'MISSING {name}: {found[name]}')
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--rev', default='HEAD')
    parser.add_argument('--engine', type=Path, default=None)
    parser.add_argument('--fork', type=Path, default=None, help='alias for --engine')
    parser.add_argument('--registry', type=Path, default=HERE / 'knob_registry.tsv')
    parser.add_argument('--verbose', action='store_true')
    args = parser.parse_args()
    try:
        engine = args.engine or args.fork or DEFAULT_ENGINE
        found_e, direct_e = names_at_engine(engine, args.rev)
        found_p, direct_p = names_at_tree(ROOT)
        found = {**found_e, **{k: v for k, v in found_p.items() if k not in found_e}}
        direct = direct_e | direct_p
        registry, duplicates = load_registry(args.registry)
    except (OSError, RuntimeError, KeyError) as error:
        print(f'knob-registry: {error}', file=sys.stderr)
        return 2
    if args.list:
        for name, path in sorted(found.items()):
            print(f'{name}\t{"direct" if name in direct else "indirect"}\t{path}')
        return 0
    errors = problems(found, registry, duplicates)
    for error in errors:
        print(error)
    if args.verbose:
        for name in sorted(set(registry) - set(found)):
            print(f'NO_SOURCE_LITERAL {name}: retained historical/build/vendor row')
    print(f'knob-registry: {len(found)} source literals ({len(direct)} direct), '
          f'{len(registry)} registry rows, {len(errors)} problem(s) '
          f'-> {"FAIL" if errors else "PASS"}')
    return 1 if errors else 0


if __name__ == '__main__':
    sys.exit(main())
