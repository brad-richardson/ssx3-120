#!/usr/bin/env python3
"""Cross-check our ISO inventory/extraction against macOS bsdtar.

Reads the original images and staged archives; writes only a small JSON report.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess


def sha256(stream):
    h = hashlib.sha256()
    while data := stream.read(4 * 1024 * 1024):
        h.update(data)
    return h.hexdigest()


def verify(report_path, archive_path):
    report = json.loads(report_path.read_text())
    iso = report['source']
    listing = subprocess.check_output(['bsdtar', '-tf', iso], text=True).splitlines()
    our_files = {e['path'] for e in report['files']}
    directories = {'.'}
    for path in our_files:
        directories.update(str(p) for p in PurePosixPath(path).parents)
    if set(listing) - directories != our_files:
        raise ValueError('ISO file list differs from bsdtar')
    proc = subprocess.Popen(['bsdtar', '-xOf', iso, report['archive']['path']], stdout=subprocess.PIPE)
    try:
        original_hash = sha256(proc.stdout)
    finally:
        proc.stdout.close()
        returncode = proc.wait()
    if returncode:
        raise ValueError('bsdtar extraction failed')
    with archive_path.open('rb') as f:
        staged_hash = sha256(f)
    if original_hash != staged_hash:
        raise ValueError('Staged archive differs from bsdtar extraction')
    return dict(iso=iso, file_count=len(our_files), inventory_matches_bsdtar=True,
                archive=str(archive_path), archive_sha256=staged_hash,
                staged_archive_matches_bsdtar=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--staging', type=Path, required=True)
    ap.add_argument('--reports', type=Path, default=Path('local/reports'))
    args = ap.parse_args()
    results = []
    for game, path in [('ssx3', 'source/ssx3/BAM.BIG'), ('tricky', 'source/tricky/GARI.BIG')]:
        result = verify(args.reports / f'{game}-disc.json', args.staging / path)
        print(f'{game}: ISO listing and staged archive match bsdtar', flush=True)
        results.append(result)
    (args.reports / 'verification.json').write_text(json.dumps(results, indent=2) + '\n')


if __name__ == '__main__':
    main()
