#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK20: world_table.py WORLD_DIR EXPERIMENT.json -- one row: instances/models/textures, solids/k12, course group bytes + records, rails."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tk12'))
from pathlib import Path
from wlib import W
d = Path(sys.argv[1]); G = json.load(open(sys.argv[2]))['group']
sc = json.load(open(d / 'scenery.json')); rl = json.load(open(d / 'rails.json'))
big = next(p for p in d.glob('*.BIG'))
g = next(x for x in W(str(big)).sdb['groups'] if x['index'] == G)
kc = g['kind_counts']
print(json.dumps(dict(world=str(big), instances=kc.get(3), models=kc.get(2), materials=kc.get(0), textures=len(sc['texture_ids']),
                      solids=len(sc['collision_enabled']), k12=kc.get(12), protected=len(sc['protected']), group_memsize=g['memsize'],
                      group_records=g['count'], rails=rl['donor_rails'], rail_curve_err=round(rl['max_curve_error'], 4),
                      solid_classes=sc['solid_classes'], skipped=sc['skipped'])))
