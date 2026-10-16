#!/usr/bin/env python3
"""TK10: turn a replace-world <NAME>.BIG into a composite overlay file (PS2XCMP1).

    make_composite.py ISO WORLD.BIG OUT.BIG [--materialized OUT_FULL.BIG]

The game sees a BIG whose SSB is the stock bam.ssb (served from the disc) with
the rebuilt group(s) appended, and whose SDB points the changed groups at the
appended copies; PHM/PSM/serial are the stock members (served from the disc).
The host file holds only: one descriptor sector, the BIG directory + new SDB,
and the appended groups. Checks, all fatal:
  - stock BAM.BIG on the disc (found by walking the ISO) parses; every member
    except the SSB and SDB is byte-identical to the world's;
  - every group of the composite decodes to the world's decoded bytes;
  - the descriptor, resolved against the ISO, reproduces the materialized
    composite BIG byte for byte.
Prints a JSON receipt.
"""
import argparse, hashlib, io, json, struct, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] /'shared'))
from inspect_disc import Region, big_members
from location_inventory import parse_sdb
from relayout_stream import write_bigf
from probe_worlds import refpack

SEC = 2048
sha = lambda b: hashlib.sha256(b).hexdigest()


def iso_find(iso, path):
    """(lbn, size) of an ISO9660 file, by walking the directories."""
    def rd(lbn, n=1):
        iso.seek(lbn * SEC); return iso.read(n * SEC)
    pvd = rd(16)
    lbn, size = struct.unpack_from('<I', pvd, 158)[0], struct.unpack_from('<I', pvd, 166)[0]
    for i, part in enumerate(path.strip('/').upper().split('/')):
        data, o, hit = rd(lbn, (size + SEC - 1) // SEC), 0, None
        while o < len(data):
            n = data[o]
            if n == 0:
                o = (o // SEC + 1) * SEC; continue
            name = data[o + 33:o + 33 + data[o + 32]].decode('latin1').split(';')[0]
            if name == part:
                hit = struct.unpack_from('<I', data, o + 2)[0], struct.unpack_from('<I', data, o + 10)[0]
            o += n
        if not hit:
            raise SystemExit(f'{path}: {part} not on the disc')
        lbn, size = hit
    return lbn, size


def members(data):
    _, m = big_members(Region(io.BytesIO(data), 0, len(data)))
    return m


def group_spans(sdb, ssb_len):
    g = parse_sdb(sdb)['groups']
    offs = [x['ssb_offset'] for x in g]
    order = sorted(range(len(offs)), key=lambda i: offs[i])
    ends = {}
    for a, b in zip(order, order[1:] + [None]):
        ends[a] = offs[b] if b is not None else ssb_len
    return [(offs[i], ends[i]) for i in range(len(offs))]


def decode(ssb, off):
    out, pos = bytearray(), off
    while True:
        tag = ssb[pos:pos + 4]
        size = struct.unpack_from('<I', ssb, pos + 4)[0]
        out += refpack(ssb[pos + 8:pos + size])[0]
        pos += size
        if tag == b'CEND':
            return bytes(out)
        if tag != b'CBXS':
            raise SystemExit(f'bad block tag {tag!r} at {pos}')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('iso'); ap.add_argument('world'); ap.add_argument('out')
    ap.add_argument('--materialized')
    a = ap.parse_args()
    iso = open(a.iso, 'rb')
    image_sectors = Path(a.iso).stat().st_size // SEC
    blbn, bsize = iso_find(iso, '/DATA/WORLDS/BAM.BIG')
    iso.seek(blbn * SEC); stock = iso.read(bsize)
    world = Path(a.world).read_bytes()
    sm, wm = members(stock), members(world)
    kind = lambda p: p.rsplit('.', 1)[-1]
    S = {kind(e['path']): e for e in sm}; Wd = {kind(e['path']): e for e in wm}
    get = lambda d, e: d[e['offset']:e['offset'] + e['size']]
    for k in ('phm', 'psm', 'txt'):
        if get(stock, S[k]) != get(world, Wd[k]):
            raise SystemExit(f'member {k} differs from stock: not a replace-world')
    s_sdb, s_ssb = get(stock, S['sdb']), get(stock, S['ssb'])
    w_sdb, w_ssb = get(world, Wd['sdb']), get(world, Wd['ssb'])
    if S['ssb']['offset'] % SEC or S['ssb']['size'] % SEC or S['phm']['offset'] % SEC:
        raise SystemExit('stock SSB/PHM not sector-aligned')
    ss, ws = group_spans(s_sdb, len(s_ssb)), group_spans(w_sdb, len(w_ssb))
    if len(ss) != len(ws):
        raise SystemExit('group count differs')
    changed = [i for i in range(len(ws)) if w_ssb[ws[i][0]:ws[i][1]] != s_ssb[ss[i][0]:ss[i][1]]]
    appended, new_off = bytearray(), {}
    for i in changed:
        new_off[i] = len(s_ssb) + len(appended)
        appended += w_ssb[ws[i][0]:ws[i][1]]
    sdb = bytearray(w_sdb)
    nloc, nodes, ngroups = struct.unpack_from('<III', sdb, 8)
    table = (80 + nloc * 88 + 15) // 16 * 16 + nodes * 96
    for i in range(ngroups):
        struct.pack_into('<I', sdb, table + 68 * i + 4, new_off.get(i, ss[i][0]))
    ssb = s_ssb + bytes(appended)
    order = [e['path'] for e in wm]
    data = {kind(p): b for p, b in [(e['path'], None) for e in wm]}
    data.update(sdb=bytes(sdb), ssb=ssb, phm=get(stock, S['phm']), psm=get(stock, S['psm']), txt=get(stock, S['txt']))
    full, offsets = write_bigf([(p, data[kind(p)]) for p in order], world)
    fm = {kind(e['path']): e for e in members(full)}
    # every group decodes to the world's bytes
    ok = all(decode(ssb, parse_sdb(bytes(sdb))['groups'][i]['ssb_offset']) == decode(w_ssb, ws[i][0])
             for i in changed) and all(new_off.get(i, ss[i][0]) == ss[i][0] and
             s_ssb[ss[i][0]:ss[i][1]] == w_ssb[ws[i][0]:ws[i][1]] for i in range(ngroups) if i not in changed)
    if not ok:
        raise SystemExit('group check failed')
    # segments
    head_end = fm['ssb']['offset']                     # dir + sdb (+ pad) from the host
    ssb_sectors = len(s_ssb) // SEC
    app_off = head_end + len(s_ssb)
    app_sectors = (len(appended) + SEC - 1) // SEC
    tail_off = fm['phm']['offset']
    if tail_off != app_off + app_sectors * SEC:
        raise SystemExit('unexpected layout after the SSB')
    tail_sectors = (len(full) - tail_off + SEC - 1) // SEC
    s_tail_lbn = blbn + S['phm']['offset'] // SEC
    if (bsize - S['phm']['offset']) != (len(full) - tail_off):
        raise SystemExit('tail length differs from stock')
    host = bytearray(full[:head_end]) + appended + bytes(app_sectors * SEC - len(appended))
    segs = [('self', SEC, head_end // SEC), ('iso', blbn + S['ssb']['offset'] // SEC, ssb_sectors),
            ('self', SEC + head_end, app_sectors), ('iso', s_tail_lbn, tail_sectors)]
    text = f'PS2XCMP1\nsize {len(full)}\nimage {image_sectors}\n' + ''.join(f'{k} {x} {n}\n' for k, x, n in segs) + 'end\n'
    if len(text) > SEC:
        raise SystemExit('descriptor too long')
    out = text.encode() + bytes(SEC - len(text)) + bytes(host)
    # reproduce the materialized BIG from the descriptor + ISO
    rebuilt = bytearray()
    for k, x, n in segs:
        if k == 'self':
            rebuilt += out[x:x + n * SEC].ljust(n * SEC, b'\0')
        else:
            iso.seek(x * SEC); rebuilt += iso.read(n * SEC)
    if bytes(rebuilt[:len(full)]) != full:
        raise SystemExit('descriptor does not reproduce the composite BIG')
    Path(a.out).write_bytes(out)
    if a.materialized:
        Path(a.materialized).write_bytes(full)
    print(json.dumps(dict(
        iso=a.iso, image_sectors=image_sectors, stock_big_lbn=blbn, stock_big_sha256=sha(stock),
        world=a.world, world_sha256=sha(world), world_bytes=len(world), changed_groups=changed,
        appended_bytes=len(appended), composite_big_bytes=len(full), composite_big_sha256=sha(full),
        segments=segs, out=a.out, out_bytes=len(out), out_sha256=sha(Path(a.out).read_bytes()),
        groups_decode_to_world=True, descriptor_reproduces_big=True), indent=1))


if __name__ == '__main__':
    main()
