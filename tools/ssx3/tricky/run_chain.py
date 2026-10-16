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
        # Recorded TK10-era -b 80d5d62b superseded: this chain's -b is
        # 543cf7a8 and -c + TK49 match pins; see lane REPORT/open questions.
        oracle_b=None,
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
        scenery_extra=['--drop-instances', '{data}/untrack-drop.txt'],
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
        # TK33b build_snow33.sh: make_scenery33 (no flags) + 521 fence drops
        scenery_tool='tk33b/make_scenery33.py',
        scenery_extra=[],
        rails_tool='tk33b/make_rails16.py',
        fence_file='{c}-fence.txt',
        oracle_sc='70d905bab0d58727a477aae92636c03a5b64ebae50a2f6c8dcfa0d82547047dd',
        oracle_f='d81692e68a5a157ab890ecb9c3c4caddb7cff5e57bb0fa55d90dd4dce0a69a97',
        oracle_b=None,
        # rails16/terrain19c identical in TK28b and TK33b tooling; tk33b kept.
        terrain_tool='tk33b/make_terrain_tex19c.py',
        terrain_extra=['--mips', '1', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='snow_L.ssh',
        oracle_c='55ddafcf7ff9c4aa4d5c992b06c1ecc8dfd52f30be3241056bb7cf9e08d76',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        bind_scenery='snow-cand-scenery.json',
        oracle_bound='4eae7401996002a38b524fa54350585fd234441d12b0837ce3a965321719960d',
        floor_group=56,
        oracle_final='c850d63e5324a7d85957da9fa14f87270f5cd5394286d0029a14e7702b31e8c3',
        fix_group=56,
        oracle_fix='3df07de95bea6050b4f41be6f83c325dc549d79b20ce49433c3595867b02e54a',
        preview_loc='BRA2',
    ),
    'gari': dict(
        big='GARI.BIG', donor='gari', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['gari_L.ssh'],
        # 09-11 run-gari-010 recipe: raw replace_terrain (no race relocation)
        build_tool='shared/replace_terrain.py',
        build_env={},
        build_args=build_world_args(
            'ARA1', 1673, '-1214.8,-195.5,-768.547891',
            '-118613.68,15753.8,-228880.14',
            73, .55, 32, ['--reset-aip', '{donor}/gari.aip',
                           '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--hide', 'A_ARA1'],
        race_aip='gc/gari.aip',
        resets_nbd='gari.nbd',
        # TK33b build_course33.sh gari-cand: make_scenery33 + soft + splitZ
        # + 349 fence drops, then rails16, then terrain19c --mips-only.
        scenery_tool='tk33b/make_scenery33.py',
        scenery_extra=['--soft-u0zero',
                       '--split-hull', '54:1254', '--split-hull', '55:1254',
                       '--split-hull', '56:1254', '--split-hull', '119:1254'],
        rails_tool='tk33b/make_rails16.py',
        fence_file='{c}-fence.txt',
        oracle_sc='1c1bdfed',
        oracle_f='93f73852',
        terrain_tool='tk33b/make_terrain_tex19c.py',
        terrain_extra=['--prune', '--compact-ids', '--lm-alpha-scale', '1.0',
                       '--lm-inset', '0.5', '--mips-only',
                       '{data}/gari-line-tex20.txt'],
        terrain_L='gari_L.ssh',
        oracle_c='ada8ba3e',
        # Staged TK49 1850bc0e is a TK42c blend/materialize gate (middle
        # ladder), NOT a make_composite30 product; wired when that rung lands.
        oracle_tk49='1850bc0e0ba74cecbc60f56b281997ac81391c50c09e31af701fc4da9cf24878',
        cand_extra=['--soft-u0zero',
                    '--split-hull', '54:1254', '--split-hull', '55:1254',
                    '--split-hull', '56:1254', '--split-hull', '119:1254'],
        oracle_cand='ada8ba3e',
        oracle_sg_scenery='d833fa45',
        oracle_sgt='72ed9d93b994a188bc79206859867a06fcff173c67760308b2af6e5750126aa0',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        bind_scenery='gari-sg-scenery.json',
        oracle_bound='58897da7e81e873115d75f07e72b37bf26206ef523b21aac966fa6e025868f38',
        floor_group=33,
        oracle_final='2ba912b3e95f428c8d9ac539e0cd8e18793171f668562141cfb4e8a6c324733a',
        fix_group=33,
        oracle_fix='3e714aca99403fc8350dfa3e6ac81835174cd77b37d20cba4279275f0c0cc5ce',
        preview_loc='ARA1',
    ),
    'mesa': dict(
        big='MESA.BIG', donor='mesa', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['mesa_L.ssh'],
        build_env={},
        build_args=build_world_args(
            'CRA3', 6, '-402.73553466796875,-372.3375549316406,4066.28466796875',
            '-135497.29947916666,-58447.231119791664,212157.515625',
            261.3, .55, 78, ['--reset-aip', '{donor}/mesa.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--gate-chain', '0,1,2,3,4'],
        resets_nbd='mesa.nbd',
        # TK33b build_course33.sh mesa-cand2: make_scenery33 + soft +
        # mode2-solid-file + 39 breakaway drops (NOT mesa-fence.txt), rails16,
        # terrain19c --mips 4. Base/exp regenerated here (TK14's archived).
        # NOTE fd9400d0 was the TK19c-era mesa-c, superseded by candt2.
        scenery_tool='tk33b/make_scenery33.py',
        scenery_extra=['--soft-u0zero', '--mode2-solid-file',
                       '{data}/mesa-trunks.txt'],
        rails_tool='tk33b/make_rails16.py',
        fence_file='mesa-breakaway.txt',
        oracle_sc='577667f3',
        oracle_f='7c32dad1',
        terrain_tool='tk33b/make_terrain_tex19c.py',
        terrain_extra=['--mips', '4', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='mesa_L.ssh',
        oracle_c='14b3daf3',
        # mesa-finalw f81f73d4 (bridge/show/waterfall steps) feeds bind, not
        # candt2 directly; middle-ladder rung pending TK37b/TKP3 recon.
        oracle_cand='14b3daf3',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        bind_scenery='mesa-final-scenery.json',
        oracle_bound='fe35aa1881eebf6eda0392cb43531b53ae2ffa513688a66fb306e262c5f6f8a4',
        floor_group=80,
        oracle_final='93668ecfb8ebad07d958d8a0c7f8e7cf55c4312476a0c7c612009e29b3e0702d',
        fix_group=80,
        oracle_fix='ab8325d74363f91a88d6156e5a7f4431ca18d113fbb563f09b6b2d98178b58f6',
        preview_loc='CRA3',
    ),
    'merq': dict(
        big='MERQUER.BIG', donor='merquer', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['merquer_L.ssh'],
        build_env={'TK10_KEEP_RESET': '68'},
        build_args=build_world_args(
            'ERA5', 51, '-88.97691345214844,-19928.7578125,17058.09765625',
            '-351954.6822916667,110245.00911458333,490273.6875',
            271.4, .55, 141, ['--reset-aip', '{donor}/merquer.aip',
                               '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '68',
                   '--gate-chain', '0,4,5,1,2,3'],
        resets_nbd=None,
        # TK16 build_course.sh merq-d: make_scenery16 + route/radius/drop.
        # merq-c re-scenery (--corridor-top-exempt 100, 69d27aa4) + terrain
        # (--mips-only merq-line-tex5, MISSING file) pending TK19c recon.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--collision-route', '{data}/merq3d.txt',
                       '--collision-radius', '1500',
                       '--drop-instances', '{data}/merq-drop.txt'],
        rails_tool='tk16/make_rails16.py',
        oracle_f='ef3ed5be',
        oracle_c='60830bffafbf9c46a542a99c6218506d8ab1b6d6412be01db3fc4f5eb212f997',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        bind_scenery='merq-cand-scenery.json',
        oracle_bound='021b9b1a0d27499f3bae6e299a0299e373e925b1eb71aa5635a7135ca4568b20',
        floor_group=148,
        oracle_final='9fcced315bf5d0f9a8ab4b7232dc1f6a2a2d4386748332ef479aea99c967998c',
        fix_group=148,
        oracle_fix='b88422281a8138e6960316a7a4fc5e239f6309752a3c021da14df161af3368d7',
        preview_loc='ERA5',
    ),
    'elys': dict(
        big='ELYSIUM.BIG', donor='elysium', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['elysium_L.ssh'],
        build_env={'TK10_KEEP_RESET': '42'},
        build_args=build_world_args(
            'EBC3', 27, '-428.06982421875,-3996.297119140625,12520.1640625',
            '-360652.0,319398.140625,739730.0',
            169.9, .55, 131, ['--reset-aip', '{donor}/elysium.aip',
                               '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '42',
                   '--gate-chain', '0,1,2,3,4'],
        resets_nbd=None,
        # TK20 build_course.sh elys-b: TK16 make_scenery16 + route/radius/
        # drop; rails20. Halted at race start (draw-table overflow); front
        # chain continues via TK22/TK26; -b/candt oracles pending that recon.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--collision-route', '{data}/elys3d.txt',
                       '--collision-radius', '1500',
                       '--drop-instances', '{data}/elys-drop.txt'],
        rails_tool='tk20/make_rails20.py',
        oracle_bound='b9df59d3ee19532927eb35d07e1132f2aab8a3410ace7a577d44bfe5d2a9b212',
        floor_group=135,
        oracle_final='169a8cf4ef60020e1604f82cb059c1ab7c140fe3a6ba2cf870a46c24afae3fd4',
        fix_group=135,
        oracle_fix='8d9e9f86e5a85e548834b20aaf8921782ee61b215d61a7c9112bb174e52b660f',
        preview_loc='EBC3',
    ),
    'aloha': dict(
        big='ALOHA.BIG', donor='aloha', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['aloha_L.ssh'],
        build_env={'TK10_KEEP_RESET': '48'},
        build_args=build_world_args(
            'ABC1', 30, '-688.0489501953125,-2878.951416015625,58693.38671875',
            '1982.6675415039062,-46116.931640625,96339.8046875',
            345.5, .55, 15, ['--reset-aip', '{donor}/aloha.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '48',
                   '--gate-chain', '0,1,2,3'],
        race_aip='gc/aloha.aip',
        resets_nbd='aloha.nbd',
        # TK20 build_course.sh aloha-b: TK16 make_scenery16 + 4 race-line
        # crossing drops; rails20. -b SHA pending; -c 0c353680 carried.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--no-collision-instance', '662',
                       '--no-collision-instance', '454',
                       '--no-collision-instance', '451',
                       '--no-collision-instance', '1846'],
        rails_tool='tk20/make_rails20.py',
        oracle_c='0c35368056f406eb1698aba45bb3b0ae97b4bc0677e22d03ce4b1daef3b87691',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        bind_scenery='aloha-b-scenery.json',
        oracle_bound='6ca2bca62db08d2d9e068b522e5e5fa6de567edde1d7a028546a839143d4c8b9',
        floor_group=22,
        oracle_final='11e6b9f8ac5ef2dcc7080a57c4f34d96aa56376fb2320588069c48f13a388bf5',
        fix_group=22,
        oracle_fix='10109cbcb9767e402b07e959bebddc380c4e5b26b542c453e937f2fac4de2abd',
        preview_loc='ABC1',
    ),
    'alaska': dict(
        big='ALASKA.BIG', donor='alaska', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['alaska_L.ssh'],
        # TK18 pipeline step 1 runs build_world with TK10_SURFACE=0.
        build_env={'TK10_SURFACE': '0', 'TK10_KEEP_RESET': '87'},
        build_args=build_world_args(
            'DSS2', 12, '-18650.419921875,4959.109375,7784.38916015625',
            '128772.49739583333,-145501.33854166666,-29729.162109375',
            154.1, .55, 113, ['--reset-aip', '{donor}/alaska.aip',
                               '--relocate-freeride-start']),
        race_tool='tk18/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '87',
                   '--race-line-kind', 'slopestyle',
                   '--markers', '0.275798,0.653856', '--chain-tolerance', '1500'],
        resets_nbd=None,
        route_tol=['1500', '{lane}/TK18/routes/alaska3d-b.txt', '--z'],
        # TK18 build_course.sh alaska-b: TK16 make_scenery16 + rails16, no
        # extras (route files are boot inputs; copies in data/).
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=[],
        rails_tool='tk16/make_rails16.py',
        oracle_b='b840713463e715de6adb545a6393f176e148585856ba8b638852bf49a532c7e2',
        oracle_c='b1bb68d2cdf2a01b02517b336efbf40d33bd744867bc2184aa560d9a1693560d',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        bind_scenery='alaska-b-scenery.json',
        oracle_bound='161ff820b26c35418853f998424b6aaea7d908199fd171b2ca4eea61e4c5968e',
        floor_group=115,
        oracle_final='50e5b4db09598d18baa4a96570ec7ece7dd1336fbe1c7acf60de02ee342303f1',
        fix_group=115,
        oracle_fix='3a8b00dd4de23ccbf53b2c91427d5c4f0f43391a0f8169c5687718ec7024a005',
        preview_loc=None,
    ),
    'mega': dict(
        big='MEGAPLE.BIG', donor='megaple', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['megapl_L.ssh'],
        lap_patch=True,
        build_env={'TK10_SURFACE': '0', 'TK10_KEEP_RESET': '42'},
        build_args=build_world_args(
            'DBC2', 75, '1630.64453125,196.73287963867188,12.964978218078613',
            '47827.451171875,-244658.3515625,278105.828125',
            279.4, .55, 90, ['--reset-aip', '{donor}/megaple.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '42',
                   '--gate-chain', '1,2,3,4,5'],
        # NOTE: no race_aip: TK18 build+race use the lap-patched donor aip.
        resets_nbd=None,
        # TK18 build_course.sh mega-b: TK16 make_scenery16 + rails16 with the
        # mega drop list. TK23 reworks this into mega-h (scenery23 +
        # solidify/slab/split-hull/boost); mega-c 3c97fa33 is the TK26 terrain
        # gate on that path. mega-b oracle below; TK23 step pending recon.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--drop-instances', '{data}/mega-drop.txt'],
        rails_tool='tk16/make_rails16.py',
        oracle_b='1bae15bf7868b9b6d8364576d2aa3bf4b85a884010d64cfe44db850b0fc617c2',
        oracle_c='3c97fa33893854b58eb92b76c2ea3dc27780d20e367af57ec716a7844254f263',
        floor_group=94,
        oracle_final='52582c77f02d72bd022a24e23ea710d3fb79134437812e9feff7494a85ecd7d4',
        fix_group=None,
        oracle_fix=None,
        preview_loc=None,
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

    # -- rung: lap-start patch (megaplex single-lap donor AIP) ----------------
    def rung_lap(self, c, r, ddir):
        out = self.W / f'{c}-lap.aip'
        run('lap', sys.executable, self.T / 'tk18' / 'lap_start.py',
            ddir / f"{r['donor']}.aip", out, '12,13,14,15,13,15',
            cwd=self.W, log=self.log(c, 'lap'))
        return out

    # -- rung: build -------------------------------------------------------
    def rung_build(self, c, r, ddir, aip=None):
        out = self.W / f'build-{c}'
        args = [a.format(donor=ddir) for a in r['build_args']]
        if aip:
            args = [str(aip) if a == str(ddir / f"{r['donor']}.aip") else a
                    for a in args]
        tool = self.T / r.get('build_tool', 'tk10/build_world.py')
        cmd = [sys.executable, tool,
               self.a.host_bam, '--world-report', self.W / 'ssx3-world.json',
               '--pbd', ddir / f"{r['donor']}.pbd"] + args + ['--output', out]
        run('build', *cmd, env=r.get('build_env'), cwd=self.W, log=self.log(c, 'build'))
        return out

    # -- rung: race --------------------------------------------------------
    def rung_race(self, c, r, bdir, ddir, aip=None):
        out = self.W / f'{c}-r.BIG'
        if aip is None:
            if r.get('race_aip', '').startswith('gc/'):
                aip = Path('/Users/brad/dev/ssx3/local/source/gamecube/tricky') / r['race_aip'][3:]
            else:
                aip = ddir / f"{r['donor']}.aip"
        run('race', sys.executable, self.T / r['race_tool'],
            bdir / 'BAM.BIG', self.a.host_bam, aip,
            bdir / 'experiment.json', c, out, *r['race_args'],
            cwd=self.W, log=self.log(c, 'race'))
        return out

    # -- rung: scenery + rails ---------------------------------------------
    def rung_scenery(self, c, r, rbig, bdir, ddir):
        w = self.W / f'w-{c}-b'
        (w / 'sc').mkdir(parents=True, exist_ok=True)
        sc = w / 'sc' / f'{c.upper()}.BIG'
        out = w / f'{c.upper()}.BIG'
        fmt = dict(donor=ddir, lane=self.lane, data=self.T / 'data')
        extra = [a.format(**fmt) for a in r['scenery_extra']]
        ff = (r.get('fence_file') or '').format(c=c)
        if ff:
            for n in (self.T / 'data' / ff).read_text().split():
                extra += ['--no-collision-instance', n]
        run('scenery', sys.executable, self.T / r['scenery_tool'],
            rbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            c, sc, '--receipt', w / 'scenery.json', *extra,
            cwd=self.W, log=self.log(c, 'scenery'))
        if r.get('oracle_sc'):
            check(sc, r['oracle_sc'], f'{c} sc')
        run('rails', sys.executable, self.T / r['rails_tool'],
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            c, out, cwd=self.W, log=self.log(c, 'rails'))
        if r.get('oracle_b'):
            check(out, r['oracle_b'], f'{c} -b')
        if r.get('oracle_f'):
            check(out, r['oracle_f'], f'{c} -f')
        return out

    # -- rung: terrain ------------------------------------------------------
    def rung_terrain(self, c, r, bbig, bdir, ddir):
        out = self.W / f'w-{c}-c' / f'{c.upper()}.BIG'
        out.parent.mkdir(parents=True, exist_ok=True)
        L = r['terrain_L']
        fmt = dict(donor=ddir, course=c.upper(), data=self.T / 'data')
        extra = [a.format(**fmt) for a in r['terrain_extra']]
        run('terrain', sys.executable, self.T / r['terrain_tool'],
            bbig, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            ddir / f"{r['donor']}.ssh", ddir / L,
            c, out, *extra,
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
        aip = None
        if 'extract' in order:
            ddir = self.rung_extract(c, r)
        if 'ps2' in order:
            ddir = ddir or self.donor / r['donor']
            self.rung_ps2(c, r, ddir)
        if r.get('lap_patch'):
            ddir = ddir or self.donor / r['donor']
            aip = self.rung_lap(c, r, ddir)
        if 'build' in order:
            ddir = ddir or self.donor / r['donor']
            bdir = self.rung_build(c, r, ddir, aip)
        if 'race' in order:
            rbig = self.rung_race(c, r, bdir, ddir, aip)
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
