#!/usr/bin/env python3
"""Give an SSX 3 location a donor Tricky race course: track chain, gates, AI lines, race-line table.

Decoded 2026-09-11 on GameCube Snow Jam (the PS2 layout is the same, little-endian
paths, big-endian table):

- Race track paths chain by coincident endpoints (Snow Jam: 3 -> 4 -> 5 -> 6 -> 7).
  A track's distance field is the 2D distance from its origin to the finish; the
  finish is the type-0 event on the last track and checkpoints are type-18 events
  (value = checkpoint index) on the tracks they fall on.
- Kind 21 (rid 0) is the flattened race line the progress meter and race logic
  read: header (node count, 20, 2, payload size), then per node (cumulative 2D
  distance of the previous node, 2D normal (-dy, dx) of the outgoing segment,
  x, y) over the chained vertices with each following track's first vertex
  dropped, a final node at the finish point copying the previous normal, then a
  trailer (total, 0, 0, u32 2, total). Node 0 carries the total instead of 0.
  Regenerating it from the stock chain reproduces the stock record.
- Start records with a zero second flag are the race gates; each names the
  AI path its rider follows. Tricky's AIP and SOP both list six start paths.
  A race slot has six gates (one per donor lane); the slopestyle slots have
  three and the backcountry slots two, so `select_donor_starts` folds the
  donor lanes down to the slot's gate count.
- The kind-21 trailer is one float total plus `b` (tag, distance) pairs. Race,
  backcountry and hub records use b=2; the three slopestyle records use b=4,
  adding two type-1 markers (see `race_markers`).
"""
import math
import struct

CHECKPOINT_EVENT, FINISH_EVENT = 18, 0
TRICKY_CHECKPOINT, TRICKY_FINISH = 11, 9


def path_geometry(points):
    """SSX 3 path body: origin, bounds, then (unit delta / 2D length, 2D length) per segment."""
    low = [min(p[k] for p in points) for k in range(3)]
    high = [max(p[k] for p in points) for k in range(3)]
    blob = bytearray(struct.pack('<9f', *points[0], *low, *high))
    for a, b in zip(points, points[1:]):
        delta = [b[k] - a[k] for k in range(3)]
        length = math.hypot(*delta[:2]) or math.dist(a, b) or 1
        blob.extend(struct.pack('<4f', *(v / length for v in delta), length))
    return bytes(blob)


def track_points(blob):
    """(header fields, xyz vertices, segment lengths, events) of an SSX 3 track path."""
    kind, u0, u1, distance, npoints, nevents = struct.unpack_from('<3If2I', blob)
    points = [struct.unpack_from('<3f', blob, 24)]
    lengths = []
    for j in range(npoints):
        x, y, z, w = struct.unpack_from('<4f', blob, 60 + 16 * j)
        points.append(tuple(points[-1][k] + v * w for k, v in enumerate((x, y, z))))
        lengths.append(w)
    events = [struct.unpack_from('<2I2f', blob, 60 + 16 * npoints + 16 * k) for k in range(nevents)]
    return (kind, u0, u1, distance), points, lengths, events


def build_track(header, points, distance, events):
    kind, u0, u1 = header[:3]
    body = path_geometry(points)
    out = struct.pack('<3If2I', kind, u0, u1, distance, len(points) - 1, len(events)) + body
    for kind_, value, start, end in events:
        out += struct.pack('<2I2f', kind_, value, start, end)
    return out


def chain_tracks(tracks, first, tolerance=500):
    """Track indices from `first` following end-to-start coincidence, up to the finish event."""
    chain = [first]
    while True:
        _, points, _, events = track_points(tracks[chain[-1]])
        if any(e[0] == FINISH_EVENT for e in events):
            return chain
        end = points[-1]
        nxt = [i for i in range(len(tracks)) if i not in chain
               and math.dist(track_points(tracks[i])[1][0], end) < tolerance]
        if not nxt:
            return chain
        chain.append(min(nxt, key=lambda i: math.dist(track_points(tracks[i])[1][0], end)))


def merged_vertices(point_lists):
    """Chained vertices with each following track's first vertex dropped."""
    verts = list(point_lists[0])
    for pts in point_lists[1:]:
        verts.extend(pts[1:])
    return verts


def point_along(points, distance):
    """Position at a 2D distance along a polyline (clamped)."""
    run = 0.0
    for a, b in zip(points, points[1:]):
        seg = math.hypot(b[0] - a[0], b[1] - a[1])
        if run + seg >= distance and seg:
            t = (distance - run) / seg
            return tuple(a[k] + t * (b[k] - a[k]) for k in range(3))
        run += seg
    return tuple(points[-1])


def race_markers(total, kind='race', extra_fractions=()):
    """Trailer marker pairs for a kind-21 record.

    Every stock record starts with (0, 0.0) and (2, total). The three stock
    slopestyle records carry two further type-1 markers at course-specific
    distances; nothing else in the world does. What a type-1 marker means is
    still unknown (see docs/aloha-conversion.md), so they are reproduced by
    position only.
    """
    markers = [(0, 0.0), (2, total)]
    if kind == 'race':
        if extra_fractions:
            raise ValueError('Race race-line tables carry no extra markers')
        return markers
    if kind != 'slopestyle':
        raise ValueError(f'Unknown race-line kind: {kind}')
    fractions = list(extra_fractions)
    if len(fractions) != 2:
        raise ValueError('Slopestyle race-line tables carry exactly two type-1 markers')
    for f in fractions:
        if not 0 < f < 1:
            raise ValueError('Type-1 marker fractions must lie inside the run')
    return markers + [(1, total * f) for f in fractions]


def race_line_table(point_lists, finish_distance, markers=None):
    """Kind-21 payload for a chain of track vertex lists ending at finish_distance (2D, from the start).

    `markers` is the trailer's (tag, distance) list; it defaults to the race
    pair [(0, 0.0), (2, finish_distance)] that every race/backcountry slot uses.
    """
    verts = merged_vertices(point_lists)
    cum = [0.0]
    for a, b in zip(verts, verts[1:]):
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    if not 0 < finish_distance <= cum[-1]:
        raise ValueError('Finish distance is outside the track chain')
    keep = [i for i, c in enumerate(cum) if c < finish_distance]
    nodes = []
    normals = []
    for i in keep:
        a, b = verts[i], verts[i + 1]
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy) or 1
        normals.append((-dy / length, dx / length))
        nodes.append([cum[i - 1] if i else finish_distance, *normals[-1], a[0], a[1]])
    finish = point_along(verts, finish_distance)
    nodes.append([cum[keep[-1]], *normals[-1], finish[0], finish[1]])
    if markers is None:
        markers = race_markers(finish_distance)
    count = len(nodes)
    out = struct.pack('>4I', count, 20, len(markers), 20 * count + 4 + 8 * len(markers))
    for node in nodes:
        out += struct.pack('>5f', *node)
    out += struct.pack('>f', finish_distance)
    for tag, distance in markers:
        out += struct.pack('>If', tag, distance)
    return out


def donor_race_chain(races, first=0, tolerance=1000):
    """Donor race paths chained from `first` by coincident endpoints."""
    chain = [first]
    while True:
        end = races[chain[-1]]['points'][-1]
        nxt = [r['index'] for r in races if r['index'] not in chain and math.dist(r['points'][0], end) < tolerance]
        if not nxt:
            return chain
        chain.append(min(nxt, key=lambda i: math.dist(races[i]['points'][0], end)))


def donor_path_distance(path):
    """Distance along a donor path in the units its events use (sum of 2D segment lengths)."""
    pts = path['points']
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def select_donor_starts(start_list, gate_count):
    """Pick `gate_count` donor start paths, keeping the outer lanes and spreading evenly.

    A race slot has one gate per donor start path and this is the identity. A
    slopestyle slot has three gates against six donor lanes, so the first and
    last donor lanes are kept and the rest are spaced evenly between them. The
    engine's own preference among the donor lanes is unverified.
    """
    if gate_count == len(start_list):
        return list(range(len(start_list)))
    if not 1 <= gate_count < len(start_list):
        raise ValueError('Gate count must be between one and the donor start count')
    if gate_count == 1:
        return [0]
    last = len(start_list) - 1
    return [round(k * last / (gate_count - 1)) for k in range(gate_count)]


def convert_race_course(races, donor_ai, start_list, apply, scale, old_tracks, starts, tail, gate_track_chain=None,
                        checkpoint_limit=2, race_line_kind='race', marker_fractions=()):
    """Replace the gate track chain with the donor race line and move the gates to the donor start paths.

    apply(point) transforms a donor point into the target world. `checkpoint_limit`
    is the slot's own checkpoint-event count; `race_line_kind` and
    `marker_fractions` shape the kind-21 trailer. Returns
    (new tracks list, new tail bytes, {ai slot: donor start path}, kind-21 payload, report).
    """
    gates = [(i, s) for i, s in enumerate(starts) if s[1] == 0]
    if len({s[-1] for _, s in gates}) != 1:
        raise ValueError('Expected the race gates to share one track path')
    if len(gates) > len(start_list):
        raise ValueError('The slot has more race gates than the donor has start paths')
    chosen = select_donor_starts(start_list, len(gates))
    start_list = [start_list[k] for k in chosen]
    chain = gate_track_chain or chain_tracks(old_tracks, gates[0][1][-1])
    donor_chain = donor_race_chain(races)
    if len(donor_chain) < 2 or len(chain) < 2:
        raise ValueError('Race lines must chain over at least two paths')
    # Donor segments in world space; distances along them (2D) for event placement.
    segments = [[apply(p) for p in races[i]['points']] for i in donor_chain]
    # Finish: the donor's first finish event on the last segment, else its end.
    last = races[donor_chain[-1]]
    finish_events = [e for e in last['events'] if e[0] == TRICKY_FINISH]
    donor_last_len = donor_path_distance(last)
    finish_frac = (finish_events[0][2] / donor_last_len) if finish_events and donor_last_len else 1.0
    finish_frac = min(1.0, max(0.05, finish_frac))
    # Checkpoints: donor type-11 events, expressed as fractions along their segment.
    checkpoints = []
    for order, i in enumerate(donor_chain):
        length = donor_path_distance(races[i])
        for e in races[i]['events']:
            if e[0] == TRICKY_CHECKPOINT and length:
                checkpoints.append((order, min(1.0, e[2] / length)))
    # Keep the slot's own checkpoint-event count (two on every stock run).
    checkpoints = checkpoints[:checkpoint_limit]
    # Distribute donor segments over the gate chain; extra donor segments fold into
    # the last track, and surplus stock tracks drop out of the chain.
    chain = chain[:len(segments)]
    n = len(chain)
    grouped = [[segments[k]] for k in range(min(n, len(segments)))]
    for extra in segments[n:]:
        grouped[-1].append(extra)
    point_lists = []
    for group in grouped:
        pts = list(group[0])
        for more in group[1:]:
            pts.extend(more[1:])
        point_lists.append(pts)
    # Per-track 2D lengths and the finish distance from the start.
    lengths = [sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(p, p[1:])) for p in point_lists]
    last_seg_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(segments[-1], segments[-1][1:]))
    finish_distance = sum(lengths) - last_seg_len + finish_frac * last_seg_len
    # Checkpoint distances along their tracks.
    seg_track = {}
    seg_offset = {}
    for t, group in enumerate(grouped):
        run = 0.0
        for k, seg in enumerate(group):
            idx = sum(len(g) for g in grouped[:t]) + k
            seg_track[idx] = t
            seg_offset[idx] = run
            run += sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(seg, seg[1:]))
    events = {t: [] for t in range(n)}
    for value, (order, frac) in enumerate(checkpoints):
        seg_len = sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(segments[order], segments[order][1:]))
        at = seg_offset[order] + frac * seg_len
        events[seg_track[order]].append((CHECKPOINT_EVENT, value, at, at))
    finish_on_last = finish_distance - sum(lengths[:-1])
    events[n - 1].append((FINISH_EVENT, 0, finish_on_last, finish_on_last))
    new_tracks = list(old_tracks)
    cumulative = 0.0
    for t, index in enumerate(chain):
        header = track_points(old_tracks[index])[0]
        new_tracks[index] = build_track(header, point_lists[t], finish_distance - cumulative, events[t])
        cumulative += lengths[t]
    markers = race_markers(finish_distance, race_line_kind, marker_fractions)
    table = race_line_table(point_lists, finish_distance, markers)
    # Gates: pair by lateral order with the donor start paths.
    donor_starts = [donor_ai[i] for i in start_list]
    for d in donor_starts:
        if len(d['points']) < 2:
            raise ValueError('Donor start path needs a direction')
    first = apply(donor_starts[0]['points'][0]), apply(donor_starts[0]['points'][1])
    heading = [first[1][k] - first[0][k] for k in range(3)]
    norm = math.hypot(*heading[:2]) or 1
    lateral = (-heading[1] / norm, heading[0] / norm)
    def lateral_of(p):
        return p[0] * lateral[0] + p[1] * lateral[1]
    old_dir = gates[0][1][5:8]
    old_lateral = (-old_dir[1], old_dir[0])
    old_norm = math.hypot(*old_lateral) or 1
    gate_order = sorted(gates, key=lambda g: (g[1][2] * old_lateral[0] + g[1][3] * old_lateral[1]) / old_norm)
    donor_order = sorted(range(len(donor_starts)), key=lambda k: lateral_of(apply(donor_starts[k]['points'][0])))
    tail = bytearray(tail)
    assignments, moved = {}, []
    for (index, s), k in zip(gate_order, donor_order):
        path = donor_starts[k]
        position, following = apply(path['points'][0]), apply(path['points'][1])
        delta = [following[j] - position[j] for j in range(3)]
        length = math.hypot(*delta)
        if not math.isfinite(length) or length == 0:
            raise ValueError('Donor start direction is zero or nonfinite')
        direction = [v / length for v in delta]
        at = len(tail) - 40 * len(starts) + 40 * index + 8
        struct.pack_into('<6f', tail, at, *position, *direction)
        assignments[s[-2]] = start_list[k]
        moved.append(dict(start_record=index, kind=s[0], ai_path=s[-2], donor_start_path=start_list[k],
                          before_position=list(s[2:5]), position=list(position), direction=direction))
    report = dict(track_chain=chain, donor_race_chain=donor_chain, track_lengths=lengths,
                  finish_distance=finish_distance, finish_fraction_of_last=finish_frac,
                  checkpoints=[(seg_track[o], f) for o, f in checkpoints], gates=moved,
                  race_line_nodes=struct.unpack_from('>I', table)[0],
                  race_line_kind=race_line_kind, race_line_markers=markers,
                  donor_start_paths=[start_list[k] for k in range(len(start_list))],
                  selected_donor_start_indices=chosen)
    return new_tracks, bytes(tail), assignments, table, report
