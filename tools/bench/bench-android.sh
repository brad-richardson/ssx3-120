#!/bin/bash
# One merged-main Odin bench (BN1): the fight-forward loop's device gate.
#
# Usage: odin_bench.sh <apk> [--suite quick|full] [--wait-min N] [--dry-run]
#   <apk>    APK to bench (e.g. ~/dev/ssx3-work/HS1-apk/main-latest.apk)
#   --wait-min N  queue up to N minutes for the Odin lease (poll every 10 s,
#            quiet except once a minute: lanes hand the lease over between
#            batches and a slower poll loses that gap;
#            default 0 = fail fast when another lane holds it).
#   --suite  quick (default) = slope60 + ap120 + race120-light;
#            full = quick + the bench/knobs.tsv variants.
#   --dry-run  validate everything without touching the device.
#
# One per-run Odin hold (BENCH-<apk>-<utc>-<pid>), fixed recipes from local/tooling/bench/legs.json,
# one table out: ~/dev/ssx3-work/bench/<apksha7>-<utc>/{table.md,table.json}.
#
# Legs (see legs.json for the pinned recipes):
#   slope60       PB9 play env, Brad's slopestyle GPU1 window (ticks
#                 3249-11300), @file replay of the full slope recording,
#                 card seeded from the recording snapshot (PB9 Part C).
#   ap120         presets/full120.env, the FH6 heavy All-Peak leg (inline
#                 134 s cut, own test card, UNPACED=0, stop 11600).
#   race120-light FH2 L3 Happiness race start under events mode (I26-FAST,
#                 empty test card).
#
# Odin rules by construction: per-run lease hold, keyguard + AC + battery
# pre-check, Performance/fan modes recorded and never changed (a mid-bench
# change is written back and fails loudly), force-stop + PB9 play-state
# restore after each leg (odin_run.py), test-card seeding and removal,
# staged-recording removal, self-recording removal.
set -u

APK=""; SUITE="quick"; DRYRUN=0; WAIT_MIN=0
while [ $# -gt 0 ]; do
  case $1 in
    --suite) SUITE=$2; shift;;
    --dry-run) DRYRUN=1;;
    --wait-min) WAIT_MIN=$2; shift;;
    -*) echo "usage: odin_bench.sh <apk> [--suite quick|full] [--wait-min N] [--dry-run]" >&2; exit 2;;
    *) [ -z "$APK" ] || { echo "one <apk> only" >&2; exit 2; }; APK=$1;;
  esac
  shift
done
[ -n "$APK" ] || { echo "usage: odin_bench.sh <apk> [--suite quick|full] [--wait-min N] [--dry-run]" >&2; exit 2; }
[ "$SUITE" = "quick" ] || [ "$SUITE" = "full" ] || { echo "bad --suite: $SUITE" >&2; exit 2; }
case "$WAIT_MIN" in ''|*[!0-9]*) echo "bad --wait-min: $WAIT_MIN" >&2; exit 2;; esac

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ ! -d "$SCRIPT_DIR/bench" ]; then
  SCRIPT_DIR="$(cd "$SCRIPT_DIR" && while [ ! -f "$PWD/local/tooling/odin_lease.sh" ] && [ "$PWD" != / ]; do cd ..; done; pwd)/local/tooling"
fi
BENCH_DIR="$SCRIPT_DIR/bench"
ODIN_RUN="$SCRIPT_DIR/odin_run.py"
LEASESH="$SCRIPT_DIR/odin_lease.sh"
PADREC_ENV="$SCRIPT_DIR/padrec_replay_env.py"
COLLATE="$BENCH_DIR/collate.py"
LEGS_JSON="$BENCH_DIR/legs.json"
KNOBS_TSV="$BENCH_DIR/knobs.tsv"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERIAL_FILE="$REPO/local/odin-serial"
WORK=$HOME/dev/ssx3-work
INPUTS=$WORK/bench-inputs
PLAYDIR=$WORK/odin-play
PLAY_ENV=$PLAYDIR/ps2x.env
PLAY_SUMS=$PLAYDIR/SHA256SUMS
PRESET=$PLAYDIR/presets/full120.env
PKG=com.ps2x.runner
FILES=/storage/emulated/0/Android/data/$PKG/files
HOLD="" # assigned below from the APK, UTC and this process

for f in "$ODIN_RUN" "$LEASESH" "$PADREC_ENV" "$COLLATE" "$LEGS_JSON" "$KNOBS_TSV"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 2; }
done
[ -f "$APK" ] || { echo "APK not found: $APK" >&2; exit 2; }
[ -f "$PLAY_ENV" ] && [ -f "$PLAY_SUMS" ] && [ -f "$PRESET" ] || { echo "missing odin-play env/preset" >&2; exit 2; }
[ -f "$REPO/local/research/FH1/fh6_present.py" ] || { echo "missing fh6_present.py" >&2; exit 2; }

echo "== bench preflight (no device)"
H1="$(shasum -a 256 "$APK" | cut -d' ' -f1)"
H2="$(shasum -a 256 "$APK" | cut -d' ' -f1)"
[ "$H1" = "$H2" ] || { echo "two APK SHA reads differ" >&2; exit 2; }
APKSHA7="$(printf %s "$H1" | cut -c1-7)"
(cd "$INPUTS" && shasum -a 256 -c SHA256SUMS) || { echo "bench-inputs SHA mismatch" >&2; exit 2; }
WANT_ENV="$(awk '$2=="ps2x.env"{print $1}' "$PLAY_SUMS")"
[ "${#WANT_ENV}" -eq 64 ] || { echo "no ps2x.env pin in $PLAY_SUMS" >&2; exit 2; }
PRESET_SHA="$(shasum -a 256 "$PRESET" | cut -d' ' -f1)"
echo "apk=$APK sha=$H1"
echo "play_env=$WANT_ENV preset=$PRESET_SHA"

# Validate legs.json + knobs.tsv and print the leg plan (python does the
# parsing once; the shell reuses its validated per-leg .env-plan files).
PLAN_DIR="$(mktemp -d /tmp/odin-bench-plan.XXXXXX)"
trap 'rm -rf "$PLAN_DIR"' EXIT
python3 - "$LEGS_JSON" "$KNOBS_TSV" "$SUITE" "$PLAN_DIR" "$INPUTS" "$PLAY_ENV" "$PRESET" "$WORK" <<'EOF'
import json, os, sys
legs_p, knobs_p, suite, plan, inputs, play_env, preset, work = sys.argv[1:9]
legs = json.load(open(legs_p))
OWNED = ('PS2X_MC_ROOT', 'PS2X_PAD_SCRIPT', 'PS2X_PAD_SCRIPT_CLOCK', 'PS2X_UNPACED')
REQ = ('rate', 'target', 'env_base', 'pad', 'card', 'unpaced', 'stop_tick',
       'cpu_window', 'scap_ticks', 'wall', 'extra_env', 'analysis')
for name, r in legs.items():
    missing = [k for k in REQ if k not in r]
    if missing:
        raise SystemExit('legs.json %s missing: %s' % (name, ','.join(missing)))
    if r['env_base'] not in ('play', 'preset'):
        raise SystemExit('legs.json %s: bad env_base' % name)
    if r['analysis'] not in ('stock', 'fh6'):
        raise SystemExit('legs.json %s: bad analysis' % name)
    if r['pad']['type'] not in ('i26', 'atfile', 'inline-file'):
        raise SystemExit('legs.json %s: bad pad type' % name)
    if r['card']['type'] not in ('empty', 'seeded'):
        raise SystemExit('legs.json %s: bad card type' % name)
    if r['unpaced'] not in (0, 1):
        raise SystemExit('legs.json %s: bad unpaced' % name)
    if r['pad']['type'] == 'atfile' and not os.path.isfile(os.path.join(inputs, r['pad']['rec'])):
        raise SystemExit('legs.json %s: missing recording %s' % (name, r['pad']['rec']))
    if r['pad']['type'] == 'inline-file':
        p = os.path.join(inputs, r['pad']['file'])
        if not os.path.isfile(p):
            raise SystemExit('legs.json %s: missing pad file %s' % (name, p))
        with open(p, 'rb') as f:
            body = f.read()
        if b'\n' in body:
            raise SystemExit('legs.json %s: pad file has newlines (would corrupt ps2x.env)' % name)
    if r['card']['type'] == 'seeded' and not os.path.isdir(os.path.join(work, r['card']['src'])):
        raise SystemExit('legs.json %s: missing card src %s' % (name, r['card']['src']))
    # Preset-diff overlays for preset legs (K=V lines in the preset that
    # differ from the play base; comments/blanks ignored).
    def parse(p):
        out = {}
        for raw in open(p):
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            out[k.strip()] = v.strip()
        return out
    base, pre = parse(play_env), parse(preset)
    if r['env_base'] == 'preset':
        diff = ['%s=%s' % (k, v) for k, v in pre.items() if base.get(k) != v]
        if not diff:
            raise SystemExit('legs.json %s: preset == play base (no overlays)' % name)
        for e in diff:
            if any(x in e for x in ('DUMP', 'TRACE', 'CAPTURE')):
                raise SystemExit('legs.json %s: refusing diag key in preset diff: %s' % (name, e))
    else:
        diff = []
    # The bench owns the card and pad keys (odin_run's mc0-test / the leg's
    # seeded test card / the leg's script). An overlay carrying them would
    # win over those and could point a leg at Brad's live card.
    skipped = [e for e in diff if e.split('=', 1)[0] in OWNED]
    if skipped:
        print('WARNING PB45-BENCH1 %s: skipping stale preset-owned keys: %s' % (name, ', '.join(skipped)), file=sys.stderr)
    diff = [e for e in diff if e.split('=', 1)[0] not in OWNED]
    for e in r['extra_env']:
        if e.split('=', 1)[0] in OWNED:
            raise SystemExit('legs.json %s: refusing bench-owned key in extra_env: %s' % (name, e))
    plan_lines = ['BASE=%s' % r['env_base']] + ['OVERLAY=%s' % e for e in diff]
    plan_lines += ['EXTRA=%s' % e for e in r['extra_env']]
    open(os.path.join(plan, name + '.plan'), 'w').write('\n'.join(plan_lines) + '\n')

order = ['slope60', 'ap120', 'race120-light']
for name in order:
    if name not in legs:
        raise SystemExit('legs.json: missing quick leg %s' % name)
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
print('plan: quick=%s variants=%s' % (' '.join(order), ','.join('%s/%s' % v[:2] for v in variants) or '(none)'))
for name in order:
    print('  %s: %s' % (name, legs[name]['desc'].split('.')[0]))
EOF
[ $? = 0 ] || { echo "leg/knob validation failed (see above)" >&2; exit 2; }

# Fork lookup: which main build (if any) produced this APK?
FORK_TIP="unknown"
for j in "$WORK"/HS1-apk/main-*.json; do
  [ -f "$j" ] || continue
  case "$j" in *-staleok.json) continue;; esac
  if [ "$(python3 -c "import json; print(json.load(open('$j'))['apk_sha'])")" = "$H1" ]; then
    FORK_TIP="$(python3 -c "import json; print(json.load(open('$j'))['fork'])")"
    echo "fork: $FORK_TIP (from $j)"
    break
  fi
done

# In-APK microVU check (recorded; warn only: custom APKs may differ).
MVU_IN_APK="$(unzip -p "$APK" 'lib/arm64-v8a/libmv2_microvu.so' 2>/dev/null | shasum -a 256 | cut -d' ' -f1)"
MVU_WANT="$(shasum -a 256 "$WORK/jnilibs/play-current/arm64-v8a/libmv2_microvu.so" | cut -d' ' -f1)"
echo "apk-microvu=$MVU_IN_APK play-current=$MVU_WANT"
[ "$MVU_IN_APK" = "$MVU_WANT" ] || echo "WARNING: APK microVU differs from play-current (custom APK?)" >&2

UTC="$(date -u +%Y%m%dT%H%M%SZ)"
HOLD="BENCH-$APKSHA7-$UTC-$$"
OUT="$WORK/bench/$APKSHA7-$UTC"
echo "out=$OUT"

if [ "$DRYRUN" = "1" ]; then
  echo "dry-run: device untouched; nothing written. Recipe plans are in $PLAN_DIR (removed on exit)."
  exit 0
fi
S="$(cat "$SERIAL_FILE")"
adb() { command adb -s "$S" "$@"; }
sh() { adb shell "$1"; }

# Read-only fail-fast pre-check (no lease): keyguard + AC + battery.
KG0="$(sh 'dumpsys window policy | grep -m1 showing' | tr -d '\r')"
BAT0="$(sh 'dumpsys battery')"
LVL0="$(printf %s "$BAT0" | grep -m1 -o 'level: [0-9]*' | grep -o '[0-9]*')"
AC0="$(printf %s "$BAT0" | grep -m1 -o 'AC powered: [a-z]*' | awk '{print $3}')"
echo "PRE0 keyguard=[$KG0] battery=${LVL0}% ac=$AC0"
case "$KG0" in *showing=false*) ;; *) echo "keyguard showing: ask Brad to unlock" >&2; exit 2;; esac
[ "${LVL0:-0}" -ge 20 ] && [ "$AC0" = "true" ] || { echo "battery/AC gate failed (need >= 20% and AC powered)" >&2; exit 2; }

echo "== hold $HOLD (wait up to ${WAIT_MIN} min)"
DEADLINE=$(( $(date +%s) + WAIT_MIN * 60 ))
until bash "$LEASESH" hold "$HOLD" 2>/dev/null | grep -q HELD-SESSION; do
  [ "$(date +%s)" -lt "$DEADLINE" ] || { echo "bench hold failed (lease busy: $(bash "$LEASESH" status | tr '\n' ' ')); not starting" >&2; exit 2; }
  NOW=$(date +%s)
  if [ $(( (NOW - DEADLINE + WAIT_MIN * 60) % 60 )) -lt 10 ]; then
    echo "lease busy: $(bash "$LEASESH" status | tr '\n' ' ')(polling every 10 s)"
  fi
  sleep 10
done
echo "HELD-SESSION $HOLD $(date -u +%FT%TZ)"
trap 'bash "$LEASESH" unhold "$HOLD" >/dev/null 2>&1; echo "unheld $HOLD"; rm -rf "$PLAN_DIR"; trap - EXIT' EXIT
# Out dir only once the device is ours (a refused hold leaves nothing behind).
mkdir -p "$OUT/legs"
cp "$LEGS_JSON" "$OUT/legs.json"
cp "$KNOBS_TSV" "$OUT/knobs.tsv"

modes() { sh 'settings get system performance_mode; settings get system fan_mode' | tr -d '\r' | tr '\n' '/' ; }
MODES_BEFORE="$(modes)"
echo "modes-before: $MODES_BEFORE"
PERF_BEFORE="$(printf %s "$MODES_BEFORE" | cut -d/ -f1)"
FAN_BEFORE="$(printf %s "$MODES_BEFORE" | cut -d/ -f2)"

# Pre-existing state refusals (never touch another session's cards/stage).
for d in mc0-padrec-test mc0-allpeak-test; do
  if ! sh "ls -d $FILES/$d 2>&1" | grep -q "No such file"; then
    echo "$d already exists on device; stop" >&2; exit 2
  fi
done
sh "ls $FILES/padrec" | sort > "$OUT/padrec-before.txt"
for rec in $(python3 -c "
import json
legs = json.load(open('$OUT/legs.json'))
for r in legs.values():
    if r['pad']['type'] == 'atfile':
        print(r['pad']['rec'])
"); do
  if sh "ls $FILES/$rec 2>&1" | grep -qv "No such file"; then
    echo "staged recording $rec already on device; stop" >&2; exit 2
  fi
done

check_modes() { # $1 = context. Restores exactly + fails loud on drift.
  M="$(modes)"
  if [ "$M" != "$MODES_BEFORE" ]; then
    echo "MODES DRIFTED ($1): was $MODES_BEFORE now $M; writing back" >&2
    sh "settings put system performance_mode $PERF_BEFORE" >/dev/null
    sh "settings put system fan_mode $FAN_BEFORE" >/dev/null
    echo "modes-after-writeback: $(modes)" >&2
    echo "bench FAILED on mode drift" >&2; exit 1
  fi
}

seed_card() { # $1 = device test dir, $2 = local src dir
  echo "seed $2 -> $FILES/$1"
  bash "$LEASESH" run "$HOLD-seed" -- adb -s "$S" push "$2/." "$FILES/$1/" > "$OUT/seed-$1.txt" 2>&1
  (cd "$2" && find . -type f -exec shasum -a 256 {} \; | awk '{print $1}' | sort) > "$OUT/mc-src-$1.txt"
  sh "cd $FILES/$1 && find . -type f -exec sha256sum {} \;" | awk '{print $1}' | sort > "$OUT/mc-dev-$1.txt"
  diff "$OUT/mc-src-$1.txt" "$OUT/mc-dev-$1.txt" || { echo "card seed mismatch: $1" >&2; exit 2; }
  echo "card-seeded-ok $1 ($(wc -l < "$OUT/mc-src-$1.txt") files)"
}

clean_card() { # $1 = device test dir
  bash "$LEASESH" run "$HOLD-clean" -- adb -s "$S" shell "rm -rf $FILES/$1 $FILES/${1}_slot1" > "$OUT/clean-$1.txt" 2>&1
  sh "ls -d $FILES/$1 2>&1" | grep -q "No such file" || { echo "card removal failed: $1" >&2; exit 1; }
  echo "card-removed-ok $1"
}

run_leg() { # $1 = legs.json leg, $2 = bench leg label suffix (leg or leg/variant)
  LEG=$1; LBL=$2
  STOP="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['stop_tick'])")"
  CPUW="$(python3 -c "import json; print('%d,%d' % tuple(json.load(open('$OUT/legs.json'))['$LEG']['cpu_window']))")"
  SCAPS="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['scap_ticks'])")"
  WALL="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['wall'])")"
  PADTYPE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['pad']['type'])")"
  CARDTYPE="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['card']['type'])")"
  UNPACED="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['unpaced'])")"
  [ -n "$STOP$CPUW$SCAPS$WALL$PADTYPE$CARDTYPE$UNPACED" ] || { echo "recipe query failed for $LEG" >&2; exit 2; }
  echo "== leg $LBL (recipe $LEG, stop=$STOP cpu=$CPUW unpaced=$UNPACED)"

  ENVS=()
  while IFS= read -r line; do
    case $line in OVERLAY=*|EXTRA=*) ENVS+=(--env "${line#*=}");; esac
  done < "$PLAN_DIR/$LEG.plan"
  # Variant overlays (plan.json) come after the recipe.
  if [ "$LBL" != "$LEG" ]; then
    while IFS= read -r kv; do ENVS+=(--env "$kv"); done < "$PLAN_DIR/variant-$LBL.env"
  fi

  if [ "$CARDTYPE" = "seeded" ]; then
    TESTDIR="$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['card']['test_dir'])")"
    CARDSRC="$WORK/$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['card']['src'])")"
    seed_card "$TESTDIR" "$CARDSRC"
    ENVS+=(--env "PS2X_MC_ROOT=$FILES/$TESTDIR")
  fi
  STAGED=""
  if [ "$PADTYPE" = "atfile" ]; then
    REC="$INPUTS/$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['pad']['rec'])")"
    echo "stage $REC"
    PADLINE="$(bash "$LEASESH" run "$HOLD-stage" -- python3 "$PADREC_ENV" "$REC" --push-to odin 2>"$OUT/stage-$LBL.txt" | grep -m1 '^PS2X_PAD_SCRIPT=@')"
    [ -n "$PADLINE" ] || { echo "record stage failed; see $OUT/stage-$LBL.txt" >&2; exit 2; }
    cat "$OUT/stage-$LBL.txt"
    DEVPATH="${PADLINE#PS2X_PAD_SCRIPT=@}"
    STAGED="$DEVPATH"
    [ "$(sh "sha256sum $DEVPATH" | cut -d' ' -f1)" = "$(shasum -a 256 "$REC" | cut -d' ' -f1)" ] || { echo "staged recording sha mismatch" >&2; exit 2; }
    ENVS+=(--env "PS2X_PAD_SCRIPT=@$DEVPATH" --env PS2X_PAD_SCRIPT_CLOCK=vsync)
  elif [ "$PADTYPE" = "inline-file" ]; then
    PADFILE="$INPUTS/$(python3 -c "import json; print(json.load(open('$OUT/legs.json'))['$LEG']['pad']['file'])")"
    ENVS+=(--env "PS2X_PAD_SCRIPT=$(cat "$PADFILE")" --env PS2X_PAD_SCRIPT_CLOCK=vsync)
  fi
  ENVS+=(--env "PS2X_UNPACED=$UNPACED")

  sh "ls $FILES/padrec" | sort > "$OUT/padrec-before-$LBL.txt"
  echo "+ odin_run.py $LBL"
  python3 "$ODIN_RUN" --label "$HOLD-$LBL" --lease "$HOLD" --out "$OUT/legs/$LBL" \
    --apk "$APK" --apk-sha "$H1" --mode screen --stop-tick "$STOP" --cpu-window "$CPUW" \
    --scap-ticks "$SCAPS" --wall "$WALL" "${ENVS[@]}" > "$OUT/leg-$LBL.log" 2>&1
  RC=$?
  echo "leg $LBL rc=$RC"

  # Cleanup first (device hygiene even when the leg failed).
  if [ -n "$STAGED" ]; then
    bash "$LEASESH" run "$HOLD-clean" -- adb -s "$S" shell "rm -f $STAGED" >/dev/null
    sh "ls $STAGED 2>&1" | grep -q "No such file" || { echo "staged recording removal failed" >&2; exit 1; }
    echo "staged-removed-ok $STAGED"
  fi
  if [ "$CARDTYPE" = "seeded" ]; then
    clean_card "$TESTDIR"
  fi
  sh "ls $FILES/padrec" | sort > "$OUT/padrec-after-$LBL.txt"
  comm -13 "$OUT/padrec-before-$LBL.txt" "$OUT/padrec-after-$LBL.txt" > "$OUT/padrec-new-$LBL.txt" || true
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    # </dev/null: adb shell reads stdin and would swallow the rest of the list
    # (09-29: the .mc dir went, its .txt was left behind).
    bash "$LEASESH" run "$HOLD-clean" -- adb -s "$S" shell "rm -rf $FILES/padrec/$f" < /dev/null
  done < "$OUT/padrec-new-$LBL.txt"
  sh "ls $FILES/padrec" | sort > "$OUT/padrec-final-$LBL.txt"
  diff "$OUT/padrec-before-$LBL.txt" "$OUT/padrec-final-$LBL.txt" > "$OUT/padrec-diff-$LBL.txt" \
    || { echo "padrec leftovers after $LBL:" >&2; cat "$OUT/padrec-diff-$LBL.txt" >&2; exit 1; }
  echo "padrec-restored $LBL"

  check_modes "after $LBL"
  [ "$RC" = "0" ] || { echo "leg $LBL failed (rc=$RC); see $OUT/leg-$LBL.log" >&2; exit 1; }
}

# Variant env files.
python3 - "$PLAN_DIR/plan.json" "$PLAN_DIR" <<'EOF' || { echo "variant plan write failed" >&2; exit 2; }
import json, sys
plan = json.load(open(sys.argv[1]))
for leg, vname, kvs in plan['variants']:
    open('%s/variant-%s-%s.env' % (sys.argv[2], leg, vname), 'w').write('\n'.join(kvs) + '\n')
EOF

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
  # Variant labels are leg-name; recipe legs (incl. race120-light) are listed
  # in plan.json order. Variant names carry no dash (validator), so ${entry%-*}
  # recovers the recipe.
  if python3 -c "import json; raise SystemExit(0 if '$entry' in json.load(open('$PLAN_DIR/plan.json'))['order'] else 1)"; then
    run_leg "$entry" "$entry"
  else
    run_leg "${entry%-*}" "$entry"
  fi
done

MODES_AFTER="$(modes)"
echo "modes-after: $MODES_AFTER"
[ "$MODES_AFTER" = "$MODES_BEFORE" ] || { echo "modes drifted over the bench" >&2; exit 1; }

python3 - "$OUT/bench.json" <<EOF
import json
json.dump({
    'name': '$APKSHA7-$UTC',
    'apk': '$APK',
    'apk_sha': '$H1',
    'apk_microvu': '$MVU_IN_APK',
    'fork': '$FORK_TIP',
    'suite': '$SUITE',
    'started_utc': '$UTC',
    'modes_before': '$MODES_BEFORE',
    'modes_after': '$MODES_AFTER',
    'play_env_sha': '$WANT_ENV',
    'preset': 'full120.env',
    'preset_sha': '$PRESET_SHA',
    'legs': '$LEGS'.split(),
}, open('$OUT/bench.json', 'w'), indent=1)
EOF
[ $? = 0 ] || { echo "bench.json write failed" >&2; exit 2; }

echo "== collate"
python3 "$COLLATE" "$OUT" || { echo "collate failed" >&2; exit 1; }
echo "bench done: $OUT/table.md"
