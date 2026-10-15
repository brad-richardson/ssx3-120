#!/usr/bin/env python3
"""CFG1 knob matrix: one row per knob across every platform source.

Canonical tool (promoted in CFG1 Part 2 from local/research/CFG1/tooling/knob_matrix.py,
which stays frozen as Part-1 evidence). Reads (Mac only, no builds/boots):
  - fork <REV> ps2xRuntime/include/ps2_knobs.h (kDefaults + curated dump list)
  - fork <REV> ps2xRuntime/ios/full120.env (iOS 120 layer)
  - <odin>/ps2x.env (Odin 60 base), <odin>/full120.env (Odin 120 layer)
    [--odin overrides <odin>] (PB58: Tricky env file retired, keys merged into ps2x.env)
  - local/tooling/build/ios/ps2x.env (iOS bundled base, working tree)
  - local/tooling/boot/ssx3_boot.py PLAY_ENV / FULL120_EVENTS_ENV (Mac play/full120)

Usage:
  knob_matrix.py [--rev <sha>] [--repo .] [--odin DIR] matrix   # markdown table on stdout
  knob_matrix.py --check [--rev <sha>] [--odin DIR]             # CI gate: FAIL on signed-off divergence
  knob_matrix.py --tsv                                         # tab-separated matrix on stdout

--rev defaults to the fork's ssx3 tip (git -C ~/dev/PS2Recomp rev-parse fork/ssx3);
CI (mac_gate.sh, release_play.sh, ios_build.sh) always passes --rev explicitly.
--check encodes the signed-off play set (AGENTS.md Product direction) as
"must be effective" per platform profile and exits 1 with FAIL lines when a
setting is missing/diverged. Open questions are WARN lines (exit 0).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve()
while not (REPO / "local" / "tooling" / "boot" / "ssx3_boot.py").exists() and REPO != REPO.parent:
    REPO = REPO.parent

FORK = Path(os.path.expanduser("~/dev/PS2Recomp"))
ODIN = Path(os.path.expanduser("~/dev/ssx3-work/odin-play"))
# ART1: the IOSR1 iPhone-Docs file is gone (lane dir); the iphone_docs layer
# is retired and always empty (it already read {} since the file was missing).


def default_rev():
    r = subprocess.run(["git", "-C", str(FORK), "rev-parse", "fork/ssx3"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot resolve fork/ssx3 tip: {r.stderr.strip()}")
    return r.stdout.strip()


def fork_show(rev, path):
    r = subprocess.run(["git", "-C", str(FORK), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot read fork {rev}:{path}: {r.stderr.strip()}")
    return r.stdout


def parse_env(text):
    """Parse a KEY=VALUE env file (last wins, '#' comments, blank lines)."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def parse_knobs_header(text):
    """Extract kDefaults [(key, value)] and the curated dump list from ps2_knobs.h.

    Also extracts the CFG1 per-platform literal tables (kIosDefaults,
    kMacDefaults) so --check sees the compiled iOS/Mac defaults as the base
    layer of those profiles (explicit env wins, reference disables all).
    """
    m = re.search(r"kDefaults\[\] = \{(.*?)\};", text, re.S)
    if not m:
        raise SystemExit("kDefaults table not found in ps2_knobs.h")
    defaults = []
    for key, val in re.findall(r'\{"([^"]+)",\s*"([^"]*)"', m.group(1)):
        defaults.append((key, val))
    m2 = re.search(r"kKnobs\[\] = \{(.*?)\};", text, re.S)
    curated = re.findall(r'"([^"]+)"', m2.group(1)) if m2 else []
    platform = {}
    for name in ("kIosDefaults", "kMacDefaults"):
        pm = re.search(re.escape(name) + r"\[\] = \{(.*?)\};", text, re.S)
        platform[name] = dict(re.findall(r'\{"([^"]+)",\s*"([^"]*)"', pm.group(1))) if pm else {}
    return defaults, curated, platform


def parse_boot_py(text):
    """Extract PLAY_ENV dict and FULL120 override keys from ssx3_boot.py."""
    m = re.search(r"PLAY_ENV = \{(.*?)\}", text, re.S)
    if not m:
        raise SystemExit("PLAY_ENV not found in ssx3_boot.py")
    play = dict(re.findall(r"'([^']+)'\s*:\s*'([^']*)'", m.group(1)))
    # FULL120_ENV = dict(PLAY_ENV, K='v', ...) ; FULL120_EVENTS_ENV adds FULL120='events'
    full = dict(play)
    m2 = re.search(r"FULL120_ENV = dict\(PLAY_ENV,(.*?)\)", text, re.S)
    if m2:
        full.update(re.findall(r"(\w+)\s*=\s*'([^']*)'", m2.group(1)))
    m3 = re.search(r"FULL120_EVENTS_ENV = dict\(FULL120_ENV,(.*?)\)", text, re.S)
    if m3:
        for k, v in re.findall(r"(\w+)\s*=\s*'([^']*)'", m3.group(1)):
            full[k] = v
    return play, full


# Code defaults when a knob is unset (fork file:line per Part-1 REPORT.md).
# 'off' = feature off / exact path. iOS/Mac compiled tables (CFG1 Part 2)
# sit above these: cf2IosDefaults / cf2MacDefaults in ps2_knobs.h.
CODE_DEFAULTS = {
    "PS2X_GS_FINISH_TIMING": "off(exact)",
    "PS2X_VU_FLOAT": "off(exact)",
    "PS2X_VIF1_REVERSE_DMA": "0(off)",
    "PS2X_MTVU": "off",
    "PS2X_MTVU_LAG": "off",
    "PS2X_MTVU_FINISH_EE": "off",
    "PS2X_MTVU_VIF1_STAT_FREE": "off",
    "PS2X_MTVU_GIF_STAGE": "off",
    "PS2X_MTVU_VIF_STAGE": "off",
    "PS2X_VU0_RECOMP": "off",
    "PS2X_VU0_DIRECT": "off",
    "PS2X_VU1_ENGINE": "static(interp)",
    "PS2X_MICROVU_FLAG_HACK": "off",
    "PS2X_GS_HANDOFF_DIET": "off",
    "PS2X_SSX3_FULL120": "off",
    "PS2X_SSX3_FULL120_FIX": "unset",
    "PS2X_SSX3_FULL120_FASTHOOKS": "off",
    "PS2X_DISPLAY_HZ": "60",
    "PS2X_PRESENT_PER_VSYNC": "off",
    "PS2X_VSYNC_LOCK": "off",
    "PS2X_VSYNC_LOCK_AUDIO": "off",
    "PS2X_STRETCH_FEED": "off(callback)",
    "PS2X_GE1_EXPORT_SIZE": "unset(640x480)",
    "PS2X_SKIP_MOVIE": "off",
    "PS2X_SOUND": "off",
    "PS2X_DEINTERLACE": "weave",
    "PS2X_AUDIO_RESAMPLE": "off(snddrv)",
    "PS2X_SPU_INTERP": "gauss(default)",
    "PS2X_SSX3_LOD_SCALE": "1.0",
    "PS2X_SSX3_TRICKY_LOD_SCALE": "1.0",
    "PS2X_SSX3_ANIM_PICK_GUARD": "on",
    "PS2X_TURBO_BOOT": "off",
    "PS2X_WIDESCREEN": "game-default?",
    "PS2X_PROFILE": "unset(exact)",
    "GE1_UPSCALE": "1x",
    "GE1_ANISO": "off",
    "GE1_DRAW_BUFFERING": "off",
    "GE1_BACKTHREAD": "android:pipelined;other:off",
    "GE1_ADRENO_DSTREAD": "auto",
    "GE1_ADRENO_BLEND_MIX": "off",
    "GE1_VK_TURNIP": "off",
    "GE1_VERTEX_KICK": "INERT(knob-gone)",
    "GE1_PIPE_FLUSH_VSYNCS": "unset",
    "GE1_TFX_PREWARM": "unset",
    "PS2X_PERF_LOG": "off",
    "PS2X_OM1_LEAN": "off",
    "PS2X_OM1_SCAN": "off",
    "PS2X_OM1_DENY": "unset",
    "PS2X_PGS_SSAA": "1",
    "PS2X_PGS_HIRES_SCANOUT": "off",
    "PS2X_PGS_PRESENT_PIPELINE": "INERT(external)",
    "PS2X_PRESENT_ZERO_COPY": "off",
    "PGS_HIER_BINNING": "INERT(external)",
    "PGS_SKIP_COMPILATION_TASKS": "unset",
}

# AGENTS.md signed-off decision per knob (short tag + section).
DECISIONS = {
    "GE1_VK_TURNIP": "Odin-default-set: Turnip 1 (Metal platforms N/A)",
    "PS2X_MTVU_GIF_STAGE": "Odin-default-set/IB5/CU3: GIF+VIF stages 1",
    "PS2X_MTVU_VIF_STAGE": "Odin-default-set/IB5/CU3: GIF+VIF stages 1",
    "GE1_DRAW_BUFFERING": "Odin-default-set/IX1: draw buffering 1",
    "PS2X_SSX3_FULL120_FASTHOOKS": "GT3/120-layer: FASTHOOKS 1 (120 only)",
    "GE1_UPSCALE": "Brad 2x Odin / 2.5 iPad / 2 iPhone (device-specific)",
    "PS2X_GE1_EXPORT_SIZE": "display-sized export (IX1/Odin-default-set)",
    "PS2X_AUDIO_RESAMPLE": "AU13: sinc play default (Odin env + iOS bundle)",
    "PS2X_AUDIO_DC_BLOCK": "AU18/AU19 (Brad 10-02): DC block on everywhere",
    "PS2X_SPU_INTERP": "gauss FINAL (default; no key)",
    "GE1_ANISO": "D8: 16x every platform (compiled Android/iOS/Mac + Odin env + Mac PLAY_ENV)",
    "PS2X_MICROVU_FLAG_HACK": "flag-hack default (CFG1: compiled Android/iOS/Mac + Odin env)",
    "PS2X_VIF1_REVERSE_DMA": "CF4 readback OFF=0; LT1b lagF on the Odin from PB21 (Brad 10-02); iOS lagF from IOSL1 (static bind wired)",
    "PS2X_MTVU_FINISH_EE": "MQ2+MQ3 probe-drain pair, always together",
    "PS2X_MTVU_VIF1_STAT_FREE": "MQ2+MQ3 probe-drain pair, always together",
    "PS2X_VU1_ENGINE": "microVU (Odin/Mac) / offline (iOS IB5)",
    "PS2X_MTVU": "MTVU 1 everywhere play",
    "PS2X_MTVU_LAG": "lag 1 (iOS-60 off deliberate, IS1 neutral)",
    "PS2X_GS_FINISH_TIMING": "S3: FINISH=pcsx2 everywhere (MQ4)",
    "PS2X_VU_FLOAT": "VF1/VC1 signed off; exact default, opt-in pcsx2 (stays runtime)",
    "GE1_ADRENO_DSTREAD": "split-on-Adreno via AUTO (explicit unset)",
    "GE1_BACKTHREAD": "pipelined default on Android (code); off Mac/iOS",
    "PS2X_SKIP_MOVIE": "movies off in play envs (Brad 09-27)",
    "PS2X_TURBO_BOOT": "BTS1 turbo boot (PB58: 1 on both Odin entries; output-only, det IDENTICAL)",
    "PS2X_VSYNC_LOCK": "PX1: 120-layer only (lock default needs Brad+AU16)",
    "PS2X_VSYNC_LOCK_AUDIO": "AU15 tempo, 120-layer only",
    "PS2X_STRETCH_FEED": "AU16 demand, 120-layer only",
    "PS2X_DISPLAY_HZ": "120 request (120-layer only)",
    "PS2X_PRESENT_PER_VSYNC": "per-VSync present (120-layer only)",
    "PS2X_SOUND": "sound 1 in play envs",
    "PS2X_DEINTERLACE": "weave (iOS explicit; code default)",
    "PS2X_SSX3_LOD_SCALE": "LOD knob; Brad's look decides (unset since PB44: TLS1 scopes the 0.8 feel test to Tricky courses; no CHECKS row: eff() has no tricky profile)",
    "PS2X_SSX3_TRICKY_LOD_SCALE": "TLS1 Tricky-only draw distance (PB44: 0.8 on the Tricky layer; stock courses from the Tricky entry at full draw; no CHECKS row: eff() has no tricky profile)",
    "PS2X_TRICKY_HUD_GPU": "HUD4 Part 2 Tricky HUD composite on GE1's queue via async helper submit (PB51: tricky layer only; GsHud 3.5 -> 0.13 ms/frame; output <= 1 level; no CHECKS row: eff() has no tricky profile)",
    "PS2X_SSX3_ANIM_PICK_GUARD": "HNG2 zero-weight anim-pick guard (default-on bug fix; =0 restores the stock trap for reference)",
    "PS2X_WIDESCREEN": "16:9 anamorphic default (unset; CFG1 D9 verified)",
    "PS2X_SSX3_FULL120": "events (120 opt-in only; never play default w/o Brad)",
    "PS2X_SSX3_FULL120_FIX": "all (120 opt-in only)",
    "PS2X_SSX3_COURSE_PICKER": "TK lanes only (not play default)",
    "PS2X_SSX3_TRICKY_MENU": "TK lanes only (not play default)",
    "PS2X_CD_OVERLAY": "TK lanes only",
    "PS2X_SSX3_COURSE_MANIFEST": "TK lanes only (path)",
    "PS2X_SSX3_PATCH_CACHE_GUARD": "TK lanes only",
    "PS2X_SSX3_DRAWTABLE_GUARD": "TK15c guard (Tricky layer)",
    "PS2X_SSX3_PATCH_CACHE_GROW": "TK34 Tricky-layer only",
    "PS2X_SSX3_SPATIAL_LIST_GUARD": "TK38 Tricky-layer only",
    "PS2X_OM1_LEAN": "OM1 offline-stage (iOS; unify AX4b)",
    "PS2X_OM1_SCAN": "OM1 offline-stage (iOS; unify AX4b)",
    "PS2X_PGS_SSAA": "iOS SSAA (4 iPad / 1 iPhone; device-specific)",
    "PS2X_PGS_HIRES_SCANOUT": "iOS scanout path (device-specific)",
    "PS2X_PGS_PRESENT_PIPELINE": "INERT under external backend (deleted CFG1)",
    "PS2X_PRESENT_ZERO_COPY": "iOS IOSurface path (device-specific)",
    "PGS_HIER_BINNING": "INERT under external backend (deleted CFG1)",
    "PGS_SKIP_COMPILATION_TASKS": "iOS precompile skip (legacy PGS key)",
    "GE1_VERTEX_KICK": "INERT: knob gone on ARMSX2 base (deleted CFG1)",
    "PS2X_GAME_THREAD_CPUS": "Odin topology pins (compiled big-cpu + env dup)",
    "PS2X_MTVU_CPUS": "Odin topology pins (compiled big-cpu + env dup)",
    "PS2X_GS_BACKEND": "external everywhere (compiled+env dup)",
    "PS2X_GS_EXTERNAL_LIBRARY": "libge1_gs.so Android / builtin iOS",
    "PS2X_GS_HANDOFF_DIET": "F9 diet 1 (compiled+env dup)",
    "PS2X_VU0_RECOMP": "F11 VU0 recomp 1 (compiled+env dup)",
    "PS2X_VU0_DIRECT": "F11 VU0 direct 1 (compiled+env dup)",
    "PS2X_PERF_LOG": "opt-in perf log (measurement; stays runtime)",
    "PS2X_PAD_SCRIPT": "user/test input script (stays runtime)",
    "PS2X_PROFILE": "reference=exact path (Mac); play marker",
    "PS2X_PAD_RECORD_DIR": "IPB36 (Brad 10-13): iOS session recording on (padrec v4 into Documents/padrec)",
    "PS2X_PAD_RECORD_KEEP": "IPB36 (Brad 10-13): newest 10 kept on iOS (Odin keeps the code default 30)",
    "PS2X_NET_SERVER": "IPB37 (Brad 10-14): interim online build, Oracle Rust lobby (login typed per device; Quick Play stays off); ipb38: ssx.bradr.dev hostname on iOS too",
    "PS2X_NET": "IPB37 (Brad 10-14): arms the DirtySock HLE with NET_SERVER (server key alone leaves nolink in charge; inert off the Online path)",
    "GE1_ONDEVICE_UPSCALE": "ipb38 (Brad 10-15): on-device upscale ON (no AI art; v6d pack; panel row toggles live)",
    "GE1_TEX_UPLOAD_CRC": "ipb38 (Brad 10-15): texture-failure diagnostic master switch (ios-magenta-2 upload CRC + staging-reuse log; removed with the lane)",
    "GE1_TEX_UPLOAD_LOG": "ipb38 (Brad 10-15): diagnostic log dir (Documents; 256 MB cap; removed with the lane)",
    "PS2X_AUTOSAVE": "ASV1 (Brad 10-14/10-15): lifecycle autosave + auto-resume on Odin and iOS; off during online sessions",
    "PS2X_AUTOSAVE_AUTORESUME_S": "ASV1: silent-resume age limit (600 s) on Odin and iOS; older (or forced) slots ask",
}

# --check: knob -> {profile: expected-effective} for signed-off play settings.
# Profiles: odin60, odin120, ios60, ios120, macplay, macref.
# CFG1 Part 2 extends D1 (iOS bundle) and D4 (Mac PLAY_ENV) past the Part-1 set.
CHECKS = {
    "GE1_VK_TURNIP": {"odin60": "1", "odin120": "1"},
    "PS2X_MTVU_GIF_STAGE": {"odin60": "1", "odin120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "PS2X_MTVU_VIF_STAGE": {"odin60": "1", "odin120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "GE1_DRAW_BUFFERING": {"odin60": "1", "odin120": "1", "ios60": "1",
                            "ios120": "1"},
    "PS2X_GS_FINISH_TIMING": {"odin60": "pcsx2", "odin120": "pcsx2",
                               "ios60": "pcsx2", "ios120": "pcsx2",
                               "macplay": "pcsx2"},
    "PS2X_MTVU_LAG": {"odin60": "1", "odin120": "1", "ios120": "1",
                       "macplay": "1"},
    "PS2X_MICROVU_FLAG_HACK": {"odin60": "1", "odin120": "1",
                                "ios60": "1", "ios120": "1",
                                "macplay": "1"},
    "GE1_CAS": {"odin60": "0.25", "odin120": "0.25", "ios60": "0.25", "ios120": "0.25"},  # IPB16: Brad 10-06
    "GE1_ANISO": {"odin60": "16", "odin120": "16",
                   "ios60": "16", "ios120": "16", "macplay": "16"},
    "PS2X_AUDIO_RESAMPLE": {"odin60": "sinc", "odin120": "sinc",
                              "ios60": "sinc", "ios120": "sinc"},
    "PS2X_AUDIO_DC_BLOCK": {"odin60": "1", "odin120": "1",
                             "ios60": "1", "ios120": "1",
                             "macplay": "1"},
    "PS2X_MTVU_FINISH_EE": {"odin60": "1", "odin120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "PS2X_MTVU_VIF1_STAT_FREE": {"odin60": "1", "odin120": "1", "ios60": "1",
                                  "ios120": "1", "macplay": "1"},
    "PS2X_SSX3_FULL120": {"odin120": "events", "ios120": "events"},
    "PS2X_SSX3_FULL120_FIX": {"odin120": "all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk", "ios120": "all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk"},  # PB62 (Brad 10-13: FLK2 flare joins odin120, iOS order; fh12 bit 15, opt-in). IPB34 (orch 10-12, matrix caught up to the shipped fork ios env 9a6e02b2: +asymk, flare order as shipped; RV32 F8). PB61: TRN2 asymk (bit 61, turns no longer bleed speed) joins odin120; IPB32: FLP2 stuntchg (bit 60, flips/spins rotate near stock speed) joins ios120 (same as PB60); PB60: FLP2 stuntchg (bit 60, flips/spins rotate near stock speed) joins odin120; PB59: BST1 boostfill (bit 59, held boost lasts as at 60) joins odin120; IPB31: BST1 boostfill joins ios120 (same as PB59); IPB29: SEL2 sel5b joins ios120 (same as PB57); PB57: SEL2 sel5b (bit 58, needs sel5) joins the odin120 feel tests; PB56: PRM1 promotes pose,c2cap,spawn,envfilt,input2,chase2 into all (Brad 10-11) + CRV1/SEL1/NRM1 feel tests trailring/sel5/normcap on odin120; ios120 untouched (IPB28 owns); PB55/IPB27: JMP7 groundstep gap-jump feel test (Brad 10-11); iOS 120 = Odin layer + kept flare (IPB26 vnret + shadowpub catch-up); PB51: JDR2 shadowpub feel test (render-only half-step publication; physics bit-identical); PB50: JMP6 vnret feel test replaces springhold (measured no-op from clean saves); IPB25: iOS 120 = Odin PB49 layer + kept flare; PB49: JMP5 springhold gap-jump feel test (Brad 10-09); PB42: JMP3/JMP4 query gap-jump feel test (Brad 10-08); PB40: FH35 trails+chase2 feel test (Brad 10-07); -ground dropped (GRD1 removed ground from kFixAll); SCRATCH line removed (lazy code default, EEP3)
    "PS2X_DISPLAY_HZ": {"odin120": "120", "ios120": "120"},
    "PS2X_PRESENT_PER_VSYNC": {"odin120": "1", "ios120": "1"},
    "PS2X_SSX3_FULL120_FASTHOOKS": {"odin120": "1", "ios120": "1"},
    "PS2X_VSYNC_LOCK": {"odin120": "1", "ios120": "1"},
    "PS2X_VSYNC_LOCK_AUDIO": {"odin120": "tempo", "ios120": "tempo"},
    "PS2X_STRETCH_FEED": {"odin120": "demand", "ios120": "demand"},
    # LT1b (Brad 10-02): lagF is the Odin play default from PB21; IOSL1 wires
    # the iOS static bind, so iOS expects lagF too. The Mac play det profile
    # stays 0 (keys).
    "PS2X_VIF1_REVERSE_DMA": {"odin60": "lagF", "odin120": "lagF",
                               "ios60": "lagF", "ios120": "lagF",
                               "macplay": "0"},
    "GE1_UPSCALE": {"odin60": "native", "odin120": "native"},  # PB52: Brad 10-10 NAT1 native on every Odin entry (was 2x since PB42b)
    "PS2X_GE1_EXPORT_SIZE": {"odin60": "display", "odin120": "display",
                              "ios60": "display", "ios120": "display"},
    "PS2X_SKIP_MOVIE": {"odin60": "1", "odin120": "1", "ios60": "1",
                         "macplay": "1"},
    "PS2X_SOUND": {"odin60": "1", "ios60": "1"},
    "GE1_TEX_REPLACE": {"odin60": "${APP_PACK}", "odin120": "${APP_PACK}",
                        "ios60": "${APP_PACK}", "ios120": "${APP_PACK}"},
    "GE1_TEX_PRELOAD": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},
    "PS2X_TRICKY_HUD_ASYNC": {"odin60": "1", "odin120": "1"},  # PB41: GSW1 Tricky HUD async composite (inert off Tricky courses)
    "PS2X_GS_WAKE_LOWWATER": {"odin60": "4096", "odin120": "4096"},  # PB41: GSW2 GS queue wake low-water
    "PS2X_MTVU_STAGE_PARK2": {"odin60": "1", "odin120": "1"},  # PB43: SPN1 park MTVU-VIF/MTVU-GIF producer waits (exact; Brad 10-08)
    "TU_AUTOTUNE_ALGO": {"odin60": "prefer_sysmem", "odin120": "prefer_sysmem"},  # PB43: TUR1 Turnip sysmem autotune (output-only, 0 px diff)
    "WAIT_SPIN_MICROSECONDS": {"odin60": "5", "odin120": "5"},  # PB43: PRF1 GS Back short spin (output-side timing)
    "PS2X_GS_GIF_BATCH": {"odin60": "1", "odin120": "1"},  # PB45: GSB1 GIF batch command (exact host transport; Elysium +7.7 %)
    "PS2X_GS_GIF_ARENA": {"odin60": "1", "odin120": "1"},  # PB50: GSB2 zero-copy GIF batches, arena views (exact host transport, needs GIF_BATCH=1; MTVU-GIF -65 % on Elysium; Brad 10-09 exact-defaults rule)
    "PS2X_SSX3_NATIVE_WORLD": {"odin60": "terrain,instances", "odin120": "terrain,instances"},  # PB48: NRT1 native static world (terrain + scenery instances; guest-affecting in VU1 scratch only, pixels identical; Brad 10-09)
    "PS2X_VIF_UNPACK_FAST": {"odin60": "1", "odin120": "1"},  # PB48: VUP1 VIF UNPACK fast paths (exact, byte-identical; Brad 10-09)
    "GE1_ADRENO_SRC1_REWRITE": {"odin60": "1", "odin120": "1"},  # PB51: OMS1 sysmem rewrite for class-B ONE_MINUS_SRC1 (output-only, GPU -3.7 pp at 2x; Brad 10-09 "Oms1 looks good")
    "GE1_MERGE_SKIP": {"odin60": "1", "odin120": "1"},  # PB52: OUT1 merge skip (exact, Mac 17.9k replay frames diff 0; no-op at native, 0 skips on Elysium)
    "GE1_EXPORT_DIRECT": {"odin60": "1", "odin120": "1"},  # PB52: OUT1 direct-to-AHB export (exact, removes the copy; Elysium-neutral at native)
    "PS2X_PRESENT_PREROTATE": {"odin60": "2", "odin120": "2"},  # PB53: OUT1 pre-rotated AHB present, sense 2 CW upright (DEVICE 2/2; kgsl -13...-14 pp)
    "PS2X_PRESENT_AHB_POOL": {"odin60": "6", "odin120": "6"},  # PB53: OUT2 AHB pool 6 covers the ~35 ms DEVICE hold (gs.busy delta 0.000, latched 121/120)
    "PS2X_INPUT_MARKER": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},  # PB56: LIM1 L3+R3 lost-input marker on Odin play (host-only; marks the pad recording, toast shows 'input marked'); IPB28: iOS writes Documents/markers/, no on-screen toast
    "PS2X_TURBO_BOOT": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},  # PB58: BTS1 turbo boot (output-only); IPB31: restore IPB30's ios rows (PB58 commit e779deb35 replaced the row and dropped them)
    "PS2X_SETTINGS_OVERLAY": {"odin60": "1", "odin120": "1"},  # PB61: SET2 Select+Start settings overlay on Odin play (Mode 120/60, Resolution, Perf; orchestrator 10-12 SET2 gate: ships in PB61); iOS rides IPB34
    "PS2X_PAD_RECORD_DIR": {"ios60": "${DOCUMENTS}/padrec", "ios120": "${DOCUMENTS}/padrec"},  # IPB36: Brad 10-13 reaffirmed iOS session recording (padrec v4)
    "PS2X_PAD_RECORD_KEEP": {"ios60": "10", "ios120": "10"},  # IPB36: newest 10 kept on iOS (pruned at arm time; code default 30)
    "PS2X_NET_SERVER": {"odin60": "ssx.bradr.dev", "odin120": "ssx.bradr.dev", "ios60": "ssx.bradr.dev", "ios120": "ssx.bradr.dev"},  # PB65: Odin uses the ssx.bradr.dev hostname (was the raw IP; the IP override still works); ipb38: iOS joins the hostname
    "PS2X_NET": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},  # PB63/IPB37: arms DirtySock HLE (server key alone leaves nolink in charge)
    "GE1_ONDEVICE_UPSCALE": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},  # PB66: Brad 10-15 on-device upscale ON on Odin (no AI art; v6d pack); ipb38: same on iOS
    "GE1_TEX_UPLOAD_CRC": {"ios60": "1", "ios120": "1"},  # ipb38: texture-failure diagnostic master switch (removed with the lane)
    "GE1_TEX_UPLOAD_LOG": {"ios60": "${DOCUMENTS}", "ios120": "${DOCUMENTS}"},  # ipb38: diagnostic log dir (256 MB cap)
    "PS2X_AUTOSAVE": {"odin60": "1", "odin120": "1", "ios60": "1", "ios120": "1"},  # PB65: ASV1 lifecycle autosave on Odin play (Brad 10-14); ios-polish-1 (Brad 10-15): wired on iOS too
    "PS2X_AUTOSAVE_AUTORESUME_S": {"odin60": "600", "odin120": "600", "ios60": "600", "ios120": "600"},  # PB65: silent resume under 10 min, otherwise ask (Brad 10-14); ios-polish-1: same on iOS
}

# Open questions: reported as WARN, never FAIL.
# (CFG1 Part 2 resolved the Part-1 sinc / SKIP_MOVIE@macplay / probe-drain@macplay
# warns into CHECKS above; D2/D8 decided by Brad and folded; D3/D9 verified,
# no action.)
WARNS = [
    ("PS2X_VU_FLOAT", "VF1/VC1 signed off, exact default; opt-in pcsx2 stays runtime (CFG1 D3 verified)"),
    ("PS2X_WIDESCREEN", "unset default is anamorphic 16:9 (CFG1 D9 verified); no env sets it by design"),
    ("PS2X_SOUND@macplay", "Mac PLAY_ENV lacks SOUND=1 (harness passes --sound; D6 verified)"),
]


def load_all(rev, odin_dir=None):
    knobs_text = fork_show(rev, "ps2xRuntime/include/ps2_knobs.h")
    defaults, curated, platform = parse_knobs_header(knobs_text)
    ios120 = parse_env(fork_show(rev, "ps2xRuntime/ios/full120.env"))
    boot_text = (REPO / "local" / "tooling" / "boot" / "ssx3_boot.py").read_text()
    mac_play, mac_full = parse_boot_py(boot_text)
    odin = Path(odin_dir) if odin_dir else ODIN
    # PB58 (TKM1): the Tricky env file is retired; its keys merged into ps2x.env
    # (odin60/odin120). The tricky profile reads empty, never from a file.
    src = {
        "android_compiled": dict(defaults),
        "ios_compiled": platform.get("kIosDefaults", {}),
        "mac_compiled": platform.get("kMacDefaults", {}),
        "odin60": parse_env((odin / "ps2x.env").read_text()),
        "odin120": parse_env((odin / "full120.env").read_text()),
        "tricky": {},  # PB58: retired profile, always empty (column kept for shape)
        "ios_bundle": parse_env(
            (REPO / "local" / "tooling" / "build" / "ios" / "ps2x.env").read_text()),
        "ios120": ios120,
        "iphone_docs": {},  # ART1: IOSR1 layer retired (file gone; was already {})
        "mac_play": mac_play,
        "mac_full": mac_full,
        "curated": curated,
    }
    return src


def eff(src, profile, knob):
    """Effective value under layering; None = unset everywhere in the stack."""
    if profile == "odin60":
        stack = [src["android_compiled"], src["odin60"]]
    elif profile == "odin120":
        stack = [src["android_compiled"], src["odin60"], src["odin120"]]
    elif profile == "ios60":
        stack = [src["ios_compiled"], src["ios_bundle"], src["iphone_docs"]]
    elif profile == "ios120":
        stack = [src["ios_compiled"], src["ios_bundle"], src["iphone_docs"], src["ios120"]]
    elif profile == "macplay":
        stack = [src["mac_compiled"], src["mac_play"]]
    elif profile == "macref":
        stack = [{"PS2X_PROFILE": "reference"}]
    else:
        raise AssertionError(profile)
    val = None
    for layer in stack:
        if knob in layer:
            val = layer[knob]
    return val


def eff_or_code(src, profile, knob):
    v = eff(src, profile, knob)
    if v is not None:
        return v
    return f"[{CODE_DEFAULTS.get(knob, 'unset')}]"


PROFILES = ["odin60", "odin120", "ios60", "ios120", "macplay", "macref"]


def all_keys(src):
    keys = set()
    for name in ("android_compiled", "odin60", "odin120", "tricky",
                 "ios_bundle", "ios120", "iphone_docs", "mac_play"):
        keys.update(src[name].keys())
    # decision-relevant code-default knobs even if unset everywhere
    keys.update(CODE_DEFAULTS.keys())
    keys.update(DECISIONS.keys())
    return sorted(keys)


def matrix_rows(src):
    rows = []
    for k in all_keys(src):
        row = {"knob": k}
        row["compiled_android"] = src["android_compiled"].get(k)
        if row["compiled_android"] is None:
            row["compiled_android"] = f"[{CODE_DEFAULTS.get(k, 'unset')}]"
        row["compiled_other"] = CODE_DEFAULTS.get(k, "unset")
        row["odin60"] = src["odin60"].get(k)
        row["odin120"] = src["odin120"].get(k)
        row["tricky"] = src["tricky"].get(k)
        row["ios_bundle"] = src["ios_bundle"].get(k)
        row["ios120"] = src["ios120"].get(k)
        row["iphone_docs"] = src["iphone_docs"].get(k)
        row["mac_play"] = src["mac_play"].get(k)
        row["mac_full"] = src["mac_full"].get(k)
        row["decision"] = DECISIONS.get(k, "")
        rows.append(row)
    return rows


def cell(v):
    # Empty-string env values are significant (they clear a lower layer,
    # e.g. iPhone Documents PS2X_PAD_SCRIPT= clears the bundled I26 script).
    if v is None or v == "":
        return "(empty)" if v == "" else "—"
    return v.replace("|", "/")


def print_matrix(src):
    cols = ["knob", "compiled_android", "compiled_other", "odin60",
            "odin120", "tricky", "ios_bundle", "ios120", "iphone_docs",
            "mac_play", "mac_full", "decision"]
    hdr = {"knob": "knob", "compiled_android": "compiled(Android)",
           "compiled_other": "code-default(iOS/Mac)", "odin60": "Odin60",
           "odin120": "Odin120-layer", "tricky": "Tricky-layer",
           "ios_bundle": "iOS-bundle", "ios120": "iOS-120-layer",
           "iphone_docs": "iPhone-Docs", "mac_play": "Mac-play",
           "mac_full": "Mac-full120ev", "decision": "AGENTS decision"}
    print("| " + " | ".join(hdr[c] for c in cols) + " |")
    print("| " + " | ".join("---" for _ in cols) + " |")
    for r in matrix_rows(src):
        out = [r["knob"]]
        out += [cell(r[c]) for c in cols[1:-1]]
        out += [(r["decision"] or "—").replace("|", "/")]
        print("| " + " | ".join(out) + " |")


def print_tsv(src):
    cols = ["knob", "compiled_android", "compiled_other", "odin60",
            "odin120", "tricky", "ios_bundle", "ios120", "iphone_docs",
            "mac_play", "mac_full", "decision"]
    print("\t".join(cols))
    for r in matrix_rows(src):
        out = []
        for c in cols:
            v = r[c]
            out.append("∅" if v is None else v)
        print("\t".join(out))


def run_check(src, profiles=None):
    fails, warns = [], []
    for knob, want in CHECKS.items():
        for prof, exp in want.items():
            if profiles is not None and prof not in profiles:
                continue
            got = eff(src, prof, knob)
            if got is None:
                got = CODE_DEFAULTS.get(knob, "unset")
                # bracketed code defaults never satisfy an explicit expectation
                fails.append(f"FAIL {knob}@{prof}: expected={exp} got=unset "
                             f"(code-default {got})")
            elif got != exp:
                fails.append(f"FAIL {knob}@{prof}: expected={exp} got={got}")
    for knob_at, msg in WARNS:
        warns.append(f"WARN {knob_at}: {msg}")
    for w in warns:
        print(w)
    if fails:
        for f in fails:
            print(f)
        print(f"CHECK FAIL: {len(fails)} divergence(s)")
        return 1
    print("CHECK PASS")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rev", default=None,
                    help="fork rev for kDefaults + iOS 120 layer (default: fork/ssx3 tip)")
    ap.add_argument("--odin", default=None,
                    help="Odin env dir; explicit --odin scopes --check to Odin profiles (default dir: ~/dev/ssx3-work/odin-play)")
    ap.add_argument("mode", nargs="?", default="matrix",
                    choices=("matrix", "tsv", "check"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    rev = args.rev or default_rev()
    src = load_all(rev, args.odin)
    if args.check or args.mode == "check":
        sys.exit(run_check(src, {"odin60", "odin120"} if args.odin else None))
    if args.mode == "tsv":
        print_tsv(src)
    else:
        print_matrix(src)


if __name__ == "__main__":
    main()
