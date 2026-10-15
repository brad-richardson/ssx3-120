#!/usr/bin/env python3
"""Write optional host shader selector configuration outside the checkout."""
import argparse
import json
from pathlib import Path
import struct

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('output', type=Path)
a = p.parse_args()
root = Path(__file__).resolve().parents[2]
output = a.output.resolve()
if output == root or root in output.parents:
    p.error('output must be outside the checkout')
if output.exists():
    p.error('refusing to overwrite an existing selector recording')
data = json.loads(Path(__file__).with_name('tfx-selectors.json').read_text())
blob = b''.join(struct.pack('<6I', *(int(x, 16) for x in row)) for row in data['selectors'])
output.parent.mkdir(parents=True, exist_ok=True)
output.write_bytes(blob)
print(f'{len(blob) // 24} host pipeline selectors, {len(blob)} bytes')
