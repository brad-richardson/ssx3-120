#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
"""bench-android.sh collator: one bench dir -> table.md + table.json.

Reads each leg's bench-android.py receipts (summary.json) plus bench.json.
No device.
Usage: collate.py <benchdir>   (reads <benchdir>/bench.json + legs/*/)
"""
import json
import sys
from pathlib import Path


def main():
    out = Path(sys.argv[1])
    bench = json.loads((out / "bench.json").read_text())
    rows = []
    for leg in bench["legs"]:
        base = leg.split("-")[0] if "-" in leg and leg.split("-")[0] in bench["legs"] else leg
        sfile = out / "legs" / leg / "summary.json"
        if not sfile.is_file():
            rows.append({"leg": leg, "verdict": "MISSING"})
            continue
        s = json.loads(sfile.read_text())
        race = s.get("race") or {}
        gpu = ((s.get("gpu") or {}).get("race") or {})
        hrace = ((s.get("hitches") or {}).get("by_phase") or {}).get("race") or {}
        rows.append({
            "leg": leg,
            "verdict": s.get("verdict", "?"),
            "race_vs": race.get("vs_per_s", "—"),
            "ratio": race.get("ratio", "—"),
            "gpu_busy": gpu.get("mean_pct", "—"),
            "hitches": hrace.get("gt100", "—"),
            "reasons": "; ".join(s.get("reasons", [])),
        })
    cols = ["leg", "verdict", "race_vs", "ratio", "gpu_busy", "hitches", "reasons"]
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows:
        lines.append("| " + " | ".join(str(r.get(c, "—")) for c in cols) + " |")
    (out / "table.md").write_text("# bench %s (%s)\n\n%s\n" % (
        bench["name"], bench["apk_sha"][:12], "\n".join(lines)))
    (out / "table.json").write_text(json.dumps({"bench": bench, "legs": rows}, indent=1) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
