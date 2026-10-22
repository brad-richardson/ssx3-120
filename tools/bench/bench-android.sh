#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Generic Android bench: one APK, fixed leg recipes, one table out.
#
# Usage: bench-android.sh --apk APK --serial SER --app-id ID --legs LEGS.json [--suite quick|full] [--dry-run]
#   --apk     APK to bench (installed with -r on every leg)
#   --serial  adb device serial
#   --app-id  installed application id (device files live under its files/ dir)
#   --legs    legs recipe file (see legs.example.json)
#   --suite   quick (default) = recipe legs only; full = + knobs.tsv variants
#   --dry-run validate everything without touching the device
#
# One out dir per run: <cwd>/bench/<apksha7>-<utc>/{table.md,table.json}.
# Legs use only per-leg test dirs under the app's files (created empty,
# removed after each leg). The device ps2x.env is rewritten per leg from
# --env-base + recipe overlays; the previous device env is NOT restored
# (back it up yourself if it matters).
#
# Device pre-check (fail fast): keyguard off, battery >= 20% and AC powered.
# No lease, no fan control, no play-state restore.
set -u

APK=""; SERIAL=""; APP_ID=""; LEGS=""; SUITE="quick"; DRYRUN=0
while [ $# -gt 0 ]; do
  case $1 in
    --apk) APK=$2; shift;;
    --serial) SERIAL=$2; shift;;
    --app-id) APP_ID=$2; shift;;
    --legs) LEGS=$2; shift;;
    --suite) SUITE=$2; shift;;
    --dry-run) DRYRUN=1;;
    -*) echo "usage: bench-android.sh --apk APK --serial SER --app-id ID --legs LEGS.json [--suite quick|full] [--dry-run]" >&2; exit 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
  shift
done
[ -n "$APK" ] && [ -n "$SERIAL" ] && [ -n "$APP_ID" ] && [ -n "$LEGS" ] \
  || { echo "usage: bench-android.sh --apk APK --serial SER --app-id ID --legs LEGS.json [--suite quick|full] [--dry-run]" >&2; exit 2; }
[ "$SUITE" = "quick" ] || [ "$SUITE" = "full" ] || { echo "bad --suite: $SUITE" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH_RUN="$SCRIPT_DIR/bench-android.py"
COLLATE="$SCRIPT_DIR/collate.py"
for f in "$BENCH_RUN" "$COLLATE" "$LEGS"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 2; }
done
[ -f "$APK" ] || { echo "APK not found: $APK" >&2; exit 2; }
KNOBS_TSV="$SCRIPT_DIR/knobs.tsv"
[ "$SUITE" = "quick" ] || [ -f "$KNOBS_TSV" ] || { echo "full suite needs $KNOBS_TSV" >&2; exit 2; }

adb() { command adb -s "$SERIAL" "$@"; }
FILES=/storage/emulated/0/Android/data/$APP_ID/files

echo "== bench preflight (no device)"
H1="$(shasum -a 256 "$APK" | cut -d' ' -f1)"
H2="$(shasum -a 256 "$APK" | cut -d' ' -f1)"
[ "$H1" = "$H2" ] || { echo "two APK SHA reads differ" >&2; exit 2; }
APKSHA7="$(printf %s "$H1" | cut -c1-7)"
echo "apk=$APK sha=$H1 serial=$SERIAL app=$APP_ID"

# Validate legs.json + knobs.tsv and print the leg plan.
PLAN_DIR="$(mktemp -d /tmp/bench-plan.XXXXXX)"
trap 'rm -rf "$PLAN_DIR"' EXIT
python3 - "$LEGS" "$KNOBS_TSV" "$SUITE" "$PLAN_DIR" <<'EOF'
import json, os, sys
legs_p, knobs_p, suite, plan = sys.argv[1:5]
legs = json.load(open(legs_p))
OWNED = ('PS2X_MC_ROOT', 'PS2X_PAD_SCRIPT', 'PS2X_PAD_SCRIPT_CLOCK', 'PS2X_UNPACED')
REQ = ('env_base', 'pad', 'card', 'unpaced', 'stop_tick',
       'cpu_window', 'scap_ticks', 'wall', 'extra_env')
for name, r in legs.items():
    if name.startswith('_'):
        continue
    missing = [k for k in REQ if k not in r]
    if missing:
        raise SystemExit('legs.json %s missing: %s' % (name, ','.join(missing)))
    if r['pad']['type'] not in ('inline', 'atfile'):
        raise SystemExit('legs.json %s: bad pad type' % name)
    if r['pad']['type'] == 'atfile' and not os.path.isfile(r['pad']['file']):
        raise SystemExit('legs.json %s: missing pad file %s' % (name, r['pad']['file']))
    if r['card']['type'] not in ('empty', 'seeded'):
        raise SystemExit('legs.json %s: bad card type' % name)
    if r['card']['type'] == 'seeded' and not os.path.isdir(r['card']['src']):
        raise SystemExit('legs.json %s: missing card src %s' % (name, r['card']['src']))
    if r['unpaced'] not in (0, 1):
        raise SystemExit('legs.json %s: bad unpaced' % name)
    for e in r['extra_env']:
        if e.split('=', 1)[0] in OWNED:
            raise SystemExit('legs.json %s: refusing bench-owned key in extra_env: %s' % (name, e))
    plan_lines = ['BASE=%s' % r['env_base']] + ['EXTRA=%s' % e for e in r['extra_env']]
    open(os.path.join(plan, name + '.plan'), 'w').write('\n'.join(plan_lines) + '\n')

order = [k for k in legs if not k.startswith('_')]
variants = []
if suite == 'full':
    for ln, raw in enumerate(open(knobs_p), 1):
        line = raw.rstrip('\n')
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        f = line.split('\t')
        if len(f) != 3:
            raise SystemExit('knobs.tsv:%d: want 3 tab fields, got %d' % (ln, len(f)))
        leg, vname, overlay = f
        if leg not in legs:
            raise SystemExit('knobs.tsv:%d: unknown leg %r' % (ln, leg))
        if not vname or any(c in vname for c in ' \t/-'):
            raise SystemExit('knobs.tsv:%d: bad name %r' % (ln, vname))
        kvs = [kv.strip() for kv in overlay.split(';') if kv.strip()]
        if not kvs or any('=' not in kv for kv in kvs):
            raise SystemExit('knobs.tsv:%d: bad overlay %r' % (ln, overlay))
        if any(kv.split('=', 1)[0].strip() in OWNED for kv in kvs):
            raise SystemExit('knobs.tsv:%d: refusing bench-owned key (card/pad/pacing) in %r' % (ln, overlay))
        variants.append((leg, vname, kvs))
    seen = set()
    for leg, vname, _ in variants:
        if (leg, vname) in seen:
            raise SystemExit('knobs.tsv: duplicate %s/%s' % (leg, vname))
        seen.add((leg, vname))
json.dump({'order': order, 'variants': variants},
          open(os.path.join(plan, 'plan.json'), 'w'))
print('plan: legs=%s variants=%s' % (' '.join(order), ','.join('%s/%s' % v[:2] for v in variants) or '(none)'))
EOF
[ $? = 0 ] || { echo "leg/knob validation failed (see above)" >&2; exit 2; }

UTC="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="bench/$APKSHA7-$UTC"
echo "out=$OUT"

if [ "$DRYRUN" = "1" ]; then
  echo "dry-run: device untouched; nothing written."
  exit 0
fi
sh() { adb shell "$1"; }

KG0="$(sh 'dumpsys window policy | grep -m1 showing' | tr -d '\r')"
BAT0="$(sh 'dumpsys battery')"
LVL0="$(printf %s "$BAT0" | grep -m1 -o 'level: [0-9]*' | grep -o '[0-9]*')"
AC0="$(printf %s "$BAT0" | grep -m1 -o 'AC powered: [a-z]*' | awk '{print $3}')"
echo "PRE0 keyguard=[$KG0] battery=${LVL0}% ac=$AC0"
case "$KG0" in *showing=false*) ;; *) echo "keyguard showing: unlock the device first" >&2; exit 2;; esac
[ "${LVL0:-0}" -ge 20 ] && [ "$AC0" = "true" ] || { echo "battery/AC gate failed (need >= 20% and AC powered)" >&2; exit 2; }

mkdir -p "$OUT/legs"
cp "$LEGS" "$OUT/legs.json"

# Variant env files.
python3 - "$PLAN_DIR/plan.json" "$PLAN_DIR" <<'EOF' || { echo "variant plan write failed" >&2; exit 2; }
import json, sys
plan = json.load(open(sys.argv[1]))
for leg, vname, kvs in plan['variants']:
    open('%s/variant-%s-%s.env' % (sys.argv[2], leg, vname), 'w').write('\n'.join(kvs) + '\n')
EOF

run_leg() { # $1 = legs.json leg, $2 = bench leg label suffix (leg or leg/variant)
  LEG=$1; LBL=$2
  STOP="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['stop_tick'])")"
  CPUW="$(python3 -c "import json; print(','.join(map(str, json.load(open('$OUT/legs.json'))['$LEG']['cpu_window'])))")"
  SCAPS="$(python3 -c "import json; print(','.join(map(str, json.load(open('$OUT/legs.json'))['$LEG']['scap_ticks']))")"
  WALL="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['wall'])")"
  PADTYPE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['pad']['type'])")"
  CARDTYPE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['card']['type'])")"
  UNPACED="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['unpaced'])")"
  ENVBASE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['env_base'])")"
  [ -n "$STOP$CPUW$SCAPS$WALL$PADTYPE$CARDTYPE$UNPACED$ENVBASE" ] || { echo "recipe query failed for $LEG" >&2; exit 2; }
  [ -f "$ENVBASE" ] || { echo "missing env_base $ENVBASE for $LEG" >&2; exit 2; }
  echo "== leg $LBL (recipe $LEG, stop=$STOP cpu=$CPUW unpaced=$UNPACED)"

  ENVS=()
  while IFS= read -r line; do
    case $line in EXTRA=*) ENVS+=(--env "${line#*=}");; esac
  done < "$PLAN_DIR/$LEG.plan"
  if [ "$LBL" != "$LEG" ]; then
    while IFS= read -r kv; do ENVS+=(--env "$kv"); done < "$PLAN_DIR/variant-$LBL.env"
  fi

  if [ "$CARDTYPE" = "seeded" ]; then
    TESTDIR="mc0-bench-$LBL"
    CARDSRC="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['card']['src'])")"
    echo "seed $CARDSRC -> $FILES/$TESTDIR"
    adb push "$CARDSRC/." "$FILES/$TESTDIR/" > "$OUT/seed-$LBL.txt" 2>&1
    ENVS+=(--env "PS2X_MC_ROOT=$FILES/$TESTDIR" --test-card-dir "$TESTDIR")
  else
    ENVS+=(--test-card-dir "mc0-bench-$LBL")
  fi
  if [ "$PADTYPE" = "atfile" ]; then
    PADFILE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['pad']['file'])")"
    ENVS+=(--env "PS2X_PAD_SCRIPT=$(cat "$PADFILE")" --env PS2X_PAD_SCRIPT_CLOCK=vsync)
  fi
  ENVS+=(--env "PS2X_UNPACED=$UNPACED")

  echo "+ bench-android.py $LBL"
  python3 "$BENCH_RUN" --label "$LBL" --out "$OUT/legs/$LBL" --serial "$SERIAL" --app-id "$APP_ID" \
    --env-base "$ENVBASE" --apk "$APK" --apk-sha "$H1" --mode screen --stop-tick "$STOP" --cpu-window "$CPUW" \
    --scap-ticks "$SCAPS" --wall "$WALL" "${ENVS[@]}" > "$OUT/leg-$LBL.log" 2>&1
  RC=$?
  echo "leg $LBL rc=$RC"
  [ "$RC" = "0" ] || { echo "leg $LBL failed (rc=$RC); see $OUT/leg-$LBL.log" >&2; exit 1; }
}

LEGS=""
while IFS= read -r l; do LEGS="$LEGS $l"; done <<EOF
$(python3 -c "import json; print('\n'.join(json.load(open('$PLAN_DIR/plan.json'))['order']))")
EOF
while IFS= read -r v; do
  [ -n "$v" ] || continue
  LEGS="$LEGS $v"
done <<EOF
$(python3 -c "import json; print('\n'.join('%s-%s' % (l, n) for l, n, _ in json.load(open('$PLAN_DIR/plan.json'))['variants']))")
EOF
echo "legs:$LEGS"

for entry in $LEGS; do
  if python3 -c "import json; raise SystemExit(0 if '$entry' in json.load(open('$PLAN_DIR/plan.json'))['order'] else 1)"; then
    run_leg "$entry" "$entry"
  else
    run_leg "${entry%-*}" "$entry"
  fi
done

python3 - "$OUT/bench.json" <<EOF
import json
json.dump({
    'name': '$APKSHA7-$UTC',
    'apk': '$APK',
    'apk_sha': '$H1',
    'serial': '$SERIAL',
    'app_id': '$APP_ID',
    'suite': '$SUITE',
    'started_utc': '$UTC',
    'legs': '$LEGS'.split(),
}, open('$OUT/bench.json', 'w'), indent=1)
EOF
[ $? = 0 ] || { echo "bench.json write failed" >&2; exit 2; }

echo "== collate"
python3 "$COLLATE" "$OUT" || { echo "collate failed" >&2; exit 1; }
echo "bench done: $OUT/table.md"
