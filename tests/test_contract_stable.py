#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""Product archive identity is engine-pinned: for the same engine pin the
product contract() must equal the engine checkout's own contract(). A
mismatch means the move split the support-bundle ABI (or the engine pin
moved without a deps.lock.json update). No disc, no network."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

sys.dont_write_bytecode = True  # never pollute the engine checkout

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'deps' / 'PS2Recomp'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    import os
    os.environ['SSX3_ENGINE_DIR'] = str(ENGINE)
    # split-p1-prep-1: the module is product-owned; the engine contract
    # reads its rows from this repo's moved copy.
    os.environ['SSX3_MODULE_DIR'] = str(ROOT / 'games/ssx3/module')
    lock = json.loads((ROOT / 'deps.lock.json').read_text())
    have = subprocess.check_output(['git', '-C', str(ENGINE), 'rev-parse', 'HEAD'], text=True).strip()
    assert have == lock['engine']['pin'], 'engine %s != lock %s' % (have, lock['engine']['pin'])
    product = load('product_prebuilt', ROOT / 'tools' / 'ssx3' / 'prebuilt.py')
    engine = load('engine_prebuilt', ENGINE / 'tools' / 'ssx3' / 'prebuilt.py')
    a, b = product.contract(), engine.contract()
    print('product : ' + a)
    print('engine  : ' + b)
    print('locked  : ' + lock['archive_identity']['contract'])
    assert a == b, 'product/engine contract split'
    assert a == lock['archive_identity']['contract'], 'contract moved without a deps.lock.json update'
    print('STABLE')


if __name__ == '__main__':
    sys.exit(main())
