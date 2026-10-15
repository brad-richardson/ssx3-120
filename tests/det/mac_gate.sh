#!/bin/bash
# One-command Mac gate (BN1): the fight-forward loop's Mac vetting.
#
# Usage: mac_gate.sh <worktree> [--profiles reference,play,full120events,play120]
#   <worktree>  PS2Recomp fork checkout to vet (e.g. a lane worktree at the
#               gated tip that would be pushed).
#
# Steps (one PASS/FAIL table at the end; exit 0 iff all PASS):
#   1. Mac build via mac_build.sh --det (shared ccache, niced).
#   2. Suite (ps2x_tests) from the worktree root.
#   3. Runner-dir check vs RUNNER_BASE (default upstream c5a9d02, the base the fork
#      was rebuilt on in UPR1 10-02), committed + dirty.
#   4. Knob-off det vs the current Mac play key (p-lane slot, 1 boot).
#   5. Self-match for each named profile key (reference, play,
#      full120events, play120; the default is 4 boots total).
#
# Keys resolve by profile+build, newest manifest wins (lanes re-mint on
# deliberate change); every candidate is printed. Det boots mirror
# baseline.py make (pins' route/backend/sound/stop/hash-every/coverage +
# the pinned microVU dylib). CN2a: the backend comes from the key's pins.
# CN2b: the det keys are backend=external (GE1, the extracted PCSX2 GS, on
# MoltenVK); the GE1 dylib's SHA-256 must equal the key's ge1_lib pin.
# backend=parallel keys (paraLLEl-GS, retired) are refused. The arm64 reference key (RF1, 09-30) is PS2X_PROFILE=reference
# (every compiled default off) with the VU1 engine on microVU: det_boot adds
# PS2X_VU1_ENGINE=microvu + PS2X_MTVU=1 (microVU refuses to start without MTVU) for it, as
# baseline.py make does. x86 det boots are retired.
set -u

WT=""; PROFILES="reference,play,full120events,play120"
while [ $# -gt 0 ]; do
  case $1 in
    --profiles) PROFILES=$2; shift;;
    -*) echo "usage: mac_gate.sh <worktree> [--profiles reference,play,full120events,play120]" >&2; exit 2;;
    *) [ -z "$WT" ] || { echo "one <worktree> only" >&2; exit 2; }; WT=$1;;
  esac
  shift
done
[ -n "$WT" ] || { echo "usage: mac_gate.sh <worktree> [--profiles reference,play,full120events,play120]" >&2; exit 2; }
WT="${WT%/}"
[ -d "$WT" ] || { echo "not a dir: $WT" >&2; exit 2; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# OMF1 lane hook: find the repo root by walking up (the lane copy sits one
# level deeper than the landed path, so a fixed ../.. is wrong for one of
# them); prefer tooling copies beside this script (lane testing).
REPO="$(cd "$SCRIPT_DIR" && while [ ! -f "$PWD/local/tooling/p_lane_lease.py" ] && [ "$PWD" != / ]; do cd ..; done; pwd)"
pick() { [ -f "$SCRIPT_DIR/$1" ] && printf '%s' "$SCRIPT_DIR/$1" || printf '%s' "$REPO/local/tooling/$1"; }
MAC_BUILD="$(pick build/mac_build.sh)"
KNOB_MATRIX="$(pick knob_matrix.py)"
KNOB_REGISTRY="$(pick knob_registry.py)"
POOL_PY="$(pick pool.py)"
BOOT="$(pick boot/ssx3_boot.py)"
BASELINE="$(pick boot/baseline.py)"
PLEASE="$(pick p_lane_lease.py)"
WORK=$HOME/dev/ssx3-work
STORE=${MAC_GATE_STORE:-$WORK/baselines}
# PI1: pinned inputs (pinned/INDEX.tsv; the keys pin both SHAs, checked below).
MVU_DYLIB=$WORK/pinned/mac/microvu-dylib-ac245573/libmv2_microvu.dylib
GE1_DYLIB=$WORK/pinned/mac/ge1-dylib-37a81b4d/libge1_gs.dylib # = ssx3_boot.py GE1_LIB
for f in "$MAC_BUILD" "$POOL_PY" "$BOOT" "$BASELINE" "$PLEASE"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 2; }
done

HEAD_SHA="$(git -C "$WT" rev-parse HEAD)" || { echo "not a git worktree: $WT" >&2; exit 2; }
SHA7="$(printf %s "$HEAD_SHA" | cut -c1-7)"
DIRTY_N="$(git -C "$WT" status --porcelain | wc -l | tr -d ' ')"
GATE="$(dirname "$WT")/mac-gate-$SHA7"
mkdir -p "$GATE"
echo "== mac_gate $WT @ $HEAD_SHA (dirty=$DIRTY_N)"
echo "   gate dir: $GATE"

RESULTS="$GATE/table.md"
: > "$RESULTS"
pass() { printf '| %s | PASS | %s |\n' "$1" "$2" | tee -a "$RESULTS"; }
fail() { printf '| %s | FAIL | %s |\n' "$1" "$2" | tee -a "$RESULTS"; }
{
  echo "# mac_gate $SHA7 ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo ""
  echo "worktree \`$WT\` @ \`$HEAD_SHA\` dirty=$DIRTY_N profiles=$PROFILES"
  echo ""
  echo "| Step | Result | Detail |"
  echo "| --- | --- | --- |"
} | tee "$RESULTS"
FAIL_N=0

# 0. Knob checks: pure file reads, no slot needed. Pins
# --rev to the tested tip, so a signed-off play setting that isn't effective
# on a platform fails the gate.
echo "== [0/5] knob-matrix check"
if python3 "$KNOB_MATRIX" --check --rev "$HEAD_SHA" > "$GATE/knob-check.log" 2>&1; then
  pass "knob-check" "signed-off play set effective at $SHA7 (log $GATE/knob-check.log)"
else
  fail "knob-check" "divergence (see $GATE/knob-check.log)"; FAIL_N=$((FAIL_N+1))
fi
if "$KNOB_REGISTRY" --check --fork "$WT" --rev "$HEAD_SHA" > "$GATE/knob-registry.log" 2>&1; then
  pass "knob-registry" "runtime reads registered at $SHA7 (log $GATE/knob-registry.log)"
else
  fail "knob-registry" "missing or invalid registration (see $GATE/knob-registry.log)"; FAIL_N=$((FAIL_N+1))
fi

# 1. Build: pool first (BLD1b), in-place fallback. The suite still runs from $WT.
echo "== [1/5] build --det (pool first)"
SUITE_BIN=""; RUNNER=""; POOL_KEY=""
if [ -z "${POOL_DISABLE:-}" ] && POOL_KEY="$(python3 "$POOL_PY" key --worktree "$WT" --flavour det 2>"$GATE/pool-key.log")"; then
  if POOL_ENTRY="$(python3 "$POOL_PY" get --worktree "$WT" --flavour det 2>"$GATE/pool-get.log")"; then
    POOL_ENTRY="$(printf %s "$POOL_ENTRY" | grep '^/' | head -1)"
    POOL_MSG="$(grep POOL "$GATE/pool-get.log" | tail -1)"
    SUITE_BIN="$POOL_ENTRY/ps2x_tests"
    RUNNER="$POOL_ENTRY/ps2EntryRunner"
    pass "build" "pool $POOL_MSG key=$POOL_KEY (log $GATE/pool-get.log)"
  else
    fail "build" "pool.py get failed (see $GATE/pool-get.log)"; FAIL_N=$((FAIL_N+1))
  fi
else
  if [ -n "${POOL_DISABLE:-}" ]; then POOL_WHY="pool disabled"; else POOL_WHY="dirty"; fi
  if nice -n 10 bash "$MAC_BUILD" "$WT" "$GATE/build" --det > "$GATE/build.log" 2>&1; then
    pass "build" "in-place ($POOL_WHY) mac_build.sh --det rc=0 (log $GATE/build.log)"
  else
    fail "build" "in-place ($POOL_WHY) see $GATE/build.log"; FAIL_N=$((FAIL_N+1))
  fi
  SUITE_BIN="$GATE/build/ps2xTest/ps2x_tests"
  RUNNER="$GATE/build/ps2xRuntime/ps2EntryRunner"
fi

# 2. Suite from the worktree root.
echo "== [2/5] suite"
if [ -x "$SUITE_BIN" ]; then
  (cd "$WT" && "$SUITE_BIN" > "$GATE/suite.log" 2>&1)
  SUITE_RC=$?
  TOTAL="$(sed -n 's/^Total Tests: \([0-9]*\)$/\1/p' "$GATE/suite.log" | tail -1)"
  PASSED="$(sed -n 's/^Passed: \([0-9]*\)$/\1/p' "$GATE/suite.log" | tail -1)"
  FAILED="$(sed -n 's/^Failed: \([0-9]*\)$/\1/p' "$GATE/suite.log" | tail -1)"
  if [ "$SUITE_RC" = "0" ] && [ -n "$TOTAL" ] && [ "$FAILED" = "0" ]; then
    pass "suite" "$PASSED/$TOTAL from the worktree root"
  else
    fail "suite" "rc=$SUITE_RC total=$TOTAL passed=$PASSED failed=$FAILED (see $GATE/suite.log)"; FAIL_N=$((FAIL_N+1))
  fi
else
  fail "suite" "missing $SUITE_BIN (build failed?)"; FAIL_N=$((FAIL_N+1))
fi

# 3. Runner-dir check.
echo "== [3/5] runner-dir check"
# Pinned upstream base (not upstream/main, which moves): bump at the next rebase.
RBASE="${RUNNER_BASE:-c5a9d02573410a2085a4b4b831b0b68ba3515440}"
RDIFF="$(git -C "$WT" diff --stat "$RBASE" HEAD -- ps2xRuntime/src/runner)"
RDIRTY="$(git -C "$WT" status --porcelain -- ps2xRuntime/src/runner)"
if [ -z "$RDIFF" ] && [ -z "$RDIRTY" ]; then
  pass "runner-dir" "empty vs $RBASE, no dirty files"
else
  fail "runner-dir" "committed=[${RDIFF:-empty}] dirty=[${RDIRTY:-empty}]"; FAIL_N=$((FAIL_N+1))
fi

# Key resolution: newest manifest among profile+build matches whose pinned fork commit is an
# ancestor of (or equal to) the tested tip. Unmerged lane-local keys are skipped (09-30: FH9/FH17
# keys minted on unpushed branches were picked by other lanes' gates).
resolve_key() { # $1 = profile -> prints key (and candidates to stderr)
  python3 - "$STORE" "$1" "$WT" <<'EOF'
import json, os, sys, subprocess
store, profile, wt = sys.argv[1:4]
def reachable(sha):
    if not sha:
        return True
    return subprocess.run(['git', '-C', wt, 'merge-base', '--is-ancestor', sha, 'HEAD'],
                          capture_output=True).returncode == 0
build = '%s-native-arm64' % profile
cands = []
if os.path.isdir(store):
    for d in sorted(os.listdir(store)):
        man_p = os.path.join(store, d, 'manifest.json')
        if not os.path.exists(man_p):
            continue
        man = json.load(open(man_p))
        pins = man['pins']
        if pins.get('knobs', 'reference') == profile and pins.get('build') == build:
            if not reachable(pins.get('fork', '')):
                print('skip (fork %s not in tip) %s' % (pins.get('fork', '')[:9], man['key']), file=sys.stderr)
                continue
            cands.append((man.get('created_utc', ''), man['key']))
if not cands:
    print('MISSING profile=%s build=%s' % (profile, build))
    sys.exit(2)
for created, key in cands:
    print('candidate %s %s' % (created, key), file=sys.stderr)
print(sorted(cands)[-1][1])
EOF
}

det_boot() { # $1 = profile, $2 = key -> 0 iff compare IDENTICAL
  PROF=$1; KEY=$2
  get() { python3 -c "import json,sys; print(json.load(open('$STORE/$KEY/manifest.json'))['pins']['$1'])"; }
  BACKEND="$(get backend)"; ROUTE="$(get route)"; SOUND="$(get sound)"
  STOP="$(get stop_tick)"; HEVERY="$(get hash_every)"; COV="$(get coverage_tick)"
  SSAA="$(get ssaa)"; HIRES="$(get hires)"; PIPE="$(get pipeline)"
  VU1LIB_PIN="$(get vu1_lib)"; GE1LIB_PIN="$(get ge1_lib)"
  GE1REN_PIN="$(python3 -c "import json; print(json.load(open('$STORE/$KEY/manifest.json'))['pins'].get('ge1_renderer', 'vk'))")"
  echo "key $KEY pins: backend=$BACKEND route=$ROUTE sound=$SOUND stop=$STOP every=$HEVERY cov=$COV ssaa=$SSAA hires=$HIRES pipe=$PIPE"
  [ -f "$MVU_DYLIB" ] || { echo "missing $MVU_DYLIB" >&2; return 2; }
  HAVE_MVU="$(shasum -a 256 "$MVU_DYLIB" | cut -d' ' -f1)"
  [ "$HAVE_MVU" = "$VU1LIB_PIN" ] || { echo "microVU dylib $HAVE_MVU != pins $VU1LIB_PIN" >&2; return 2; }
  case $BACKEND in
    external)
      [ -f "$GE1_DYLIB" ] || { echo "missing $GE1_DYLIB" >&2; return 2; }
      HAVE_GE1="$(shasum -a 256 "$GE1_DYLIB" | cut -d' ' -f1)"
      [ "$HAVE_GE1" = "$GE1LIB_PIN" ] || { echo "GE1 dylib $HAVE_GE1 != pins $GE1LIB_PIN" >&2; return 2; };;
    cpu) ;;
    *) echo "key $KEY backend=$BACKEND: paraLLEl-GS is retired (CN2b); re-mint on external" >&2; return 2;;
  esac
  [ "$SSAA" = "1" ] && [ "$HIRES" = "0" ] && [ "$PIPE" = "0" ] || { echo "ssaa/hires/pipeline are paraLLEl pins (want 1/0/0)" >&2; return 2; }
  OUTD="$GATE/det-$PROF"
  [ ! -e "$OUTD" ] || { echo "existing output: $OUTD" >&2; return 2; }
  ENVS=(--env "PS2X_MICROVU_LIB=$MVU_DYLIB")
  [ "$PROF" = "reference" ] && ENVS+=(--env "PS2X_VU1_ENGINE=microvu" --env "PS2X_MTVU=1")
  WALL=()
  [ "$BACKEND" = "cpu" ] && WALL=(--wall 590) # CN2a: CPU raster races run slower; stay under 600 s
  [ "$BACKEND" = "external" ] && WALL=(--ge1-lib "$GE1_DYLIB" --env "GE1_RENDERER=$GE1REN_PIN")
  python3 "$BOOT" --mode det --backend "$BACKEND" --runner "$RUNNER" ${WALL[@]+"${WALL[@]}"} \
    --label "mac-gate-$SHA7-$PROF" --out "$OUTD" --stop-tick "$STOP" --sound "$SOUND" \
    --route "$ROUTE" --profile "$PROF" --coverage-tick "$COV" --hash-every "$HEVERY" \
    "${ENVS[@]}" > "$GATE/boot-$PROF.log" 2>&1
  BRC=$?
  [ "$BRC" = "0" ] || { echo "boot $PROF failed (rc=$BRC); see $GATE/boot-$PROF.log" >&2; return 1; }
  python3 "$BASELINE" --store "$STORE" compare --key "$KEY" --cand "$OUTD" 2>&1 | tee "$GATE/compare-$PROF.log" | tail -3
  tail -1 "$GATE/compare-$PROF.log" | grep -q ": IDENTICAL$" || return 1
  return 0
}

# 4+5. Det boots (one p-lane slot for the session).
echo "== [4/5] det boots (profiles: $PROFILES)"
SLOT=""
if [ ! -x "$RUNNER" ]; then
  fail "det" "missing $RUNNER (build failed?)"; FAIL_N=$((FAIL_N+1))
else
  SLOT="$(python3 "$PLEASE" claim "mac-gate-$SHA7")" || SLOT=""
  [ -n "$SLOT" ] || { echo "no p-lane slot free" >&2; exit 2; }
  echo "slot: $SLOT"
  trap 'python3 "$PLEASE" release "$SLOT" >/dev/null 2>&1; echo "released slot $SLOT"' EXIT
  # Step 4 (knob-off det vs the Mac play key) always runs, even when
  # --profiles omits play; duplicates are dropped.
  case ",$(printf %s "$PROFILES" | tr -d ' ')," in *,play,*) ;; *) PROFILES="play,$PROFILES";; esac
  OLD_IFS="$IFS"; IFS=","
  # shellcheck disable=SC2086
  set -- $PROFILES
  IFS="$OLD_IFS"
  for PROF in "$@"; do
    PROF="$(printf %s "$PROF" | tr -d ' ')"
    case $PROF in
      reference|play|full120|full120events|play120) ;;
      *) echo "bad profile: $PROF (want reference|play|full120|full120events|play120)" >&2; exit 2;;
    esac
    KEY="$(resolve_key "$PROF" 2>"$GATE/key-$PROF.txt")" || { cat "$GATE/key-$PROF.txt" >&2; fail "key-$PROF" "key resolution failed"; FAIL_N=$((FAIL_N+1)); continue; }
    cat "$GATE/key-$PROF.txt"
    if det_boot "$PROF" "$KEY"; then
      pass "key-$PROF" "$KEY IDENTICAL"
    else
      fail "key-$PROF" "$KEY DIFFER or boot failed (see $GATE/compare-$PROF.log $GATE/boot-$PROF.log)"; FAIL_N=$((FAIL_N+1))
    fi
  done
fi

echo ""
cat "$RESULTS"
if [ "$FAIL_N" = "0" ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAIL_N)"; exit 1; fi
