#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Shipped RefPack packing decisions (no game bytes). [tk-speed-1]

Every world-archive pack in the Tricky chain funnels through
relayout_stream.pack_group, which searches per changed group:
  1. block cuts via largest_fitting() — binary search with greedy
     refpack_encode probes (window: full 131072 history, longest match
     per position, one-byte lookahead), minus a 96-byte margin, then a
     shrink-by-512 loop on the optimal payload;
  2. per-block commands via refpack_optimal.plan() — hash chains on
     3-byte keys (candidates=128 per key, skip_threshold=64 reuses a long
     match's tail), then backward dynamic programming over the 2/3/4-byte
     match forms plus 4..112 literal runs.
Search config everywhere: max_decoded=81920, margin=96, candidates=128,
skip_threshold=64 (all assemble_archive call sites use defaults; only
jobs varies, which never affects bytes).

A decision record is keyed by the sha256 of the decoded group bytes and
holds only parameters and offsets:
  cuts: decoded lengths of each block;
  per block: ordered command strings + the payload sha256.
No literal bytes, no compressed payload bytes, no game bytes of any kind:
literals are re-read from the user's own decoded bytes at replay, exactly
like the VU0/OM1 offset manifests.

Replay (REFPACK_DECISIONS=<data json>): when the input hash matches and
the params match, emit from the recorded commands with no search. Any
mismatch falls back to the full search and logs REFPACK-SEARCH-FALLBACK
loudly. Every replayed payload is verified against its recorded sha256
plus a refpack decode round-trip before use.

Capture (REFPACK_CAPTURE=<dir>): after each searched pack, one atomic
<group-sha>.json per changed group. gen_refpack_decisions.py merges a
capture dir into the committed data file.
"""
import hashlib
import json
import os
import tempfile
from pathlib import Path

PARAMS = {
    'max_decoded': 81920,
    'margin': 96,
    'candidates': 128,
    'skip_threshold': 64,
    'greedy_candidates': 64,
}

DATA_ENV = 'REFPACK_DECISIONS'
CAPTURE_ENV = 'REFPACK_CAPTURE'

_cache = {}


def group_sha(raw):
    return hashlib.sha256(bytes(raw)).hexdigest()


def serialize_pick(pick):
    kind = pick[0]
    if kind == 'lit':
        return f"L{pick[1]}"
    if kind == 'match':
        return f"M{pick[1]}.{pick[2]}.{pick[3]}"
    if kind == 'stop':
        return f"S{pick[1]}"
    raise ValueError(f'unknown pick kind {kind!r}')


def parse_pick(s):
    if not isinstance(s, str) or not s:
        raise ValueError('malformed pick string')
    tag, rest = s[0], s[1:]
    if tag == 'L':
        k = int(rest)
        return ('lit', k)
    if tag == 'M':
        p, L, D = rest.split('.')
        return ('match', int(p), int(L), int(D))
    if tag == 'S':
        return ('stop', int(rest))
    raise ValueError('malformed pick string')


def load_data(path):
    """Load the committed decisions file; returns {group_sha: record}."""
    key = str(path)
    try:
        mtime = Path(path).stat().st_mtime_ns
    except OSError:
        return {}
    hit = _cache.get(key)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}
    table = {}
    if isinstance(doc, dict):
        for rec in doc.get('groups', {}).values():
            if (isinstance(rec, dict) and isinstance(rec.get('sha256'), str)
                    and isinstance(rec.get('cuts'), list)
                    and isinstance(rec.get('blocks'), list)):
                table[rec['sha256']] = rec
    _cache[key] = (mtime, table)
    return table


def params_match(rec):
    return (isinstance(rec.get('params'), dict)
            and all(rec['params'].get(k) == v for k, v in PARAMS.items()))


def emit_group(raw, rec):
    """Replay a recorded decision into (blocks_bytes, nblocks).

    Raises ValueError on anything unexpected; the caller falls back to
    the search. Verifies each payload against its recorded sha256 and a
    refpack decode round-trip.
    """
    from refpack_optimal import emit_commands
    from probe_worlds import refpack
    from relayout_stream import BLOCK, CAPACITY
    import struct as _struct

    raw = bytes(raw)
    if not params_match(rec):
        raise ValueError('params differ')
    cuts = rec['cuts']
    blocks = rec['blocks']
    if (not all(isinstance(c, int) and c > 0 for c in cuts)
            or len(blocks) != len(cuts) or sum(cuts) != len(raw)):
        raise ValueError('cuts do not cover the input')
    out, start = [], 0
    for length, brec in zip(cuts, blocks):
        chunk = raw[start:start + length]
        if brec.get('cmds') is None:
            # Cuts-only record: skip the largest_fitting probe, still run
            # the deterministic optimal parse for the recorded cut.
            from refpack_optimal import plan as _plan
            picks = _plan(chunk)
            payload = emit_commands(chunk, picks)
        else:
            picks = [parse_pick(s) for s in brec['cmds']]
            payload = emit_commands(chunk, picks)
        if len(payload) > CAPACITY:
            raise ValueError('replayed payload overflows the block')
        if hashlib.sha256(payload).hexdigest() != brec['sha']:
            raise ValueError('replayed payload sha differs')
        if refpack(payload)[0] != chunk:
            raise ValueError('replayed payload round trip failed')
        tag = b'CEND' if start + length >= len(raw) else b'CBXS'
        out.append(tag + _struct.pack('<I', BLOCK) + payload
                   + bytes(CAPACITY - len(payload)))
        start += length
    return b''.join(out), len(blocks)


def capture_group(capture_dir, raw, cuts, plans, payloads):
    """Atomically store one searched group decision. Best effort."""
    try:
        sha = group_sha(raw)
        rec = {
            'sha256': sha,
            'params': dict(PARAMS),
            'cuts': list(cuts),
            'blocks': [
                {'cmds': [serialize_pick(p) for p in plan],
                 'sha': hashlib.sha256(bytes(p)).hexdigest()}
                for plan, p in zip(plans, payloads)
            ],
        }
        dest = Path(capture_dir) / f'{sha}.json'
        if dest.exists():
            return
        fd, tmp = tempfile.mkstemp(dir=str(capture_dir), prefix=sha,
                                    suffix='.tmp')
        with os.fdopen(fd, 'w') as f:
            f.write(json.dumps(rec) + '\n')
        os.replace(tmp, dest)
    except OSError:
        pass
