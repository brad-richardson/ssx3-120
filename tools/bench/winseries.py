#!/usr/bin/env python3
"""PL3 canonical perf-window tool: per-window CPU/present series from perf logs.

Parses old and new perf logs:
- threads="..." cells keep their FULL labels (Name#id). Roles are summed by
  the base name (GameThread/MTVU/MTVU-VIF/MTVU-GIF/GsWorker/Audio/main),
  so the old duplicate-name bug (an idle MTVU#11 overwriting the active
  MTVU#8 after suffix-stripping) cannot happen. A trailing "*" on a cell
  (new stable thread ID this window) is accepted and counted in a newcol.
- [perf-cpu] lines (new logs) are read and cross-checked against the
  recomputed role sums.
- [perf-present] lines (new logs) give uframes/udup/frame age.
- Unknown lines ([perf-stage], [perf-mtvu], ...) are kept for stage stats
  but otherwise ignored.

usage:
  winseries.py <perf.log> [stage ...] [--span TICK0 TICK1] [--slow] [--full]
             [--event-floor F]
  No flags: one row per window (IPB3 shape, role-summed CPU columns).
  --span TICK0 TICK1: restrict to TICK0 < tick <= TICK1.
  --slow: aggregate summary of slow windows (vsyncs_per_s < 100, IPR1 rule).
  --full: aggregate summary of full-speed windows (vsyncs_per_s >= 118).
  --event-floor F: with --slow/--full, also require tick-rate >= F (0 = off,
    the IPR1 rule; 90 keeps event windows only on mixed menu/race logs).
"""
import re
import statistics
import sys

SLOW_MAX = 100.0
FULL_MIN = 118.0

# Mirrors ps2_perf_log.h roleOf (base thread name -> role). The main thread
# reads "main" only in new logs (isMain flag); old logs cannot identify it,
# so their main-thread CPU stays in "other".
ROLES = ["game", "mtvu", "vif", "gif", "gs", "main", "audio", "other"]


def role_of(base):
    if base == "GameThread":
        return "game"
    if base == "MTVU-VIF":
        return "vif"
    if base == "MTVU-GIF":
        return "gif"
    if base == "MTVU":
        return "mtvu"
    if base == "GsWorker":
        return "gs"
    if base == "main":
        return "main"
    if base == "Audio":
        return "audio"
    return "other"


def parse(path):
    rows = []
    cur = None
    for line in open(path, errors="replace"):
        if line.startswith("[perf] ") and "tick=" in line:
            m = re.search(r"t=([\d.]+)s tick=(\d+) vsyncs_per_s=([\d.]+) presents=(\d+) maxgap_ms=([\d.]+)",
                          line)
            if not m:
                continue
            tm = re.search(r'thermal=(\d)', line)
            row = dict(t=float(m[1]), tick=int(m[2]), vs=float(m[3]), pr=int(m[4]),
                       gap=float(m[5]), therm=int(tm[1]) if tm else -1,
                       threads={}, st={}, cpu=None, present=None)
            text = re.search(r'threads="([^"]*)"', line)
            if text and text[1] != "na":
                for cell in text[1].split():
                    mm = re.match(r"(\S+?)=([\d.]+)(\*)?$", cell)
                    if not mm:
                        continue
                    label, val, star = mm[1], float(mm[2]), mm[3]
                    base = label.rsplit("#", 1)[0] if "#" in label else label
                    row["threads"][label] = (val, star == "*", base)
            cur = row
            rows.append(cur)
        elif line.startswith("[perf-stage]") and cur is not None:
            m = re.search(r"stage=(\S+) n=(\d+) mean=([-\d.]+) p50=([-\d.]+) p95=([-\d.]+) "
                          r"p99=([-\d.]+) max=([-\d.]+)", line)
            if m:
                row = cur
                assert row is not None
                row["st"][m[1]] = (int(m[2]), float(m[3]), float(m[6]), float(m[7]))
        elif line.startswith("[perf-cpu]") and cur is not None:
            cells = dict(re.findall(r"(game|mtvu|vif|gif|gs|main|audio|other|total)=([\d.]+)", line))
            if cells:
                row = cur
                assert row is not None
                row["cpu"] = {k: float(v) for k, v in cells.items()}
        elif line.startswith("[perf-present]") and cur is not None:
            m = re.search(r"presents=(\d+) uframes=(\d+) udup=(\d+) "
                          r"frame_age_ms_p50=([-\d.]+) frame_age_ms_max=([-\d.]+)", line)
            if m:
                row = cur
                assert row is not None
                row["present"] = dict(presents=int(m[1]), uframes=int(m[2]), udup=int(m[3]),
                                      age_p50=float(m[4]), age_max=float(m[5]))
    for i, r in enumerate(rows):
        r["dt"] = r["t"] - (rows[i - 1]["t"] if i else 0.0)
        r["updates"] = r["tick"] - (rows[i - 1]["tick"] if i else 0)
        role = {k: 0.0 for k in ROLES}
        new_ids = 0
        for label, (val, star, base) in r["threads"].items():
            role[role_of(base)] += val
            if star:
                new_ids += 1
        r["role"] = role
        r["new_ids"] = new_ids
    return rows


def show_windows(rows, stages):
    hdr = ("t tick vs pr gap th " + " ".join(stages) +
           " cpu:Game cpu:MTVU cpu:Gs cpu:GIF cpu:VIF cpu:main cpu:audio cpu:other uf udup age_p50")
    print(hdr)
    for r in rows:
        vals = []
        for s in stages:
            v = r["st"].get(s)
            vals.append("%.2f" % v[1] if v else "na")
        g = r["role"]
        p = r["present"] or {}
        print("%d %d %.1f %d %.1f %d %s %.1f %.1f %.1f %.1f %.1f %.1f %.1f %.1f %s %s %s" % (
            r["t"], r["tick"], r["vs"], r["pr"], r["gap"], r["therm"], " ".join(vals),
            g["game"], g["mtvu"], g["gs"], g["gif"], g["vif"],
            g["main"], g["audio"], g["other"],
            p.get("uframes", "na"), p.get("udup", "na"), p.get("age_p50", "na")))


def show_summary(name, rows):
    dur = sum(r["dt"] for r in rows)
    updates = sum(r["updates"] for r in rows)
    print("== %s: n=%d seconds=%.1f updates=%d vs_weighted=%.2f" %
          (name, len(rows), dur, updates, updates / dur if dur else 0.0))
    print("   thermal=%s gap>20ms_windows=%d gap>33ms_windows=%d" % (
        dict(__import__("collections").Counter(r["therm"] for r in rows)),
        sum(r["gap"] > 20 for r in rows), sum(r["gap"] > 33 for r in rows)))
    stage_names = sorted(set().union(*[r["st"] for r in rows])) if rows else []
    for sname in stage_names:
        ss = [r["st"][sname] for r in rows if sname in r["st"]]
        if ss:
            print("   stage %-14s window_mean=%7.3f n_weighted=%7.3f" %
                  (sname, statistics.mean(v[1] for v in ss),
                   sum(v[0] * v[1] for v in ss) / sum(v[0] for v in ss) if sum(v[0] for v in ss) else 0.0))
    if updates > 0:
        cells = []
        for role in ROLES:
            ms = sum(r["role"][role] for r in rows)
            cells.append("%s=%.3fms/up=%.4fcores" % (role, ms / updates, ms / dur / 1000 if dur else 0.0))
        print("   cpu " + " ".join(cells))
        tot = sum(sum(r["threads"][label][0] for label in r["threads"]) for r in rows)
        print("   cpu_all_threads=%.3fms/up=%.4fcores" % (tot / updates, tot / dur / 1000 if dur else 0.0))
    pr = sum(r["pr"] for r in rows)
    uf = sum((r["present"] or {}).get("uframes", 0) for r in rows if r["present"])
    hav = [r for r in rows if r["present"]]
    if hav:
        ages = [r["present"]["age_p50"] for r in hav if r["present"]["age_p50"] >= 0]
        print("   presents=%d uframes=%d unique_ratio=%.3f age_p50_med=%s age_max_max=%s" % (
            pr, uf, uf / pr if pr else 0.0,
            ("%.2f" % statistics.median(ages)) if ages else "na",
            ("%.2f" % max(r["present"]["age_max"] for r in hav)) if ages else "na"))
    else:
        print("   presents=%d (no [perf-present] lines: old log)" % pr)
    with_cpu = [r for r in rows if r["cpu"]]
    if with_cpu:
        diffs = []
        for r in with_cpu:
            for role in ROLES:
                diffs.append(abs(r["cpu"].get(role, 0.0) - r["role"][role]))
        print("   [perf-cpu] lines=%d max_abs_diff_vs_resum=%.2fms" % (len(with_cpu), max(diffs)))


def main(argv):
    path = argv[1]
    stages = []
    span = None
    want_slow = want_full = False
    event_floor = 0.0
    i = 2
    while i < len(argv):
        a = argv[i]
        if a == "--span":
            span = (int(argv[i + 1]), int(argv[i + 2]))
            i += 3
        elif a == "--slow":
            want_slow = True
            i += 1
        elif a == "--full":
            want_full = True
            i += 1
        elif a == "--event-floor":
            event_floor = float(argv[i + 1])
            i += 2
        else:
            stages.append(a)
            i += 1
    if not stages:
        stages = ["ee.busy", "ee.cpu", "mtvu.busy", "gs.busy", "gpu.busy"]
    rows = parse(path)
    if span:
        rows = [r for r in rows if span[0] < r["tick"] <= span[1]]
    if event_floor > 0:
        rows = [r for r in rows if (r["updates"] / r["dt"] if r["dt"] else 0) >= event_floor]
    if want_slow or want_full:
        if want_slow:
            show_summary("slow(vs<100)", [r for r in rows if r["vs"] < SLOW_MAX])
        if want_full:
            show_summary("full(vs>=118)", [r for r in rows if r["vs"] >= FULL_MIN])
    else:
        show_windows(rows, stages)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("usage: winseries.py <perf.log> [stage ...] [--span T0 T1] [--slow] [--full] "
                 "[--event-floor F]")
    main(sys.argv)
