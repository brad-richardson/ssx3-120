#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK10: replace-world build = tools/replace_terrain.py + race gates on the donor opening.

    [TK10_KEEP_RESET=N] build_world.py <replace_terrain.py args...>

Same recipe as run-gari-010 (replace_terrain.py with --reset-aip and
--relocate-freeride-start), plus relocate_race_starts=True in the one
make_reset_aip call (TK6 P2: the race gates move to the donor's first start
path and their shared track path gets the donor race line). Everything else,
including all of replace_terrain.py's checks and readback, is unchanged.

TK10_KEEP_RESET=N: the host has fewer reset-path slots than the donor has
reset paths (ABC1 48 vs Aloha 71). Keep the N longest donor reset paths (by
polyline length); the others are read as non-reset (fields[6] = 0), so path
indices, the start list and the race line are untouched. The kept indices go
to stderr.

TK10_SURFACE=N: write N into the template patch's u16 at +8 (the surface type:
0 groomed on Snow Jam's race line, 2/3 powder where the rider sinks 15-40
units, measured on stock rides) before every imported patch is made from it.
Garibaldi's template (ARA1 rid 1673) is type 0; ABC1 has no type-0 patch.
"""
import functools, math, os, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
import course_route, replace_terrain


def ai_paths_keeping(keep):
    base = course_route.ai_paths

    def ai_paths(data):
        paths = base(data)
        reset = [p for p in paths if p['fields'][6]]
        length = lambda p: sum(math.dist(a, b) for a, b in zip(p['points'], p['points'][1:]))
        kept = {p['index'] for p in sorted(reset, key=length, reverse=True)[:keep]}
        for p in paths:
            if p['fields'][6] and p['index'] not in kept:
                p['fields'] = (*p['fields'][:6], 0)
        print(f'TK10_KEEP_RESET={keep}: kept {len(kept)}/{len(reset)} donor reset paths {sorted(kept)}', file=sys.stderr)
        return paths
    return ai_paths


if __name__ == '__main__':
    keep = int(os.environ.get('TK10_KEEP_RESET', '0'))
    if keep:
        course_route.ai_paths = ai_paths_keeping(keep)
    surface = os.environ.get('TK10_SURFACE')
    if surface is not None:
        base_record = replace_terrain.make_record
        replace_terrain.make_record = lambda template, coeffs: base_record(
            template[:8] + struct.pack('<H', int(surface)) + template[10:], coeffs)
        print(f'TK10_SURFACE={surface}: template surface type set', file=sys.stderr)
    replace_terrain.make_reset_aip = functools.partial(course_route.make_reset_aip, relocate_race_starts=True)
    replace_terrain.main()
