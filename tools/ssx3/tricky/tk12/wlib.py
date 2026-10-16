"""TK12 world helpers: decode an SSX 3 PS2 world archive (BAM.BIG / renamed <NAME>.BIG) by group."""
import hashlib, io, struct, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT /'shared'))
sys.path.insert(0, str(ROOT / 'tk6'))
from probe_worlds import resource_records
from location_inventory import parse_sdb
from retrack import members_of, decode_group

sha = lambda b: hashlib.sha256(b).hexdigest()


class W:
    def __init__(self, path):
        self.path = str(path)
        self.data = Path(path).read_bytes()
        md = dict(members_of(self.data))
        self.members = md
        stem = next(p for p in md if p.endswith('.sdb')).rsplit('/', 1)[1][:-4]
        self.stem = stem
        self.ssb = md[f'data/worlds/{stem}.ssb']
        self.sdb_bytes = md[f'data/worlds/{stem}.sdb']
        self.sdb = parse_sdb(self.sdb_bytes)
        self._cache = {}

    def locations(self):
        return self.sdb['locations']

    def loc(self, name):
        return next(l for l in self.sdb['locations'] if l['name'] == name)

    def raw(self, g):
        if g not in self._cache:
            self._cache[g] = decode_group(self.ssb, self.sdb['groups'][g]['ssb_offset'])[0]
        return self._cache[g]

    def records(self, g):
        return list(resource_records(self.raw(g)))
