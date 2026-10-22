#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Public determinism gate (Mac host): four det keys against your own disc.
#
# Usage: mac_gate.sh --work DIR --iso FILE [--elf FILE] [--profiles reference,play,full120events,play120] [--engine DIR] [--jobs N] [--suffix S]
#   --work      external private work dir (prepare/mac-libs/mac outputs live here)
#   --iso       own supported USA disc image (prepare step only)
#   --elf       boot ELF (default: DIR/SLUS_207.72 from a previous prepare)
#   --engine    engine checkout (default: this repo's deps/PS2Recomp submodule)
#   --suffix    appended to the gate dir name (parallel gates; default empty)
#
# Steps (one PASS/FAIL table at the end; exit 0 iff all PASS):
#   0. Knob checks (matrix signed-off set + registry coverage).
#   1. Build: prepare (disc -> codegen) unless inputs.json exists,
#      mac-libs (GE1/microVU), mac (runner + suite).
#   2. Suite (ps2x_tests) from the engine root.
#   3. Runner-dir check vs RUNNER_BASE (must stay empty).
#   4. One det boot per profile + public-key compare (IDENTICAL).
#
# Backend libs are the just-built dylibs. Their SHAs are recorded in the
# receipt; a mismatch against the key's lib pins warns (build-environment
# bytes vary by host) but does not fail: the det hash is
# renderer-independent with readback off, so only guest hashes, snd rows
# and coverage can fail the gate.
set -u

WORK=""; ISO=""; ELF=""; PROFILES="reference,play,full120events,play120"; ENGINE=""; JOBS=4; SUFFIX=""
while [ $# -gt 0 ]; do
  case $1 in
    --work) WORK=$2; shift;;
    --iso) ISO=$2; shift;;
    --elf) ELF=$2; shift;;
    --profiles) PROFILES=$2; shift;;
    --engine) ENGINE=$2; shift;;
    --jobs) JOBS=$2; shift;;
    --suffix) SUFFIX=$2; shift;;
    -*) echo "usage: mac_gate.sh --work DIR --iso FILE [--elf FILE] [--profiles ...] [--engine DIR] [--jobs N] [--suffix S]" >&2; exit 2;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
  shift
done
[ -n "$WORK" ] || { echo "--work is required" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENGINE="${ENGINE:-$ROOT/deps/PS2Recomp}"
BUILD="$ROOT/tools/ssx3/build.py"
BOOT="$ROOT/tests/det/boot/ssx3_boot.py"
COMPARE="$ROOT/tests/det/compare.py"
KEYS="$ROOT/tests/det/keys"
MATRIX="$ROOT/tests/det/knob_matrix.py"
REGISTRY="$ROOT/tests/det/knob_registry.py"
for f in "$BUILD" "$BOOT" "$COMPARE" "$MATRIX" "$REGISTRY"; do
  [ -f "$f" ] || { echo "missing $f" >&2; exit 2; }
done

ENGINE_SHA="$(git -C "$ENGINE" rev-parse HEAD)" || { echo "engine is not a git checkout: $ENGINE" >&2; exit 2; }
SHA7="$(printf %s "$ENGINE_SHA" | cut -c1-7)"
LOCK_SHA="$(python3 -c "import json; print(json.load(open('$ROOT/deps.lock.json'))['engine']['pin'])")"
[ "$ENGINE_SHA" = "$LOCK_SHA" ] || { echo "engine $ENGINE_SHA != deps.lock.json $LOCK_SHA" >&2; exit 2; }
[ -z "$(git -C "$ENGINE" status --porcelain)" ] || { echo "engine checkout is dirty" >&2; exit 2; }
mkdir -p "$WORK"
GATE="$WORK/det-gate-$SHA7$SUFFIX"
mkdir -p "$GATE"
echo "== mac_gate engine $ENGINE_SHA work $WORK"
echo "   gate dir: $GATE"

RESULTS="$GATE/table.md"
{
  echo "# mac_gate $SHA7 ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
  echo ""
  echo "engine \`$ENGINE_SHA\` work \`$WORK\` profiles=$PROFILES"
  echo ""
  echo "| Step | Result | Detail |"
  echo "| --- | --- | --- |"
} > "$RESULTS"
pass() { printf '| %s | PASS | %s |\n' "$1" "$2" | tee -a "$RESULTS"; }
fail() { printf '| %s | FAIL | %s |\n' "$1" "$2" | tee -a "$RESULTS"; }
FAIL_N=0

echo "== [0/4] knob checks"
if python3 "$MATRIX" --check --rev "$ENGINE_SHA" --engine "$ENGINE" > "$GATE/knob-check.log" 2>&1; then
  pass "knob-check" "signed-off set effective at $SHA7 (log knob-check.log)"
else
  fail "knob-check" "divergence (see $GATE/knob-check.log)"; FAIL_N=$((FAIL_N+1))
fi
if python3 "$REGISTRY" --check --engine "$ENGINE" --rev "$ENGINE_SHA" > "$GATE/knob-registry.log" 2>&1; then
  pass "knob-registry" "runtime reads registered at $SHA7 (log knob-registry.log)"
else
  fail "knob-registry" "missing or invalid registration (see $GATE/knob-registry.log)"; FAIL_N=$((FAIL_N+1))
fi

echo "== [1/4] build (prepare, mac-libs, mac)"
if [ ! -f "$WORK/inputs.json" ]; then
  if [ -z "$ISO" ]; then
    fail "build" "--work has no inputs.json and no --iso given"; echo ""; cat "$RESULTS"; echo "GATE FAIL ($FAIL_N)"; exit 1
  fi
  python3 "$BUILD" prepare --work "$WORK" --iso "$ISO" --engine "$ENGINE" --jobs "$JOBS" > "$GATE/prepare.log" 2>&1 \
    || { fail "build" "prepare failed (see $GATE/prepare.log)"; echo ""; cat "$RESULTS"; echo "GATE FAIL ($FAIL_N)"; exit 1; }
fi
GE1_DYLIB="$WORK/libs-build/ge1-adapter/libge1_gs.dylib"
MVU_DYLIB="$WORK/libs-build/libmv2_microvu.dylib"
if [ ! -f "$GE1_DYLIB" ] || [ ! -f "$MVU_DYLIB" ]; then
  python3 "$BUILD" mac-libs --work "$WORK" --engine "$ENGINE" --jobs "$JOBS" > "$GATE/mac-libs.log" 2>&1 \
    || { fail "build" "mac-libs failed (see $GATE/mac-libs.log)"; echo ""; cat "$RESULTS"; echo "GATE FAIL ($FAIL_N)"; exit 1; }
fi
RUNNER_BUILD="$WORK/runner-build-det"
if [ ! -x "$RUNNER_BUILD/ps2xRuntime/ps2EntryRunner" ]; then
  python3 "$BUILD" mac --work "$WORK" --engine "$ENGINE" --jobs "$JOBS" --suite --det > "$GATE/mac-det.log" 2>&1 \
    || { fail "build" "mac --det failed (see $GATE/mac-det.log)"; echo ""; cat "$RESULTS"; echo "GATE FAIL ($FAIL_N)"; exit 1; }
fi
if [ -x "$RUNNER_BUILD/ps2xRuntime/ps2EntryRunner" ]; then
  pass "build" "prepare + mac-libs + mac --det (logs in $GATE)"
else
  fail "build" "runner missing after build"; FAIL_N=$((FAIL_N+1))
fi
RUNNER="$RUNNER_BUILD/ps2xRuntime/ps2EntryRunner"
SUITE_BIN="$RUNNER_BUILD/ps2xTest/ps2x_tests"

echo "== [2/4] suite"
if [ -x "$SUITE_BIN" ]; then
  (cd "$ENGINE" && "$SUITE_BIN" > "$GATE/suite.log" 2>&1)
  SUITE_RC=$?
  TOTAL="$(sed -n 's/^Total Tests: \([0-9]*\)$/\1/p' "$GATE/suite.log" | tail -1)"
  FAILED="$(sed -n 's/^Failed: \([0-9]*\)$/\1/p' "$GATE/suite.log" | tail -1)"
  if [ "$SUITE_RC" = "0" ] && [ -n "$TOTAL" ] && [ "$FAILED" = "0" ]; then
    pass "suite" "$TOTAL/$TOTAL from the engine root"
  else
    fail "suite" "rc=$SUITE_RC failed=$FAILED (see $GATE/suite.log)"; FAIL_N=$((FAIL_N+1))
  fi
else
  fail "suite" "missing $SUITE_BIN"; FAIL_N=$((FAIL_N+1))
fi

echo "== [3/4] runner-dir check"
RBASE="${RUNNER_BASE:-c5a9d02573410a2085a4b4b831b0b68ba3515440}"
RDIFF="$(git -C "$ENGINE" diff --stat "$RBASE" HEAD -- ps2xRuntime/src/runner)"
RDIRTY="$(git -C "$ENGINE" status --porcelain -- ps2xRuntime/src/runner)"
if [ -z "$RDIFF" ] && [ -z "$RDIRTY" ]; then
  pass "runner-dir" "empty vs $RBASE, no dirty files"
else
  fail "runner-dir" "committed=[${RDIFF:-empty}] dirty=[${RDIRTY:-empty}]"; FAIL_N=$((FAIL_N+1))
fi

resolve_key() { # $1 = profile -> prints key dir name
  python3 - "$KEYS" "$1" "$ENGINE" <<'EOF'
import json, os, sys, subprocess
keys, profile, engine = sys.argv[1:4]
def reachable(sha):
    if not sha:
        return True
    return subprocess.run(['git', '-C', engine, 'merge-base', '--is-ancestor', sha, 'HEAD'],
                          capture_output=True).returncode == 0
cands = []
for d in sorted(os.listdir(keys)):
    man_p = os.path.join(keys, d, 'manifest.json')
    if not os.path.exists(man_p):
        continue
    man = json.load(open(man_p))
    if man.get('profile') != profile:
        continue
    if not reachable(man['pins'].get('fork', '')):
        print('skip (engine %s not in tip) %s' % (man['pins'].get('fork', '')[:9], man['key']), file=sys.stderr)
        continue
    cands.append(d)
if not cands:
    print('MISSING profile=%s' % profile)
    sys.exit(2)
print(sorted(cands)[-1])
EOF
}

echo "== [4/4] det boots (profiles: $PROFILES)"
ELF="${ELF:-$WORK/SLUS_207.72}"
[ -f "$ELF" ] || { fail "det" "missing ELF $ELF (run prepare first)"; FAIL_N=$((FAIL_N+1)); }
if [ -z "$ISO" ]; then
  ISO="$(python3 -c "import json; print(json.load(open('$WORK/inputs.json'))['iso'])")"
fi
[ -f "$ISO" ] || { fail "det" "missing ISO $ISO"; FAIL_N=$((FAIL_N+1)); }
OLD_IFS="$IFS"; IFS=","
# shellcheck disable=SC2086
set -- $PROFILES
IFS="$OLD_IFS"
for PROF in "$@"; do
  PROF="$(printf %s "$PROF" | tr -d ' ')"
  case $PROF in
    reference|play|full120|full120events|play120) ;;
    *) echo "bad profile: $PROF" >&2; exit 2;;
  esac
  KEY="$(resolve_key "$PROF" 2>"$GATE/key-$PROF.txt")" || { cat "$GATE/key-$PROF.txt" >&2; fail "key-$PROF" "key resolution failed"; FAIL_N=$((FAIL_N+1)); continue; }
  get() { python3 -c "import json; print(json.load(open('$KEYS/$KEY/manifest.json'))['pins']['$1'])"; }
  BACKEND="$(get backend)"; ROUTE="$(get route)"; SOUND="$(get sound)"
  STOP="$(get stop_tick)"; HEVERY="$(get hash_every)"; COV="$(get coverage_tick)"
  REG_SHA="$(get codegen_register)"
  GE1_PIN="$(get ge1_lib)"; MVU_PIN="$(get vu1_lib)"
  HAVE_GE1="$(shasum -a 256 "$GE1_DYLIB" | cut -d' ' -f1)"
  HAVE_MVU="$(shasum -a 256 "$MVU_DYLIB" | cut -d' ' -f1)"
  LIBNOTE="ge1=$HAVE_GE1 microvu=$HAVE_MVU"
  if [ "$HAVE_GE1" != "$GE1_PIN" ] || [ "$HAVE_MVU" != "$MVU_PIN" ]; then
    LIBNOTE="$LIBNOTE (key pins differ: backend bytes vary by host; recorded, not gated)"
  fi
  echo "key $KEY pins: backend=$BACKEND route=$ROUTE sound=$SOUND stop=$STOP every=$HEVERY cov=$COV"
  echo "libs: $LIBNOTE" | tee -a "$GATE/libs-$PROF.txt"
  OUTD="$GATE/det-$PROF"
  ENVS=(--env "PS2X_MICROVU_LIB=$MVU_DYLIB" --env "GE1_GS_RESOURCES_DIR=$WORK/resources")
  if [ "$PROF" = "reference" ]; then
    ENVS+=(--env "PS2X_VU1_ENGINE=microvu" --env "PS2X_MTVU=1")
  fi
  if python3 "$BOOT" --mode det --backend "$BACKEND" --runner "$RUNNER" \
    --label "mac-gate-$SHA7$SUFFIX-$PROF" --out "$OUTD" --iso "$ISO" --elf "$ELF" \
    --codegen "$WORK/codegen" \
    --ge1-lib "$GE1_DYLIB" --microvu-lib "$MVU_DYLIB" \
    --stop-tick "$STOP" --sound "$SOUND" --route "$ROUTE" --profile "$PROF" \
    --coverage-tick "$COV" --hash-every "$HEVERY" \
    "${ENVS[@]}" > "$GATE/boot-$PROF.log" 2>&1; then
    HAVE_REG="$(python3 -c "import json; print(json.load(open('$OUTD/result.json'))['codegen_register_sha'])")"
    REGNOTE="register=$HAVE_REG"
    if [ "$HAVE_REG" != "$REG_SHA" ]; then
      # Registration-table ownership can move without guest change (the
      # deterministic-generator re-pin); only guest hashes gate. Recorded.
      REGNOTE="$REGNOTE (key pins $REG_SHA; ownership-only drift, not gated)"
    fi
    echo "$REGNOTE" | tee -a "$GATE/libs-$PROF.txt"
    if python3 "$COMPARE" --key "$KEYS/$KEY" --cand "$OUTD" 2>&1 | tee "$GATE/compare-$PROF.log" | tail -1 | grep -q ": IDENTICAL$"; then
      pass "key-$PROF" "$KEY IDENTICAL ($LIBNOTE; $REGNOTE)"
    else
      fail "key-$PROF" "$KEY DIFFER (see $GATE/compare-$PROF.log)"; FAIL_N=$((FAIL_N+1))
    fi
  else
    fail "key-$PROF" "boot failed (see $GATE/boot-$PROF.log)"; FAIL_N=$((FAIL_N+1))
  fi
done

echo ""
cat "$RESULTS"
if [ "$FAIL_N" = "0" ]; then echo "GATE PASS"; exit 0; else echo "GATE FAIL ($FAIL_N)"; exit 1; fi
