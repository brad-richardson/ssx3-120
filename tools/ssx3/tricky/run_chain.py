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
import json
import os
import shutil
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
STOCK_COURSPIC = 'fb3f9d1e43a69a8f9773283e126d1a41a77e590747c53ed4ac5452fa0960c31b'

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


# Rung order. 'resets' must stay between 'race' and 'scenery': the ov-s
# output (not the race output) is the scenery base wherever GC reset data
# exists (regression: gari scenery once ran on the race world and missed).
# Middle ladder: sg (show-gate variant) / cand (soft variant) / finalw
# (mesa show+unprotect+waterfall variant) -> bind (effects) -> floor (f11 =
# worlds-final). Stages that a course has not wired yet are skipped with a
# note (never silently): the oracles, not the rung list, are the gate.
RUN_ORDER = ['extract', 'ps2', 'build', 'race', 'resets', 'scenery',
             'terrain', 'sg', 'cand', 'finalw', 'bind', 'floor', 'composite',
             'play', 'blend', 'fix', 's1', 's2', 's3', 's4', 's5', 'routes']
# Tail (TKP5 chain.sh order): play = TK37b composite10 of the floor final;
# blend = TK42c materialize + patch_blend + composite to the staged TK49;
# fix = TK56 class-row rebind of staged; s1 = TK53 triggers (mesa);
# s2 = TK57 glass/blocks; s3 = TK51 proxies; s4 = TK58 hulls;
# s5 = TKP5 relocate to the final preset world. Preview/COURSPIT is a
# separate --course preview run (one archive for all courses).


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
        # staged == TK49; pipe passes the tail through to the TKP5 final.
        oracle_staged='b353d0614897de85520d3917a81e1c7aafc95011d270b147a80a30eb2810a4bc',
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
        # TK31 wrapper passes UNTRACK (uppercase); race/terrain stems
        # lowercase per the verbatim race command (terrain stem unknown,
        # oracles decide).
        stem_scenery='UNTRACK',
        oracle_b='b83d43c7',
        oracle_c='6cce9262',
        terrain_tool='tk30/make_terrain_tex30.py',
        terrain_extra=['--mips', '1', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='untrac_L.ssh',
        composite_tool='tk30/make_composite30.py',
        oracle_tk49='4cd09ece16c8ede2943c7ab69e067267899e4e62985e656a1acc2f83d89e2f9f',
        floor_group=None, bind=None,
        # staged == TK49; s3 proxies feed the reloc below.
        oracle_staged='4cd09ece16c8ede2943c7ab69e067267899e4e62985e656a1acc2f83d89e2f9f',
        oracle_s3='e6ea3205e05b901ad5c8c79dec5842c4173cd73bfc951ed087d8b51eba1f11cb',
        fix_group=None, preview_loc='ESS3',
        # Relocate group verbatim (TKP5 tooling/relocate.sh group()); the
        # TK56 fix is unwired for untrack but s5 still relocates p151->179.
        s5_group=158,
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
        oracle_r='8b203740',
        # Same-input ov-s: PS2 .pbd surfaces, 304 flagged;
        # downstream bound 4eae7401 + final c850d63e match the reference digests.
        oracle_ovs='ebd5605f9adcbe51878d595790121c6b9623ce26ad55bd3f8ecdada325ade3e7',
        resets_pbd=True,
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
        # Full SHA doubly confirmed: TK28b REPORT + fresh run4 (the previous
        # value 55ddafcf7ff9c4aa... was a mistranscription; the driver
        # caught it at the terrain gate).
        oracle_c='55ddafcf126d2a749e6679b1ffa200415cf5ce1e4fed2934448700e19ab58be1',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        # Snowdream has no separate cand: snow-cand == snow-b content (same
        # tool/flags/base), so snow-c == snow-candt (55ddafcf both). Bind the
        # -c world with the -b scenery (proven: 4eae7401 in run4/run5).
        bind_base='c',
        oracle_bound='4eae7401996002a38b524fa54350585fd234441d12b0837ce3a965321719960d',
        floor_group=56,
        oracle_final='c850d63e5324a7d85957da9fa14f87270f5cd5394286d0029a14e7702b31e8c3',
        fix_group=56,
        oracle_fix='3df07de95bea6050b4f41be6f83c325dc549d79b20ce49433c3595867b02e54a',
        oracle_play='b9da986022987d8a24cacc624aa30b1cf73c9f8a064fe14165270f3e4fef8295',
        oracle_mat='8b6b14a85f0d96cdbe141ef43fb765541828d601320688debcd4c7544bf082f7',
        oracle_blend='f2768fd9df165c9801e38a7dfd6358747ed3537fdde1c2f1982c31dd56dfa9ab',
        oracle_staged='ddaa3c2fb411a754e5e113f32fada3bc2de4b05c287bf9a1878f0dbb2fbfede0',
        oracle_s2='09a3f736f897f273c7a373fe20d33871074e3e99ffe11b148b4bf665dcd8b104',
        oracle_s3='28687171e5d11271672963a98254fa1e820e6c27b8d18e006d4f70357753f51d',
        oracle_s4='f394b59826a3f99e2bf361fcfcb7011d0851d6eff1a6091918674e5810a3d6a9',
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
        resets_pbd=True,
        # Machine receipt, run1 ov-s-gari.log.
        oracle_ovs='a314cbd5ea2d2d89e8000fd7b5bfdde42dceb34e50d0ccd2a87b18058afe571a',
        oracle_r='4066438d9a3a7944cdd370c9931d9038da4bacc69ca13ddb132819163b22fa51',
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
        # No cand rung: gari-cand rebuilds -c (TK33b gari-candt == ada8ba3e)
        # and feeds nothing (bind runs on sgt). The sg rung below covers it.
        sg_show=[136, 138],
        bind_base='sgt',
        bind_scenery='sg',
        oracle_sg_scenery='d833fa45',
        oracle_sgt='72ed9d93b994a188bc79206859867a06fcff173c67760308b2af6e5750126aa0',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        oracle_bound='58897da7e81e873115d75f07e72b37bf26206ef523b21aac966fa6e025868f38',
        floor_group=33,
        oracle_final='2ba912b3e95f428c8d9ac539e0cd8e18793171f668562141cfb4e8a6c324733a',
        fix_group=33,
        # fix = TK56 class-row rebind OF THE STAGED TK49 (1850bc0e), not a
        # floor successor: floor(bound) = final 2ba912b3 (worlds-final side
        # branch). Order: ... terrain, bind, floor, [TK42c stage TK49],
        # TK56 fix, TK57, TK51, TK58, reloc, preview, stage.
        oracle_fix='3e714aca99403fc8350dfa3e6ac81835174cd77b37d20cba4279275f0c0cc5ce',
        oracle_play='ec33194a4a0f210eaff98ee2c549acea40fa4874073eb33d8f88f721045e2b4b',
        oracle_mat='fc2939d0c9b6d06de4b60ab857af9109b68eb55f6cabc0df4213d5bba1cddc5d',
        oracle_blend='7b9d955a135dc19a1c68674c0fcdc0aeb897eed1518f7bc9ab661df657124dfe',
        # staged == oracle_tk49 above (TKP4 composite of the blend).
        oracle_s2='51736b49ea9a4da28f52aaff64a6afec38638d0692c48759941ff059c188f286',
        oracle_s3='5d27161d495c65fbfc1ac253830a7ca22aa69edef698b7c407fb14d813642340',
        oracle_s4='5d27161d495c65fbfc1ac253830a7ca22aa69edef698b7c407fb14d813642340',
        tk57_blocks=[145, 146, 147, 158, 520, 531, 532, 1087, 1088, 1101,
                     1318, 1319, 1320, 2115, 2116, 2117, 2118, 2950, 2951,
                     2952, 3170, 3186],
        preview_loc='ARA1',
    ),
    'mesa': dict(
        big='MESA.BIG', donor='mesa', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['mesa_L.ssh'],
        # TK10_SURFACE=0 (TK14 canonical): the resets rung requires template
        # surface 0; without it make_resets refuses (mesa run4).
        build_env={'TK10_SURFACE': '0'},
        build_args=build_world_args(
            'CRA3', 6, '-402.73553466796875,-372.3375549316406,4066.28466796875',
            '-135497.29947916666,-58447.231119791664,212157.515625',
            261.3, .55, 78, ['--reset-aip', '{donor}/mesa.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--gate-chain', '0,1,2,3,4'],
        resets_pbd=True,
        # Same-input ov-s: PS2 .pbd surfaces, 242 flagged;
        # downstream finalw f81f73d4 + bound fe35aa18 + final 93668ecf
        # match the reference digests.
        oracle_ovs='8ad629acf1658e62666e31dd716fb33a7aaa9c3d9964195fbfa67bfc247b08a0',
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
        # mesa-finalw: cand2 flags + start-gate show (63/64) + bridge
        # unprotect (463/475), rails, mesa terrain flags, waterfall bind.
        # (No cand rung: mesa-candt2 == -c, gated above; bind runs on finalw.)
        finalw_show=[63, 64],
        finalw_unprotect=[463, 475],
        finalw_bind_names=['Mdl_Big_Waterfall', 'Mdl_Mini_Waterfall',
                           'Mdl_End_Waterfall', 'Mdl_End_Waterfallmist'],
        oracle_finalw_sc='6c12965314f377df77bd50786146444f735169e4bb2a5f35d2ba69411572e8c4',
        oracle_finalw_f='4c560c70edf29c69d2cd6f0c89fa15cb4518f929717087185c6f5c361c518507',
        oracle_finalt='a489c69c6ef56ec99905edabfedde1b9ec5e6b2f058a11134ce4552f7c20c795',
        oracle_finalw='f81f73d4363c4574ff18b12c675dfeb0bfd63a1b22c6336baaa4ce20a533dc97',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd',
                    'Mdl_SpeedBoost', 'Mdl_TrickBoost', 'Mdl_TrickB00st'],
        bind_base='finalw',
        bind_scenery='final',
        oracle_bound='fe35aa1881eebf6eda0392cb43531b53ae2ffa513688a66fb306e262c5f6f8a4',
        floor_group=80,
        oracle_final='93668ecfb8ebad07d958d8a0c7f8e7cf55c4312476a0c7c612009e29b3e0702d',
        fix_group=80,
        oracle_fix='ab8325d74363f91a88d6156e5a7f4431ca18d113fbb563f09b6b2d98178b58f6',
        oracle_play='cea3bcce0b0acce82999a04fe6129c31b996b60e8ac2cbcacf5a515050da0ea7',
        oracle_mat='ab62e169bff3192ae68a7667bf22e8c73817fd89c3ac0cb791e57ca32c69650c',
        oracle_blend='9a37eaf88d29a661da4db98bae1e472baa804e3c8dee16928c8646509c034b7e',
        oracle_staged='f5d08b10ae22337e85217348a00ae94556f85601f34f9b05f02a12807e627c90',
        tk53_rids=[706, 842, 843, 856, 943, 989, 1485, 1486, 1702, 1706,
                   1953, 1954, 1993, 1994],
        oracle_s1='a550279a090fca1d93ef2a347f25d02c44416f00ae59342bf77fdef21fd21d41',
        oracle_s2='f535e718843aaa5c53a059f154ebc4554d71535e4ac1eb332ff7085d381dca1e',
        oracle_s3='20384b5cb8a878eec9d446b3ae61a23883cac3575fc8a5a2e243d0a4c7e3101d',
        oracle_s4='f256ccf544a157ec18d545230bd6a6cdd2ee373752a5c8c42a112157f3dc18eb',
        preview_loc='CRA3',
    ),
    'merq': dict(
        big='MERQUER.BIG', donor='merquer', ps2_tool='tk16/ps2_as_gc.py',
        # NOTE truncated stem: merque_L.ssh (verified in donor extract).
        donor_extra=['merque_L.ssh'],
        # Pinned census file uses the short stem (merq.json, not merquer).
        effects='merq',
        # NOTE: no TK10_SURFACE here (bisect 10-16: WITH surface=0 the
        # fence rung gives 3eb8dc18, not merq-d ef3ed5be; merq has no
        # resets so the template surface only shifts content bytes).
        build_env={'TK10_KEEP_RESET': '68'},
        build_args=build_world_args(
            'ERA5', 51, '-88.97691345214844,-19928.7578125,17058.09765625',
            '-351954.6822916667,110245.00911458333,490273.6875',
            271.4, .55, 141, ['--reset-aip', '{donor}/merquer.aip',
                               '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '68',
                   '--gate-chain', '0,4,5,1,2,3'],
        resets_pbd=False,
        # TK16 build_course.sh merq-d: make_scenery16 + route/radius/drop.
        # merq-c (--corridor-top-exempt 100, 69d27aa4) was a race-feasibility
        # variant; the texture candidate runs TK19c terrain on merq-d.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--collision-route', '{data}/merq3d.txt',
                       '--collision-radius', '1500',
                       '--drop-instances', '{data}/merq-drop.txt'],
        rails_tool='tk16/make_rails16.py',
        # Stems verbatim: scenery/rails MERQ (TK16 wrapper uppercase); the
        # lowercase race base reconverges here (proven 10-16: -f ef3ed5be on
        # the 'merq' race world), so no stem_race override. Terrain 'merq'
        # (TK19 m5 verbatim).
        stem_scenery='MERQ',
        stem_terrain='merq',
        oracle_sc='658eda9e18acf7e4a4e311e935bc66c5a16da2859efeed0f47fb935b39c42133',
        oracle_f='ef3ed5be',
        # TK33b merq-cand: soft + 33-glass keep + merq-d trims on the race
        # base (MERQ stem); rails16; m5 terrain flags, 'merq' stem.
        cand_extra=['--soft-u0zero', '--soft-u0zero-keep-file',
                    '{data}/merq-glass-keep.txt',
                    '--collision-route', '{data}/merq3d.txt',
                    '--collision-radius', '1500',
                    '--drop-instances', '{data}/merq-drop.txt'],
        cand_stem_scenery='MERQ',
        cand_stem_terrain='merq',
        oracle_cand_sc='60e1f21c',
        oracle_candt='e5394e3c',
        bind_base='candt',
        bind_scenery='cand',
        # TK19 m5 recipe via TK19c: --mips 4 --prune --compact-ids
        # --mips-only merq-line-tex5 --offline-tex-max 64 --lm 1.0/0.5.
        terrain_tool='tk19c/make_terrain_tex.py',
        terrain_extra=['--mips', '4', '--prune', '--compact-ids',
                       '--mips-only', '{data}/merq-line-tex5.txt',
                       '--offline-tex-max', '64',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='merque_L.ssh',
        oracle_c='60830bffafbf9c46a542a99c6218506d8ab1b6d6412be01db3fc4f5eb212f997',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        oracle_bound='021b9b1a0d27499f3bae6e299a0299e373e925b1eb71aa5635a7135ca4568b20',
        floor_group=148,
        oracle_final='9fcced315bf5d0f9a8ab4b7232dc1f6a2a2d4386748332ef479aea99c967998c',
        fix_group=148,
        oracle_fix='b88422281a8138e6960316a7a4fc5e239f6309752a3c021da14df161af3368d7',
        oracle_play='ebbfa60309508aaa9fa5cab59d984c05d9d1b224e61fa0ded9c3b9b6cff87478',
        oracle_mat='34d7ebe1a930b44317a9b3d3092790182798775af205fb5e1622c45c77ccf11a',
        oracle_blend='067b653a05724176739f9aab800f9106b3ff9b87d6ff5de23a0e1402bdeb2b5a',
        oracle_staged='b71d561d1d329a652b03a6cccc5dbc5a06289224fd3216c11ebc2d5353655b38',
        oracle_s2='0df07b6e2a6382a19062680ba71839280d102fcf7d17cfa82bbe86d2c6f3c0fc',
        oracle_s3='cb6b07e2d47a2707eec014fb0ed28b1a2cf33b74a71202cb10ee0c55d1784984',
        oracle_s4='89dd2561aea3904c33b16f2c962e8d5cc4478106e7c15aa83dc779cfaed02791',
        preview_loc='ERA5',
        # tk-routes-2: Merqury sewer wall w2819 (cover --rids 2819).
        routes_group=148,
        routes_args=['cover', '--rids', '2819'],
        oracle_routes='a23d529a044d574512b61e065d351d97553a01cba906bd0274d3d56090f0da99',
    ),
    'elys': dict(
        big='ELYSIUM.BIG', donor='elysium', ps2_tool='tk16/ps2_as_gc.py',
        # NOTE truncated stem: TK20/src/elysium/elysiu_L.ssh, not elysium_L.
        donor_extra=['elysiu_L.ssh'],
        build_env={'TK10_SURFACE': '0', 'TK10_KEEP_RESET': '42'},
        build_args=build_world_args(
            'EBC3', 27, '-428.06982421875,-3996.297119140625,12520.1640625',
            '-360652.0,319398.140625,739730.0',
            169.9, .55, 131, ['--reset-aip', '{donor}/elysium.aip',
                               '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '42',
                   '--gate-chain', '0,1,2,3,4'],
        resets_pbd=False,
        # TK20 build_course.sh elys-b: TK16 make_scenery16 + route/radius/
        # drop; rails20. Halted at race start (draw-table overflow); front
        # chain continues via TK22/TK26; -b/candt oracles pending that recon.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--collision-route', '{data}/elys3d.txt',
                       '--collision-radius', '1500',
                       '--drop-instances', '{data}/elys-drop.txt'],
        rails_tool='tk20/make_rails20.py',
        # TK20 wrapper passes ELYS (uppercase) to scenery+rails; TK26 eb1
        # terrain also takes ELYS.
        stem_scenery='ELYS',
        stem_terrain='ELYS',
        # TK33b elys-cand: soft + 110-glass/reset keep + elys-b trims on the
        # race base (ELYS stem); rails16; eb1 terrain flags, 'elys' stem.
        cand_extra=['--soft-u0zero', '--soft-u0zero-keep-file',
                    '{data}/elys-glassreset-keep.txt',
                    '--collision-route', '{data}/elys3d.txt',
                    '--collision-radius', '1500',
                    '--drop-instances', '{data}/elys-drop.txt'],
        cand_stem_scenery='ELYS',
        cand_stem_terrain='elys',
        oracle_cand_sc='ef001690',
        oracle_candt='60e1b402',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        bind_base='candt',
        bind_scenery='cand',
        # Pinned census file uses the short stem (elys.json, not elysium).
        effects='elys',
        # TK26 elys-eb1 terrain (TK19c tool): --mips 4 --mips-only
        # elys-line-tex20 --prune --prune-kinds 10 --compact-ids --lm 1.0
        # --offline-tex-max 32. eb1 68207cc0; candt 60e1b402 path after eb1
        # pending recon.
        terrain_tool='tk19c/make_terrain_tex.py',
        terrain_extra=['--mips', '4', '--mips-only',
                       '{data}/elys-line-tex20.txt', '--prune',
                       '--prune-kinds', '10', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--offline-tex-max', '32'],
        terrain_L='elysiu_L.ssh',
        oracle_c='68207cc0',
        oracle_bound='b9df59d3ee19532927eb35d07e1132f2aab8a3410ace7a577d44bfe5d2a9b212',
        floor_group=135,
        oracle_final='169a8cf4ef60020e1604f82cb059c1ab7c140fe3a6ba2cf870a46c24afae3fd4',
        fix_group=135,
        oracle_fix='8d9e9f86e5a85e548834b20aaf8921782ee61b215d61a7c9112bb174e52b660f',
        oracle_play='9fee3f4fd2b55b71b843862bc7584bc3c547e3d871fd4add5af120ea947e263a',
        oracle_mat='dd64f261378d8d8414f791a23b339df8a5fe874a36afd1e70c938e3305314fd6',
        oracle_blend='fb04d5034efd7d85a73b689a4b50501782f60abb54e2aaeea7b6a65a583eb5db',
        blend_spec='blend-spec-elys2.json',
        oracle_staged='60728a31dd088ff8572f0cfd6fa899b3e9390488750e87cc1bd1fb7c3596ae6d',
        oracle_s2='caffab52336cf25cb21f101ba938ac2d82b15157f00dfbfba67899258f863b81',
        oracle_s4='caffab52336cf25cb21f101ba938ac2d82b15157f00dfbfba67899258f863b81',
        preview_loc='EBC3',
    ),
    'aloha': dict(
        big='ALOHA.BIG', donor='aloha', ps2_tool='tk16/ps2_as_gc.py',
        donor_extra=['aloha_L.ssh'],
        build_env={'TK10_SURFACE': '0', 'TK10_KEEP_RESET': '48'},
        build_args=build_world_args(
            'ABC1', 30, '-688.0489501953125,-2878.951416015625,58693.38671875',
            '1982.6675415039062,-46116.931640625,96339.8046875',
            345.5, .55, 15, ['--reset-aip', '{donor}/aloha.aip',
                              '--relocate-freeride-start']),
        race_tool='tk12/make_race_world.py',
        race_args=['--base-race-starts', '--keep-reset', '48',
                   '--gate-chain', '0,1,2,3'],
        resets_pbd=True,
        oracle_r='368a76e0',
        # Same-input ov-s: PS2 .pbd surfaces, 415 flagged;
        # downstream -c 0c353680 + bound 6ca2bca6 + final 11e6b9f8
        # match the reference digests.
        oracle_ovs='0cd565f4f8e60db1090b06907b907665d6477f9d10683d90747ee8b53a013b66',
        # TK20 build_course.sh aloha-b: TK16 make_scenery16 + 4 race-line
        # crossing drops; rails20. -b SHA pending; -c 0c353680 carried.
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--no-collision-instance', '662',
                       '--no-collision-instance', '454',
                       '--no-collision-instance', '451',
                       '--no-collision-instance', '1846'],
        rails_tool='tk20/make_rails20.py',
        # TK20 wrapper passes ALOHA (uppercase) to scenery+rails.
        stem_scenery='ALOHA',
        # TK20 aloha-b rails gate (machine receipt, TK19c): 23ac9638.
        oracle_b='23ac963810f89a6a4a60cbaab2db3464fa51347a25474a48d4611f9cfc8be11c',
        # TK19 a2 recipe via TK19c: --mips 4 --mips-only aloha-line-tex20
        # --offline-tex-max 64 --prune --compact-ids --lm 1.0/0.5.
        terrain_tool='tk19c/make_terrain_tex.py',
        terrain_extra=['--mips', '4', '--mips-only',
                       '{data}/aloha-line-tex20.txt',
                       '--offline-tex-max', '64', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='aloha_L.ssh',
        oracle_c='0c35368056f406eb1698aba45bb3b0ae97b4bc0677e22d03ce4b1daef3b87691',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        # A-bind on the -c world with the -b scenery (generic A == the
        # proven specific trio: 6ca2bca6 both ways).
        bind_base='c',
        bind_scenery='aloha-b-scenery.json',
        oracle_bound='6ca2bca62db08d2d9e068b522e5e5fa6de567edde1d7a028546a839143d4c8b9',
        floor_group=22,
        oracle_final='11e6b9f8ac5ef2dcc7080a57c4f34d96aa56376fb2320588069c48f13a388bf5',
        fix_group=22,
        oracle_fix='10109cbcb9767e402b07e959bebddc380c4e5b26b542c453e937f2fac4de2abd',
        oracle_play='f027776344dc70913be33d7595a558b0eeeae933b303b3c84cb20c135c189bfe',
        oracle_mat='61f2c4322b9623cb0fce4f8a98f7fffc8eb8d97f79f32dcc7e9741f5c192bdd3',
        oracle_blend='9c74ff34071e3894ce12854fb2520918de39318c4a1aa27970261003ffa226aa',
        oracle_staged='c43b5a1247639e87a91754109041fe4c43146b8bf26eb0b92723ede61ec4d537',
        oracle_s2='152500a194eb676b8363a88cd9db697fe06a0f74f2a5ed8de11497d4f2478940',
        oracle_s3='c5cd4aa6add1dd24aa47835501d86cd022845bbfb48ac7589b1234aaa0038dcc',
        oracle_s4='344fc3a1faaeced330ad9eaeb0bae5de8b7296ae2eda0abd04ad1830a22453b3',
        preview_loc='ABC1',
        # tk-routes-2: Aloha covers3 (A per-wedge --rids + B/C whole-cover
        # --cover-rids, order A,B,C verbatim).
        routes_group=22,
        routes_args=['cover',
                     '--rids', '85', '86', '87', '88', '89', '90', '91', '92',
                     '--cover-rids', '952', '953', '954', '955', '956', '957',
                     '958', '959',
                     '--cover-rids', '1614', '1615', '1616', '1617', '1618',
                     '1619', '1620', '1621'],
        oracle_routes='971c2f3733d319c519465d7726e972bacd8657ea7b482cd0ab461f868777ca5e',
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
        resets_pbd=False,
        route_tol=['1500', '{data}/alaska3d-b.txt', '--z'],
        # TK18 build_course.sh alaska-b: TK16 make_scenery16 + rails16, no
        # extras (route files are boot inputs; copies in data/).
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=[],
        rails_tool='tk16/make_rails16.py',
        # TK18 wrapper passes ALASKA (uppercase); its race used 'alaska'.
        stem_scenery='ALASKA',
        oracle_b='b840713463e715de6adb545a6393f176e148585856ba8b638852bf49a532c7e2',
        # TK19c alaska-c: --mips-only top-10 2,0,26,11,3,143,1,10,147,165 (=
        # data/alaska-line-tex10.txt content) + --offline-tex-max 32.
        # --mips 4 inferred from every sibling TK19c terrain row; the b1bb68d2
        # oracle decides at runtime.
        terrain_tool='tk19c/make_terrain_tex.py',
        terrain_extra=['--mips', '4', '--mips-only',
                       '{data}/alaska-line-tex10.txt',
                       '--offline-tex-max', '32', '--prune', '--compact-ids',
                       '--lm-alpha-scale', '1.0', '--lm-inset', '0.5'],
        terrain_L='alaska_L.ssh',
        oracle_c='b1bb68d2cdf2a01b02517b336efbf40d33bd744867bc2184aa560d9a1693560d',
        bind_names=['Mdl_Jumbotron', 'Mdl_Lcdscan', 'Mdl_Lcd'],
        # A-bind on the -c world with the -b scenery (TKP2 census: 3 rows;
        # generic A == specific Mdl_LcdscanRed: 161ff820 both ways, run6).
        bind_base='c',
        bind_scenery='alaska-b-scenery.json',
        oracle_bound='161ff820b26c35418853f998424b6aaea7d908199fd171b2ca4eea61e4c5968e',
        floor_group=115,
        oracle_final='50e5b4db09598d18baa4a96570ec7ece7dd1336fbe1c7acf60de02ee342303f1',
        fix_group=115,
        oracle_fix='3a8b00dd4de23ccbf53b2c91427d5c4f0f43391a0f8169c5687718ec7024a005',
        oracle_play='cdc2ef41649ba228898829b82398c66886cc402f90a2acba9ed57f5fccbeff12',
        oracle_mat='c5a1d6c6cf2a103b3e83674789b3122ca56763ac0ff442e1b35999f8fdea3b20',
        oracle_blend='c4a2e2fe4e9cff8a663bb3dcf8069b2e02c460471476ad52862b64cb104fac7b',
        oracle_staged='7887e47e8e30d153bfa7030ff7b79ceb5718e8120e1272d6ff65f38b0ec5e6c6',
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
        # Stems verbatim (TK18): race 'megaple', scenery/rails 'MEGA';
        # TK23c u2 terrain also 'MEGA'.
        stem_race='megaple',
        stem_scenery='MEGA',
        stem_terrain='MEGA',
        # NOTE: no race_aip: TK18 build+race use the lap-patched donor aip.
        resets_pbd=False,
        # TK18 mega-b (checkpoint, superseded branch): TK16 make_scenery16 +
        # rails16 with the TK18 drop list. The FINAL path skips mega-b:
        # mega2-r -> mega-h = scenery23 on the race world with TK21 extras
        # (--drop-instances data/mega-d-drop.txt, --no-collision-file
        # data/mega-d-nocollide.txt, --pose 292:5/57:5, --boost-json
        # data/boost-all-g.json) + TK23 extras (--solidify-file
        # data/fence-solidify.txt, --slab 20, --split-hull 163:750,
        # --boost-json data/boost-tk23.json), oracle 261ed4ef; then terrain23
        # mega-u2 (TK23c recipe: --mips 4 --prune --prune-orphans
        # --exact-bounds --mips-only data/mega-line-tex15.txt
        # --offline-tex-max 64, no compact-ids), oracle 969158da; then TK26
        # mega-c re-terrain (--mips 4 ... --mips-only <MISSING
        # mega-line-tex20> --offline-tex-max 64), oracle 3c97fa33.
        # mega-h/u2/c rungs pending (need tk21/tk23 scenery/terrain wiring).
        scenery_tool='tk16/make_scenery16.py',
        scenery_extra=['--drop-instances', '{data}/mega-drop.txt'],
        # No rails at this rung (TK18: Megaplex has no rails); 1bae15bf is
        # the scenery output. Rails arrive with TK21's mega-g.
        rails_tool=None,
        oracle_b='1bae15bf7868b9b6d8364576d2aa3bf4b85a884010d64cfe44db850b0fc617c2',
        oracle_c='3c97fa33893854b58eb92b76c2ea3dc27780d20e367af57ec716a7844254f263',
        floor_group=94,
        oracle_final='52582c77f02d72bd022a24e23ea710d3fb79134437812e9feff7494a85ecd7d4',
        # Play == staged == TKP5 final for Megaplex (no signs, no tail steps);
        # reachable once mega-h/u2/c lands the floor input.
        oracle_play='8c28ceeda86f321402fc07e7ac8a056e366d32167e3e0feaaf9e796e1e54bafd',
        oracle_staged='8c28ceeda86f321402fc07e7ac8a056e366d32167e3e0feaaf9e796e1e54bafd',
        fix_group=None,
        oracle_fix=None,
        preview_loc=None,
        # tk-routes-2: Megaplex tube w94 slot-2 binding (boost --rid 94
        # --program 34 --slot 2). Mega s5 is unwired (pending mega-h/u2/c
        # tail), so the routes base is the recorded TKP5-final reference,
        # input-SHA-gated before use.
        routes_group=94,
        routes_args=['boost', '--rid', '94', '--program', '34', '--slot', '2'],
        oracle_routes='0d0677c46d04fc99c735db84543d2fb344fcc17faf653db9d5fa5c6318f524bc',
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
        self.lane = Path(a.lane_dir) if a.lane_dir else None
        self.donor = self.W / 'donor'
        (self.W / 'logs').mkdir(parents=True, exist_ok=True)

    def log(self, *names):
        return str(self.W / 'logs' / '-'.join(names))

    # -- public-input helpers -------------------------------------------------
    # Pins are an optional comparison input; the workdir build always wins
    # when present. Every substitution below is judged fail-closed by the
    # rung oracles (recorded pins), so a wrong default can only fail loudly.
    def pin_file(self, *parts):
        if not self.a.pins:
            return None
        p = Path(self.a.pins).joinpath(*parts)
        return p if p.is_file() else None

    def need(self, value, what):
        if value is None:
            raise SystemExit(f'no workdir input for {what}; pass --pins')
        return value

    # -- effects census (public default) --------------------------------------
    # The pinned donor effects census (pins/scenery-effects/effects) wins
    # when present; otherwise regenerate it in-workdir with the ported
    # tk18/tricky_effects.py from the extract+ps2 rung outputs. Either way
    # the bind oracle judges the result fail-closed.
    def effects_json(self, c, r, ddir):
        wanted = self.pin_file('scenery-effects', 'effects',
                               f"{r.get('effects', r['donor'])}.json")
        if wanted is not None:
            return wanted
        out = self.W / 'effects' / f"{r.get('effects', r['donor'])}.json"
        if not out.is_file():
            # Donor files live in the stem subdir (donor/<stem>/); ddir is
            # the donor root when bind/finalw omit it, or the stem dir
            # itself from the extract return. Prefer whichever layout
            # actually holds the ps2 rung outputs.
            sdir = ddir / r['donor']
            if not (sdir / f"{r['donor']}-ps2.gsf").is_file():
                sdir = ddir
            out.parent.mkdir(parents=True, exist_ok=True)
            run('effects', sys.executable, self.T / 'tk18' / 'tricky_effects.py',
                sdir / f"{r['donor']}-ps2.gsf", sdir / f"{r['donor']}.map",
                sdir / f"{r['donor']}.pbd", out,
                cwd=self.W, log=self.log(c, 'effects'))
        return out

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
    # Stem case is verbatim per lane wrapper: it names the world members
    # (rename_world_members), so 'MERQ' != 'merq' in bytes. TK16/TK18/TK20
    # wrappers pass UPPERCASE stems; TK28b/TK33b/TK30 pass lowercase.
    def rung_race(self, c, r, bdir, ddir, aip=None):
        out = self.W / f'{c}-r.BIG'
        # PS2-only: the race conversion always reads the workdir donor .aip
        # (the pinned Tricky PS2 extract). The race oracle judges fail-closed.
        if aip is None:
            aip = ddir / f"{r['donor']}.aip"
        run('race', sys.executable, self.T / r['race_tool'],
            bdir / 'BAM.BIG', self.a.host_bam, aip,
            bdir / 'experiment.json', r.get('stem_race', c), out,
            *r['race_args'],
            cwd=self.W, log=self.log(c, 'race'))
        if r.get('oracle_r'):
            check(out, r['oracle_r'], f'{c} -r')
        return out

    # -- rung: resets (ov-s) -------------------------------------------------
    # Applies the PS2 donor's authored reset surfaces (the .pbd patch tail)
    # to the race world. The ov-s output (not the race output) is the
    # scenery base on every course with reset surfaces. Courses without
    # them pass the race world through unchanged.
    def rung_resets(self, c, r, rbig, bdir, ddir):
        if not r.get('resets_pbd'):
            return rbig
        out = self.W / f'ov-s-{c.upper()}.BIG'
        run('resets', sys.executable, self.T / 'tk12' / 'make_resets.py',
            rbig, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            c, out, cwd=self.W, log=self.log(c, 'resets'))
        if r.get('oracle_ovs'):
            check(out, r['oracle_ovs'], f'{c} ov-s')
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
        stem = r.get('stem_scenery', c)
        # All scenery tools take --tmp (default: a lab home path): keep
        # scratch in this run's per-course workdir (read-only HOME safe,
        # unique across concurrent builds, inside disk accounting).
        run('scenery', sys.executable, self.T / r['scenery_tool'],
            rbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            stem, sc, '--receipt', w / 'scenery.json',
            '--tmp', str(w / 'tmp'), *extra,
            cwd=self.W, log=self.log(c, 'scenery'))
        if r.get('oracle_sc'):
            check(sc, r['oracle_sc'], f'{c} sc')
        # Some courses ship scenery-only at this rung (Megaplex has no
        # rails: DBC2 carries no rail-class spline to template; TK21 adds
        # rails later via make_rails21 + another host's receipt).
        if not r.get('rails_tool'):
            if r.get('oracle_b'):
                check(sc, r['oracle_b'], f'{c} -b')
            return sc
        # Workdir scratch for the rails bam-rename copy (public default;
        # the tools keep the lane path when the 7th positional is absent).
        tmpdir = self.W / 'tmp'
        tmpdir.mkdir(parents=True, exist_ok=True)
        run('rails', sys.executable, self.T / r['rails_tool'],
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            stem, out, str(tmpdir), cwd=self.W, log=self.log(c, 'rails'))
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
            r.get('stem_terrain', c), out, *extra,
            '--receipt', out.parent / 'terrain.json',
            cwd=self.W, log=self.log(c, 'terrain'))
        if r.get('oracle_c'):
            check(out, r['oracle_c'], f'{c} -c')
        return out

    # -- rung: show-gate variant (sg) ---------------------------------------
    # Rebuilds scenery from the ov-s base with the candidate flags plus
    # --show-hidden-instance N (start gates), then rails + the candidate
    # terrain flags. Proven on gari (sg d833fa45 -> sgt 72ed9d93).
    def rung_sg(self, c, r, sbig, bdir, ddir):
        w = self.W / f'w-{c}-sg'
        (w / 'sc').mkdir(parents=True, exist_ok=True)
        sc = w / 'sc' / f'{c.upper()}.BIG'
        out = w / f'{c.upper()}.BIG'
        fmt = dict(donor=ddir, lane=self.lane, data=self.T / 'data')
        extra = [a.format(**fmt) for a in r['scenery_extra']]
        ff = (r.get('fence_file') or '').format(c=c)
        if ff:
            for n in (self.T / 'data' / ff).read_text().split():
                extra += ['--no-collision-instance', n]
        for n in r.get('sg_show', []):
            extra += ['--show-hidden-instance', str(n)]
        run('sg-scenery', sys.executable, self.T / r['scenery_tool'],
            sbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            r.get('stem_scenery', c), sc, '--receipt', w / 'scenery.json',
            '--tmp', str(w / 'tmp'), *extra,
            cwd=self.W, log=self.log(c, 'sg-scenery'))
        if r.get('oracle_sg_scenery'):
            check(sc, r['oracle_sg_scenery'], f'{c} sg-sc')
        run('sg-rails', sys.executable, self.T / r['rails_tool'],
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            r.get('stem_scenery', c), out,
            cwd=self.W, log=self.log(c, 'sg-rails'))
        tdir = self.W / f'w-{c}-sgt'
        tdir.mkdir(parents=True, exist_ok=True)
        tout = tdir / f'{c.upper()}.BIG'
        L = r['terrain_L']
        fex = dict(donor=ddir, course=c.upper(), data=self.T / 'data')
        tex = [a.format(**fex) for a in r['terrain_extra']]
        run('sg-terrain', sys.executable, self.T / r['terrain_tool'],
            out, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            ddir / f"{r['donor']}.ssh", ddir / L,
            r.get('stem_terrain', c), tout, *tex,
            '--receipt', tdir / 'terrain.json',
            cwd=self.W, log=self.log(c, 'sg-terrain'))
        if r.get('oracle_sgt'):
            check(tout, r['oracle_sgt'], f'{c} sgt')
        return tout

    # -- rung: soft-candidate variant (cand) ----------------------------------
    # Rebuilds scenery from the RACE base (pre-rails: kind 3/12 assert) with
    # soft-u0zero + keep-file + the course trims, then rails + the course
    # terrain flags. Proven: merq (60e1f21c -> e5394e3c), elys (ef001690 ->
    # 60e1b402, lowercase terrain stem).
    def rung_cand(self, c, r, sbig, bdir, ddir):
        w = self.W / f'w-{c}-cand'
        (w / 'sc').mkdir(parents=True, exist_ok=True)
        sc = w / 'sc' / f'{c.upper()}.BIG'
        out = w / f'{c.upper()}.BIG'
        fmt = dict(donor=ddir, lane=self.lane, data=self.T / 'data')
        extra = [a.format(**fmt) for a in r['cand_extra']]
        ff = (r.get('fence_file') or '').format(c=c)
        if ff:
            for n in (self.T / 'data' / ff).read_text().split():
                extra += ['--no-collision-instance', n]
        stem = r.get('cand_stem_scenery', c)
        run('cand-scenery', sys.executable, self.T / 'tk33b' / 'make_scenery33.py',
            sbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            stem, sc, '--receipt', w / 'scenery.json', *extra,
            cwd=self.W, log=self.log(c, 'cand-scenery'))
        if r.get('oracle_cand_sc'):
            check(sc, r['oracle_cand_sc'], f'{c} cand-sc')
        run('cand-rails', sys.executable, self.T / r.get('cand_rails_tool', 'tk33b/make_rails16.py'),
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            stem, out, cwd=self.W, log=self.log(c, 'cand-rails'))
        tdir = self.W / f'w-{c}-candt'
        tdir.mkdir(parents=True, exist_ok=True)
        tout = tdir / f'{c.upper()}.BIG'
        L = r['terrain_L']
        fex = dict(donor=ddir, course=c.upper(), data=self.T / 'data')
        tex = [a.format(**fex) for a in r['terrain_extra']]
        run('cand-terrain', sys.executable, self.T / r['terrain_tool'],
            out, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            ddir / f"{r['donor']}.ssh", ddir / L,
            r.get('cand_stem_terrain', c), tout, *tex,
            '--receipt', tdir / 'terrain.json',
            cwd=self.W, log=self.log(c, 'cand-terrain'))
        if r.get('oracle_candt'):
            check(tout, r['oracle_candt'], f'{c} candt')
        return tout

    # -- public-input helpers -------------------------------------------------
    # Pins/GC dirs are optional lane inputs; the workdir build always wins
    # when present. Every substitution below is judged fail-closed by the
    # rung oracles (recorded pins), so a wrong default can only fail loudly.
    def pin_file(self, *parts):
        if not self.a.pins:
            return None
        p = Path(self.a.pins).joinpath(*parts)
        return p if p.is_file() else None

    # -- effects census (public default) --------------------------------------
    # The pinned donor effects census (pins/scenery-effects/effects) wins
    # when present; otherwise regenerate it in-workdir with the ported
    # tk18/tricky_effects.py from the extract+ps2 rung outputs. Either way
    # the bind oracle judges the result fail-closed.
    def effects_json(self, c, r, ddir):
        wanted = self.pin_file('scenery-effects', 'effects',
                               f"{r.get('effects', r['donor'])}.json")
        if wanted is not None:
            return wanted
        out = self.W / 'effects' / f"{r.get('effects', r['donor'])}.json"
        if not out.is_file():
            # Donor files live in the stem subdir (donor/<stem>/); ddir is
            # the donor root when bind/finalw omit it, or the stem dir
            # itself from the extract return. Prefer whichever layout
            # actually holds the ps2 rung outputs.
            sdir = ddir / r['donor']
            if not (sdir / f"{r['donor']}-ps2.gsf").is_file():
                sdir = ddir
            out.parent.mkdir(parents=True, exist_ok=True)
            run('effects', sys.executable, self.T / 'tk18' / 'tricky_effects.py',
                sdir / f"{r['donor']}-ps2.gsf", sdir / f"{r['donor']}.map",
                sdir / f"{r['donor']}.pbd", out,
                cwd=self.W, log=self.log(c, 'effects'))
        return out

    # -- rung: finalw variant (mesa) ------------------------------------------
    # Rebuilds scenery from the ov-s base with the candidate flags plus
    # --show-hidden-instance (start gate) and --unprotect-instance (bridge
    # boards), then rails + the course terrain flags + the waterfall bind.
    # Proven: mesa (6c129653 -> 4c560c70 -> a489c69c -> f81f73d4).
    def rung_finalw(self, c, r, sbig, bdir, ddir):
        w = self.W / f'w-{c}-final'
        (w / 'sc').mkdir(parents=True, exist_ok=True)
        sc = w / 'sc' / f'{c.upper()}.BIG'
        out = w / f'{c.upper()}.BIG'
        fmt = dict(donor=ddir, lane=self.lane, data=self.T / 'data')
        extra = [a.format(**fmt) for a in r['scenery_extra']]
        ff = (r.get('fence_file') or '').format(c=c)
        if ff:
            for n in (self.T / 'data' / ff).read_text().split():
                extra += ['--no-collision-instance', n]
        for n in r.get('finalw_show', []):
            extra += ['--show-hidden-instance', str(n)]
        for n in r.get('finalw_unprotect', []):
            extra += ['--unprotect-instance', str(n)]
        run('finalw-scenery', sys.executable, self.T / r['scenery_tool'],
            sbig, bdir / 'experiment.json',
            ddir / f"{r['donor']}.pbd", ddir / f"{r['donor']}.ssh",
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            r.get('stem_scenery', c), sc, '--receipt', w / 'scenery.json',
            *extra, cwd=self.W, log=self.log(c, 'finalw-scenery'))
        if r.get('oracle_finalw_sc'):
            check(sc, r['oracle_finalw_sc'], f'{c} finalw-sc')
        run('finalw-rails', sys.executable, self.T / r['rails_tool'],
            sc, bdir / 'experiment.json',
            ddir / f"{r['donor']}-ps2.nbd", ddir / f"{r['donor']}-ps2.gsf",
            r.get('stem_scenery', c), out,
            cwd=self.W, log=self.log(c, 'finalw-rails'))
        if r.get('oracle_finalw_f'):
            check(out, r['oracle_finalw_f'], f'{c} finalw-f')
        tdir = self.W / f'w-{c}-finalt'
        tdir.mkdir(parents=True, exist_ok=True)
        tout = tdir / f'{c.upper()}.BIG'
        L = r['terrain_L']
        fex = dict(donor=ddir, course=c.upper(), data=self.T / 'data')
        tex = [a.format(**fex) for a in r['terrain_extra']]
        run('finalw-terrain', sys.executable, self.T / r['terrain_tool'],
            out, bdir / 'experiment.json', ddir / f"{r['donor']}.pbd",
            ddir / f"{r['donor']}.ssh", ddir / L,
            r.get('stem_terrain', c), tout, *tex,
            '--receipt', tdir / 'terrain.json',
            cwd=self.W, log=self.log(c, 'finalw-terrain'))
        if r.get('oracle_finalt'):
            check(tout, r['oracle_finalt'], f'{c} finalt')
        fdir = self.W / f'w-{c}-finalw'
        fdir.mkdir(parents=True, exist_ok=True)
        fout = fdir / f'{c.upper()}.BIG'
        effects = self.effects_json(c, r, ddir)
        run('finalw-bind', sys.executable, self.T / 'tk24c' / 'bind_effects.py',
            tout, w / 'scenery.json', effects, fout,
            '--names', *r['finalw_bind_names'], '--kind0-as-trigger',
            '--receipt', fdir / 'bind.json',
            cwd=self.W, log=self.log(c, 'finalw-bind'))
        if r.get('oracle_finalw'):
            check(fout, r['oracle_finalw'], f'{c} finalw')
        return fout

    # -- rung: bind effects --------------------------------------------------
    # bind_effects.py on the candidate (or sgt) world with the pinned donor
    # effects census + by-name sign binds. Proven on gari (sgt -> 58897da8)
    # and snow (candt -> 4eae7401).
    def rung_bind(self, c, r, world, scenery_json, ddir=None):
        out = self.W / f'w-{c}-bound' / f'{c.upper()}.BIG'
        out.parent.mkdir(parents=True, exist_ok=True)
        effects = self.effects_json(c, r, ddir or self.donor)
        run('bind', sys.executable, self.T / 'tk24c' / 'bind_effects.py',
            world, scenery_json, effects, out,
            '--names', *r['bind_names'], '--kind0-as-trigger',
            '--receipt', out.parent / 'bind.json',
            cwd=self.W, log=self.log(c, 'bind'))
        if r.get('oracle_bound'):
            check(out, r['oracle_bound'], f'{c} bound')
        return out

    # -- rung: AABB floor (worlds-final) -------------------------------------
    # aabb_floor.py --group G --level 11. The f11 output IS worlds-final
    # (proven: gari 2ba912b3, snow c850d63e).
    def rung_floor(self, c, r, world):
        out = self.W / f'w-{c}-f11' / f'{c.upper()}.BIG'
        out.parent.mkdir(parents=True, exist_ok=True)
        run('floor', sys.executable, self.T / 'tk37' / 'aabb_floor.py',
            world, out, '--group', str(r['floor_group']), '--level', '11',
            '--receipt', out.parent / 'aabb.json',
            cwd=self.W, log=self.log(c, 'floor'))
        if r.get('oracle_final'):
            check(out, r['oracle_final'], f'{c} final')
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

    # -- rung: play composite (TK37b) -----------------------------------------
    # make_composite of the floor final: the play world + the materialized
    # full world (the blend mat input). Proven: gari ec33194a/fc2939d0.
    def rung_play(self, c, r, final):
        w = self.W / f'w-{c}-play'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        mat = w / 'mat.BIG'
        run('play', sys.executable, self.T / 'tk10' / 'make_composite.py',
            self.a.ssx3_iso, final, out, '--materialized', mat,
            cwd=self.W, log=self.log(c, 'play'))
        if r.get('oracle_play'):
            check(out, r['oracle_play'], f'{c} play')
        if r.get('oracle_mat'):
            check(mat, r['oracle_mat'], f'{c} mat')
        return out, mat

    # -- rung: TK42c blend + staged composite ----------------------------------
    # patch_blend of the mat world with the course spec, then make_composite
    # to the staged TK49. Proven: gari 7b9d955a -> 1850bc0e.
    def rung_blend(self, c, r, mat):
        w = self.W / f'w-{c}-blend'
        w.mkdir(parents=True, exist_ok=True)
        bout = w / f'{c.upper()}-blend.BIG'
        spec = self.T / 'data' / r.get('blend_spec', f'blend-spec-{c}.json')
        run('blend', sys.executable, self.T / 'tk42c' / 'patch_blend.py',
            mat, bout, '--spec', spec, '--receipt', w / 'blend.json',
            cwd=self.W, log=self.log(c, 'blend'))
        if r.get('oracle_blend'):
            check(bout, r['oracle_blend'], f'{c} blend')
        sout = w / f'{c.upper()}.BIG'
        run('staged', sys.executable, self.T / 'tk10' / 'make_composite.py',
            self.a.ssx3_iso, bout, sout,
            cwd=self.W, log=self.log(c, 'staged'))
        if r.get('oracle_staged'):
            check(sout, r['oracle_staged'], f'{c} staged')
        return sout

    # -- rung: TK56 class-row rebind -------------------------------------------
    # fix_binds of the staged TK49 (materializes internally). Proven: gari
    # 1850bc0e -> 3e714aca.
    def rung_fix(self, c, r, staged):
        w = self.W / f'w-{c}-fix'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        run('fix', sys.executable, self.T / 'tk56' / 'fix_binds.py',
            self.a.ssx3_iso, staged, out, '--group', str(r['fix_group']),
            '--receipt', w / 'fix.json', '--work', w / 'work',
            cwd=self.W, log=self.log(c, 'fix'))
        if r.get('oracle_fix'):
            check(out, r['oracle_fix'], f'{c} fix')
        return out

    # -- rung: TK53 invisible break triggers (mesa) ------------------------------
    def rung_s1(self, c, r, fix):
        w = self.W / f'w-{c}-s1'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        # Public default: the workdir donor + our own finalw receipt. The s1
        # oracle judges the substitution fail-closed.
        mesa_pbd = self.donor / 'mesa' / 'mesa.pbd'
        if not mesa_pbd.is_file():
            mesa_pbd = self.need(self.pin_file('donor', 'mesa', 'mesa.pbd'),
                                 's1 mesa donor')
        mesa_sc = self.W / 'w-mesa-final' / 'scenery.json'
        if not mesa_sc.is_file():
            mesa_sc = self.need(self.pin_file('scenery-effects', 'scenery',
                                              'mesa-final-scenery.json'),
                                's1 mesa-final scenery')
        run('s1', sys.executable, self.T / 'tk53' / 'trigger_step.py',
            self.a.ssx3_iso, fix, out, '--group', str(r['fix_group']),
            '--rids', *[str(n) for n in r['tk53_rids']],
            '--donor-pbd', mesa_pbd,
            '--scenery', mesa_sc,
            '--receipt', w / 's1.json', '--work', w / 'work',
            cwd=self.W, log=self.log(c, 's1'))
        if r.get('oracle_s1'):
            check(out, r['oracle_s1'], f'{c} s1')
        return out

    # -- rung: TK57 glass panes + blocks -----------------------------------------
    # Glass specs come from a workdir-generated targets file (no tracked
    # decoded tables): make_glass_targets.py decodes the Break* functions
    # from the workdir donor + the resolved scenery receipt. The static
    # table below is lane-authored recipe data (names only), never content.
    # gari also passes --blocks. Proven: gari 3e714aca -> 51736b49.
    GLASS_COURSES = {
        'gari': ('gari', 'gari-sg', 'GARI'),
        'snow': ('snow', 'snow-cand', 'SNOW'),
        'mesa': ('mesa', 'mesa-final', 'MESA'),
        'merq': ('merquer', 'merq-cand', 'MERQ'),
        'aloha': ('aloha', 'aloha-b', 'ALOHA'),
        'elys': ('elysium', 'elys-cand', 'ELYS'),
    }

    def need(self, value, what):
        if value is None:
            raise SystemExit(f'no public input for {what}; pass --pins')
        return value

    def rung_s2(self, c, r, base):
        w = self.W / f'w-{c}-s2'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        if c not in self.GLASS_COURSES:
            raise SystemExit(f'{c}: no glass course mapping; s2 needs a GLASS_COURSES entry')
        donor_stem, scenery_name, world = self.GLASS_COURSES[c]
        # Public default: the workdir donor + our own run receipts, mapped
        # from the pins-compatible scenery names. The s2 oracle judges
        # every substitution fail-closed.
        gt_scenery = {'gari-sg': self.W / 'w-gari-sg' / 'scenery.json',
                      'snow-cand': self.W / 'w-snow-b' / 'scenery.json',
                      'mesa-final': self.W / 'w-mesa-final' / 'scenery.json',
                      'merq-cand': self.W / 'w-merq-b' / 'scenery.json',
                      'aloha-b': self.W / 'w-aloha-b' / 'scenery.json',
                      'elys-cand': self.W / 'w-elys-b' / 'scenery.json'}
        scenery = gt_scenery.get(scenery_name)
        if scenery is None or not scenery.is_file():
            scenery = self.need(self.pin_file('scenery-effects', 'scenery',
                                              f"{scenery_name}-scenery.json"),
                                f"s2 {scenery_name} scenery")
        donor_pbd = self.donor / donor_stem / f"{donor_stem}.pbd"
        if not donor_pbd.is_file():
            donor_pbd = self.need(self.pin_file('donor', donor_stem,
                                                f"{donor_stem}.pbd"),
                                  f"s2 {donor_stem} donor")
        glass_json = w / 'glass-targets.json'
        if not glass_json.is_file():
            run('s2-glass', sys.executable, self.T / 'tk57' / 'make_glass_targets.py',
                '--donor-dir', donor_pbd.parent, '--donor-stem', donor_stem,
                '--scenery-receipt', scenery, '--scenery-name', scenery_name,
                '--world', world, '--course', c, '--out', glass_json,
                cwd=self.W, log=self.log(c, 's2-glass'))
        gt = json.loads(glass_json.read_text())[c]
        cmd = [sys.executable, self.T / 'tk57' / 'interact_step.py',
               self.a.ssx3_iso, base, out, '--group', str(r['fix_group']),
               '--scenery', scenery,
               '--donor-pbd', donor_pbd,
               '--stock-bam', self.a.host_bam]
        if r.get('tk57_blocks'):
            cmd += ['--blocks', *[str(b) for b in r['tk57_blocks']]]
        cmd += ['--glass', *gt['specs'], '--receipt', w / 's2.json',
                '--work', w / 'work']
        run('s2', *cmd, cwd=self.W, log=self.log(c, 's2'))
        if r.get('oracle_s2'):
            check(out, r['oracle_s2'], f'{c} s2')
        return out

    # -- rung: TK51 hidden riding proxies -----------------------------------------
    # Census from a workdir-generated file (no tracked decoded tables):
    # make_hidden_census.py surveys the workdir donor tree (which the
    # extract rung builds); scenery comes from our own run receipts under
    # pins-compatible names, pins only as a comparison fallback.
    def rung_s3(self, c, r, base):
        w = self.W / f'w-{c}-s3'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        stem = {'merq': 'merquer'}.get(c, r['donor'])
        ddir = self.donor
        if not (ddir / stem / f'{stem}.pbd').exists():
            ddir = self.need(Path(self.a.pins) / 'donor'
                             if self.a.pins else None, f's3 {stem} donor')
        # Public default: our own run receipts under pins-compatible names
        # (s3 reads <name>-scenery.json for the course's own entry; mesa
        # borrows aloha-b, so aloha must run first). The s3 oracle judges.
        compat = self.W / 'scenery-compat'
        compat.mkdir(parents=True, exist_ok=True)
        need = {'gari': ('gari-f', 'w-gari-b'), 'snow': ('snow-f', 'w-snow-b'),
                'aloha': ('aloha-b', 'w-aloha-b'), 'merq': ('merq-d', 'w-merq-b'),
                # mesa-final is mesa's own finalw receipt (tk51 config's
                # anchor 1443/1400 rejects anything else fail-closed).
                'mesa': ('mesa-final', 'w-mesa-final')}.get(c)
        scenery_dir = Path(self.a.pins) / 'scenery-effects' / 'scenery' \
            if self.a.pins else None
        if need is not None:
            own = self.W / need[1] / 'scenery.json'
            link = compat / f'{need[0]}-scenery.json'
            if own.is_file() and (scenery_dir is None or
                                  not (scenery_dir / f'{need[0]}-scenery.json').is_file()):
                if link.is_symlink() or link.exists():
                    link.unlink()
                link.symlink_to(own)
                scenery_dir = compat
        if scenery_dir is None:
            if c == 'untrack':
                # tk51 config gates the only scenery-dir read on a truthy
                # scenery name; untrack's entry is None, so the dir is
                # never read. The (empty) compat dir is behavior-identical.
                scenery_dir = compat
            else:
                raise SystemExit(f'{c}: no s3 scenery receipt; pass --pins')
        census_json = w / 'hidden-census.json'
        if not census_json.is_file():
            run('s3-census', sys.executable, self.T / 'tk51' / 'make_hidden_census.py',
                '--donor-dir', ddir / stem, '--stem', stem,
                '--out', census_json,
                cwd=self.W, log=self.log(c, 's3-census'))
        run('s3', sys.executable, self.T / 'tk51' / 'patch_hidden_colliders.py',
            base, out, '--course', c,
            '--census', census_json,
            '--scenery-dir', scenery_dir,
            '--donor-dir', ddir,
            '--ssx3-iso', self.a.ssx3_iso,
            cwd=self.W, log=self.log(c, 's3'))
        if r.get('oracle_s3'):
            check(out, r['oracle_s3'], f'{c} s3')
        return out

    # -- rung: TK58 exact convex hull rebuild ---------------------------------------
    # Untracked has no hull config: s3 passes through byte-identical (TKP5).
    def rung_s4(self, c, r, base):
        w = self.W / f'w-{c}-s4'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        stem = {'merq': 'merquer', 'elys': 'elysium'}.get(c, r['donor'])
        ddir = self.donor
        if not (ddir / stem).is_dir():
            ddir = self.need(Path(self.a.pins) / 'donor'
                             if self.a.pins else None, f's4 {stem} donor')
        if c == 'untrack':
            shutil.copy(base, out)
            assert sha256(out) == sha256(base), 'untrack s4 copy'
            print(f'{c} s4 copy (no hull config)', flush=True)
            return out
        run('s4', sys.executable, self.T / 'tk58' / 'fix_hulls.py',
            base, out, '--course', c, '--donor-dir', ddir,
            '--ssx3-iso', self.a.ssx3_iso,
            '--receipt', w / 's4.json',
            cwd=self.W, log=self.log(c, 's4'))
        if r.get('oracle_s4'):
            check(out, r['oracle_s4'], f'{c} s4')
        return out

    # -- rung: TKP5 relocate ----------------------------------------------------------
    # Programs = TK56 fix receipt plan (moved) + s3 patch.json (created).
    # Pipe/Mega pass through (nothing to relocate). Proven: gari -> 991929fe.
    # The relocate group is the TKP5 relocate.sh group() map, NOT the TK56
    # fix group: they coincide except untrack (reloc 158, no TK56 fix).
    def rung_s5(self, c, r, base, staged, fix_receipt):
        w = self.W / f'w-{c}-s5'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        progs = []
        if fix_receipt and Path(fix_receipt).exists():
            plan = json.loads(Path(fix_receipt).read_text()).get('plan', [])
            progs += [str(p['program_moved_to']) for p in plan
                      if p.get('program_moved_to') is not None]
        patch = self.W / f'w-{c}-s3' / f'{c.upper()}.patch.json'
        if patch.exists():
            cb = json.loads(patch.read_text()).get('class_binding')
            if cb:
                progs.append(str(cb['create_program']))
        if not progs:
            shutil.copy(base, out)
            print(f'{c} s5 copy (nothing to relocate)', flush=True)
        else:
            run('s5', sys.executable, self.T / 'tkp5' / 'relocate_step.py',
                self.a.ssx3_iso, base, out, '--group',
                str(r.get('s5_group', r['fix_group'])),
                '--programs', *progs, '--reference', staged,
                '--receipt', w / 's5.json', '--work', w / 'work',
                cwd=self.W, log=self.log(c, 's5'))
        if TKP5_FINALS.get(c):
            check(out, TKP5_FINALS[c], f'{c} s5-final')
        return out

    # -- rung: tk-routes world-data patches (post-golden follow-up) ---------------
    # route_step.py (tkroutes port of tk-routes-1 tooling) on the s5-final
    # play world. Aloha covers3 (group 22: A per-wedge --rids + B/C whole
    # --cover-rids), merq wall2819 (group 148: --rids 2819), mega tube94s2
    # (group 94: boost --rid 94 --program 34 --slot 2). Mega's s5 is unwired
    # (pending mega-h/u2/c tail: needs mega-line-tex20 plus the floor->s5
    # wiring), so with no chain-built base the rung records a skip instead
    # of reading any outside reference. Outputs are the post-routes golden.
    def rung_routes(self, c, r, base):
        if base is None:
            print(f'{c} routes skipped (no chain base: mega tail pending)', flush=True)
            return None
        w = self.W / f'w-{c}-routes'
        w.mkdir(parents=True, exist_ok=True)
        out = w / f'{c.upper()}.BIG'
        run('routes', sys.executable, self.T / 'tkroutes' / 'route_step.py',
            self.a.ssx3_iso, base, out, '--group', str(r['routes_group']),
            '--receipt', w / 'routes.json', '--work', w / 'work',
            *r['routes_args'],
            cwd=self.W, log=self.log(c, 'routes'))
        if r.get('oracle_routes'):
            check(out, r['oracle_routes'], f'{c} routes-final')
        return out

    # -- global: COURSPIT preview chain -------------------------------------------
    # Frozen COURSPIT from the stock ISO, then one preview_step per course
    # (members are disjoint, order fixed). Proven: 3332b0af (TKP5).
    PREVIEW_ORDER = ['pipe', 'untrack', 'alaska', 'aloha', 'elys', 'gari',
                     'mega', 'merq', 'mesa', 'snow']

    def run_preview(self):
        cp = self.W / 'cp'
        cp.mkdir(parents=True, exist_ok=True)
        base = cp / 'COURSPIT-base.BIG'
        if not base.exists():
            run('extract-courspic', sys.executable,
                SHARED / 'inspect_disc.py', self.a.ssx3_iso,
                '--extract', 'DATA/UI/COURSPIC.BIG', '--output', base,
                cwd=self.W, log=self.log('preview', 'extract'))
        # Proven (run6): the 10 preview steps fully overwrite TK9's slots,
        # so stock COURSPIC is the base; no frozen intermediate is needed.
        check(base, STOCK_COURSPIC, 'courspic stock')
        cur = base
        for c in self.PREVIEW_ORDER:
            out = cp / f'COURSPIT-{c}.BIG'
            run(f'preview-{c}', sys.executable,
                self.T / 'tkp5' / 'preview_step.py',
                '--courspit', cur, '--donor-iso', self.a.tricky_iso,
                '--course', c, '--out', out,
                '--receipt', cp / f'preview-{c}.json',
                cwd=self.W, log=self.log('preview', c))
            cur = out
        check(cur, TKP5_COURSPIT, 'courspit final')
        agg = cp / 'COURSPIT.BIG'
        shutil.copy2(cur, agg)
        print(f'preview done {agg}', flush=True)

    def run_course(self, c, upto='all'):
        assert c in COURSES, c
        r = COURSES[c]
        order = RUN_ORDER
        if upto != 'all':
            order = order[:order.index(upto) + 1]
        ddir = bdir = rbig = sbig = bbig = cbig = None
        sgt = bound = final = finalw = None
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
        if 'resets' in order:
            sbig = self.rung_resets(c, r, rbig, bdir, ddir)
        if 'scenery' in order:
            bbig = self.rung_scenery(c, r, sbig, bdir, ddir)
        if 'terrain' in order:
            if r.get('terrain_tool'):
                cbig = self.rung_terrain(c, r, bbig, bdir, ddir)
            else:
                print(f'{c} terrain skipped (mega-h/u2/c pending tex20)',
                      flush=True)
        if 'cand' in order:
            if r.get('cand_extra'):
                cbig = self.rung_cand(c, r, sbig, bdir, ddir)
            else:
                print(f'{c} cand skipped (not wired)', flush=True)
        if 'sg' in order:
            if r.get('sg_show'):
                sgt = self.rung_sg(c, r, sbig, bdir, ddir)
            else:
                print(f'{c} sg skipped (no sg_show)', flush=True)
        if 'finalw' in order:
            if r.get('finalw_show'):
                finalw = self.rung_finalw(c, r, sbig, bdir, ddir)
            else:
                print(f'{c} finalw skipped (no finalw_show)', flush=True)
        if 'bind' in order:
            if r.get('bind_names') and r.get('bind_base'):
                if r['bind_base'] == 'sgt':
                    wworld = sgt
                elif r['bind_base'] == 'candt':
                    wworld = cbig
                elif r['bind_base'] == 'c':
                    wworld = cbig
                elif r['bind_base'] == 'finalw':
                    wworld = finalw
                else:
                    raise SystemExit(f'{c}: unknown bind_base {r["bind_base"]}')
                bs = r.get('bind_scenery', 'cand' if r.get('cand_extra') else 'b')
                sjson = self.W / f'w-{c}-{bs}' / 'scenery.json' \
                    if bs in ('sg', 'cand', 'final') \
                    else self.W / f'w-{c}-b' / 'scenery.json'
                bound = self.rung_bind(c, r, wworld, sjson, ddir)
            else:
                print(f'{c} bind skipped (not wired)', flush=True)
        if 'floor' in order:
            if bound is not None and r.get('floor_group'):
                final = self.rung_floor(c, r, bound)
            else:
                print(f'{c} floor skipped (not wired)', flush=True)
        if 'composite' in order:
            if r.get('composite_tool'):
                tk49 = self.rung_composite(c, r, cbig)
            else:
                tk49 = None
                print(f'{c} composite skipped (TK42c staging pending)',
                      flush=True)
        else:
            tk49 = None
        latest = None
        staged = None
        fix_receipt = None
        if 'play' in order:
            if final is not None and r.get('oracle_play'):
                play, mat = self.rung_play(c, r, final)
                latest = play
            else:
                print(f'{c} play skipped (no floor final)', flush=True)
        if 'blend' in order:
            if latest is not None and r.get('oracle_blend'):
                staged = self.rung_blend(c, r, mat)
                latest = staged
            elif r.get('oracle_staged') and tk49 is not None:
                staged = tk49
                latest = staged
                print(f'{c} blend skipped (staged == TK49)', flush=True)
            else:
                print(f'{c} blend skipped (not wired)', flush=True)
        if 'fix' in order:
            if staged is not None and r.get('fix_group'):
                latest = self.rung_fix(c, r, staged)
                fix_receipt = str(self.W / f'w-{c}-fix' / 'fix.json')
            else:
                print(f'{c} fix skipped (TK56 unchanged)', flush=True)
        if 's1' in order:
            if r.get('tk53_rids'):
                latest = self.rung_s1(c, r, latest)
            else:
                print(f'{c} s1 skipped (mesa only)', flush=True)
        if 's2' in order:
            if r.get('oracle_s2'):
                latest = self.rung_s2(c, r, latest)
            else:
                print(f'{c} s2 skipped (no glass)', flush=True)
        if 's3' in order:
            if r.get('oracle_s3'):
                latest = self.rung_s3(c, r, latest)
            else:
                print(f'{c} s3 skipped (no proxies)', flush=True)
        if 's4' in order:
            if r.get('oracle_s4') or c == 'untrack':
                latest = self.rung_s4(c, r, latest)
            else:
                print(f'{c} s4 skipped (no hulls)', flush=True)
        if 's5' in order:
            if latest is not None and TKP5_FINALS.get(c):
                # s5 output feeds the routes rung (tk-routes-2 follow-up).
                latest = self.rung_s5(c, r, latest, staged, fix_receipt)
            else:
                print(f'{c} s5 skipped (not wired)', flush=True)
        if 'routes' in order:
            if r.get('routes_args'):
                self.rung_routes(c, r, latest)
            else:
                print(f'{c} routes skipped (not a routes course)', flush=True)
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
    ap.add_argument('--lane-dir', default=None,
                    help='legacy lane recipe dir (unused: recipes live in tools/ssx3/tricky/data)')
    ap.add_argument('--pins', default=None,
                    help='optional pinned lane inputs; the workdir build wins when present')
    a = ap.parse_args()
    ch = Chain(a)
    ensure_world_report(ch)
    if a.course == 'preview':
        ch.run_preview()
    else:
        ch.run_course(a.course, a.rung)


if __name__ == '__main__':
    main()
