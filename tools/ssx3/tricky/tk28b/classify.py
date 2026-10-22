#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK28: classify wipeouts (sel 2/st 8), resets (sel 3/st 9) and short ground->air pops from a taps.py TSV.
   classify.py TAPS.tsv T0 T1 [OUT-prefix]
Ground = sel 0 (st 0..3); air = sel 1. Normal = R+0x370 (ground normal while grounded; air code rewrites it).
A pop = an air episode of <= 8 ticks that starts from ground with nz >= 0.85 (gentle ground, < ~32 deg)."""
import csv, math, sys
rows = list(csv.DictReader(open(sys.argv[1]), delimiter='\t'))
t0, t1 = int(sys.argv[2]), int(sys.argv[3])
for r in rows:
    for k in r:
        r[k] = float(r[k]) if k in ('x','y','z','vx','vy','vz','nx','ny','nz','u','v','h454') else int(r[k])
    r['sp'] = math.sqrt(r['vx']**2 + r['vy']**2 + r['vz']**2)
rows = [r for r in rows if t0 <= r['tick'] <= t1]
byt = {r['tick']: r for r in rows}
def eps(pred):
    out, cur = [], None
    for r in rows:
        if pred(r):
            if cur is None: cur = [r['tick'], r['tick']]
            else: cur[1] = r['tick']
        elif cur: out.append(cur); cur = None
    if cur: out.append(cur)
    return out
def last_ground(t, back=200):
    for k in range(t - 1, t - back, -1):
        r = byt.get(k)
        if r and r['sel'] == 0: return r
def angle(a, b):
    d = a['nx']*b['nx'] + a['ny']*b['ny'] + a['nz']*b['nz']
    return math.degrees(math.acos(max(-1, min(1, d))))
pfx = sys.argv[4] if len(sys.argv) > 4 else None
print(f'ticks {len(rows)} ({t0}-{t1})')
W = eps(lambda r: r['sel'] == 2 and r['st'] == 8)
Rz = eps(lambda r: r['sel'] == 3 and r['st'] == 9)
A = eps(lambda r: r['sel'] == 1)
lines = ['kind\tfrom\tto\tair_before\tground_nz_before\tmin_ground_nz_30\tland_nz\tland_vs_ground_deg\tspeed_10_before\tspeed_at\tpatch_before\tpatch_at\tpos']
for kind, E in (('wipeout', W), ('reset', Rz)):
    for a, b in E:
        g = last_ground(a)
        air = 0; k = a - 1
        while byt.get(k) and byt[k]['sel'] == 1: air += 1; k -= 1
        gs = [byt[k] for k in range(a - 60, a) if byt.get(k) and byt[k]['sel'] == 0][-30:]
        mn = min((x['nz'] for x in gs), default=float('nan'))
        r_at = byt[a]; r10 = byt.get(a - 10, r_at)
        lines.append(f"{kind}\t{a}\t{b}\t{air}\t{g['nz'] if g else float('nan'):.3f}\t{mn:.3f}\t{r_at['nz']:.3f}\t"
                     f"{angle(g, r_at) if g else float('nan'):.1f}\t{r10['sp']:.0f}\t{r_at['sp']:.0f}\t{g['patch'] if g else -1}\t{r_at['patch']}\t"
                     f"{r_at['x']:.0f},{r_at['y']:.0f},{r_at['z']:.0f}")
print('\n'.join(lines))
# pops
pops = []
for a, b in A:
    n = b - a + 1
    g0 = byt.get(a - 1); g1 = byt.get(b + 1)
    if not g0 or not g1 or g0['sel'] != 0 or g1['sel'] != 0: continue
    if n <= 8 and g0['nz'] >= 0.85:
        pops.append((a, n, g0, g1))
gentle = [r for r in rows if r['sel'] == 0 and r['nz'] >= 0.85]
print(f"air episodes {len(A)}; pops (<=8 ticks, from ground nz>=0.85) {len(pops)}; gentle-ground ticks {len(gentle)}; "
      f"pops per 1000 gentle ticks {1000*len(pops)/max(1,len(gentle)):.1f}")
pl = ['from\tticks\tnz_takeoff\tnz_land\tland_vs_takeoff_deg\tpatch_takeoff\tpatch_land\tsame_patch\tu\tv\tsp_before\tsp_after\tpos']
for a, n, g0, g1 in pops:
    pl.append(f"{a}\t{n}\t{g0['nz']:.3f}\t{g1['nz']:.3f}\t{angle(g0,g1):.1f}\t{g0['patch']}\t{g1['patch']}\t{int(g0['patch']==g1['patch'])}\t"
              f"{g0['u']:.3f}\t{g0['v']:.3f}\t{g0['sp']:.0f}\t{g1['sp']:.0f}\t{g0['x']:.0f},{g0['y']:.0f},{g0['z']:.0f}")
if pfx:
    open(pfx + '-events.tsv', 'w').write('\n'.join(lines) + '\n')
    open(pfx + '-pops.tsv', 'w').write('\n'.join(pl) + '\n')
