#!/usr/bin/env python3
"""Extract a Tricky race line and measure a ride against its transformed route.

Race-line layout follows the pinned SSX-Library AIPSOPHandler.cs. Coordinates
are accumulated xyz*w displacements. A route is a measurement reference; its
presence does not establish that the imported terrain is traversable.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import struct

from import_terrain import apply
from race_course import convert_race_course, path_geometry


def race_paths(data):
    def read(fmt, offset):
        if offset < 0 or offset + struct.calcsize(fmt) > len(data):
            raise ValueError('Truncated Tricky race-line data')
        return struct.unpack_from(fmt, data, offset)

    magic, types, _, offset = read('<4I', 0)
    if magic != 0x0a0a0a0a or types != 2:
        raise ValueError('Unsupported Tricky AIP header')
    pos = offset + 16
    version, size, count, _ = read('<4I', pos)
    if version != 1 or pos + 8 + size != len(data):
        raise ValueError('Invalid Tricky race-line size')
    pos += 16
    paths = []
    for i in range(count):
        kind, u0, u1, distance, npoints, nevents = read('<3If2I', pos)
        origin = read('<3f', pos + 24)
        points = [origin]
        pos += 60
        for _ in range(npoints):
            x, y, z, w = read('<4f', pos)
            points.append(tuple(points[-1][k] + v * w for k, v in enumerate((x, y, z))))
            pos += 16
        events = []
        for _ in range(nevents):
            events.append(read('<2I2f', pos))
            pos += 16
        if not all(math.isfinite(v) for point in points for v in point):
            raise ValueError('Nonfinite race-line coordinate')
        paths.append(dict(index=i, kind=kind, unknown=[u0, u1],
                          distance_to_finish=distance, points=points, events=events))
    if pos != len(data):
        raise ValueError('Unexpected trailing race-line data')
    return paths


def ai_paths(data):
    if len(data) < 24 or struct.unpack_from('<2I', data) != (0x0a0a0a0a, 2):
        raise ValueError('Unsupported Tricky AIP header')
    end = 16 + struct.unpack_from('<I', data, 12)[0]
    count, starts = struct.unpack_from('<2I', data, 16)
    pos = 24 + 4 * starts
    if not pos <= end <= len(data):
        raise ValueError('Invalid Tricky AI section bounds')
    paths = []
    for i in range(count):
        if pos + 72 > end:
            raise ValueError('Truncated Tricky AI path')
        fields = struct.unpack_from('<9I', data, pos)
        origin = struct.unpack_from('<3f', data, pos + 36)
        pos += 72
        npoints, nevents = fields[-2:]
        if pos + 16 * (npoints + nevents) > end:
            raise ValueError('Truncated Tricky AI vectors/events')
        points = [origin]
        for _ in range(npoints):
            x, y, z, w = struct.unpack_from('<4f', data, pos)
            points.append(tuple(points[-1][k] + v * w for k, v in enumerate((x, y, z))))
            pos += 16
        pos += nevents * 16
        if not all(math.isfinite(v) for p in points for v in p):
            raise ValueError('Nonfinite Tricky AI coordinates')
        paths.append(dict(index=i, fields=fields[:7], points=points))
    if pos != end:
        raise ValueError('Unexpected trailing Tricky AI data')
    return paths


def start_list(data):
    """The donor's start-path indices (AIP and SOP share this header)."""
    count = struct.unpack_from('<I', data, 20)[0]
    if 24 + 4 * count > len(data):
        raise ValueError('Truncated Tricky start list')
    return [struct.unpack_from('<I', data, 24 + 4 * i)[0] for i in range(count)]


def donor_paths(path):
    """Read a Tricky `.aip` (Race) or `.sop` (Showoff) file.

    Both files are the same container: the magic-0x0a0a0a0a header, the start
    list, the 72-byte AI-path section and the 60-byte race-path section. The
    Showoff file simply carries the Showoff authoring - a different start line,
    its own race chain and checkpoints, and roughly a third of the AI paths
    because there are no CPU opponents. Verified on all ten GameCube Tricky
    courses; the parsers are unchanged.
    """
    data = Path(path).read_bytes()
    paths = ai_paths(data)
    races = race_paths(data)
    starts = start_list(data)
    if any(i >= len(paths) for i in starts):
        raise ValueError('Donor start list references an absent path')
    return dict(path=str(path), data=data, sha256=hashlib.sha256(data).hexdigest(),
                ai_paths=paths, race_paths=races, start_list=starts)


def ssx3_paths(data):
    """Split a bounded SSX 3 AIP while retaining index-addressed records."""
    def read(fmt, pos):
        if pos < 0 or pos + struct.calcsize(fmt) > len(data):
            raise ValueError('Truncated SSX 3 AIP')
        return struct.unpack_from(fmt, data, pos)

    if read('<I', 0)[0] != 0x69696969:
        raise ValueError('Unsupported SSX 3 AIP header')
    pos, sections = 4, []
    for header, size in (('<9I', 72), ('<3If2I', 60)):
        count = read('<I', pos)[0]
        pos += 4
        paths = []
        for _ in range(count):
            fields = read(header, pos)
            end = pos + size + 16 * sum(fields[-2:])
            if end > len(data):
                raise ValueError('Truncated SSX 3 path vectors/events')
            paths.append(data[pos:end])
            pos = end
        sections.append(paths)
    tail = pos
    count = read('<I', pos)[0]
    pos += 4 + 8 * count
    count = read('<I', pos)[0]
    pos += 4
    starts = [read('<2I6f2I', pos + i * 40) for i in range(count)]
    if pos + count * 40 != len(data):
        raise ValueError('Invalid SSX 3 start-table size')
    if any(s[-2] >= len(sections[0]) or s[-1] >= len(sections[1]) for s in starts):
        raise ValueError('SSX 3 start references an absent path')
    return sections[0], sections[1], data[tail:], starts


def make_reset_aip(data, matrix, translation, scale, original, *, relocate_start=False, relocate_race_starts=False,
                   race_course=None, race_data=None):
    """Convert donor AI and track paths for experimental SSX 3 freeride resets.

    Path layouts match SSX-Library's WorldAIP.cs. Existing indexed paths and
    start tables remain available for transport/camera references. Eligible
    downhill slots receive donor reset paths without foreign events. Path
    counts stay unchanged. relocate_start moves the existing freeride start
    and its indexed approach paths to the donor's first start path. This
    supplies no new race setup. race_data is an alternative donor path file for
    the race/gate conversion only (a `.sop` for a Showoff course); the reset
    paths still come from `data`.
    """
    if race_data is None:
        race_data = data
    donor_ai = ai_paths(data)
    ai = [path for path in donor_ai if path['fields'][6]]

    def geometry(points):
        return path_geometry([apply(matrix, translation, p) for p in points])

    old_ai, old_tracks, tail, starts = ssx3_paths(original)
    # SLUS-20772 0x26afb8 stores per-path distances in an 800-byte stack
    # array; adding path 201 overwrites the saved position pointer at +800.
    if len(old_ai) > 200:
        raise ValueError('Original path count exceeds the observed 200-entry runtime scratch array')
    entry_paths = {s[-2] for s in starts if s[:2] == (1, 1)}
    if len(entry_paths) != 1:
        raise ValueError('Expected one freeride transport entry path')
    slots = [i for i, path in enumerate(old_ai)
             if i not in entry_paths and struct.unpack_from('<I', path, 24)[0]]
    if len(ai) > len(slots):
        raise ValueError('Donor reset paths exceed the available original slots')
    replacements = dict(zip(slots, ai))
    start_edit = None
    if relocate_start:
        start_count = struct.unpack_from('<I', data, 20)[0]
        if not start_count:
            raise ValueError('Donor has no start path')
        donor_index = struct.unpack_from('<I', data, 24)[0]
        if donor_index >= len(donor_ai):
            raise ValueError('Donor start references an absent path')
        donor = donor_ai[donor_index]
        if len(donor['points']) < 2:
            raise ValueError('Donor start path needs a direction')
        entries = [(i, s) for i, s in enumerate(starts) if s[:2] == (1, 1)]
        if len(entries) != 1:
            raise ValueError('Expected one freeride start record')
        start_index, start = entries[0]
        entry_ai, entry_track = start[-2:]
        replacements[entry_ai] = donor
        # Keep the native indexed track slot and its type flags. Its geometry
        # follows the same donor opening as the arrival/reset path, with no
        # foreign events. Other starts and track paths remain byte-identical.
        track_header = struct.unpack_from('<3I', old_tracks[entry_track])
        races = race_paths(data)
        if not races:
            raise ValueError('Donor has no race path')
        old_tracks[entry_track] = struct.pack('<3If2I', *track_header,
            races[0]['distance_to_finish'] * scale, len(donor['points']) - 1, 0) + geometry(donor['points'])
        position, following = [apply(matrix, translation, p) for p in donor['points'][:2]]
        delta = [following[k] - position[k] for k in range(3)]
        length = math.hypot(*delta)
        if not math.isfinite(length) or length == 0:
            raise ValueError('Donor start direction is zero or nonfinite')
        direction = [v / length for v in delta]
        tail = bytearray(tail)
        offset = len(tail) - 40 * len(starts) + 40 * start_index + 8
        struct.pack_into('<6f', tail, offset, *position, *direction)
        start_edit = dict(start_record=start_index, donor_start_path=donor_index,
                          ai_path=entry_ai, track_path=entry_track,
                          before_position=start[2:5], position=position,
                          direction=direction)
    race_edit = None
    if race_course is not None:
        race_ai = donor_ai if race_data is data else ai_paths(race_data)
        starts_here = start_list(race_data)
        if any(i >= len(race_ai) for i in starts_here):
            raise ValueError('Donor start list references an absent path')
        old_tracks, tail, assignments, race_course['table'], race_edit = convert_race_course(
            race_paths(race_data), race_ai, starts_here, lambda p: apply(matrix, translation, p), scale,
            old_tracks, starts, tail,
            checkpoint_limit=race_course.get('checkpoint_limit', 2),
            race_line_kind=race_course.get('race_line_kind', 'race'),
            marker_fractions=race_course.get('marker_fractions', ()))
        for slot, donor_index in assignments.items():
            replacements[slot] = race_ai[donor_index]
    elif relocate_race_starts:
        # Race gates are the start records with a zero second flag (types 0-5 on
        # Snow Jam). Keep their lateral spacing along the original start line,
        # re-express it across the donor opening's direction, and give their
        # shared track path the donor race line so progress and resets follow it.
        donor_index = struct.unpack_from('<I', data, 24)[0]
        if not struct.unpack_from('<I', data, 20)[0] or donor_index >= len(donor_ai):
            raise ValueError('Donor has no usable start path')
        donor = donor_ai[donor_index]
        if len(donor['points']) < 2:
            raise ValueError('Donor start path needs a direction')
        races = race_paths(data)
        if not races:
            raise ValueError('Donor has no race path')
        gates = [(i, s) for i, s in enumerate(starts) if s[1] == 0]
        if not gates or len({s[-1] for _, s in gates}) != 1:
            raise ValueError('Expected race gates sharing one track path')
        position, following = [apply(matrix, translation, p) for p in donor['points'][:2]]
        delta = [following[k] - position[k] for k in range(3)]
        length = math.hypot(*delta)
        if not math.isfinite(length) or length == 0:
            raise ValueError('Donor start direction is zero or nonfinite')
        direction = [v / length for v in delta]
        lateral = [-direction[1], direction[0], 0]
        norm = math.hypot(*lateral[:2]) or 1
        lateral = [v / norm for v in lateral]
        old_dir = gates[0][1][5:8]
        old_lateral = [-old_dir[1], old_dir[0], 0]
        old_norm = math.hypot(*old_lateral[:2]) or 1
        old_lateral = [v / old_norm for v in old_lateral]
        centre = [sum(s[2 + k] for _, s in gates) / len(gates) for k in range(3)]
        tail = bytearray(tail)
        moved = []
        for index, s in gates:
            offset = sum((s[2 + k] - centre[k]) * old_lateral[k] for k in range(3)) * scale
            new_position = [position[k] + lateral[k] * offset for k in range(3)]
            at = len(tail) - 40 * len(starts) + 40 * index + 8
            struct.pack_into('<6f', tail, at, *new_position, *direction)
            moved.append(dict(start_record=index, kind=s[0], before_position=s[2:5], position=new_position, lateral_offset=offset))
        track_index = gates[0][1][-1]
        track_header = struct.unpack_from('<3I', old_tracks[track_index])
        race_points = [apply(matrix, translation, p) for p in races[0]['points']]
        blob = bytearray(struct.pack('<9f', *race_points[0],
                                     *[min(q[k] for q in race_points) for k in range(3)],
                                     *[max(q[k] for q in race_points) for k in range(3)]))
        for a, b in zip(race_points, race_points[1:]):
            d = [b[k] - a[k] for k in range(3)]
            seg = math.hypot(*d[:2]) or math.dist(a, b) or 1
            blob.extend(struct.pack('<4f', *(v / seg for v in d), seg))
        old_tracks[track_index] = struct.pack('<3If2I', *track_header, races[0]['distance_to_finish'] * scale,
                                              len(race_points) - 1, 0) + bytes(blob)
        race_edit = dict(gates=moved, direction=direction, track_path=track_index,
                         track_points=len(race_points), donor_start_path=donor_index)
    out = bytearray(struct.pack('<2I', 0x69696969, len(old_ai)))
    disabled = 0
    for i, path in enumerate(old_ai):
        if i in replacements:
            donor = replacements[i]
            out.extend(struct.pack('<9I', *donor['fields'], len(donor['points']) - 1, 0))
            out.extend(geometry(donor['points']))
        else:
            path = bytearray(path)
            if i not in entry_paths:
                disabled += bool(struct.unpack_from('<I', path, 24)[0])
                struct.pack_into('<I', path, 24, 0)
            out.extend(path)
    out.extend(struct.pack('<I', len(old_tracks)))
    out.extend(b''.join(old_tracks))
    out.extend(tail)
    ssx3_paths(out)
    return bytes(out), dict(donor_reset_paths=len(ai), skipped_donor_nonreset_paths=len(donor_ai) - len(ai),
                            donor_path_events=0, ai_path_count=len(old_ai), track_path_count=len(old_tracks),
                            replaced_path_indices=sorted(replacements),
                            disabled_original_reset_paths=disabled, retained_entry_paths=sorted(entry_paths),
                            start_table_entries=len(starts), freeride_start=start_edit, race_starts=race_edit,
                            source_sha256=hashlib.sha256(data).hexdigest())


class Route:
    def __init__(self, points):
        if len(points) < 2 or any(len(p) != 3 or not all(math.isfinite(v) for v in p) for p in points):
            raise ValueError('Route needs at least two finite XYZ points')
        self.points = points
        self.lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
        self.cumulative = [0.0]
        for length in self.lengths:
            self.cumulative.append(self.cumulative[-1] + length)
        self.length = self.cumulative[-1]
        if self.length == 0:
            raise ValueError('Route has zero length')

    def nearest(self, xyz, start=0, end=None):
        best = None
        for i in range(start, min(len(self.lengths), end if end is not None else len(self.lengths))):
            a, b = self.points[i:i + 2]
            length = self.lengths[i]
            if not length:
                continue
            t = min(1, max(0, sum((xyz[k] - a[k]) * (b[k] - a[k]) for k in range(3)) / length**2))
            point = [a[k] + t * (b[k] - a[k]) for k in range(3)]
            distance = math.dist(xyz, point)
            if best is None or distance < best['distance']:
                best = dict(segment=i, t=t, distance=distance, point=point,
                            progress=self.cumulative[i] + t * length)
        if best is None:
            raise ValueError('No nonzero route segments in search range')
        return best

    def at(self, distance):
        distance = min(self.length, max(0, distance))
        for i, length in enumerate(self.lengths):
            if length and self.cumulative[i + 1] >= distance:
                t = (distance - self.cumulative[i]) / length
                return [self.points[i][k] + t * (self.points[i + 1][k] - self.points[i][k]) for k in range(3)]
        return list(self.points[-1])


def route_points(paths, indices, experiment, *, from_anchor=False):
    """Keep the donor opening unless an explicitly partial route is requested."""
    points = [apply(experiment['matrix'], experiment['translation'], p)
              for i in indices for p in paths[i]['points']]
    start = min(range(len(points)), key=lambda i: math.dist(points[i], experiment['target_anchor'])) if from_anchor else 0
    return points[start:], start


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('aip', type=Path)
    ap.add_argument('experiment', type=Path)
    ap.add_argument('--paths', default='0,1,2,3,4,5')
    ap.add_argument('--from-anchor', action='store_true',
                    help='Explicitly omit the donor opening before the placement anchor (partial route)')
    ap.add_argument('--output', type=Path, required=True)
    args = ap.parse_args()
    if args.output.exists():
        ap.error('Output already exists')
    data = args.aip.read_bytes()
    paths = race_paths(data)
    indices = [int(x) for x in args.paths.split(',')]
    experiment = json.loads(args.experiment.read_text())
    points, start = route_points(paths, indices, experiment, from_anchor=args.from_anchor)
    route = Route(points)
    result = dict(source=str(args.aip), source_sha256=hashlib.sha256(data).hexdigest(),
                  experiment=str(args.experiment), race_paths=indices, first_point_index=start,
                  includes_donor_opening=start == 0, length=route.length, points=points)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'points'}, indent=2))


if __name__ == '__main__':
    main()
