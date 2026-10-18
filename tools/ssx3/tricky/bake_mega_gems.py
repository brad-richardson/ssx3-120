#!/usr/bin/env python3
"""Bake MEGA's authored MultiplierScore rows from the exact current preset world.

Game data stays outside Git. The donor-to-host match uses the TK18 placement,
the current world's kind-3 translation, and monotone donor/world instance order.
Every source and output gets two SHA-256 reads; ambiguous matches fail closed.
"""
import argparse
import hashlib
import json
import math
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.dont_write_bytecode = True
# Port: the lane repo-rooted every import; here every helper is a sibling in
# this package (wlib, tricky_pbd, replace_terrain/placement come from the
# world-pipeline port; parse_desc lives in sibling speech_merge.py).
_HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(_HERE / d) for d in ('tk12', 'tk13', 'tk30',
                                        'shared')] + [str(_HERE)]

from speech_merge import parse_desc
from wlib import W
from tricky_pbd import PBD
from replace_terrain import placement


def sha(data):
    return hashlib.sha256(data).hexdigest()


def twice(path):
    a = Path(path).read_bytes()
    b = Path(path).read_bytes()
    if a != b:
        raise ValueError(f"two reads differ: {path}")
    return a, {"path": str(path), "sha256_read1": sha(a), "sha256_read2": sha(b), "bytes": len(a)}


def materialize(desc, iso):
    size, segs = parse_desc(desc)
    out = bytearray()
    with open(iso, "rb") as src:
        for kind, offset, count in segs:
            if kind == "self":
                block = desc[offset:offset + 2048 * count]
            else:
                src.seek(offset * 2048)
                block = src.read(2048 * count)
            if len(block) != 2048 * count:
                raise ValueError("truncated preset or ISO segment")
            out.extend(block)
    return bytes(out[:size])


def f32(x):
    return struct.unpack("<f", struct.pack("<f", x))[0]


def main():
    ap = argparse.ArgumentParser()
    for name in ("descriptor", "iso", "pbd", "effects", "sidecar", "world_out", "out", "receipt"):
        ap.add_argument("--" + name.replace("_", "-"), required=True)
    a = ap.parse_args()

    desc, desc_pin = twice(a.descriptor)
    world = materialize(desc, a.iso)
    if world != materialize(desc, a.iso):
        raise ValueError("two materializations differ")
    world_path = Path(a.world_out)
    world_path.parent.mkdir(parents=True, exist_ok=True)
    world_path.write_bytes(world)
    world_back, world_pin = twice(world_path)
    if world_back != world:
        raise ValueError("world write differs")
    wb = W(world_path)
    records = sorted((e["rid"], data) for e, data in wb.records(94) if e["kind"] == 3)
    if [rid for rid, _ in records] != list(range(len(records))):
        raise ValueError("non-contiguous kind-3 IDs")

    pbd_bytes, pbd_pin = twice(a.pbd)
    effects_bytes, effects_pin = twice(a.effects)
    sidecar, side_pin = twice(a.sidecar)
    pbd = PBD(pbd_bytes)
    effects = json.loads(effects_bytes)
    programs = effects["programs"]
    gems = sorted((x for x in effects["instances"] if x["name"].startswith("Gem_TrickMultiplier")),
                  key=lambda x: x["instance"])
    if len(gems) != 38:
        raise ValueError(f"expected 38 donor gems, got {len(gems)}")

    matrix, translation = placement(
        [1630.64453125, 196.73287963867188, 12.964978218078613],
        [47827.451171875, -244658.3515625, 278105.828125], 279.4, .55)
    pending = defaultdict(list)
    max_error = 0.0
    for gem in gems:
        donor = gem["instance"]
        ops = [op for effect in gem["slot_effects"] if effect != -1
               for op in programs[str(effect)] if op["op"] == 14]
        if len(ops) != 1 or len(ops[0]["args"]) != 1:
            raise ValueError(f"donor {donor}: unexpected MultiplierScore program")
        value = struct.unpack("<f", struct.pack("<I", ops[0]["args"][0]))[0]
        if value not in (2.0, 3.0, 5.0) or not gem["player_collision"] or gem["collision_mode"] != 2:
            raise ValueError(f"donor {donor}: unexpected contact or value")
        model = pbd.instances[donor]["model"]
        if model != {2: 322, 3: 323, 5: 324}[int(value)]:
            raise ValueError(f"donor {donor}: model/value mismatch")
        src = pbd.instances[donor]["matrix"][3][:3]
        pred = [sum(matrix[k][j] * src[j] for j in range(3)) + translation[k] for k in range(3)]
        matches = []
        for rid, data in records:
            xyz = struct.unpack_from("<3f", data, 64)
            error = math.dist(pred, xyz)
            if error < .08:
                matches.append((rid, error, data))
        if not matches:
            raise ValueError(f"donor {donor}: no host instance")
        key = tuple(x[0] for x in matches)
        pending[key].append((gem, value, model, matches))

    rows = []
    model_map = {}
    used_rids = set()
    for candidate_rids, group in pending.items():
        if len(candidate_rids) != len(group):
            raise ValueError(f"ambiguous donor/host match: {candidate_rids}, {len(group)} donors")
        for (gem, value, model, matches), rid in zip(sorted(group, key=lambda x: x[0]["instance"]), candidate_rids):
            if rid in used_rids:
                raise ValueError(f"host rid reused: {rid}")
            used_rids.add(rid)
            match = next(x for x in matches if x[0] == rid)
            _, error, data = match
            max_error = max(max_error, error)
            host_model = struct.unpack_from("<I", data, 128)[0]
            if model in model_map and model_map[model] != host_model:
                raise ValueError(f"donor model {model}: inconsistent host model")
            model_map[model] = host_model
            xyz = struct.unpack_from("<3f", data, 64)
            radius = struct.unpack_from("<f", data, 92)[0]
            if not math.isfinite(radius) or radius <= 0:
                raise ValueError(f"host rid {rid}: invalid radius")
            values = [repr(float(v)) for v in (*xyz, radius)]
            if [f32(float(t)) for t in values] != list((*xyz, radius)):
                raise ValueError(f"host rid {rid}: float round-trip differs")
            rows.append(["MEGA", str(rid), *values, str(int(value)), gem["name"]])
    rows.sort(key=lambda row: int(row[1]))
    if len(rows) != 38 or len(used_rids) != 38 or len(model_map) != 3:
        raise ValueError("incomplete donor/host gem coverage")
    prior = [line.split() for line in sidecar.decode().splitlines() if line.strip() and not line.startswith("#")]
    if len(prior) != 483 or any(row[0] == "MEGA" for row in prior):
        raise ValueError("unexpected input sidecar")
    all_rows = prior + rows
    if len(all_rows) != 521:
        raise ValueError("unexpected table size")
    spheres = defaultdict(list)
    for row in all_rows:
        spheres[row[0]].append(tuple(float(x) for x in row[2:6]))
    gaps = {}
    courses = sorted(spheres)
    for ci, course in enumerate(courses):
        for other in courses[ci + 1:]:
            gap = min(math.dist(x[:3], y[:3]) - x[3] - y[3]
                      for x in spheres[course] for y in spheres[other])
            if gap < 10000:
                raise ValueError(f"inter-course sphere gap {course}/{other}: {gap}")
            gaps[f"{course}-{other}"] = round(gap, 3)
    data = sidecar + (b"" if sidecar.endswith(b"\n") else b"\n")
    data += "".join(" ".join(row) + "\n" for row in rows).encode()
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    back, output_pin = twice(out)
    if back != data or not back.startswith(sidecar):
        raise ValueError("sidecar write or prefix differs")
    receipt = {
        "descriptor": desc_pin, "materialized_world": world_pin,
        "donor_pbd": pbd_pin, "donor_effects": effects_pin,
        "input_sidecar": side_pin, "output_sidecar": output_pin,
        "world_group": 94, "world_instances": len(records), "donor_gems": len(gems),
        "added_rows": len(rows), "total_rows": len(all_rows),
        "values": dict(Counter(row[6] for row in rows)),
        "donor_to_host_models": model_map, "max_match_error": round(max_error, 5),
        "inter_course_min_gaps": gaps,
        "rows": [{"rid": int(row[1]), "value": int(row[6]), "donor": row[7]} for row in rows],
    }
    Path(a.receipt).write_text(json.dumps(receipt, indent=2) + "\n")
    print(f"MEGA {len(rows)}/{len(gems)} rows; total {len(all_rows)}; max match error {max_error:.4f}; SHA {sha(data)}")


if __name__ == "__main__":
    main()
