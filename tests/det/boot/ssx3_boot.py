#!/usr/bin/env python3
"""Shared bounded boot, promoted verbatim from F5's driver (RS2; F5's copy left
in place). RS2 deltas: --out DIR makes the run dir configurable (default keeps
the F5 run dir), the lease claim is labelled boot-<label>, and the pre-held
slot env is SSX3_HELD_SLOT (F5_HELD_SLOT still honoured). Otherwise identical.

F5 bounded boot (from RP1's driver): canonical codegen pins, --route i26|fr1r1,
--dump-ticks a,b,c (PS2X_FRAME_DUMP_ONCE_TICKS, exactly 3), --wall.
FR1-R1 to the race, GE1 GS backend (CN2b). Builds use the PROMOTED F4
codegen (~/dev/ssx3-work/codegen-ssx3); register_functions.cpp is identical
to pre-promotion, so the changed file sub_003FE828 is pinned too.
F5 changes: wall cap 1800 s (B5 post-race), --env also applies in speed
mode (B6 pipelined), default route fr1r1. No --dump-every: RP1's
PS2X_FRAME_DUMP_EVERY knob was not folded, so the flag is gone rather
than silently ignored.

Modes (runner built with PS2X_GS_SHADOW_PARALLEL=ON; backend chosen at runtime):
  speed  diagnostics-off runner: PS2X_VSYNC_RATE_LOG=1 ([vsync-rate] every 5 s)
         + PS2X_UNPACED=1 (FP1: speed runs measure unpaced headroom);
         takes the EXCLUSIVE mini lease (all slots). Stops at --stop-tick.
         Samples `ps -M <pid>` in the race window (Q4 host cost).
  det    det-hash runner (PS2X_ENABLE_DET_HASH_TAP=ON): PS2X_DET_HASH_EVERY=1 and
         continuous presentation-frame capture (PS2X_FRAME_DUMP_DIR) with a
         0.5 s snapshotter tagging upload-latest pairs by det-hash tick (Q3).
         One mini slot. No GS stream capture. Stops at --stop-tick + 3 s settle.

--backend external|cpu (CN2b; default external): external = GE1, the extracted
PCSX2 GS (ARMSX2 base, PCSX2 fork ssx3-armsx2 c5ba3a0c4b) on MoltenVK:
PS2X_GS_BACKEND=external + PS2X_GS_EXTERNAL_LIBRARY (pinned dylib GE1_LIB,
SHA-checked, resources beside it) + a per-run GE1_GS_DATA_DIR. cpu = the
software GS (CN2a: det-identical, 6-8x slower). paraLLEl-GS (parallel) is
retired; its keys stay in the store as history.

Both modes: PS2X_DETERMINISTIC=1, PS2X_SKIP_MOVIE=1 (dev-only), vsync pad clock,
empty mc0/mc1 under the run dir. Wall cap 500 s, no-progress cap 120 s, log cap
64 MiB. Only the recorded runner PID is signalled; the lease is released on
every path. A (wall, tick) trace is written every poll for phase-rate analysis.

CF3 profiles (--profile reference|play; default: none, today's behavior):
  reference  marker only (PS2X_PROFILE=reference): the host has no compiled
             defaults, so this records intent in result.json and the [knobs]
             line. The a3efbfe (x86) and aa74ca4 (arm64) keys bind to it.
   play       the guest-affecting play knobs (PS2X_PROFILE=play): microVU +
              MTVU(+LAG) + stock 60 (SPR1: split120 retired; was split120) +
              readback off + pcsx2 FINISH (CF4; was lagV before). Mini/arm64 only:
             x86 keeps reference + explicit env (no microVU there), so
             --host bradflix --profile play is refused loudly. The caller
             supplies PS2X_MICROVU_LIB (no absolute paths in code); explicit
             --env wins over the profile. baseline.py make passes --profile
             from the pins and --microvu-lib from its own flag.
  full120    FH1 full-120 branch (fork branch full120 only): the play knobs with
             split120 off (PS2X_SSX3_SIM_MODE=stock), PS2X_SSX3_FULL120=1,
             the branch's fix set (PS2X_SSX3_FULL120_FIX=all) and sun-glare
             readback off (PS2X_VIF1_REVERSE_DMA=0). Guest VBlanks run
             at 2x59.94 Hz, so stop/dump/hash ticks count 120 Hz ticks
             (I26 race HUD ~3428). Mini only, like play.
  full120events  FH5: as full120 with PS2X_SSX3_FULL120=events (stock 60
             in boot/menus/loading/pause/results, full120 from race entry
             to exit), so ticks count 60 Hz outside races, 120 Hz inside.

SS1 save states (det mode; runner built from fork ss1-savestate or later):
  --save-at T --save-path F [--exit-after-save]  write one state at the first
      saveable dispatcher point with vsync tick >= T (a clean exit after the
      save counts as target); --load F restores it after init; --strict also
      refuses a runner-SHA mismatch (default: one warning line). A loaded run's
      det-hash lines start at T+1. States hold game RAM: scratch only.

Usage: ssx3_boot.py --mode speed|det [--backend external|cpu] --runner PATH --label NAME [--out DIR] [--unpaced] [--vu1-stats] [--stack-kb N]
        (--host bradflix retired 09-30: x86 is retired as a target; the flag
        remains only to fail loudly. History below.)

Host split (HS1, retired 09-30): --host bradflix ran one det boot on bradflix (Docker,
GPU + Xvfb when backend=parallel) via the synced remote driver
bradflix_det_boot.py, then pulls the run dir back so baseline.py compare
works unchanged. --runner is a bradflix path; default --out is
~/dev/ssx3-work/from-bradflix/<label>. Speed mode stays on the mini.
SS2: --save-at/--save-path/--exit-after-save/--load/--strict pass through:
--save-path NAME saves run/<label>/NAME in the container and pulls it back
to the mini lane dir; --load FILE syncs a mini state to bradflix first.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, '/Users/brad/dev/ssx3/local/tooling')
from p_lane_lease import LEASE_WAIT_S, claim, claim_wait, release, runner_pids  # noqa: E402
import signal  # noqa: E402

BRADFLIX_HOST = "bradflix"
BRADFLIX_ROOT = "dev/ssx3-work/HS1"
BRADFLIX_DRIVER = "bradflix_det_boot.py"
BRADFLIX_FROM = Path.home() / "dev" / "ssx3-work" / "from-bradflix"

# CF3 host play profile: the guest-affecting play knobs (brief item 2).
# The S1 exact-perf set (VU1_BLOCKS/FLAG_ELIDE/DIET) stays device-only:
# exact by design, re-proven on-device by the Odin A=B gate. GT4: VU0_RECOMP
# + VU0_DIRECT are on here (the Odin play env has them; without them the Mac
# interprets every VU0 microprogram, ~14 % of GameThread in GT3's profile);
# needs a runner built with PS2X_VU0_RECOMP_DIR (mac_build.sh passes it). PS2X_MICROVU_LIB comes from the caller (baseline.py --microvu-lib).
# CF4: sun-glare readback off (Brad 09-28 "let's turn readback off"): the
# play default is the literal "0", matching the Odin play env and the
# Android compiled default.
PLAY_ENV = {
    'PS2X_PROFILE': 'play',
    'PS2X_VU1_ENGINE': 'microvu',
    'PS2X_MTVU': '1',
    'PS2X_MTVU_LAG': '1',
    'PS2X_SSX3_SIM_MODE': 'stock',
    'PS2X_VIF1_REVERSE_DMA': '0',
    'PS2X_GS_FINISH_TIMING': 'pcsx2',
    'PS2X_VU0_RECOMP': '1',
    'PS2X_VU0_DIRECT': '1',
    # CU3 (Odin default set, Android compiled defaults): the VU1 GIF/VIF stages.
    # Transport only: both Mac keys stay IDENTICAL with them on (no re-mint).
    'PS2X_MTVU_GIF_STAGE': '1',
    'PS2X_MTVU_VIF_STAGE': '1',
    # CFG1 Part 2 (D1/D4/D5): the microVU flag hack (Brad 09-30 default) +
    # the MQ2+MQ3 probe-drain pair (Brad 10-02, always together) + the
    # movie skip play default. All det IDENTICAL (no re-mint); mirrors the
    # compiled kDefaults / Mac table in the fork.
    'PS2X_MICROVU_FLAG_HACK': '1',
    'PS2X_MTVU_FINISH_EE': '1',
    'PS2X_MTVU_VIF1_STAT_FREE': '1',
    'PS2X_SKIP_MOVIE': '1',
    # CFG1 Part 2 (D8): anisotropic 16x everywhere (Brad 10-02). Output-only;
    # Mac play det IDENTICAL (no re-mint).
    'GE1_ANISO': '16',
    # KEY3 (SRV3 gate 10-14): desktop joins the mobile play envs (TK39,
    # signed-off play behaviour on both devices): heap ceiling 0x01F31000.
    # FULL120_EVENTS_ENV/PLAY120_ENV inherit via dict(); reference keeps the
    # exact path (no PLAY_ENV). Guest-affecting: the three play-family keys
    # re-mint; snd/coverage stay IDENTICAL (SRV3 §5).
    'PS2X_HLE_POOLS_LOW': '1',
}

# FH1: the full-120 branch profile (PS2X_PROFILE stays play: the runtime has
# no full120 compiled defaults; the extra keys select the branch's mode).
FULL120_ENV = dict(PLAY_ENV, PS2X_SSX3_SIM_MODE='stock', PS2X_SSX3_FULL120='1',
                   PS2X_SSX3_FULL120_FIX='all', PS2X_VIF1_REVERSE_DMA='0')
FULL120_EVENTS_ENV = dict(FULL120_ENV, PS2X_SSX3_FULL120='events')
PLAY120_ENV = dict(FULL120_EVENTS_ENV,
                   PS2X_SSX3_FULL120_FIX='all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk',
                   PS2X_SSX3_FULL120_FASTHOOKS='1', PS2X_VIF1_REVERSE_DMA='lagF')


def main_bradflix(args):
    raise SystemExit("--host bradflix retired: x86 retired 09-30 as a target; "
                     "bradflix is now the APK/lib build host only "
                     "(bradflix_apk.sh, bradflix_ge1_lib.sh)")
    if args.mode != "det":
        raise SystemExit("--host bradflix supports det only (speed stays on mini/Odin)")
    if args.profile in ('play', 'full120', 'full120events', 'play120'):
        raise SystemExit("--host bradflix --profile play refused: x86 keeps "
                         "reference + explicit env (no microVU there)")
    if ".." in args.label or "/" in args.label:
        raise SystemExit("bad label")
    lane = Path(args.out) if args.out else BRADFLIX_FROM / args.label
    if lane.exists():
        raise SystemExit("refusing to reuse %s" % lane)

    held = os.environ.get("SSX3_HELD_SLOT_BRADFLIX")
    if held:
        slot = held
        print(json.dumps({"event": "using-held-slot", "host": "bradflix", "slot": slot}), flush=True)
    else:
        slot = claim("boot-" + args.label, host="bradflix")
        while slot is None:
            print("bradflix lease busy; retrying in 30 s", flush=True)
            time.sleep(30)
            slot = claim("boot-" + args.label, host="bradflix")

    here = Path(__file__).resolve().parent
    rc = 1
    try:
        r = subprocess.run(["scp", str(here / BRADFLIX_DRIVER),
                            f"{BRADFLIX_HOST}:{BRADFLIX_ROOT}/{BRADFLIX_DRIVER}"])
        if r.returncode != 0:
            raise SystemExit("driver sync failed")
        pad = ROUTE_FR1R1 if args.route == "fr1r1" else ROUTE
        # Driver path is relative: ssh lands in $HOME, and a quoted ~/..
        # would not tilde-expand on the remote end.
        remote = ["python3", f"{BRADFLIX_ROOT}/{BRADFLIX_DRIVER}",
                  "--runner", args.runner, "--label", args.label, "--slot", str(slot),
                  "--stop-tick", str(args.stop_tick), "--sound", args.sound,
                  "--coverage-tick", str(args.coverage_tick),
                  "--backend", args.backend, "--route", args.route,
                  "--pad-script", pad, "--hash-every", str(args.hash_every),
                  "--wall", str(args.wall)]
        if args.dump_ticks:
            remote += ["--dump-ticks", args.dump_ticks]
        if args.no_snap:
            remote += ["--no-snap"]
        if args.unpaced:
            remote += ["--unpaced"]
        if args.vu1_stats:
            remote += ["--vu1-stats"]
        if args.stack_kb:
            remote += ["--stack-kb", str(args.stack_kb)]
        if args.save_at:
            if not args.save_path:
                raise SystemExit('--save-at needs --save-path')
            save_name = os.path.basename(args.save_path)
            if save_name in ('', '.', '..'):
                raise SystemExit('bad --save-path %r' % args.save_path)
            remote += ["--save-at", str(args.save_at), "--save-path", save_name]
            if args.exit_after_save:
                remote += ["--exit-after-save"]
        if args.load:
            if not Path(args.load).is_file():
                raise SystemExit('no such state %s' % args.load)
            staging = "load-staging/%s.state" % args.label
            r = subprocess.run(["ssh", BRADFLIX_HOST,
                                "mkdir -p %s/load-staging" % BRADFLIX_ROOT])
            if r.returncode != 0:
                raise SystemExit("staging dir failed")
            r = subprocess.run(["scp", args.load,
                                "%s:%s/%s" % (BRADFLIX_HOST, BRADFLIX_ROOT, staging)])
            if r.returncode != 0:
                raise SystemExit("state sync failed")
            remote += ["--load", staging]
        if args.strict:
            remote += ["--strict"]
        env_extra = list(args.env)
        if args.profile == 'reference':
            env_extra.append('PS2X_PROFILE=reference')
        for kv in env_extra:
            remote += ["--env", kv]
        print("+ ssh %s %s" % (BRADFLIX_HOST, " ".join(shlex.quote(w) for w in remote[:6]) + " ..."),
              flush=True)
        r = subprocess.run(["ssh", BRADFLIX_HOST] + [" ".join(shlex.quote(w) for w in remote)])
        rc = r.returncode
        print("== pull back to %s" % lane, flush=True)
        lane.mkdir(parents=True)
        rdir = f"~/{BRADFLIX_ROOT}/run/{args.label}"
        subprocess.run(["scp", f"{BRADFLIX_HOST}:{rdir}/result.json",
                        f"{BRADFLIX_HOST}:{rdir}/boot.log",
                        f"{BRADFLIX_HOST}:{rdir}/trace.jsonl",
                        f"{BRADFLIX_HOST}:{rdir}/snd.log", str(lane) + "/"],
                       capture_output=True)
        subprocess.run(["scp", "-r", f"{BRADFLIX_HOST}:{rdir}/frames", str(lane) + "/"],
                       capture_output=True)
        if args.save_at:
            subprocess.run(["scp", f"{BRADFLIX_HOST}:{rdir}/{os.path.basename(args.save_path)}",
                            str(lane) + "/"], capture_output=True)
        files = {}
        for root, _ds, fs in os.walk(lane):
            for f in fs:
                p = Path(root) / f
                files[str(p.relative_to(lane))] = sha_of(p)
        (lane / "pull.json").write_text(json.dumps(
            {"host": "bradflix", "slot": slot, "remote_run": rdir,
             "pulled_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "driver_rc": rc, "files": files}, indent=2) + "\n")
        have = (lane / "boot.log").exists() and (lane / "result.json").exists()
        print(json.dumps({"event": "pulled", "lane": str(lane), "driver_rc": rc,
                          "complete": have}), flush=True)
        return 0 if rc == 0 and have else 1
    finally:
        if not held:
            release(slot, host="bradflix")

# PI1: default run dir (was F5/run) and pinned inputs (~/dev/ssx3-work/pinned, INDEX.tsv; pinned.py verify).
RUNS = Path('/Users/brad/dev/ssx3-work/runs')
PINNED = Path('/Users/brad/dev/ssx3-work/pinned')
ELF = PINNED / 'game/ssx3-usa-3c2f8eb1/cd/SLUS_207.72'
ISO = PINNED / 'game/ssx3-usa-3c2f8eb1/SSX 3 (USA).iso'
CODEGEN = Path('/Users/brad/dev/ssx3-work/codegen-ssx3/register_functions.cpp')
CODEGEN_VF0 = Path('/Users/brad/dev/ssx3-work/codegen-ssx3/sub_003FE828_0x3fe828.cpp')
ISO_SHA = '3c2f8eb182c9c6208a6e8172a41e61c98f420abe3f42c845f6829aeb9761ebf5'
ELF_SHA = '1b49d05ca2793922180851b9e1ce9ae2291d61a7863565ac4e71f12e967af7bc'
CODEGEN_SHA = 'ad96edb68ce78994684c736b745d79f02d88367eb4d5bfeb2d7b05d126b0867c'  # SPR1: ABI-v2 re-pin (CG1-codegen-ssx3)
CODEGEN_VF0_SHA = '49f6e097cda9ca2a86a4cf5d3a98271b8a3ce69df3a359f8c35c5792471d0400'
# GE1S: GE1 (the extracted PCSX2 GS, ARMSX2 base, PCSX2 fork ssx3-armsx2 aa1399997e) on the native
# Metal renderer (CN2b ran MoltenVK; its per-draw barriers split Metal render passes, ~2.5x slower in races).
# Pinned copy of the GE1S Mac dylib + its resources (resources.sha256 beside it).
GE1_DIR = PINNED / 'mac/ge1-dylib-37a81b4d'  # PI1 (was mac/ge1-dylib-d12f2572)
GE1_LIB = GE1_DIR / 'libge1_gs.dylib'
GE1_LIB_SHA = '37a81b4d7a200e882bb1f3c5a2fc939f023275a59f35043a50187284596d45cc'
GE1_RENDERER = 'metal'  # GE1S: --env GE1_RENDERER=vk selects MoltenVK (the CN2b path)

# I26-FAST (local/research/I26/ROUTES.md), race HUD at tick ~1714.
ROUTE = ('10611:start:250,12780:cross:250,14749:cross:250,16567:cross:250,18336:cross:250,'
         '19770:cross:250,21205:down:150,21706:down:150,22306:cross:250,24025:cross:250,'
         '28512:cross:200,28763:down:700,29513:cross:200,29764:down:700,30514:cross:200,'
         '30765:down:700,31515:cross:200,31766:down:700,32516:cross:200,32767:down:700,'
         '33517:cross:200,33768:down:700,34518:cross:200,34769:down:700,35519:cross:200,'
         '35770:down:700,36520:cross:200,36771:down:700,37521:cross:200,37772:down:700,'
         '38522:down:30000')
# FR1-R1: I26-FAST minus the final down hold (the rider rides the whole course untouched).
ROUTE_FR1R1 = ROUTE.rsplit(',', 1)[0]

WALL_CAP_S = 500
PROGRESS_CAP_S = 120
LOG_CAP_BYTES = 64 * 1024 * 1024
FRAMES_CAP_BYTES = 1 * 1024 * 1024 * 1024
SNAP_PERIOD_S = 0.5
POLL_S = 0.5
PS_EVERY_S = 10
PS_START_TICK = 1750
PS_MAX_SAMPLES = 12

HASH_TICK = re.compile(rb'\[det-hash:v1\] tick=(\d+)\b')
HASH_ERROR_MARKERS = (b'[det-hash] null', b'[det-hash] line cap', b'[det-hash] invalid')
RATE_TICK = re.compile(rb'\[vsync-rate\] tick=(\d+)\b')
GS_PATH = re.compile(r'^\[gs-path\].*$', re.M)
GS_FATAL = re.compile(r'^\[gs:external\] GE1 .*failed.*$', re.M)


def sha_of(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def dir_bytes(path):
    total = 0
    for root, _ds, fs in os.walk(path):
        for f in fs:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def main():
    global LOG_CAP_BYTES, WALL_CAP_S
    ap = argparse.ArgumentParser()
    ap.add_argument('--mode', choices=('speed', 'det'), required=True)
    ap.add_argument('--backend', choices=('external', 'cpu'), default='external')
    ap.add_argument('--ge1-lib', default=str(GE1_LIB),
                    help='CN2b: GE1 dylib for --backend external (resources = its dir/resources); '
                         'only the pinned default is SHA-checked against GE1_LIB_SHA')
    ap.add_argument('--runner', required=True)
    ap.add_argument('--label', required=True)
    ap.add_argument('--out', default='',
                    help='run dir (default: ~/dev/ssx3-work/runs/LABEL)')
    ap.add_argument('--stop-tick', type=int, default=2400)
    ap.add_argument('--sound', choices=('on', 'off'), default='on')
    ap.add_argument('--coverage-tick', type=int, default=2400)
    ap.add_argument('--unpaced', action='store_true',
                    help='F3: set PS2X_UNPACED=1 in det mode (B2); speed mode always sets it')
    ap.add_argument('--vu1-stats', action='store_true',
                    help='F4: set PS2X_VU1_RECOMP_STATS=1 (VX2: prints the [vu0-recomp] line only)')
    ap.add_argument('--stack-kb', type=int, default=0,
                    help='F4-2b: set PS2X_GAME_THREAD_STACK_KB=N (flatness test)')
    ap.add_argument('--route', choices=('i26', 'fr1r1'), default='fr1r1')
    ap.add_argument('--dump-ticks', default='')
    ap.add_argument('--wall', type=int, default=500)
    ap.add_argument('--log-cap-mb', type=int, default=64,
                    help='boot.log cap in MiB (default 64; per-lane override)')
    ap.add_argument('--hash-every', type=int, default=1)
    ap.add_argument('--no-snap', action='store_true')
    ap.add_argument('--profile', choices=('reference', 'play', 'full120', 'full120events', 'play120'), default='',
                    help='CF3: reference marker or host play knobs (play: mini only)')
    ap.add_argument('--env', action='append', default=[], help='extra K=V (wins over --profile)')
    ap.add_argument('--elf', default=str(ELF),
                    help='TK2: boot ELF (SHA-pinned like the default); its dir is the cd root, '
                         'so a lane-owned cd tree clone can add files (mini only)')
    ap.add_argument('--save-at', type=int, default=0, help='SS1: save a state at this vsync tick')
    ap.add_argument('--save-path', default='', help='SS1: state file for --save-at')
    ap.add_argument('--exit-after-save', action='store_true', help='SS1: stop the runner after the save')
    ap.add_argument('--load', default='', help='SS1: restore this state after init')
    ap.add_argument('--strict', action='store_true', help='SS1: refuse a runner-SHA mismatch on load')
    ap.add_argument('--host', choices=('mini', 'bradflix'), default='mini')
    args = ap.parse_args()
    if args.log_cap_mb <= 0:
        ap.error('--log-cap-mb must be positive')
    LOG_CAP_BYTES = args.log_cap_mb * 1024 * 1024
    if args.host == 'bradflix':
        raise SystemExit("--host bradflix retired: x86 retired 09-30 as a target; "
                         "bradflix is now the APK/lib build host only "
                         "(bradflix_apk.sh, bradflix_ge1_lib.sh)")
        if Path(args.elf).resolve() != ELF:
            raise SystemExit('--elf is mini only')
        return main_bradflix(args)
    WALL_CAP_S = min(args.wall, 1800)

    runner = Path(args.runner).resolve()
    # Absolute: the runner's cwd is the lane, so relative env paths would nest.
    lane = (Path(args.out) if args.out else RUNS / args.label).resolve()
    if lane.exists():
        raise SystemExit('refusing to reuse %s' % lane)
    lane.mkdir(parents=True)
    (lane / 'mc0').mkdir()
    (lane / 'mc1').mkdir()
    frames_dir = lane / 'frames'
    snap_dir = frames_dir / 'snap'
    if args.mode == 'det':
        snap_dir.mkdir(parents=True)
    log = lane / 'boot.log'

    elf = Path(args.elf).resolve()
    pins = {str(ISO): ISO_SHA, str(elf): ELF_SHA, str(CODEGEN): CODEGEN_SHA,
            str(CODEGEN_VF0): CODEGEN_VF0_SHA}
    big = [runner, ISO, elf, CODEGEN, CODEGEN_VF0]
    ge1_lib = Path(args.ge1_lib).resolve()
    if args.backend == 'external':
        big.append(ge1_lib)
        if ge1_lib == GE1_LIB:
            pins[str(GE1_LIB)] = GE1_LIB_SHA
    reads = [{str(p): sha_of(p) for p in big} for _ in range(2)]
    if reads[0] != reads[1]:
        raise SystemExit('two SHA reads differ')
    for path, want in pins.items():
        if reads[0][path] != want:
            raise SystemExit('SHA mismatch for %s' % path)

    env = {k: v for k, v in os.environ.items() if not k.startswith('PS2X_')}
    env.update(PS2X_CD_IMAGE=str(ISO), PS2X_SKIP_MOVIE='1', PS2X_DETERMINISTIC='1',
               PS2X_PAD_SCRIPT_CLOCK='vsync', PS2X_PAD_SCRIPT=(ROUTE_FR1R1 if args.route == 'fr1r1' else ROUTE),
               PS2X_MC_ROOT=str(lane / 'mc0'), PS2X_MISSING_FUNCTION_POLICY='stop',
               COPYFILE_DISABLE='1')
    if args.backend == 'external':
        # GE1S: GE1 on native Metal (the adapter's Mac default is Vulkan, so set it; back thread off).
        (lane / 'ge1-data').mkdir()
        env.update(PS2X_GS_BACKEND='external', PS2X_GS_EXTERNAL_LIBRARY=str(ge1_lib), GE1_RENDERER=GE1_RENDERER,
                   GE1_GS_RESOURCES_DIR=str(ge1_lib.parent / 'resources'),
                   GE1_GS_DATA_DIR=str(lane / 'ge1-data'))
    if args.mode == 'det':
        env.update(PS2X_DET_HASH_EVERY=str(args.hash_every), PS2X_FRAME_DUMP_DIR=str(frames_dir),
                   PS2X_SND_LOG=str(lane / 'snd.log'),
                   PS2X_COVERAGE_TICK=str(args.coverage_tick))
        if args.sound == 'on':
            env['PS2X_SOUND'] = '1'
        if args.unpaced:
            env['PS2X_UNPACED'] = '1'
        if args.vu1_stats:
            env['PS2X_VU1_RECOMP_STATS'] = '1'
        if args.stack_kb:
            env['PS2X_GAME_THREAD_STACK_KB'] = str(args.stack_kb)
        if args.dump_ticks:
            env['PS2X_FRAME_DUMP_ONCE_TICKS'] = args.dump_ticks
        tick_re = HASH_TICK
    else:
        env.update(PS2X_VSYNC_RATE_LOG='1', PS2X_SOUND='1',
                   PS2X_UNPACED='1')  # FP1: speed runs measure unpaced headroom
        tick_re = RATE_TICK
    if args.profile == 'reference':
        env['PS2X_PROFILE'] = 'reference'
    elif args.profile == 'play':
        env.update(PLAY_ENV)
    elif args.profile == 'full120':
        env.update(FULL120_ENV)
    elif args.profile == 'full120events':
        env.update(FULL120_EVENTS_ENV)
    elif args.profile == 'play120':
        env.update(PLAY120_ENV)
    for kv in args.env:
        k, v = kv.split('=', 1)
        env[k] = v
    if (args.save_at or args.load) and args.mode != 'det':
        raise SystemExit('--save-at/--load need --mode det (PS2X_DETERMINISTIC=1)')
    if args.save_at:
        if not args.save_path:
            raise SystemExit('--save-at needs --save-path')
        env['PS2X_SAVESTATE_SAVE_AT'] = str(args.save_at)
        env['PS2X_SAVESTATE_PATH'] = str(Path(args.save_path).resolve())
        if args.exit_after_save:
            env['PS2X_SAVESTATE_EXIT_AFTER_SAVE'] = '1'
    if args.load:
        if not Path(args.load).exists():
            raise SystemExit('no such state %s' % args.load)
        env['PS2X_SAVESTATE_LOAD'] = str(Path(args.load).resolve())
    if args.strict:
        env['PS2X_SAVESTATE_STRICT'] = '1'

    exclusive = args.mode == 'speed'
    held = os.environ.get('SSX3_HELD_SLOT') or os.environ.get('F5_HELD_SLOT')
    if held:
        slot = held  # pre-held by gb8_watch.py across back-to-back boots; no release here
        print(json.dumps({'event': 'using-held-slot', 'slot': slot}), flush=True)
    else:
        # LS1: SIGTERM unwinds through the finally below (release + runner kill).
        signal.signal(signal.SIGTERM, lambda sg, fr: sys.exit(128 + sg))
        slot = claim_wait('boot-' + args.label, exclusive=exclusive)
        if slot is None:
            return 3  # claim_wait printed INFRA-BLOCKED
    # LS1 c: a speed leg never starts beside another ps2EntryRunner (unleased boots, other lanes).
    runner_check = None
    if args.mode == 'speed':
        names = ('ps2EntryRunner', runner.name)
        t_chk, others = time.monotonic(), runner_pids(names)
        while others and time.monotonic() - t_chk < LEASE_WAIT_S:
            print(json.dumps({'event': 'speed-runner-check', 'others': others, 'waiting': True}), flush=True)
            time.sleep(10)
            others = runner_pids(names)
        runner_check = {'others': others, 'waited_s': round(time.monotonic() - t_chk, 1)}
        print(json.dumps({'event': 'speed-runner-check', **runner_check}), flush=True)
        if others:
            print('INFRA-BLOCKED: speed leg %s refused: other ps2EntryRunner pids %s alive > %d s'
                  % (args.label, others, LEASE_WAIT_S), flush=True)
            if not held:
                release(slot)
            return 3

    result = {'label': args.label, 'mode': args.mode, 'backend': args.backend,
              'runner': str(runner), 'sha_reads': reads, 'stop_tick': args.stop_tick,
              'slot': slot, 'exclusive': exclusive, 'runner_check': runner_check, 'sound': args.sound,
              'env': {k: v for k, v in env.items()
                      if k.startswith('PS2X_') or k.startswith('GE1_')},
              'load_start': os.getloadavg()}
    proc = None
    stop_snap = threading.Event()
    shared = {'tick': 0}
    trace_path = lane / 'trace.jsonl'

    def snapshotter(t0):
        latest = frames_dir / 'upload-latest.png'
        latest_txt = frames_dir / 'upload-latest.txt'
        while not stop_snap.wait(SNAP_PERIOD_S):
            el = time.monotonic() - t0
            try:
                if latest.exists():
                    shutil.copyfile(latest, snap_dir / ('snap-%06dt-%07.2fs.png'
                                                        % (shared['tick'], el)))
                if latest_txt.exists():
                    shutil.copyfile(latest_txt, snap_dir / ('snap-%06dt-%07.2fs.txt'
                                                            % (shared['tick'], el)))
            except OSError:
                pass

    try:
        t0 = time.monotonic()
        trace = trace_path.open('w')
        with log.open('wb') as out:
            proc = subprocess.Popen([str(runner), str(elf)], cwd=lane, env=env,
                                    stdout=out, stderr=subprocess.STDOUT)
            result['runner_pid'] = proc.pid
            print(json.dumps({'event': 'boot', 'label': args.label, 'pid': proc.pid,
                              'slot': slot}), flush=True)
            th = None
            if args.mode == 'det' and not args.no_snap and not args.dump_ticks:
                th = threading.Thread(target=snapshotter, args=(t0,), daemon=True)
                th.start()
            last_tick, last_progress, bound = 0, t0, None
            ps_samples, last_ps = 0, 0.0
            try:
                while True:
                    now = time.monotonic()
                    if proc.poll() is not None:
                        bound = 'exit'
                        if args.exit_after_save and b'[savestate] saved' in log.read_bytes()[-65536:]:
                            bound = 'target'
                        break
                    if now - t0 >= WALL_CAP_S:
                        bound = 'wall_cap'
                        break
                    if log.stat().st_size > LOG_CAP_BYTES:
                        bound = 'log_cap'
                        break
                    if args.mode == 'det' and dir_bytes(frames_dir) > FRAMES_CAP_BYTES:
                        bound = 'frames_cap'
                        break
                    tail = log.read_bytes()[-65536:]
                    if args.mode == 'det' and any(m in tail for m in HASH_ERROR_MARKERS):
                        bound = 'hash_error'
                        break
                    m = tick_re.findall(tail)
                    if m and int(m[-1]) > last_tick:
                        last_tick = int(m[-1])
                        last_progress = now
                        shared['tick'] = last_tick
                    trace.write(json.dumps({'wall_s': round(now - t0, 3),
                                            'tick': last_tick}) + '\n')
                    trace.flush()
                    if now - last_progress >= PROGRESS_CAP_S:
                        bound = 'progress_cap'
                        break
                    if (args.mode == 'speed' and last_tick >= PS_START_TICK
                            and ps_samples < PS_MAX_SAMPLES and now - last_ps >= PS_EVERY_S):
                        last_ps = now
                        try:
                            ps = subprocess.run(['ps', '-M', str(proc.pid)],
                                                capture_output=True, timeout=15)
                            (lane / ('ps-race-%02d.txt' % ps_samples)).write_bytes(ps.stdout)
                            ps_samples += 1
                        except (OSError, subprocess.SubprocessError):
                            pass
                    if last_tick >= args.stop_tick:
                        bound = 'target'
                        if args.mode == 'det':
                            time.sleep(3)  # let the snapshotter grab final frames
                        break
                    time.sleep(POLL_S)
            finally:
                stop_snap.set()
                if th is not None:
                    th.join(timeout=10)
            if proc.poll() is None:
                proc.terminate()
                try:
                    result['runner_rc'] = proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    result['runner_rc'] = proc.wait(timeout=10)
                    bound += '+kill'
            else:
                result['runner_rc'] = proc.returncode
        trace.close()
        text = log.read_text(errors='replace')
        gs_path = GS_PATH.findall(text)
        gs_fatal = GS_FATAL.findall(text)
        hud_wall = None
        prev = None
        for line in trace_path.read_text().splitlines():
            row = json.loads(line)
            if row['tick'] >= 1714:
                if prev is not None and row['tick'] != prev['tick']:
                    f = (1714 - prev['tick']) / (row['tick'] - prev['tick'])
                    hud_wall = round(prev['wall_s'] + f * (row['wall_s'] - prev['wall_s']), 2)
                else:
                    hud_wall = row['wall_s']
                break
            prev = row
        result.update(bound=bound, elapsed_s=round(time.monotonic() - t0, 3),
                      last_tick=last_tick, log_bytes=log.stat().st_size,
                      hud_wall_s=hud_wall, ps_samples=ps_samples,
                      gs_path_line=gs_path[0] if gs_path else None,
                      gs_fatal=gs_fatal[0] if gs_fatal else None,
                      load_end=os.getloadavg())
        (lane / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps({k: v for k, v in result.items()
                          if k not in ('env', 'sha_reads')}), flush=True)
        return 0 if bound == 'target' else 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.kill()
            proc.wait()
        if not held:
            release(slot)


if __name__ == '__main__':
    sys.exit(main())
