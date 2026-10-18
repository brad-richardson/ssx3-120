#!/usr/bin/env python3
"""Public Tricky-layer builder: two discs in, <work>/tricky device layout out.

Contract (tk-build-1; called by tools/ssx3/build.py `tricky` step):
  build_tricky.py --tricky-iso <Tricky.iso> --ssx3-iso <SSX3.iso>
      --work <work>/tricky [--jobs N] [--routes-ref-dir DIR]
      [--vgmstream VGMSTREAM-CLI] [--ffmpeg FFMPEG]

Everything is derived from the user's own two discs plus in-repo recipe
text; nothing game-derived ships. System tools needed: python3,
vgmstream-cli (speech catalog only), ffmpeg (music/song), bsdtar (HUD).
Refuses an existing --work dir. Fails closed: every stage output is
SHA-checked against its recorded golden before use.

Known skips (loud + recorded, not silent): mega world routes without a
verified --routes-ref-dir (pending mega-h/u2/c tail), the 9-member speech
merge (TK43b meter builders lost), the ENGLISH composite (lane-private
tk-audio-2f base). The manifest then carries the built courses only.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
T = Path(__file__).resolve().parent
sys.path.insert(0, str(T / 'shared'))
sys.path.insert(0, str(T))
from inspect_disc import Region, iso_files, big_members  # noqa: E402
from inventory_tricky import decode_member  # noqa: E402
from run_chain import COURSES, TKP5_FINALS  # noqa: E402

# -- course plan ------------------------------------------------------------
# aloha runs before mesa (mesa's s3 rung borrows aloha-b's receipt);
# mega runs last (its routes rung needs the reference input).
BUILD_COURSES = ['pipe', 'untrack', 'snow', 'gari', 'aloha',
                 'mesa', 'merq', 'elys', 'alaska', 'mega']
# World finals, imported from the driver's recorded pins (never
# transcribed): routes finals from the course recipes, s5 finals from the
# TKP5 table (mega excluded: it never reaches s5). Oracle note (orch
# 10-18): MERQ publishes the post-routes final a23d529a (tk-routes-2
# wall-2819 fix, shipped in PB70/IPB40); the TKP5 s5 pin bcbaf32d is
# stale for MERQ and is never gated (merq has an oracle_routes entry).
ROUTES_COURSES = {c: r['oracle_routes'] for c, r in COURSES.items()
                  if r.get('oracle_routes')}
S5_FINALS = {c: TKP5_FINALS[c] for c in TKP5_FINALS if c != 'mega'}
# Device payload goldens (tk-port-2 REPORT section 2 + TKP5 section 1).
# Preview is a recorded 12-char pin (tk-port-1 REPORT section 6).
PREVIEW_PIN = '3332b0af708d'
AUDIO_SHA = {'speech-tricky-big': 'ed417ac3ff2c19c38fadfaadfeba0b5ff695402ab335655952a4e3dcb861a1',
             'music-tricky-big': '975c0ae473214de48e37202ba284c71b54043a6a605a23f93f052ba9372',
             'music2-tricky-big': '0bc17a154244d8cf8b2b1406564fce5ace6dff496d129e277fad017098b08',
             'trickyhud.bin': 'a7ccacd07b9982bbc34e48aab463e4e0e652b284a442ea7ff91068e163b8',
             'tricky-song.wav': '9c5dbf717c1614b71a01671d3b8a846a93ef3c8f05f70efeaa606dca435',
             'tricky-gems.tsv': '3f4cd72b511b6bab83b9e869e4ca4436c8fb0d92bde9072078b4bef66c',
             'tricky-resetvol.tsv': '77acce0393b151d33165829b33a938eebdcee0e3eb800d65b514aa2f7d1ab6c'}
GEMS_345_SHA = 'c8407f435bd4697a0a93c98e5dd64579697e21000936564a5af7eca26b18c13'
HUD_ATLAS_SHA = 'a7ccacd07b9982bbc34e48aab463e4e0e652b284a442ea7ff91068e163b8'
RESETVOL_SHA = '77acce0393b151d33165829b33a938eebdcee0e3eb800d65b514aa2f7d1ab6c'
SPEECH7_SHA = '07dcb666f9e20a1791f16dec7eb57bb27677c9069a0346e3d927b62a2e7c0877'
SPEECH_TARGETS = ['Arcade_Uber', 'Big_Air_Amateur_mac', 'Big_Air_Expert_mac',
                  'Big_Air_Pro_mac', 'Wipeout_Amateur_mac', 'Wipeout_Expert_mac',
                  'Wipeout_Pro_mac', 'Arcade_Power_Ups', 'Arcade_Icons']
# Full BIG paths (data\speech\mc\...), as the standalone lane job used;
# big_member takes the basename for the output name (split on '\').
TRICKY_SPEECH_MEMBERS = ['data\\speech\\mc\\Wipeout.dat',
                         'data\\speech\\mc\\MS_Wipeout.dat',
                         'data\\speech\\mc\\Uber.dat',
                         'data\\speech\\mc\\Big_Air.dat',
                         'data\\speech\\mc\\MS_Big_Air.dat',
                         'data\\speech\\mc\\Tricky.dat',
                         'data\\speech\\mc\\Turbo.dat',
                         'data\\speech\\mc\\Boost_Icon.dat',
                         'data\\speech\\mc\\Bonus.dat']
SSX3_SPEECH_DIR = 'data\\speech\\char\\'
GEM_COURSES = (('GARI', 33), ('SNOW', 56), ('MESA', 80),
               ('MERQ', 148), ('ALOHA', 22), ('ELYS', 135))
GEM_DONOR_STEM = {'MERQ': 'merquer', 'ELYS': 'elysium'}
# itstricky (Tricky JUKEBOX.BIG): stereo 36 kHz, 6,602,400 frames; the
# staged song is its 48 kHz resample gained -1.69 dB to the music bed.
SONG_MEMBER = 'itstricky'
SONG_GAIN_DB = -1.69
# TP menu panes shipped in the staged preset (DATA/UI members of the built
# COURSPIT; mega has none — parked).
TP_PANES = ['TPABC1', 'TPARA1', 'TPBHP1', 'TPBRA2', 'TPCRA3',
            'TPDSS2', 'TPEBC3', 'TPERA5', 'TPESS3']
APP_ID = 'com.ssx3.recomp'

# -- generated text payloads ------------------------------------------------
# m-tricky.txt: course manifest rows for the BUILT courses only (mega is
# omitted until its world builds; the aliases/poke are verbatim from the
# staged preset, device paths baked on PUBLIC_APP_ID).
MANIFEST_ROWS = [('0', 'GARI', 'Garibaldi'), ('1', 'SNOW', 'Snowdream'),
                 ('2', 'MESA', 'Mesablanca'), ('4', 'MERQ', 'Merqury City'),
                 ('6', 'ALASKA', 'Alaska'), ('7', 'UNTRACK', 'Untracked'),
                 ('11', 'PIPE', 'Pipedream'), ('14', 'ALOHA', 'Aloha Ice Jam'),
                 ('16', 'ELYS', 'Elysium Alps')]
# Manifest alias -> run_chain course key (rows for skipped courses are
# dropped so the menu only lists built worlds).
MANIFEST_COURSE = {'GARI': 'gari', 'SNOW': 'snow', 'MESA': 'mesa',
                   'MERQ': 'merq', 'ALASKA': 'alaska',
                   'UNTRACK': 'untrack', 'PIPE': 'pipe',
                   'ALOHA': 'aloha', 'ELYS': 'elys', 'MEGA': 'mega'}
MANIFEST_HEAD = '# build_tricky.py: course rows for the built courses only.\nmode = Tricky\n'
MANIFEST_ALIASES = [
    'alias = /DATA/AUDIO/SPEECH.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/speech-tricky-big',
    'alias = /DATA/AUDIO/MUSIC.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music-tricky-big',
    'alias = /DATA/AUDIO/MUSIC2.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music2-tricky-big',
    'alias = /DATA/AUDIO/ENGLISH.BIG:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/english-silent-big',
    'alias = /DATA/CONFIG/MUSIC.INF:/storage/emulated/0/Android/data/'
    f'{APP_ID}/files/music-inf-tricky-big',
]
MUSIC_INF_SHA = ('a94186c0bf6306f2916ffac7a365bd8c07e659be0fa18db0b668d97f06bc1aaf')
MANIFEST_POKE = 'poke = 0x47BDB0:data/ui/courspic.big:data/ui/courspit.big\n'

# tricky.env: the 16 Tricky-only keys (preset-parity-1 Part 3 table; base
# presets carry none of these). Values match PB70 play (odin-play-PB70);
# device paths in repo/PUBLIC_APP_ID form, rewritten to the built id at
# stage time. Every key is read by the runtime (see test_tricky_env_keys).
TRICKY_ENV = f"""# build_tricky.py: Tricky env layer (Tricky only; layered over ps2x.env).
PS2X_CD_OVERLAY=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-ov
PS2X_SSX3_COURSE_MANIFEST=/storage/emulated/0/Android/data/{APP_ID}/files/m-tricky.txt
PS2X_SSX3_COURSE_PICKER=1
PS2X_SSX3_TRICKY_MENU=1
PS2X_SSX3_TRICKY_HUD=1
PS2X_SSX3_TRICKY_HUD_ART=/storage/emulated/0/Android/data/{APP_ID}/files/trickyhud.bin
PS2X_SSX3_TRICKY_MUSIC_INF=/storage/emulated/0/Android/data/{APP_ID}/files/music-inf-tricky-big
PS2X_SSX3_TRICKY_SONG=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-song.wav
PS2X_SSX3_TRICKY_GEMS=1
PS2X_SSX3_TRICKY_GEMS_TABLE=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-gems.tsv
PS2X_SSX3_TRICKY_LOD_SCALE=0.8
PS2X_SSX3_TRICKY_RESETVOL=1
PS2X_SSX3_TRICKY_RESETVOL_TABLE=/storage/emulated/0/Android/data/{APP_ID}/files/tricky-resetvol.tsv
PS2X_SSX3_MEGA_LAPS=1
PS2X_SSX3_TRICKY_DJ_GATE=1
PS2X_SSX3_TRICKY_MC=1
"""


def sha(path):
    h1 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    h2 = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    assert h1 == h2, f'unstable read: {path}'
    return h1


def check(path, want, what):
    got = sha(path)
    if got != want:
        raise SystemExit(f'{what}: SHA mismatch\n  got  {got}\n  want {want}')
    print(f'{what}: {got[:12]} OK', flush=True)


def run(logdir, name, *argv, cwd=None):
    logdir = Path(logdir)
    logdir.mkdir(parents=True, exist_ok=True)
    log = logdir / f'{name}.log'
    print(f'[{name}] {" ".join(str(a) for a in argv)}', flush=True)
    t0 = time.time()
    with open(log, 'wb') as f:
        r = subprocess.run(list(map(str, argv)), stdout=f,
                           stderr=subprocess.STDOUT, cwd=cwd)
    dt = time.time() - t0
    print(f'[{name}] rc={r.returncode} {dt:.0f}s ({log.name})', flush=True)
    if r.returncode != 0:
        tail = log.read_bytes()[-3000:].decode('utf8', 'replace')
        raise SystemExit(f'{name} failed rc={r.returncode} (see {log}):\n{tail}')
    return log


def iso_member(iso, want):
    """Find a disc member by basename (case-insensitive); fail closed."""
    with open(iso, 'rb') as f:
        _, files = iso_files(Region(f, 0, Path(iso).stat().st_size))
    hits = [e for e in files
            if e['path'].replace('\\', '/').split('/')[-1].upper() == want.upper()]
    if len(hits) != 1:
        raise SystemExit(f'{iso}: member {want}: {len(hits)} hits')
    return hits[0]


def extract_member(iso, member_path, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(iso, 'rb') as f:
        reg = Region(f, 0, Path(iso).stat().st_size)
        _, files = iso_files(reg)
        e = next(e for e in files if e['path'].upper() == member_path.upper())
        f.seek(e['offset'])
        out.write_bytes(f.read(e['size']))
    back = out.read_bytes()
    assert sha(out) == hashlib.sha256(back).hexdigest()
    return out


def big_member(iso, big_iso_path, want, out):
    """Extract one member from a BIG container on the disc (no full decode)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(iso, 'rb') as f:
        reg = Region(f, 0, Path(iso).stat().st_size)
        _, files = iso_files(reg)
        big = next(e for e in files if e['path'].upper() == big_iso_path.upper())
        sub = Region(f, big['offset'], big['size'])
        _, members = big_members(sub)
        norm = want.upper().replace('/', '\\')
        if '\\' in norm:
            # big_members stores forward slashes; normalize both sides.
            hits = [m for m in members
                    if m['path'].upper().replace('/', '\\') == norm]
        else:
            hits = [m for m in members
                    if m['path'].upper().replace('\\', '/').split('/')[-1] == norm]
        if len(hits) != 1:
            raise SystemExit(f'{big_iso_path}: member {want}: {len(hits)} hits')
        raw, _ = decode_member(sub, hits[0])
        out.write_bytes(raw)
    back = out.read_bytes()
    assert sha(out) == hashlib.sha256(back).hexdigest()
    return out


class Build:
    def __init__(self, a):
        self.a = a
        self.W = Path(a.work)
        if self.W.exists():
            raise SystemExit(f'refusing existing work dir: {self.W}')
        self.W.mkdir(parents=True)
        self.logs = self.W / 'logs'
        self.skipped = {}

    def world_final_rel(self, c):
        """Planned final relpath + golden: routes-final else s5-final."""
        if c in ROUTES_COURSES:
            return f'w-{c}-routes/{c.upper()}.BIG', ROUTES_COURSES[c]
        return f'w-{c}-s5/{c.upper()}.BIG', S5_FINALS[c]

    def world_final_path(self, c):
        """Planned final path + golden: routes-final else s5-final."""
        rel, want = self.world_final_rel(c)
        return self.W / rel, want

    def world_final(self, c):
        """Collect a course's gated final: routes-final else s5-final."""
        p, want = self.world_final_path(c)
        check(p, want, f'{c} final')
        return p

    def phase_worlds(self):
        # host-bam: stock BAM.BIG bytes from the user's SSX3 ISO.
        host_bam = self.W / 'host-bam' / 'BAM.BIG'
        bam = iso_member(self.a.ssx3_iso, 'BAM.BIG')
        extract_member(self.a.ssx3_iso, bam['path'], host_bam)
        for c in BUILD_COURSES:
            try:
                run(self.logs, f'world-{c}', sys.executable, T / 'run_chain.py',
                    '--tricky-iso', self.a.tricky_iso, '--ssx3-iso', self.a.ssx3_iso,
                    '--host-bam', host_bam, '--workdir', self.W,
                    '--course', c, '--rung', 'all',
                    *(['--routes-ref-dir', self.a.routes_ref_dir]
                      if self.a.routes_ref_dir else []),
                    cwd=self.W)
            except SystemExit as e:
                if c == 'mega' and 'routes-ref-dir' in str(e):
                    print('MEGA SKIPPED: no verified routes base '
                          '(pending mega-h/u2/c tail); continuing without it')
                    self.skipped['mega'] = str(e)
                    continue
                if 'gc-resets-missing' in str(e):
                    print(f'{c.upper()} SKIPPED: resets need the GameCube '
                          f'donor (pass --gc-dir); continuing without it')
                    self.skipped[f'resets-{c}'] = str(e)
                    continue
                raise
            self.world_final(c)
        print(f'worlds done; skipped: {sorted(self.skipped)}', flush=True)

    def resume_worlds(self, reuse):
        """Resume from speech: copy a finished run's world finals + host
        BAM into this fresh workdir, then rerun preview onward. Courses
        with no final in the reuse dir take the same loud skip keys
        phase_worlds uses (mega / resets-<course>)."""
        reuse = Path(reuse)
        if reuse.resolve() == self.W.resolve():
            raise SystemExit(f'--reuse-worlds must differ from --work: {reuse}')
        host = reuse / 'host-bam' / 'BAM.BIG'
        if not host.is_file():
            raise SystemExit(f'reuse worlds lack host BAM: {host}')
        dest = self.W / 'host-bam' / 'BAM.BIG'
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(host, dest)
        for c in BUILD_COURSES:
            rel, _want = self.world_final_rel(c)
            src = reuse / rel
            if not src.is_file():
                key = 'mega' if c == 'mega' else f'resets-{c}'
                self.skipped[key] = f'reused worlds lack {rel}'
                print(f'{c.upper()} REUSED-SKIP: no {rel} in {reuse}; '
                      f'continuing without it', flush=True)
                continue
            dst = self.W / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            self.world_final(c)
        print(f'worlds reused from {reuse}; skipped: {sorted(self.skipped)}',
              flush=True)

    def phase_preview(self):
        run(self.logs, 'preview', sys.executable, T / 'run_chain.py',
            '--tricky-iso', self.a.tricky_iso, '--ssx3-iso', self.a.ssx3_iso,
            '--host-bam', self.W / 'host-bam' / 'BAM.BIG', '--workdir', self.W,
            '--course', 'preview', cwd=self.W)
        out = self.W / 'cp' / 'COURSPIT.BIG'
        got = sha(out)
        if not got.startswith(PREVIEW_PIN):
            raise SystemExit(f'preview COURSPIT pin mismatch: {got[:12]} != {PREVIEW_PIN}')
        print(f'preview COURSPIT: {got[:12]} OK', flush=True)
        return out

    def phase_menu(self, courspit):
        dest = self.W / 'menu' / 'DATA' / 'UI'
        receipt = self.W / 'menu' / 'menu.json'
        # --member takes the COURSPIT input names (ABC1.ssh); the TP names
        # are the staged outputs menu_assets.py writes (TPABC1.SSH).
        run(self.logs, 'menu', sys.executable, T / 'tk52' / 'menu_assets.py',
            '--courspit', courspit,
            *sum((['--member', m[2:] + '.ssh'] for m in TP_PANES), []),
            '--out', self.W / 'menu', '--receipt', receipt, cwd=self.W)
        got = sorted(p.name for p in dest.glob('TP*'))
        want = sorted(f'{m}.SSH' for m in TP_PANES)
        if got != want:
            raise SystemExit(f'menu panes differ: {got} != {want}')
        print(f'menu panes OK: {len(got)}', flush=True)
        return dest

    def phase_speech(self):
        sp = self.W / 'speech'
        ex = sp / 'extract'
        for m in TRICKY_SPEECH_MEMBERS:
            big_member(self.a.tricky_iso, 'DATA/AUDIO/SPEECH.BIG',
                       m, ex / 'tricky' / m.split('\\')[-1])
        for t in SPEECH_TARGETS:
            big_member(self.a.ssx3_iso, 'DATA/AUDIO/SPEECH.BIG',
                       SSX3_SPEECH_DIR + t + '.dat',
                       ex / 'ssx3' / (t + '.dat'))
        run(self.logs, 'speech-catalog', sys.executable, T / 'speech_catalog.py',
            '--vgm', self.a.vgmstream, '--extract-dir', ex,
            '--streams-dir', sp / 'streams', '--wav-dir', sp / 'wav',
            '--lines-tsv', sp / 'lines.tsv', cwd=self.W)
        for t in SPEECH_TARGETS:
            dec = T / 'data' / 'speech-decisions' / (t + '.dat.decisions.tsv')
            mapping = sp / 'mapping' / (t + '.tsv')
            mapping.parent.mkdir(parents=True, exist_ok=True)
            # Single-candidate mapping from the pinned decisions: the pinned
            # source is tried first, exactly as the lane runs chose it.
            rows = [l.rstrip('\n').split('\t') for l in dec.read_text().splitlines()[1:]]
            mapping.write_text('slot\tsource\n' + ''.join(f'{r[0]}\t{r[1]}\n' for r in rows))
            out = sp / 'built' / (t + '.dat')
            out.parent.mkdir(parents=True, exist_ok=True)
            run(self.logs, f'speech-slot-{t}', sys.executable, T / 'speech_slots.py',
                str(ex / 'ssx3' / (t + '.dat')), str(dec), str(mapping), str(out),
                '--target', t, '--loudrows', sp / 'loud.tsv',
                '--tk25b-work', sp, '--work-dir', sp / ('work-' + t),
                cwd=self.W)
            run(self.logs, f'speech-check-{t}', sys.executable, T / 'check_slots.py',
                str(ex / 'ssx3' / (t + '.dat')), str(out), str(dec),
                str(sp / 'check'), cwd=self.W)
        members = sp / 'members'
        members.mkdir(exist_ok=True)
        for t in SPEECH_TARGETS:
            # speech_composite TARGETS carry the .dat extension.
            shutil.copy2(sp / 'built' / (t + '.dat'), members / (t + '.dat'))
        out = self.W / 'speech-tricky-big'
        run(self.logs, 'speech-composite', sys.executable, T / 'speech_composite.py',
            self.a.ssx3_iso, members, out, cwd=self.W)
        check(out, SPEECH7_SHA, 'speech-tricky-big')
        return out

    def phase_music(self):
        mu = self.W / 'music'
        assign = {}
        for line in (T / 'music-pool-default.tsv').read_text().splitlines():
            if not line or line.startswith(('#', 'slot')):
                continue
            slot, song = line.split('\t')[:2]
            assign[slot] = song
        songs = sorted(set(assign.values()))
        ex = mu / 'extract'
        for song in songs:
            for ext in ('mpf', 'mus'):
                big_member(self.a.tricky_iso, 'DATA/AUDIO/MUSIC.BIG',
                           f'{song}.{ext}', ex / f'{song}.{ext}')
        # template.sch: stock bep.mus stream 0 (any stock stream works).
        bepb = mu / 'stock-bep.mus'
        big_member(self.a.ssx3_iso, 'DATA/AUDIO/MUSIC.BIG', 'bep.mus', bepb)
        data = bepb.read_bytes()
        offs = [m.start() for m in re.finditer(b'SCHl', data)]
        if len(offs) < 1:
            raise SystemExit('stock bep.mus has no SCHl stream')
        end = offs[1] if len(offs) > 1 else len(data)
        tmpl = mu / 'template.sch'
        tmpl.write_bytes(data[offs[0]:end])
        stage = mu / 'stage'
        stage.mkdir(parents=True, exist_ok=True)
        for song in songs:
            mpf, mus = ex / f'{song}.mpf', ex / f'{song}.mus'
            run(self.logs, f'music-remus-{song}', sys.executable, T / 'music_remus.py',
                mus, mpf, tmpl, stage / f'{song}.mus', stage / f'{song}.tsv',
                f'jobs={self.a.jobs}', cwd=self.W)
            run(self.logs, f'music-convert-{song}', sys.executable, T / 'mpf3to4.py',
                mpf, stage / f'{song}.mpf', cwd=self.W)
        run(self.logs, 'music-selfcheck', sys.executable, T / 'music_selfcheck.py',
            '--stage-dir', stage, '--assign-tsv', T / 'music-pool-default.tsv',
            cwd=self.W)
        pool = mu / 'pool'
        run(self.logs, 'music-pool', sys.executable, T / 'build_music_pool.py',
            '--ssx3-iso', self.a.ssx3_iso, '--stage-dir', stage,
            '--assign-tsv', T / 'music-pool-default.tsv', '--out-dir', pool,
            cwd=self.W)
        return pool

    def phase_music_inf(self):
        out = self.W / 'music-inf-tricky-big'
        run(self.logs, 'music-inf', sys.executable, T / 'patch_music_inf.py',
            '--ssx3-iso', self.a.ssx3_iso,
            '--map-tsv', T / 'music-banner-default.tsv',
            '--out-composite', out,
            '--materialized', self.W / 'music-inf-patched.inf', cwd=self.W)
        check(out, MUSIC_INF_SHA, 'music-inf-tricky-big')
        return out

    def phase_english(self):
        base = self.a.english_base
        if not base:
            raise SystemExit('english-base-missing: the 8-member Tricky '
                             'ENGLISH base needs the lost tk-audio-2 merge '
                             'recipe (pass --english-base)')
        out = self.W / 'english-silent-big'
        run(self.logs, 'english-silence', sys.executable,
            T / 'silence_intro_banks.py', '--iso', self.a.ssx3_iso,
            '--base-composite', base, '--out-composite', out,
            '--materialized', self.W / 'english-silent-mat.big', cwd=self.W)
        return out

    def phase_song(self):
        jb = self.W / 'song' / 'JUKEBOX.BIG'
        extract_member(self.a.tricky_iso, 'DATA/AUDIO/JUKEBOX.BIG', jb)
        wav = self.W / 'song' / 'song-36k.wav'
        # Cap above the 6,602,400-frame member; decode stops at member end.
        run(self.logs, 'song-decode', sys.executable, T / 'song_eaxa.py',
            'decode', jb, SONG_MEMBER, '210', wav, cwd=self.W)
        final = self.W / 'tricky-song.wav'
        run(self.logs, 'song-resample', self.a.ffmpeg, '-hide_banner', '-loglevel', 'error',
            '-y', '-i', wav, '-ar', '48000',
            '-af', f'volume={SONG_GAIN_DB}dB', final, cwd=self.W)
        check(final, AUDIO_SHA['tricky-song.wav'], 'tricky-song.wav')
        return final

    def phase_hud(self):
        hud = self.W / 'hud'
        hud.mkdir(parents=True, exist_ok=True)
        run(self.logs, 'hud-atlas', sys.executable, T / 'make_hud_atlas.py',
            self.a.ssx3_iso, hud / 'atlas', cwd=self.W)
        out = hud / 'atlas' / 'trickyhud.bin'
        check(out, HUD_ATLAS_SHA, 'trickyhud.bin')
        return out

    def phase_gems(self):
        ge = self.W / 'gems'
        specs = []
        for arch, group in GEM_COURSES:
            c = arch.lower()
            if f'resets-{c}' in self.skipped:
                continue
            world = self.W / f'w-{c}-f11' / f'{arch}.BIG'
            scenery = self.W / f'w-{c}-b' / 'scenery.json'
            effects = self.W / 'effects' / f'{GEM_DONOR_STEM.get(arch, arch)}.json'
            specs += ['--course', f'{arch}:{world}:{scenery}:{effects}:{group}']
        partial = len(specs) < len(GEM_COURSES)
        sidecar = ge / 'gems-345.tsv'
        run(self.logs, 'bake-gems', sys.executable, T / 'bake_gems.py',
            *specs, '--out', sidecar, '--receipt', ge / 'gems-345-receipt.json',
            cwd=self.W)
        if partial:
            got = sha(sidecar)
            print(f'gems-345 partial ({len(specs)}/{len(GEM_COURSES)} '
                  f'courses): {got[:12]} (expected delta vs staging)',
                  flush=True)
            self.skipped['gems-partial'] = \
                f'gems-345 without GC-resets courses; sidecar {got[:12]}'
        else:
            check(sidecar, GEMS_345_SHA, 'gems-345')
        run(self.logs, 'donor-inventory', sys.executable, T / 'donor_inventory.py',
            '--iso', self.a.tricky_iso, '--out', ge / 'donor-bindings',
            '--receipt', ge / 'donor-receipt.json', cwd=self.W)
        worlds = ge / 'worlds'
        worlds.mkdir(parents=True, exist_ok=True)
        for c in ('pipe', 'alaska', 'untrack'):
            # gem_step materializes <course>.BIG (lowercase) composites.
            shutil.copy2(self.W / f'w-{c}-s5' / f'{c.upper()}.BIG',
                         worlds / f'{c}.BIG')
        out = ge / 'gems-483.tsv'
        run(self.logs, 'gem-step', sys.executable, T / 'gem_step.py',
            '--worlds', worlds, '--iso', self.a.ssx3_iso,
            '--donor', self.W / 'donor', '--donor-bindings', ge / 'donor-bindings',
            '--sidecar', sidecar, '--work', ge / 'work52',
            '--out', out, '--receipt', ge / 'gems-483-receipt.json',
            '--alaska-scenery', self.W / 'w-alaska-b' / 'scenery.json',
            cwd=self.W)
        if partial:
            got = sha(out)
            print(f'gems-483 partial: {got[:12]} (expected delta vs staging)',
                  flush=True)
            self.skipped['gems-483-partial'] = f'tricky-gems.tsv {got[:12]}'
        else:
            check(out, AUDIO_SHA['tricky-gems.tsv'], 'tricky-gems.tsv')
        print('GEMS SKIPPED: +38 MEGA needs the mega world (see MEGA SKIPPED)', flush=True)
        self.skipped['gems-mega'] = 'bake_mega_gems needs MEGA.BIG (pending mega-h/u2/c tail)'
        return out

    def phase_resetvol(self):
        out = self.W / 'tricky-resetvol.tsv'
        run(self.logs, 'resetvol', sys.executable, T / 'bake_reset_volumes.py',
            out, '--donor-dir', self.W / 'donor',
            '--untrack-dir', self.W / 'donor' / 'untrack',
            '--plans', T / 'resetvol-plans.json', cwd=self.W)
        check(out, RESETVOL_SHA, 'tricky-resetvol.tsv')
        return out

    def phase_layout(self, built):
        """Assemble the device layout (public equivalent of stage.py).

        Device files land at the top of --work (beside intermediates);
        the builder stages only the allowlisted payload by name.
        """
        lay = self.W
        ov = lay / 'tricky-ov' / 'DATA' / 'WORLDS'
        ov.mkdir(parents=True, exist_ok=True)
        for c, final in built['worlds'].items():
            shutil.copy2(final, ov / f'{c.upper()}.BIG')
        ui = lay / 'tricky-ov' / 'DATA' / 'UI'
        ui.mkdir(parents=True, exist_ok=True)
        shutil.copy2(built['courspit'], ui / 'COURSPIT.BIG')
        for pane in (self.W / 'menu' / 'DATA' / 'UI').glob('TP*'):
            shutil.copy2(pane, ui / pane.name)
        for name in ('speech-tricky-big', 'music-tricky-big', 'music2-tricky-big',
                     'music-inf-tricky-big', 'english-silent-big',
                     'trickyhud.bin', 'tricky-song.wav',
                     'tricky-gems.tsv', 'tricky-resetvol.tsv'):
            src = built.get(name)
            if src is None:
                continue
            shutil.copy2(src, lay / name)
        rows = [row for row in MANIFEST_ROWS
                if f"resets-{MANIFEST_COURSE[row[1]]}" not in self.skipped]
        if 'mega' not in self.skipped:
            rows.append(('15', 'MEGA', 'Tokyo Megaplex'))
        lines = [MANIFEST_HEAD] + MANIFEST_ALIASES + \
            [f'row = {e}:{a}:{n}' for e, a, n in rows] + \
            [MANIFEST_POKE.rstrip('\n')]
        (lay / 'm-tricky.txt').write_text('\n'.join(lines) + '\n')
        (lay / 'tricky.env').write_text(TRICKY_ENV)
        if self.skipped:
            (lay / 'SKIPPED.txt').write_text(
                'Omitted inputs (loud, not silent):\n' +
                ''.join(f'- {k}: {v}\n' for k, v in sorted(self.skipped.items())))
        print(f"layout done: {len(built['worlds'])} worlds; "
              f'skipped: {sorted(self.skipped)}', flush=True)
        return lay


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--tricky-iso', required=True)
    ap.add_argument('--ssx3-iso', required=True)
    ap.add_argument('--work', required=True)
    ap.add_argument('--jobs', default=str(os.cpu_count() or 8))
    ap.add_argument('--from-phase', default=None, choices=['speech'],
                    help='resume at a later phase, reusing a finished run')
    ap.add_argument('--reuse-worlds', default=None,
                    help='finished --work dir whose world finals + host BAM '
                         'are copied into the fresh --work dir (fresh dir '
                         'only; never the same dir)')
    ap.add_argument('--routes-ref-dir', default=None)
    ap.add_argument('--english-base', default=None)
    ap.add_argument('--vgmstream', default='vgmstream-cli')
    ap.add_argument('--ffmpeg', default='ffmpeg')
    a = ap.parse_args(argv)
    if a.from_phase == 'speech' and not a.reuse_worlds:
        raise SystemExit('--from-phase speech needs --reuse-worlds <dir>')
    b = Build(a)
    t0 = time.time()
    if a.from_phase == 'speech':
        b.resume_worlds(a.reuse_worlds)
    else:
        b.phase_worlds()
    courspit = b.phase_preview()
    b.phase_menu(courspit)
    speech = b.phase_speech()
    pool = b.phase_music()
    try:
        english = b.phase_english()
    except SystemExit as e:
        if 'english-base-missing' not in str(e):
            raise
        print(f'ENGLISH SKIPPED: {e}', flush=True)
        b.skipped['english-base'] = str(e)
        english = None
    built = {'worlds': {},
             'courspit': courspit,
             'speech-tricky-big': speech,
             'music-tricky-big': pool / 'music1-tkp4b',
             'music2-tricky-big': pool / 'music2-tkp4b',
             'music-inf-tricky-big': b.phase_music_inf(),
             'english-silent-big': english,
             'trickyhud.bin': b.phase_hud(),
             'tricky-song.wav': b.phase_song(),
             'tricky-gems.tsv': b.phase_gems(),
             'tricky-resetvol.tsv': b.phase_resetvol()}
    for c in BUILD_COURSES:
        if (c == 'mega' and 'mega' in b.skipped) or f'resets-{c}' in b.skipped:
            continue
        built['worlds'][c] = b.world_final(c)
    for name in ('music-tricky-big', 'music2-tricky-big'):
        check(built[name], AUDIO_SHA[name], name)
    lay = b.phase_layout(built)
    dt = time.time() - t0
    print(f'BUILD_TRICKY OK in {dt:.0f}s: {lay} '
          f'({len(built["worlds"])} worlds)', flush=True)


if __name__ == '__main__':
    main()
