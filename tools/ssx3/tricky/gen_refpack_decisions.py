#!/usr/bin/env python3
"""Build the committed RefPack decisions file from a capture dir. [tk-speed-1]

A capture dir holds one <group-sha>.json per searched group (written by
pack_group when REFPACK_CAPTURE=<dir>). This merges them into
tools/ssx3/tricky/data/refpack-decisions.json, keyed by input content
hash, decisions only (cuts + command strings + payload shas — no game
bytes), with a header recording the source run and tool versions.

Usage:
  REFPACK_CAPTURE=<capdir> run_chain.py ... --course pipe   # capture
  gen_refpack_decisions.py --capture <capdir> --source run-10/pipe
  # then replay:
  REFPACK_DECISIONS=tools/ssx3/tricky/data/refpack-decisions.json run_chain.py ...

Byte-identity gate: replay must reproduce the oracle finals exactly
(run-10 per-course finals, oracle8 late phases). A hash mismatch falls
back to the search with a loud REFPACK-SEARCH-FALLBACK log line.
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE / 'shared'
sys.path.insert(0, str(SHARED))
import refpack_decisions as dec  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--capture', type=Path, required=True,
                    help='capture dir with <sha>.json files')
    ap.add_argument('--source', default='',
                    help='provenance label, e.g. run-10/pipe-capture')
    ap.add_argument('--output', type=Path,
                    default=HERE / 'data' / 'refpack-decisions.json')
    ap.add_argument('--check', type=Path, default=None,
                    help='optional: verify every record replays from raw?? '
                         '(no raw stored; validates schema + pick syntax only)')
    ap.add_argument('--with-commands', action='store_true',
                    help='keep per-block command strings (experiment only: '
                         '~600x bigger; default strips to cuts-only)')
    args = ap.parse_args()
    files = sorted(args.capture.glob('*.json'))
    if not files:
        ap.error(f'no capture files in {args.capture}')
    groups = {}
    for f in files:
        try:
            rec = json.loads(f.read_text())
        except ValueError:
            print(f'skip unreadable {f.name}', flush=True)
            continue
        sha = rec.get('sha256')
        if not isinstance(sha, str) or f.name != f'{sha}.json':
            print(f'skip misnamed {f.name}', flush=True)
            continue
        if not dec.params_match(rec):
            print(f'skip params-mismatch {sha[:16]}', flush=True)
            continue
        try:
            for b in rec['blocks']:
                for s in b['cmds']:
                    dec.parse_pick(s)
        except (KeyError, ValueError, TypeError):
            print(f'skip malformed {sha[:16]}', flush=True)
            continue
        prev = groups.get(sha)
        if prev is not None and prev != rec:
            ap.error(f'conflicting captures for {sha[:16]}')
        if not args.with_commands:
            rec = dict(rec)
            rec['blocks'] = [dict(b, cmds=None) for b in rec['blocks']]
        groups[sha] = rec
    doc = {
        'format': 'refpack-decisions-v1',
        'source': args.source,
        'params': dict(dec.PARAMS),
        'groups': groups,
    }
    blob = json.dumps(doc, sort_keys=True) + '\n'
    digest = hashlib.sha256(blob.encode()).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(blob)
    nblocks = sum(len(r['blocks']) for r in groups.values())
    print(f'wrote {args.output}: groups={len(groups)} blocks={nblocks} '
          f'bytes={len(blob)} sha256={digest[:16]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
