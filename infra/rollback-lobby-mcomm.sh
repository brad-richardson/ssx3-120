#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Roll back the Oracle lobby host from the Rust ssx3-lobby to MComm.
# Stops (and disables) the ssx3-lobby systemd unit, then restarts the
# MComm compose stack in /srv/ssx3-lobby (image + compose file were left
# in place by infra/deploy-lobby-rust.sh). MComm data is untouched.
# Usage: infra/rollback-lobby-mcomm.sh [--dry-run] [--host=IP] [--user=ssx3]
set -eu

HOST=""
USER="ssx3"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --host=*) HOST="${arg#--host=}" ;;
    --user=*) USER="${arg#--user=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -z "$HOST" ]; then
  HOST="$(tofu -chdir="$SCRIPT_DIR/oci" output -raw lobby_address)"
fi
TARGET="$USER@$HOST"
REMOTE_DIR="/srv/ssx3-lobby"
SERVICE="ssx3-lobby.service"

step() { printf '%s\n' "$*"; }

step "roll back $TARGET to MComm quickplay"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run (ssh $TARGET, sudo): systemctl disable --now $SERVICE"
  step "would run (ssh $TARGET): cd $REMOTE_DIR && docker compose up -d"
  step "would run: python3 tcp connect $HOST:11000 and :13505 (retry up to 60 s)"
else
  step "1. stop and disable $SERVICE"
  ssh $SSH_OPTS "$TARGET" sudo "systemctl disable --now $SERVICE || echo 'unit not active'"
  step "2. restart the MComm stack"
  ssh $SSH_OPTS "$TARGET" "cd $REMOTE_DIR && docker compose up -d"
  step "3. health check TCP 11000/13505 on $HOST (retry up to 60 s each)"
  for port in 11000 13505; do
    if python3 -c "
import socket, sys, time
for _ in range(12):
    try:
        socket.create_connection(('$HOST', $port), timeout=5).close(); sys.exit(0)
    except OSError:
        time.sleep(5)
sys.exit(1)"; then
      step "ok: $HOST:$port reachable"
    else
      step "FAIL: $HOST:$port unreachable after 60 s"
      exit 1
    fi
  done
fi

step "done."
