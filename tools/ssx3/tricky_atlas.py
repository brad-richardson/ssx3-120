#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12,<3.13"
# dependencies = ["Pillow==13.0.0"]
# ///
"""Tricky picker atlas step (standalone; version-independent).

Bakes the picker preview atlas (`tklist-art.bin`, TKLIST1: the SSX 3
`moun` mountain + the Tricky TP course previews) into an EXISTING
converted/imported Tricky layer and records it — without touching any
Tricky converter input, so `tricky_converter_version()` never moves for
this file.

Run AFTER `build.py tricky` (convert) or import, e.g.::

    uv run tools/ssx3/tricky_atlas.py --work <work> --ssx3-iso <SSX3.iso>

(Pillow is provisioned by the inline metadata above; a bare `python3`
works only where Pillow is already installed.)

The step is keyed on its own inputs (this file, the bake script
`build_tk_list_art.py`, `tklist-codes.tsv`, the SSX 3 `FE_1.SSH` bytes and
the staged TP pane bytes) and (re)bakes when the atlas is missing or the
key is stale. It also repairs record drift: a reconverted layer whose
`tricky_inputs.json` lost the atlas entries gets them re-applied without
a rebake when the atlas bytes still match.

Placement: the atlas lands at `<work>/tricky/tricky-ov/tklist-art.bin`.
`tricky-ov/` is an allowlisted whole tree, so both stage paths copy it
with zero builder changes, and the record glob already covers tree
contents — the step only extends `tricky_inputs.json` `outputs` (atlas +
re-hashed `tricky.env`). The env row
`PS2X_SSX3_TRICKY_LIST_ART=.../files/tricky-ov/tklist-art.bin` is
maintained in the staged `tricky.env` by this step (same Android device
prefix the driver bakes, so the iOS `${BUNDLE}/tricky/` rewrite applies).
No game bytes land in git: only parsers, rects and short names.

Atlas record: `<work>/tklist-atlas.json` (key + output hash). `--check-only`
exits 0 when current, 1 with a message when a bake/re-apply is due.
Everything fails closed with the exact field named.
"""

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

try:
    import build as _build
except ImportError:  # package mode: repo root on sys.path, not tools/ssx3
    from tools.ssx3 import build as _build


def _bake_module():
    """The bake script module, imported lazily (it needs Pillow).

    Tests inject a stub via sys.modules; production resolves the real
    module beside this file (Pillow comes from the builder env).
    """
    try:
        import build_tk_list_art as mod
    except ImportError:  # package mode
        from tools.ssx3 import build_tk_list_art as mod
    return mod

HERE = Path(__file__).resolve().parent
DEFAULT_CODES = HERE / 'tklist-codes.tsv'

ATLAS_NAME = 'tklist-art.bin'
ATLAS_REL = 'tricky-ov/' + ATLAS_NAME
ENV_KEY = 'PS2X_SSX3_TRICKY_LIST_ART'
ATLAS_RECORD_NAME = 'tklist-atlas.json'


def read_codes(path):
    """Parse an event=CODE TSV (comments #, blanks skipped)."""
    codes = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            ev, code = line.split('=', 1)
            codes[int(ev)] = code.strip()
    return codes


def file_sha(path):
    """Single-read SHA-256 (inputs are hashed, not verified, here)."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pane_bytes(ui_dir, code):
    """TP pane bytes from a staged UI dir or a Tricky ISO."""
    ui = Path(ui_dir)
    if ui.is_dir():
        return (ui / ('TP' + code + '.SSH')).read_bytes()
    return _tkart.iso_read(ui, 'DATA/UI/TP' + code + '.SSH')


def atlas_key(codes_path, ui_dir, ssx3_iso):
    """Own-inputs key: codes + scripts + exact source bytes (fast).

    The SSX 3 side hashes only the FE_1.SSH bytes the bake reads (not the
    whole 4 GB ISO); the Tricky side hashes the mapped TP panes.
    """
    codes = read_codes(codes_path)
    bake_mod = _bake_module()
    bake_path = getattr(bake_mod, '__file__', None)
    fe1 = bake_mod.iso_read(Path(ssx3_iso), 'DATA/UI/FE_1.SSH')
    panes = {}
    for code in sorted(set(codes.values())):
        panes[code] = hashlib.sha256(pane_bytes(ui_dir, code)).hexdigest()
    return {'codes': file_sha(codes_path),
            # Lists, not tuples: the key round-trips through JSON.
            'codes_rows': sorted([ev, code] for ev, code in codes.items()),
            'bake_script': file_sha(bake_path) if bake_path else 'stub:' + bake_mod.__name__,
            'step_script': file_sha(__file__),
            'fe1_sha256': hashlib.sha256(fe1).hexdigest(),
            'fe1_bytes': len(fe1),
            'panes': panes}


def atlas_record_path(work):
    return Path(work) / ATLAS_RECORD_NAME


def load_atlas_record(work):
    try:
        return json.loads(atlas_record_path(work).read_text())
    except (OSError, ValueError):
        return None


def bake_atlas(ssx3_iso, ui_dir, codes_path, ov_dir):
    """Run the bake script (inherits this interpreter's Pillow)."""
    try:
        import PIL  # noqa: F401
    except ImportError:
        raise ValueError('picker atlas bake needs Pillow in this interpreter '
                         '(builder env, or: uv run --with Pillow python3 '
                         'tools/ssx3/tricky_atlas.py ...)')
    ui = Path(ui_dir)
    if not ui.is_dir():
        # iso_read raises ValueError for a missing member; fail closed here
        # for a missing path with the exact field.
        raise ValueError('picker atlas bake refuses: no staged overlay UI at ' + str(ui))
    cmd = [sys.executable, str(HERE / 'build_tk_list_art.py'),
           '--ssx3-iso', str(ssx3_iso), '--tricky-ui', str(ui),
           '--codes', str(codes_path), '--out', str(ov_dir)]
    subprocess.run(cmd, check=True)
    art = Path(ov_dir) / ATLAS_NAME
    if not art.is_file():
        raise ValueError('picker atlas bake left no ' + ATLAS_NAME + ' at ' + str(art))
    return art


def ensure_env_row(tricky_dir):
    """Maintain the LIST_ART row in the staged tricky.env.

    Returns True when the file changed (appended or migrated from a stale
    path); the caller re-hashes tricky.env into the record.
    """
    env = Path(tricky_dir) / 'tricky.env'
    try:
        lines = env.read_text().splitlines()
    except OSError:
        raise ValueError('picker atlas refuses: no staged tricky.env at ' + str(env))
    # Same Android device prefix the driver bakes (single source:
    # build.PUBLIC_APP_ID), so the stage id rewrites (Android --app-id,
    # iOS ${BUNDLE}/tricky/) apply untouched.
    want = (ENV_KEY + '=/storage/emulated/0/Android/data/'
            + _build.PUBLIC_APP_ID + '/files/' + ATLAS_REL)
    changed = False
    out = []
    seen = False
    for line in lines:
        if line.startswith(ENV_KEY + '='):
            seen = True
            if line != want:
                out.append(want)
                changed = True
            else:
                out.append(line)
        else:
            out.append(line)
    if not seen:
        out.append(want)
        changed = True
    if changed:
        env.write_text('\n'.join(out) + '\n')
    return changed


def file_entry(candidate):
    candidate = Path(candidate)
    return {'sha256': _build.sha2(candidate),
            'bytes': candidate.stat().st_size}


def extend_tricky_record(work):
    """Add/refresh the atlas + tricky.env entries in tricky_inputs.json.

    Returns True when the record changed. The record glob already covers
    tree contents, so this only fills entries the convert-time record
    predates (fresh converts) or lost (reconverted layers).
    """
    tricky = Path(work) / 'tricky'
    record_path = _build.tricky_inputs_path(work)
    try:
        record = json.loads(record_path.read_text())
    except (OSError, ValueError):
        raise ValueError('picker atlas refuses: no tricky record at ' + str(record_path)
                         + ' (run tricky convert/import first)')
    outputs = record.get('outputs')
    if not isinstance(outputs, dict):
        raise ValueError('picker atlas refuses: record has no outputs map: ' + str(record_path))
    changed = False
    for relative in (ATLAS_REL, 'tricky.env'):
        candidate = tricky / relative
        if not candidate.is_file():
            raise ValueError('picker atlas refuses: missing staged file ' + str(candidate))
        entry = file_entry(candidate)
        if outputs.get(relative) != entry:
            outputs[relative] = entry
            changed = True
    if changed:
        record_path.write_text(json.dumps(record, indent=2) + '\n')
    return changed


def ensure(work, ssx3_iso, ui_dir=None, codes_path=None, check_only=False):
    """Bake/repair the atlas; return 'current', 'baked' or 're-applied'."""
    work = Path(work)
    tricky = work / 'tricky'
    if not tricky.is_dir():
        raise ValueError('picker atlas refuses: no tricky layer at ' + str(tricky)
                         + ' (run tricky convert/import first)')
    ui = Path(ui_dir) if ui_dir else tricky / 'tricky-ov' / 'DATA' / 'UI'
    codes = Path(codes_path) if codes_path else DEFAULT_CODES
    if not codes.is_file():
        raise ValueError('picker atlas refuses: codes TSV missing at ' + str(codes))
    key = atlas_key(codes, ui, ssx3_iso)
    art = tricky / ATLAS_REL
    stored = load_atlas_record(work)
    key_match = stored is not None and stored.get('key') == key
    file_match = (art.is_file() and stored is not None
                  and stored.get('outputs', {}).get('atlas', {}).get('sha256')
                  == hashlib.sha256(art.read_bytes()).hexdigest())
    if check_only:
        if key_match and file_match:
            print('picker atlas current: ' + str(art))
            return 'current'
        print('picker atlas stale (missing or key drift); re-run without --check-only')
        return 'stale'
    status = 'current'
    if not (key_match and file_match):
        bake_atlas(ssx3_iso, ui, codes, tricky / 'tricky-ov')
        atlas_record_path(work).write_text(json.dumps(
            {'version': 1, 'key': key,
             'outputs': {'atlas': dict({'rel': ATLAS_REL}, **file_entry(art))}}, indent=2) + '\n')
        # The key just baked is current by construction; re-derive match.
        key_match, file_match, status = True, True, 'baked'
    if ensure_env_row(tricky):
        status = 're-applied' if status == 'current' else status
    if extend_tricky_record(work) and status == 'current':
        status = 're-applied'
    print('picker atlas ' + status + ': ' + str(art))
    return status


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--work', required=True)
    ap.add_argument('--ssx3-iso', required=True)
    ap.add_argument('--tricky-ui', default=None)
    ap.add_argument('--codes', default=None)
    ap.add_argument('--check-only', action='store_true')
    a = ap.parse_args()
    status = ensure(a.work, a.ssx3_iso, a.tricky_ui, a.codes, a.check_only)
    if status == 'stale':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
