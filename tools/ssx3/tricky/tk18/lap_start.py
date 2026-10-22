#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""TK18: Megaplex single-lap donor AIP. Tricky's Megaplex is a lap course: the 6 start lanes (AI 0-5) sit just
above the finish, laps restart at the top (AI 12-15, at the race line's first point). For one SSX 3 lap the start
list is rewritten to the top lanes; nothing else changes (count stays 6, so no offsets move).

    lap_start.py IN.aip OUT.aip 12,13,14,15,13,15
"""
import hashlib, struct, sys
d = bytearray(open(sys.argv[1], 'rb').read())
new = [int(v) for v in sys.argv[3].split(',')]
n = struct.unpack_from('<I', d, 20)[0]
assert n == len(new), (n, new)
print('old', list(struct.unpack_from(f'<{n}I', d, 24)), 'new', new)
struct.pack_into(f'<{n}I', d, 24, *new)
open(sys.argv[2], 'wb').write(d)
print(hashlib.sha256(d).hexdigest())
