#!/usr/bin/env python3
"""Verify the byte-preservation manifest (no disc, no network).

- Every file listed in SOURCE_MANIFEST.tsv must match its recorded SHA256.
- Every fork-origin row must additionally match the pinned engine object
  (`git show <engine-pin>:<old-path>` inside deps/PS2Recomp).
- Lab/infra rows carry provenance in SOURCE_PROVENANCE.md; their split-time
  bytes are recorded by the manifest hash (auditable against the cited sources).
- Authored (new) and composition rows may change after the split; a changed
  hash is reported as EDITED-SINCE-SPLIT, not a failure. Verbatim rows stay
  strict.
Usage: python3 tests/provenance/check_manifest.py  (run from the repo root)
"""
import hashlib
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def sha256_file(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def main():
    engine = ROOT / 'deps' / 'PS2Recomp'
    pin = subprocess.check_output(['git', '-C', str(engine), 'rev-parse', 'HEAD'], text=True).strip()
    bad, expected, edited, checked, engine_checked = [], [], [], 0, 0
    with (ROOT / 'SOURCE_MANIFEST.tsv').open() as f:
        header = f.readline()
        assert header.startswith('new_path\torigin'), header
        for line in f:
            new, origin, old, want, size, status = line.rstrip('\n').split('\t')
            p = ROOT / new
            if status.startswith('removed:'):
                if p.exists():
                    bad.append((new, 'marked removed but present'))
                    continue
                expected.append((new, status))
                checked += 1
                continue
            if not p.is_file():
                bad.append((new, 'missing file'))
                continue
            if sha256_file(p) != want:
                # Verbatim rows must stay byte-identical to the engine copy.
                # Authored (new) and composition rows are this repo's own files:
                # edits after the split are expected and reported, not fatal.
                if status == 'verbatim':
                    bad.append((new, 'manifest sha mismatch (verbatim row edited; '
                                     'reclassify it as composition:<reason>)'))
                else:
                    edited.append(new)
                    checked += 1
                continue
            if origin.startswith('fork@'):
                if status == 'verbatim':
                    exp = hashlib.sha256(subprocess.check_output(
                        ['git', '-C', str(engine), 'show', pin + ':' + old])).hexdigest()
                    if exp != want:
                        bad.append((new, 'differs from engine@%s:%s' % (pin[:8], old)))
                        continue
                    engine_checked += 1
                elif status == 'moved':
                    # split-p1-prep-1: product-owned since the split; the engine
                    # checkout no longer carries these bytes, so they verify
                    # against the frozen source commit named by the fork@ origin.
                    ref = origin.split('@', 1)[1]
                    exp = hashlib.sha256(subprocess.check_output(
                        ['git', '-C', str(engine), 'show', ref + ':' + old])).hexdigest()
                    if exp != want:
                        bad.append((new, 'differs from %s:%s' % (origin, old)))
                        continue
                    engine_checked += 1
                elif status.startswith('composition:'):
                    expected.append((new, status))
                else:
                    bad.append((new, 'unknown status ' + status))
                    continue
            checked += 1
    print('checked=%d engine_verified=%d composition=%d bad=%d engine=%s' % (
        checked, engine_checked, len(expected), len(bad), pin[:8]))
    for name, why in expected:
        print('EXPECTED-DIFF %s: %s' % (name, why))
    for name in edited:
        print('EDITED-SINCE-SPLIT %s' % name)
    for name, why in bad:
        print('BAD %s: %s' % (name, why))
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
