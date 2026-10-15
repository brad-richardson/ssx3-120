# Generic Android bench (`tools/bench/`)

One APK, fixed leg recipes, one table out. No lease, no fan control, no
play-state save/restore — the driver only touches per-leg test dirs under
the app's files, which it removes after each leg.

## Quick start

```sh
cp tools/bench/legs.example.json /tmp/legs.json
# fill in your pad files and test-card dirs in /tmp/legs.json
tools/bench/bench-android.sh --apk app-release.apk --serial SER \
  --app-id com.ssx3.recomp --legs /tmp/legs.json
```

A single leg:

```sh
python3 tools/bench/bench-android.py --label A1 --out legs/A1 \
  --serial SER --app-id com.ssx3.recomp --env-base presets/ps2x.env \
  --apk app-release.apk --stop-tick 2500
```

Re-summarize a leg dir without a device:

```sh
python3 tools/bench/bench-android.py --summarize-only legs/A1
```

## Leg recipes (`legs.example.json`)

Each leg: base env file, pad (`inline` title-idle or `atfile` with your own
pad file — recordings are yours, never shipped), card (`empty` test dir or
`seeded` from your local card copy), unpaced flag, stop tick, CPU/screencap
windows, wall cap, extra env overlays. Bench-owned keys (card, pad, pacing)
are refused in overlays. `--suite full` adds `knobs.tsv` knob-variant legs
(same 3-tab format as the example: `leg<TAB>name<TAB>K=V;...`).

## What a leg does

Keyguard off + battery/AC pre-check, `adb install -r`, base env + run keys
(empty test card, pad, unpaced) + your `--env` overlays pushed as the device
`ps2x.env`, launch, full sampler set (vsync-rate, present-latency fast poll,
schedstat windows, thermal, gpubusy, residency, screencaps, optional
simpleperf profile), force-stop, per-leg cleanup, `summary.json` +
`summary.txt` + `hitches.txt` + `phases.txt`. `collate.py` rolls legs into
`table.md`/`table.json`.

Device notes: the app must already stage its boot ELF, disc image and env
companions (see `apps/android/README.md`); the bench only writes the env,
the test card and its record dirs. Test legs force achievements off and
redirect telemetry/record dirs under a per-leg dir so your data is never
touched. A leg on a SurfaceFlinger tracker stuck at 60 Hz fails loudly
instead of burning the run.

Provenance: generalized from the lab's `odin_bench.sh`/`odin_run.py`
(repo-split-2); sampler set, analysis and summary format unchanged.
Present-window 120 Hz analysis and knob-variant recipes stay lab-side until
their own generalization.
