#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Back up the local OpenTofu state to private pinned storage (never git).
# Usage: infra/backup-state.sh
set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
STATE="${STATE:-$SCRIPT_DIR/oci/terraform.tfstate}"
DEST="${DEST:-./infra-state-backup}"

if [ ! -f "$STATE" ]; then
  echo "no state file yet: $STATE (nothing to back up)"
  exit 0
fi

mkdir -p "$DEST"
cp -p "$STATE" "$DEST/terraform.tfstate"
if [ -f "$STATE.backup" ]; then
  cp -p "$STATE.backup" "$DEST/terraform.tfstate.backup"
fi
echo "backed up to $DEST"
ls -la "$DEST"
