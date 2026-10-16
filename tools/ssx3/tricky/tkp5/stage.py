#!/usr/bin/env python3
"""TKP5: stage the Tricky preset at ~/dev/ssx3-work/odin-play-tricky-tkp5/ (device layout of odin-play/tricky/) and the
Mac twin env/manifest under ~/dev/ssx3-work/TKP5/, then diff against the current device preset.

    stage.py WORLDS_DIR COURSPIT MENU_DIR GEMS_TSV --receipt R.json
Every new file is copied, read back twice and SHA-checked; unchanged preset files are APFS clones (cp -c) of
odin-play/tricky/. tricky.env and m-tricky.txt keep every setting; only their header comments name TKP5.
"""
import argparse, hashlib, json, shutil, subprocess
from pathlib import Path

HOME = Path.home()
CUR = HOME / 'dev/ssx3-work/odin-play/tricky'
OUT = HOME / 'dev/ssx3-work/odin-play-tricky-tkp5'
MAC = HOME / 'dev/ssx3-work/TKP5'
DEV = '/storage/emulated/0/Android/data/com.ps2x.runner/files'
COURSES = ['ALASKA', 'ALOHA', 'ELYS', 'GARI', 'MEGA', 'MERQ', 'MESA', 'PIPE', 'SNOW', 'UNTRACK']
NOTE = ('# TKP5 (10-07): TK56 class rows, TK53 Mesa cover triggers, TK57 glass + Garibaldi Roller blocks, TK51 riding proxies,\n'
        '# TK58 exact hulls, TKP5 event-stub relocation; real Tricky loading/menu previews (all courses); TK52 gems on Alaska/Pipedream.\n')


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def sha2(p):
    a, b = sha(p), sha(p)
    assert a == b, p
    return a


def put(src, rel, items):
    dst = OUT / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    s = sha2(src)
    shutil.copyfile(src, dst)
    assert sha2(dst) == s
    items[rel] = dict(source=str(src), sha256=s, bytes=dst.stat().st_size)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('worlds'); ap.add_argument('courspit'); ap.add_argument('menu'); ap.add_argument('gems')
    ap.add_argument('--receipt', required=True)
    a = ap.parse_args()
    assert not OUT.exists(), f'{OUT} exists: use a fresh stage'
    items = {}
    for c in COURSES:
        put(Path(a.worlds) / f'{c}.BIG', f'tricky-ov/DATA/WORLDS/{c}.BIG', items)
    put(a.courspit, 'tricky-ov/DATA/UI/COURSPIT.BIG', items)
    for p in sorted(Path(a.menu, 'DATA/UI').glob('TP*.SSH')):
        put(p, f'tricky-ov/DATA/UI/{p.name}', items)
    put(a.gems, 'tricky-gems.tsv', items)
    for rel in ('speech-tricky-big', 'music-tricky-big', 'music2-tricky-big', 'trickyhud.bin', 'tricky-song.wav'):
        s = sha2(CUR / rel)
        subprocess.run(['cp', '-c', str(CUR / rel), str(OUT / rel)], check=True)
        assert sha2(OUT / rel) == s
        items[rel] = dict(source=str(CUR / rel), sha256=s, bytes=(OUT / rel).stat().st_size, unchanged=True)
    # env + manifest: settings identical, header note added
    for rel in ('tricky.env', 'm-tricky.txt'):
        old = (CUR / rel).read_text()
        (OUT / rel).write_text(NOTE + old)
        items[rel] = dict(source=str(CUR / rel), sha256=sha2(OUT / rel), bytes=(OUT / rel).stat().st_size,
                          settings_identical=[l for l in old.splitlines() if l and not l.startswith('#')]
                          == [l for l in (OUT / rel).read_text().splitlines() if l and not l.startswith('#')])
        assert items[rel]['settings_identical']
    # Mac twins (device paths -> staged dir)
    for rel, mrel in (('tricky.env', 'tricky-mac.env'), ('m-tricky.txt', 'm-tricky-mac.txt')):
        txt = (OUT / rel).read_text().replace(DEV + '/m-tricky.txt', str(MAC / 'm-tricky-mac.txt')).replace(DEV, str(OUT))
        assert '/storage/' not in txt
        (MAC / mrel).write_text(txt)
    # SHA256SUMS + diff against the current device preset
    lines = [f"{v['sha256']}  {k}" for k, v in sorted(items.items())]
    (OUT / 'SHA256SUMS').write_text('\n'.join(lines) + '\n')
    cur = {str(p.relative_to(CUR)): sha2(p) for p in CUR.rglob('*') if p.is_file() and not p.name.startswith('.')}
    diff = []
    for k in sorted(set(cur) | set(items)):
        o, n = cur.get(k), items.get(k, {}).get('sha256')
        diff.append(dict(path=k, current=o, staged=n,
                         change='added' if o is None else 'removed' if n is None else 'same' if o == n else 'changed'))
    rc = dict(stage=str(OUT), mac_env=str(MAC / 'tricky-mac.env'), mac_manifest=str(MAC / 'm-tricky-mac.txt'),
              files=items, diff_vs_device_preset=diff)
    Path(a.receipt).write_text(json.dumps(rc, indent=2) + '\n')
    for d in diff:
        print(f"{d['change']:8} {d['path']:40} {(d['current'] or '-')[:16]} -> {(d['staged'] or '-')[:16]}")


if __name__ == '__main__':
    main()
