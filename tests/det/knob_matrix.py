#!/usr/bin/env python3
"""Knob matrix: one row per knob across every platform source.

Reads (no builds/boots):
  - engine <REV> ps2xRuntime/include/ps2_knobs.h (kDefaults + curated dump list)
  - engine <REV> ps2xRuntime/ios/full120.env (iOS 120 layer)
  - <env-dir>/ps2x.env (Android 60 base), <env-dir>/full120.env (Android 120 layer)
    [--env-dir overrides it; default: this repo's presets/]
  - presets/ios.env (iOS bundled base)
  - tests/det/boot/ssx3_boot.py PLAY_ENV / FULL120_EVENTS_ENV (Mac play/full120)

Usage:
  knob_matrix.py [--rev <sha>] [--engine DIR] [--env-dir DIR] matrix   # markdown table on stdout
  knob_matrix.py --check [--rev <sha>] [--env-dir DIR]                 # CI gate: FAIL on signed-off divergence
  knob_matrix.py --tsv                                         # tab-separated matrix on stdout

--rev defaults to the engine checkout's HEAD; mac_gate.sh always passes
--rev explicitly.
--check encodes the signed-off play set as
"must be effective" per platform profile and exits 1 with FAIL lines when a
setting is missing/diverged. Open questions are WARN lines (exit 0).
"""
import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # tests/det -> repo root
ENGINE_DEFAULT = ROOT / "deps" / "PS2Recomp"
PRESETS_DEFAULT = ROOT / "presets"
# The retired iPhone-Docs layer is always empty.


def engine_show(engine, rev, path):
    r = subprocess.run(["git", "-C", str(engine), "show", f"{rev}:{path}"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot read engine {rev}:{path}: {r.stderr.strip()}")
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


# Code defaults when a knob is unset.
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
    "GE1_VK_TURNIP": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "GE1_CAS": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
    "GE1_UPSCALE": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "PS2X_GE1_EXPORT_SIZE": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
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
    "GE1_VK_TURNIP": "Android-default-set: Turnip 1 (Metal platforms N/A)",
    "PS2X_MTVU_GIF_STAGE": "Android-default-set/IB5/CU3: GIF+VIF stages 1",
    "PS2X_MTVU_VIF_STAGE": "Android-default-set/IB5/CU3: GIF+VIF stages 1",
    "GE1_DRAW_BUFFERING": "Android-default-set/IX1: draw buffering 1",
    "PS2X_SSX3_FULL120_FASTHOOKS": "GT3/120-layer: FASTHOOKS 1 (120 only)",
    "GE1_UPSCALE": "2x Android / 2.5 iPad / 2 iPhone (device-specific)",
    "PS2X_GE1_EXPORT_SIZE": "display-sized export (IX1/Android-default-set)",
    "PS2X_AUDIO_RESAMPLE": "sinc play default (Android env + iOS bundle)",
    "PS2X_AUDIO_DC_BLOCK": "AU18/AU19 : DC block on everywhere",
    "PS2X_SPU_INTERP": "gauss FINAL (default; no key)",
    "GE1_ANISO": "16x every platform (compiled Android/iOS/Mac + Android env + Mac PLAY_ENV)",
    "PS2X_MICROVU_FLAG_HACK": "flag-hack default (compiled Android/iOS/Mac + Android env)",
    "PS2X_VIF1_REVERSE_DMA": "CF4 readback OFF=0; LT1b lagF on Android from PB21 ; iOS lagF from IOSL1 (static bind wired)",
    "PS2X_MTVU_FINISH_EE": "MQ2+MQ3 probe-drain pair, always together",
    "PS2X_MTVU_VIF1_STAT_FREE": "MQ2+MQ3 probe-drain pair, always together",
    "PS2X_VU1_ENGINE": "microVU (Android/Mac) / offline (iOS)",
    "PS2X_MTVU": "MTVU 1 everywhere play",
    "PS2X_MTVU_LAG": "lag 1 (iOS-60 off deliberate, IS1 neutral)",
    "PS2X_GS_FINISH_TIMING": "S3: FINISH=pcsx2 everywhere (MQ4)",
    "PS2X_VU_FLOAT": "VF1/VC1 signed off; exact default, opt-in pcsx2 (stays runtime)",
    "GE1_ADRENO_DSTREAD": "split-on-Adreno via AUTO (explicit unset)",
    "GE1_BACKTHREAD": "pipelined default on Android (code); off Mac/iOS",
    "PS2X_SKIP_MOVIE": "movies off in play envs",
    "PS2X_TURBO_BOOT": "turbo boot (1 on both Android entries; output-only, det IDENTICAL)",
    "PS2X_VSYNC_LOCK": "PX1: 120-layer only (lock default needs sign-off)",
    "PS2X_VSYNC_LOCK_AUDIO": "AU15 tempo, 120-layer only",
    "PS2X_STRETCH_FEED": "AU16 demand, 120-layer only",
    "PS2X_DISPLAY_HZ": "120 request (120-layer only)",
    "PS2X_PRESENT_PER_VSYNC": "per-VSync present (120-layer only)",
    "PS2X_SOUND": "sound 1 in play envs",
    "PS2X_DEINTERLACE": "weave (iOS explicit; code default)",
    "PS2X_SSX3_LOD_SCALE": "LOD knob; look-and-feel decides (unset since PB44: TLS1 scopes the 0.8 feel test to Tricky courses; no CHECKS row: eff() has no tricky profile)",
    "PS2X_SSX3_TRICKY_LOD_SCALE": "TLS1 Tricky-only draw distance (PB44: 0.8 on the Tricky layer; stock courses from the Tricky entry at full draw; no CHECKS row: eff() has no tricky profile)",
    "PS2X_TRICKY_HUD_GPU": "HUD4 Part 2 Tricky HUD composite on GE1's queue via async helper submit (PB51: tricky layer only; GsHud 3.5 -> 0.13 ms/frame; output <= 1 level; no CHECKS row: eff() has no tricky profile)",
    "PS2X_SSX3_ANIM_PICK_GUARD": "HNG2 zero-weight anim-pick guard (default-on bug fix; =0 restores the stock trap for reference)",
    "PS2X_WIDESCREEN": "16:9 anamorphic default (unset; CFG1 D9 verified)",
    "PS2X_SSX3_FULL120": "events (120 opt-in only; never a play default)",
    "GE1_VK_TURNIP": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "GE1_CAS": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
    "GE1_UPSCALE": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "PS2X_GE1_EXPORT_SIZE": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
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
    "PS2X_GAME_THREAD_CPUS": "Android topology pins (compiled big-cpu + env dup)",
    "PS2X_MTVU_CPUS": "Android topology pins (compiled big-cpu + env dup)",
    "PS2X_GS_BACKEND": "external everywhere (compiled+env dup)",
    "PS2X_GS_EXTERNAL_LIBRARY": "libge1_gs.so Android / builtin iOS",
    "PS2X_GS_HANDOFF_DIET": "F9 diet 1 (compiled+env dup)",
    "PS2X_VU0_RECOMP": "F11 VU0 recomp 1 (compiled+env dup)",
    "PS2X_VU0_DIRECT": "F11 VU0 direct 1 (compiled+env dup)",
    "PS2X_PERF_LOG": "opt-in perf log (measurement; stays runtime)",
    "PS2X_PAD_SCRIPT": "user/test input script (stays runtime)",
    "PS2X_PROFILE": "reference=exact path (Mac); play marker",
    "PS2X_PAD_RECORD_DIR": "IPB36 : iOS session recording on (padrec v4 into Documents/padrec)",
    "PS2X_PAD_RECORD_KEEP": "IPB36 : newest 10 kept on iOS (Android keeps the code default 30)",
    "PS2X_NET_SERVER": "IPB37 : interim online build, self-hosted Rust lobby (login typed per device; Quick Play stays off)",
    "PS2X_NET": "IPB37 : arms the DirtySock HLE with NET_SERVER (server key alone leaves nolink in charge; inert off the Online path)",
    "GE1_ONDEVICE_UPSCALE": "ipb38 : on-device upscale ON (no AI art; v6d pack; panel row toggles live)",
    "GE1_TEX_UPLOAD_CRC": "ipb38 : texture-failure diagnostic master switch (ios-magenta-2 upload CRC + staging-reuse log; removed with the lane)",
    "GE1_TEX_UPLOAD_LOG": "ipb38 : diagnostic log dir (Documents; 256 MB cap; removed with the lane)",
    "PS2X_AUTOSAVE": "ASV1: lifecycle autosave + auto-resume on Android and iOS; off during online sessions",
    "PS2X_AUTOSAVE_AUTORESUME_S": "ASV1: silent-resume age limit (600 s) on Android and iOS; older (or forced) slots ask",
}

# --check: knob -> {profile: expected-effective} for signed-off play settings.
# Dropped groups (not preset policy, checked elsewhere): PS2X_NET* (online
# default-off; online lanes own it), GE1_TEX_REPLACE/PRELOAD + GE1_ONDEVICE_UPSCALE
# (pack-gated; base preset is pack-free, stage path covered by
# tools/ssx3/tests/test_stage_fonts.py), GE1_TEX_UPLOAD_* (knob removed),
# TU_AUTOTUNE_ALGO (device-gates-1 H5: preset leaves it unset so the GPU-aware
# backend picks prefer_sysmem only under the Adreno 8xx Turnip).
# Profiles: android60, android120, ios60, ios120, macplay, macref.
# CFG1 Part 2 extends D1 (iOS bundle) and D4 (Mac PLAY_ENV) past the Part-1 set.
CHECKS = {
    "PS2X_MTVU_GIF_STAGE": {"android60": "1", "android120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "PS2X_MTVU_VIF_STAGE": {"android60": "1", "android120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "GE1_DRAW_BUFFERING": {"android60": "1", "android120": "1", "ios60": "1",
                            "ios120": "1"},
    "PS2X_GS_FINISH_TIMING": {"android60": "pcsx2", "android120": "pcsx2",
                               "ios60": "pcsx2", "ios120": "pcsx2",
                               "macplay": "pcsx2"},
    "PS2X_MTVU_LAG": {"android60": "1", "android120": "1", "ios120": "1",
                       "macplay": "1"},
    "PS2X_MICROVU_FLAG_HACK": {"android60": "1", "android120": "1",
                                "ios60": "1", "ios120": "1",
                                "macplay": "1"},
    "GE1_ANISO": {"android60": "16", "android120": "16",
                   "ios60": "16", "ios120": "16", "macplay": "16"},
    "PS2X_AUDIO_RESAMPLE": {"android60": "sinc", "android120": "sinc",
                              "ios60": "sinc", "ios120": "sinc"},
    "PS2X_AUDIO_DC_BLOCK": {"android60": "1", "android120": "1",
                             "ios60": "1", "ios120": "1",
                             "macplay": "1"},
    "PS2X_MTVU_FINISH_EE": {"android60": "1", "android120": "1", "ios60": "1",
                             "ios120": "1", "macplay": "1"},
    "PS2X_MTVU_VIF1_STAT_FREE": {"android60": "1", "android120": "1", "ios60": "1",
                                  "ios120": "1", "macplay": "1"},
    "PS2X_SSX3_FULL120": {"android120": "events", "ios120": "events"},
    "GE1_VK_TURNIP": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "GE1_CAS": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
    "GE1_UPSCALE": {"android60": "auto", "android120": "auto"},  # shipped preset policy: per-device auto
    "PS2X_GE1_EXPORT_SIZE": {"android60": "auto", "android120": "auto", "ios60": "auto", "ios120": "auto"},  # shipped preset policy: per-device auto
    "PS2X_SSX3_FULL120_FIX": {"android120": "all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk", "ios120": "all,jcam,pid,jcam2,ground2,life2,inputchain,trails,query,vnret,shadowpub,groundstep,trailring,sel5,normcap,sel5b,boostfill,stuntchg,flare,asymk"},  # PB62 (FLK2 flare joins android120, iOS order; fh12 bit 15, opt-in). IPB34 (orch 10-12, matrix caught up to the shipped iOS env: +asymk, flare order as shipped; RV32 F8). PB61: TRN2 asymk (bit 61, turns no longer bleed speed) joins android120; IPB32: FLP2 stuntchg (bit 60, flips/spins rotate near stock speed) joins ios120 (same as PB60); PB60: FLP2 stuntchg (bit 60, flips/spins rotate near stock speed) joins android120; PB59: BST1 boostfill (bit 59, held boost lasts as at 60) joins android120; IPB31: BST1 boostfill joins ios120 (same as PB59); IPB29: SEL2 sel5b joins ios120 (same as PB57); PB57: SEL2 sel5b (bit 58, needs sel5) joins the android120 feel tests; PB56: PRM1 promotes pose,c2cap,spawn,envfilt,input2,chase2 into all  + CRV1/SEL1/NRM1 feel tests trailring/sel5/normcap on android120; ios120 untouched (IPB28 owns); PB55/IPB27: JMP7 groundstep gap-jump feel test ; iOS 120 = Android layer + kept flare (IPB26 vnret + shadowpub catch-up); PB51: JDR2 shadowpub feel test (render-only half-step publication; physics bit-identical); PB50: JMP6 vnret feel test replaces springhold (measured no-op from clean saves); IPB25: iOS 120 = Android PB49 layer + kept flare; PB49: JMP5 springhold gap-jump feel test ; PB42: JMP3/JMP4 query gap-jump feel test ; PB40: FH35 trails+chase2 feel test ; -ground dropped (GRD1 removed ground from kFixAll); SCRATCH line removed (lazy code default, EEP3)
    "PS2X_DISPLAY_HZ": {"android120": "120", "ios120": "120"},
    "PS2X_PRESENT_PER_VSYNC": {"android120": "1", "ios120": "1"},
    "PS2X_SSX3_FULL120_FASTHOOKS": {"android120": "1", "ios120": "1"},
    "PS2X_VSYNC_LOCK": {"android120": "1", "ios120": "1"},
    "PS2X_VSYNC_LOCK_AUDIO": {"android120": "tempo", "ios120": "tempo"},
    "PS2X_STRETCH_FEED": {"android120": "demand", "ios120": "demand"},
    # LT1b : lagF is the Android play default; IOSL1 wires
    # the iOS static bind, so iOS expects lagF too. The Mac play det profile
    # stays 0 (keys).
    "PS2X_VIF1_REVERSE_DMA": {"android60": "lagF", "android120": "lagF",
                               "ios60": "lagF", "ios120": "lagF",
                               "macplay": "0"},
    "PS2X_SKIP_MOVIE": {"android60": "1", "android120": "1", "ios60": "1",
                         "macplay": "1"},
    "PS2X_SOUND": {"android60": "1", "ios60": "1"},
    "PS2X_TRICKY_HUD_ASYNC": {"android60": "1", "android120": "1"},  # PB41: GSW1 Tricky HUD async composite (inert off Tricky courses)
    "PS2X_GS_WAKE_LOWWATER": {"android60": "4096", "android120": "4096"},  # PB41: GSW2 GS queue wake low-water
    "PS2X_MTVU_STAGE_PARK2": {"android60": "1", "android120": "1"},  # PB43: SPN1 park MTVU-VIF/MTVU-GIF producer waits (exact; )
    "WAIT_SPIN_MICROSECONDS": {"android60": "5", "android120": "5"},  # PB43: PRF1 GS Back short spin (output-side timing)
    "PS2X_GS_GIF_BATCH": {"android60": "1", "android120": "1"},  # PB45: GSB1 GIF batch command (exact host transport; Elysium +7.7 %)
    "PS2X_GS_GIF_ARENA": {"android60": "1", "android120": "1"},  # PB50: GSB2 zero-copy GIF batches, arena views (exact host transport, needs GIF_BATCH=1; MTVU-GIF -65 % on Elysium; exact-defaults rule)
    "PS2X_SSX3_NATIVE_WORLD": {"android60": "terrain,instances", "android120": "terrain,instances"},  # PB48: NRT1 native static world (terrain + scenery instances; guest-affecting in VU1 scratch only, pixels identical; )
    "PS2X_VIF_UNPACK_FAST": {"android60": "1", "android120": "1"},  # PB48: VUP1 VIF UNPACK fast paths (exact, byte-identical; )
    "GE1_ADRENO_SRC1_REWRITE": {"android60": "1", "android120": "1"},  # PB51: OMS1 sysmem rewrite for class-B ONE_MINUS_SRC1 (output-only, GPU -3.7 pp at 2x; "Oms1 looks good")
    "GE1_MERGE_SKIP": {"android60": "1", "android120": "1"},  # PB52: OUT1 merge skip (exact, Mac 17.9k replay frames diff 0; no-op at native, 0 skips on Elysium)
    "GE1_EXPORT_DIRECT": {"android60": "1", "android120": "1"},  # PB52: OUT1 direct-to-AHB export (exact, removes the copy; Elysium-neutral at native)
    "PS2X_PRESENT_PREROTATE": {"android60": "2", "android120": "2"},  # PB53: OUT1 pre-rotated AHB present, sense 2 CW upright (DEVICE 2/2; kgsl -13...-14 pp)
    "PS2X_PRESENT_AHB_POOL": {"android60": "6", "android120": "6"},  # PB53: OUT2 AHB pool 6 covers the ~35 ms DEVICE hold (gs.busy delta 0.000, latched 121/120)
    "PS2X_INPUT_MARKER": {"android60": "1", "android120": "1", "ios60": "1", "ios120": "1"},  # PB56: LIM1 L3+R3 lost-input marker on Android play (host-only; marks the pad recording, toast shows 'input marked'); IPB28: iOS writes Documents/markers/, no on-screen toast
    "PS2X_TURBO_BOOT": {"android60": "1", "android120": "1", "ios60": "1", "ios120": "1"},  # PB58: BTS1 turbo boot (output-only); IPB31: restore IPB30's ios rows (PB58 commit e779deb35 replaced the row and dropped them)
    "PS2X_SETTINGS_OVERLAY": {"android60": "1", "android120": "1"},  # PB61: SET2 Select+Start settings overlay on Android play (Mode 120/60, Resolution, Perf; orchestrator 10-12 SET2 gate: ships in PB61); iOS rides IPB34
    "PS2X_PAD_RECORD_DIR": {"ios60": "${DOCUMENTS}/padrec", "ios120": "${DOCUMENTS}/padrec"},  # IPB36: iOS session recording (padrec v4)
    "PS2X_PAD_RECORD_KEEP": {"ios60": "10", "ios120": "10"},  # IPB36: newest 10 kept on iOS (pruned at arm time; code default 30)
    "PS2X_AUTOSAVE": {"android60": "1", "android120": "1", "ios60": "1", "ios120": "1"},  # PB65: ASV1 lifecycle autosave on Android play; wired on iOS too
    "PS2X_AUTOSAVE_AUTORESUME_S": {"android60": "600", "android120": "600", "ios60": "600", "ios120": "600"},  # PB65: silent-resume age limit (600 s) on Android and iOS; older (or forced) slots ask
}

# Open questions: reported as WARN, never FAIL.
# (sinc / SKIP_MOVIE@macplay / probe-drain@macplay warns resolved into CHECKS above.)
WARNS = [
    ("PS2X_VU_FLOAT", "VF1/VC1 signed off, exact default; opt-in pcsx2 stays runtime (CFG1 D3 verified)"),
    ("PS2X_WIDESCREEN", "unset default is anamorphic 16:9 (CFG1 D9 verified); no env sets it by design"),
    ("PS2X_SOUND@macplay", "Mac PLAY_ENV lacks SOUND=1 (harness passes --sound; D6 verified)"),
]


def load_all(engine, rev, env_dir=None):
    knobs_text = engine_show(engine, rev, "ps2xRuntime/include/ps2_knobs.h")
    defaults, curated, platform = parse_knobs_header(knobs_text)
    ios120 = parse_env(engine_show(engine, rev, "ps2xRuntime/ios/full120.env"))
    boot_text = (ROOT / "tests" / "det" / "boot" / "ssx3_boot.py").read_text()
    mac_play, mac_full = parse_boot_py(boot_text)
    envs = Path(env_dir) if env_dir else PRESETS_DEFAULT
    # The Tricky env file is retired; its keys merged into ps2x.env.
    # The tricky profile reads empty, never from a file.
    src = {
        "android_compiled": dict(defaults),
        "ios_compiled": platform.get("kIosDefaults", {}),
        "mac_compiled": platform.get("kMacDefaults", {}),
        "android60": parse_env((envs / "ps2x.env").read_text()),
        "android120": parse_env((envs / "full120.env").read_text()),
        "tricky": {},  # retired profile, always empty (column kept for shape)
        "ios_bundle": parse_env((PRESETS_DEFAULT / "ios.env").read_text()),
        "ios120": ios120,
        "iphone_docs": {},  # retired layer, always empty
        "mac_play": mac_play,
        "mac_full": mac_full,
        "curated": curated,
    }
    return src


def eff(src, profile, knob):
    """Effective value under layering; None = unset everywhere in the stack."""
    if profile == "android60":
        stack = [src["android_compiled"], src["android60"]]
    elif profile == "android120":
        stack = [src["android_compiled"], src["android60"], src["android120"]]
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


PROFILES = ["android60", "android120", "ios60", "ios120", "macplay", "macref"]


def all_keys(src):
    keys = set()
    for name in ("android_compiled", "android60", "android120", "tricky",
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
        row["android60"] = src["android60"].get(k)
        row["android120"] = src["android120"].get(k)
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
    cols = ["knob", "compiled_android", "compiled_other", "android60",
            "android120", "tricky", "ios_bundle", "ios120", "iphone_docs",
            "mac_play", "mac_full", "decision"]
    hdr = {"knob": "knob", "compiled_android": "compiled(Android)",
           "compiled_other": "code-default(iOS/Mac)", "android60": "Android60",
           "android120": "Android120-layer", "tricky": "Tricky-layer",
           "ios_bundle": "iOS-bundle", "ios120": "iOS-120-layer",
           "iphone_docs": "iPhone-Docs", "mac_play": "Mac-play",
           "mac_full": "Mac-full120ev", "decision": "signed-off policy"}
    print("| " + " | ".join(hdr[c] for c in cols) + " |")
    print("| " + " | ".join("---" for _ in cols) + " |")
    for r in matrix_rows(src):
        out = [r["knob"]]
        out += [cell(r[c]) for c in cols[1:-1]]
        out += [(r["decision"] or "—").replace("|", "/")]
        print("| " + " | ".join(out) + " |")


def print_tsv(src):
    cols = ["knob", "compiled_android", "compiled_other", "android60",
            "android120", "tricky", "ios_bundle", "ios120", "iphone_docs",
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
                    help="engine rev for kDefaults + iOS 120 layer (default: engine HEAD)")
    ap.add_argument("--engine", default=None,
                    help="engine checkout (default: this repo's deps/PS2Recomp)")
    ap.add_argument("--env-dir", default=None,
                    help="Android env dir (ps2x.env + full120.env); explicit --env-dir scopes "
                         "--check to Android profiles (default: this repo's presets/)")
    ap.add_argument("mode", nargs="?", default="matrix",
                    choices=("matrix", "tsv", "check"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    engine = Path(args.engine) if args.engine else ENGINE_DEFAULT
    r = subprocess.run(["git", "-C", str(engine), "rev-parse", args.rev or "HEAD"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"cannot resolve engine rev: {r.stderr.strip()}")
    rev = r.stdout.strip()
    src = load_all(str(engine), rev, args.env_dir)
    if args.check or args.mode == "check":
        sys.exit(run_check(src, {"android60", "android120"} if args.env_dir else None))
    if args.mode == "tsv":
        print_tsv(src)
    else:
        print_matrix(src)


if __name__ == "__main__":
    main()
