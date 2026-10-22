#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Back up the live lobby's persistent data (/srv/ssx3-lobby/data) to the Mac.
# Safe to run while the lobby serves: the game rewrites its JSON files per
# event (Session.json may lag an in-flight race by seconds; accounts,
# personas and highscores are stable). Tars on the host first so only one
# file crosses SSH and nothing is ever deleted by a computed path.
# Usage: infra/backup-lobby.sh [--host=IP] [--user=ssx3] [--dest=DIR]
set -eu

HOST=""
USER="ssx3"
DEST="${DEST:-./lobby-backups}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"

for arg in "$@"; do
  case "$arg" in
    --host=*) HOST="${arg#--host=}" ;;
    --user=*) USER="${arg#--user=}" ;;
    --dest=*) DEST="${arg#--dest=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$HOST" ]; then
  HOST="$(tofu -chdir="$SCRIPT_DIR/oci" output -raw lobby_address)"
fi
TARGET="$USER@$HOST"

DAY="$(date -u +%Y-%m-%d)"
mkdir -p "$DEST/$DAY"
ssh $SSH_OPTS "$TARGET" 'tar -czf /tmp/ssx3-lobby-data.tgz -C /srv/ssx3-lobby data'
scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$TARGET:/tmp/ssx3-lobby-data.tgz" "$DEST/$DAY/data.tgz"
ssh $SSH_OPTS "$TARGET" 'rm /tmp/ssx3-lobby-data.tgz'
echo "backed up to $DEST/$DAY/data.tgz"
ls -la "$DEST/$DAY"
