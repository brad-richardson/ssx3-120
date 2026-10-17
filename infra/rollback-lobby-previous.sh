#!/bin/sh
# Roll back the Oracle lobby host to the previous Rust ssx3-lobby.
# Restores /usr/local/bin/ssx3-lobby.previous (saved by
# infra/deploy-lobby-rust.sh) and /etc/ssx3-lobby/lobby.toml.previous,
# then restarts the unit, attests the running hash and checks TCP 11000.
# Usage: infra/rollback-lobby-previous.sh [--dry-run] [--host=IP] [--user=ssx3]
set -eu

HOST=""
USER="ssx3"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"
DRY_RUN=0
SERVICE="ssx3-lobby.service"

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

step() { printf '%s\n' "$*"; }

ssh_run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    step "would run (ssh $TARGET, sudo): $*"
  else
    step "run (ssh $TARGET, sudo): $*"
    ssh $SSH_OPTS "$TARGET" sudo "$@"
  fi
}

step "roll back $TARGET to ssx3-lobby.previous"
if [ "$DRY_RUN" -eq 0 ]; then
  ssh $SSH_OPTS "$TARGET" "sudo sh -c 'test -x /usr/local/bin/ssx3-lobby.previous && test -f /etc/ssx3-lobby/lobby.toml.previous'" \
    || { step "FAIL: previous binary/config not present"; exit 1; }
fi
ssh_run ln -sfn /usr/local/bin/ssx3-lobby.previous /usr/local/bin/ssx3-lobby
ssh_run cp -p /etc/ssx3-lobby/lobby.toml.previous /etc/ssx3-lobby/lobby.toml
ssh_run systemctl daemon-reload
ssh_run systemctl restart "$SERVICE"
if [ "$DRY_RUN" -eq 0 ]; then
  sleep 3
  ssh $SSH_OPTS "$TARGET" sudo "systemctl is-active $SERVICE"
  ssh $SSH_OPTS "$TARGET" 'sudo sha256sum /usr/local/bin/ssx3-lobby' | awk '{print "restored binary SHA-256: " $1}'
  python3 -c "
import socket, sys
s = socket.create_connection(('$HOST', 11000), timeout=10)
body = b'PROD=SSX-PS2-2004\nLANG=en\nSLUS=SLUS_20772\n\x00'
s.sendall(b'@dir' + b'\x00\x00\x00\x00' + (12 + len(body)).to_bytes(4, 'big') + body)
d = s.recv(512)
ok = d[:4] == b'@dir' and b'PORT=10901' in d
print('healthy' if ok else 'UNHEALTHY: ' + repr(d[:60]))
sys.exit(0 if ok else 1)"
fi
step "done."
