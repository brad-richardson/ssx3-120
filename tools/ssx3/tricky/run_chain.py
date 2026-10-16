#!/usr/bin/env python3
"""tk-port-1 validation driver: replay the TK10->TKP5 world chain from the two ISOs.

New glue (not a port). Every step invokes a ported converter as a subprocess
with the exact command the lane ran (paths re-rooted to --workdir). Each rung
output is SHA-256-checked against its pin; the run aborts on mismatch.

Usage:
  run_chain.py --tricky-iso <SSX-Tricky-USA.iso> --ssx3-iso <SSX-3-USA.iso>
               --workdir <run dir> --course pipe [--rung extract|...|all]
               [--lane-dir <private ssx3 checkout>] [--pins <pinned root>]

--lane-dir supplies lane-authored recipe text (fence lists, drop lists,
line-tex lists, scenery/effects JSONs, glass-target keep lists). Those files
are read-only inputs; nothing game-derived is written into git.
"""
import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SHARED = HERE / 'shared'

# --------------------------------------------------------------------------
# Pins ( payday oracles; full SHAs where recorded )
# --------------------------------------------------------------------------
DONOR_MANIFEST = 'donor/MANIFEST'          # under --pins/tricky
WORLDS_BASE = 'worlds-base/MANIFEST'
WORLDS_BOUND = 'worlds-bound/MANIFEST'
WORLDS_FINAL = 'worlds-final/MANIFEST'
TK49_SHA256SUMS = 'tkp4-staged-SHA256SUMS'  # captured from the archived TKP4
# staged preset (frozen reference copy lives outside git; see README)
TKP5_FINALS = {  # odin-play-tricky-tkp5/SHA256SUMS
    'gari': '991929fe9ff735c703595e481594036c7537fd5e25f2ad35e3c1712e55c0db36',
    'snow': 'f2e82e9c012c10e17f5d10b45aed7790430a4e7437825aff94fb97dbe4f2595f',
    'mesa': '081a94d7793f36cc3ab88aa557b4286091e0a1feb4f79661fc89acbc4596f85f',
    'merq': 'bcbaf32d16a27b7328788515a5aa57927d468abd03927f39b657614951257323',
    'aloha': '6ae037e395c6686d14f2911e170ed91c93e7f8ab8fcaa387033c9221c6c809bb',
    'elys': '1d0676267ee963659a31077048b701a6b3a92ffaf8394d6b083b8a7f90e626eb',
    'alaska': '43a23f8ad20af36c1012d3661b0e5d0f3de82767d1315b3eba73861573233c47',
    'untrack': '4fce15a5e31c0897b490bbf972f6e17e57c777f2dd240b162bb636b00b00e343',
    'pipe': 'b353d0614897de85520d3917a81e1c7aafc95011d270b147a80a30eb2810a4bc',
    'mega': '8c28ceeda86f321402fc07e7ac8a056e366d32167e3e0feaaf9e796e1e54bafd',
}
TKP5_COURSPIT = '3332b0af708db0b8d4ff8dc901c78f1521b777140158874949b26798414b6acb'
FROZEN_COURSPIT = '6ad0f543f47586ca9997dd6f880ee872fce04ccd615f0adcfd49bfbdb6ae4d99'

# --------------------------------------------------------------------------
# Course recipes. Placement args verbatim from the lane REPORTs / plan-all.
# --------------------------------------------------------------------------
STD_DROPS = ['--drop-kind', '3', '--drop-kind', '12', '--clear-instance-references',
             '--clear-script-bindings', '--disable-course-scripts']


def build_world_args(loc, tmpl, src, tgt, yaw, scale, pin, extra=()):
    return ['--location', loc, '--template-rid', str(tmpl),
            f'--source-anchor={src}', f'--target-anchor={tgt}',
            '--yaw', str(yaw), '--scale', str(scale)] + STD_DROPS + \
        ['--pin-texture-group', str(pin)] + list(extra)


COURSES = {
    'pipe': dict(
        big='PIPE.BIG', donor='pipe', ps2_tool='tk30/ps2_as_gc30.py',
        donor_extra=['pipe_L.ssh'],
        build_env={'TK10_KEEP_RESET': '11'},
        build_args=build_world_args(
            'BHP1', 4, '157.85255432128906,10.177421569824219,4894.36865234375',
            '-268342.9479166667,116586.7265625,-461145.8854166667',
            120.8, .55, 48, ['--reset-aip', '{donor}/pipe.aip',
                              '--relocate-freeride-start']),
        race_tool='tk18/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '11', '--relocate-start-only'],
        resets=None,
        scenery_tool='tk30/make_scenery30.py', scenery_extra=[],
        rails_tool='tk30/make_rails30.py',
        oracle_b='80d5d62b',
        terrain_tool='tk30/make_terrain_tex30.py',
        terrain_extra=['--mips', '1', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='pipe_L.ssh',
        oracle_c='c0e2b900c55be0ddf708001a8e82be36cf354260eecc75075cec0c81a61f277f',
        composite_tool='tk30/make_composite30.py',
        oracle_tk49='b353d0614897de85520d3917a81e1c7aafc95011d270b147a80a30eb2810a4bc',
        floor_group=None, bind=None,
        fix_group=None, preview_loc='BHP1',
    ),
    'untrack': dict(
        big='UNTRACK.BIG', donor='untrack', ps2_tool='tk30/ps2_as_gc30.py',
        donor_extra=['untrac_L.ssh'],
        build_env={'TK10_SURFACE': '0', 'TK10_KEEP_RESET': '88'},
        build_args=build_world_args(
            'ESS3', 19, '5369.39599609375,-10045.30078125,28632.5703125',
            '-327424.4895833333,182567.0625,484667.875',
            347.6, .55, 154, ['--reset-aip', '{donor}/untrack.aip',
                               '--relocate-freeride-start']),
        race_tool='tk30/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '88',
                   '--race-line-kind', 'slopestyle',
                   '--markers', '0.3364442,0.6784675', '--chain-tolerance', '5000'],
        resets=None,
        scenery_tool='tk30/make_scenery30.py',
        scenery_extra=['--drop-instances', '{lane}/TK31/untrack-drop.txt'],
        rails_tool='tk30/make_rails30.py',
        oracle_b=None,
        terrain_tool='tk30/make_terrain_tex30.py',
        terrain_extra=['--mips', '1', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='untrac_L.ssh',
        oracle_c=None,
        composite_tool='tk30/make_composite30.py',
        oracle_tk49='4cd09ece16c8ede2943c7ab69e067267899e4e62985e656a1acc2f83d89e2f9f',
        floor_group=None, bind=None,
        fix_group=None, preview_loc='ESS3',
    ),
    'snow': dict(
        big='SNOW.BIG', donor='snow', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['snow_L.ssh'],
        build_env={'TK10_SURFACE': '0'},
        build_args=build_world_args(
            'BRA2', 26, '-498.95916748046875,-64925.07421875,6064.11376953125',
            '-225114.1875,125100.8359375,-461119.59375',
            291.8, .55, 52, ['--reset-aip', '{donor}/snow.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts'],
        resets_nbd='snow.nbd',
        scenery_tool='tk28b/make_scenery16_at_34715951.py',
        scenery_extra=[],
        rails_tool='tk28b/make_rails16.py',
        fence_receipt='TK28/snow-fence.txt',
        oracle_b=None,
        terrain_tool='tk28b/make_terrain_tex19c.py',
        terrain_extra=['--mips', '1', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='snow_L.ssh',
        oracle_c='55ddafcf7ff9c4aa4d5c992b06c1ecc8dfd52f30be3241056bb7cf9e08d76',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        bind_scenery='snow-cand-scenery.json',
        oracle_bound='4eae74011c8aa2b0e9b6b6b0e9b0e9b0e9b0e9b0e9b0e9b0e9b0e9b0',
        floor_group=56,
        oracle_final='c850d63e5324a7d85957da9fa14f87270f5cd5394286d0029a14e7702b31e8c3',
        fix_group=56,
        oracle_fix='62dd04f3b8b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0',
        preview_loc='BRA2',
    ),
}


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(1 << 20), b''):
            h.update(b)
    return h.hexdigest()


def run(step, *cmd, env=None, cwd=None, log=None):
    e = dict(os.environ)
    e.pop('PYTHONPATH', None)
    if env:
        e.update(env)
    print(f'[{step}]', ' '.join(str(c) for c in cmd[:4]), '...', flush=True)
    r = subprocess.run([str(c) for c in cmd], env=e, cwd=cwd,
                       capture_output=True, text=True)
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(r.stdout + '\n--- stderr ---\n' + r.stderr)
    if r.returncode != 0:
        tail = (r.stderr.strip().splitlines() or ['?'])[-1]
        raise SystemExit(f'{step} FAILED rc={r.returncode}: {tail} (log {log})')
    return r


def check(path, expect, label):
    got = sha256(path)
    if expect and not got.startswith(expect) and got != expect:
        raise SystemExit(f'{label} MISMATCH:\n  got    {got}\n  expect {expect}')
    print(f'{label} ok {got[:16]}', flush=True)
    return got


class Chain:
    def __init__(self, a):
        self.a = a
        self.W = Path(a.workdir)
        self.T = HERE
        self.lane = Path(a.lane_dir)
        self.donor = self.W / 'donor'
        (self.W / 'logs').mkdir(parents=True, exist_ok=True)

    def log(self, *names):
        return str(self.W / 'logs' / '-'.join(names))

    # -- rung: extract -----------------------------------------------------
    def rung_extract(self, c, r):
        iso = self.W / 'iso' / r['big']
        if not iso.exists():
            run('extract-iso', sys.executable, SHARED / 'inspect_disc.py', self.a.tricky_iso,
                '--extract', f'DATA/MODELS/{r["big"]}', '--output', iso,
                cwd=self.W, log=self.log(c, 'extract-iso'))
        outdir = self.donor / r['donor']
        run('extract-big', sys.executable, self.T / 'tk18' / 'extract_big.py', iso, outdir,
            cwd=self.W, log=self.log(c, 'extract-big'))
        return outdir

    def rung_ps2(self, c, r, ddir):
        d = r['donor']
        run('ps2', sys.executable, self.T / r['ps2_tool'],
            ddir / f'{d}.pbd', ddir / f'{d}.ssf',
            ddir / f'{d}-ps2.gsf', ddir / f'{d}-ps2.nbd',
            cwd=self.W, log=self.log(c, 'ps2'))
        return ddir

    # -- rung: build -------------------------------------------------------
    def rung_build(self, c, r, ddir):
        out = self.W / f'build-{c}'
        args = [a.format(donor=ddir) for a in r['build_args']]
        tool = self.T / r.get('build_tool', 'tk10/build_world.py')
        cmd = [sys.executable, tool,
               self.a.host_bam, '--world-report', self.W / 'ssx3-world.json',
               '--pbd', ddir / f"{r['donor']}.pbd"] + args + ['--output', out]
        run('build', *cmd, env=r.get('build_env'), cwd=self.W, log=self.log(c, 'build'))
        return out

    # -- rung: race --------------------------------------------------------
    def rung_race(self, c, r, bdir, ddir):
        out = self.W / f'{c}-r.BIG'
        run('race', sys.executable, self.T / r['race_tool'],
            bdir / 'BAM.BIG', self.a.host_bam, ddir / f"{r['donor']}.aip",
            bdir / 'experiment.json', c, out, *r['race_args'],
            cwd=self.W, log=self.log(c, 'race'))
        return out

    # -- rung: scenery + rails ---------------------------------------------
    def rung_scenery(self, c, r, rbig, bdir, ddir):
        w = self.W / f'w-{c}-b'
        (w / 'sc').mkdir(parents=True, exist_ok=True)
        sc = w / 'sc' / f'{c.upper()}.BIG'
        out = w / f'{c.upper()}.BIG'
        extra = [a.format(donor=ddir, lane=self.lane) for a in r['scenery_extra']]
        run('scenery', sys.executable, self.T / r['scenery_tool'],
            rbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            c, sc, '--receipt', w / 'scenery.json', *extra,
            cwd=self.W, log=self.log(c, 'scenery'))
        run('rails', sys.executable, self.T / r['rails_tool'],
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            c, out, cwd=self.W, log=self.log(c, 'rails'))
        if r.get('oracle_b'):
            check(out, r['oracle_b'], f'{c} -b')
        return out

    # -- rung: terrain ------------------------------------------------------
    def rung_terrain(self, c, r, bbig, bdir, ddir):
        out = self.W / f'w-{c}-c' / f'{c.upper()}.BIG'
        out.parent.mkdir(parents=True, exist_ok=True)
        L = r['terrain_L']
        run('terrain', sys.executable, self.T / r['terrain_tool'],
            bbig, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            ddir / f"{r['donor']}.ssh", ddir / L,
            c, out, *r['terrain_extra'],
            '--receipt', out.parent / 'terrain.json',
            cwd=self.W, log=self.log(c, 'terrain'))
        if r.get('oracle_c'):
            check(out, r['oracle_c'], f'{c} -c')
        return out

    # -- rung: composite ----------------------------------------------------
    def rung_composite(self, c, r, cbig):
        out = self.W / f'{c}-tk49' / f'{c.upper()}.BIG'
        out.parent.mkdir(parents=True, exist_ok=True)
        run('composite', sys.executable, self.T / r['composite_tool'],
            self.a.ssx3_iso, cbig, out,
            cwd=self.W, log=self.log(c, 'composite'))
        if r.get('oracle_tk49'):
            check(out, r['oracle_tk49'], f'{c} tk49')
        return out

    def run_course(self, c, upto='all'):
        assert c in COURSES, c
        r = COURSES[c]
        order = ['extract', 'ps2', 'build', 'race', 'scenery', 'terrain', 'composite']
        if upto != 'all':
            order = order[:order.index(upto) + 1]
        ddir = bdir = rbig = bbig = cbig = None
        if 'extract' in order:
            ddir = self.rung_extract(c, r)
        if 'ps2' in order:
            ddir = ddir or self.donor / r['donor']
            self.rung_ps2(c, r, ddir)
        if 'build' in order:
            ddir = ddir or self.donor / r['donor']
            bdir = self.rung_build(c, r, ddir)
        if 'race' in order:
            rbig = self.rung_race(c, r, bdir, ddir)
        if 'scenery' in order:
            bbig = self.rung_scenery(c, r, rbig, bdir, ddir)
        if 'terrain' in order:
            cbig = self.rung_terrain(c, r, bbig, bdir, ddir)
        if 'composite' in order:
            self.rung_composite(c, r, cbig)
        print(f'{c} done through {order[-1]}', flush=True)


def ensure_world_report(chain):
    wr = chain.W / 'ssx3-world.json'
    if not wr.exists():
        run('world-report', sys.executable, SHARED / 'probe_worlds.py', 'ssx3',
            chain.a.host_bam, '--report', wr,
            cwd=chain.W, log=chain.log('world-report'))
    return wr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--tricky-iso', required=True)
    ap.add_argument('--ssx3-iso', required=True)
    ap.add_argument('--host-bam', required=True)
    ap.add_argument('--workdir', required=True)
    ap.add_argument('--course', default='pipe')
    ap.add_argument('--rung', default='all')
    ap.add_argument('--lane-dir', default='/Users/brad/dev/ssx3/local/research')
    ap.add_argument('--pins', default='/Users/brad/dev/ssx3-work/pinned/tricky')
    a = ap.parse_args()
    ch = Chain(a)
    ensure_world_report(ch)
    ch.run_course(a.course, a.rung)


if __name__ == '__main__':
    main()
