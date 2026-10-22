#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# verify-turnip.sh — verify the pinned Turnip release assets before/after use.
# 1. Downloads binary + notices from tools/ssx3/turnip.json URLs.
# 2. Enforces the pinned SHA-256 (same gate as tools/ssx3/build.py). Archive
#    pins verify the download, then the extracted `library` member's SHA.
# 3. Verifies GitHub build-provenance attestation for assets we publish.
#    Third-party releases carry `"attested": false`: no attestation exists,
#    so the double SHA pin is the integrity check (see trust note in the
#    README and the manifest `source` field).
# Fail-closed: any mismatch aborts. Requires: curl, python3, gh (logged in
# for attestation verify; SHA check needs no auth).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
MANIFEST=$ROOT/tools/ssx3/turnip.json
WORK=${1:-$(mktemp -d /tmp/turnip-verify-XXXXXX)}
mkdir -p "$WORK"
echo "manifest: $MANIFEST"
echo "work: $WORK"
mget() { # manifest-key field [default]
  python3 -c "import json;e=json.load(open('$MANIFEST'))['$1'];print(e.get('$2','${3-}'))"
}
fetch() { # url outfile
  curl -sSL -o "$2" "$1"
}
shais() { # file
  shasum -a 256 "$1" | cut -d' ' -f1
}
attest() { # manifest-key file
  if [ "$(mget "$1" attested true)" = "False" ]; then
    echo "$1 attestation SKIPPED (unattested pin; SHA is the check — see manifest source)"
  else
    gh attestation verify "$2" --owner brad-richardson
    echo "$1 attestation OK"
  fi
}
check_raw() { # manifest-key filename
  key=$1
  want=$(mget "$key" sha256)
  [ ${#want} -eq 64 ] || { echo "bad sha pin for $key" >&2; exit 1; }
  fetch "$(mget "$key" url)" "$WORK/$2"
  got=$(shais "$WORK/$2")
  [ "$got" = "$want" ] || { echo "$key SHA MISMATCH got=$got want=$want" >&2; exit 1; }
  echo "$key sha OK $got"
  attest "$key" "$WORK/$2"
}
check_archived() { # manifest-key filename
  key=$1
  wantarc=$(mget "$key" archive_sha256)
  wantso=$(mget "$key" sha256)
  lib=$(mget "$key" library)
  [ ${#wantarc} -eq 64 ] || { echo "bad archive sha pin for $key" >&2; exit 1; }
  [ ${#wantso} -eq 64 ] || { echo "bad driver sha pin for $key" >&2; exit 1; }
  [ -n "$lib" ] || { echo "no library member for $key" >&2; exit 1; }
  fetch "$(mget "$key" url)" "$WORK/$2.zip"
  gotarc=$(shais "$WORK/$2.zip")
  [ "$gotarc" = "$wantarc" ] || { echo "$key ARCHIVE SHA MISMATCH got=$gotarc want=$wantarc" >&2; exit 1; }
  echo "$key archive sha OK $gotarc"
  python3 - "$WORK/$2.zip" "$lib" "$WORK/$2" <<'EOF'
import sys, zipfile
from pathlib import Path
zpath, lib, out = sys.argv[1], sys.argv[2], sys.argv[3]
if lib.startswith('/') or '..' in Path(lib).parts:
    sys.exit('unsafe Turnip library path: ' + lib)
with zipfile.ZipFile(zpath) as z:
    if lib not in z.namelist():
        sys.exit('Turnip library missing from archive: ' + lib)
    Path(out).write_bytes(z.read(lib))
EOF
  gotso=$(shais "$WORK/$2")
  [ "$gotso" = "$wantso" ] || { echo "$key DRIVER SHA MISMATCH got=$gotso want=$wantso" >&2; exit 1; }
  echo "$key driver sha OK $gotso"
  attest "$key" "$WORK/$2.zip"
}
check() { # manifest-key filename
  if [ -n "$(mget "$1" archive_sha256)" ]; then
    check_archived "$1" "$2"
  else
    check_raw "$1" "$2"
  fi
}
check binary libvulkan_freedreno.so
check notices turnip-notices.zip
echo VERIFY-OK
