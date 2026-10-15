#!/bin/sh
# Deploy the Rust ssx3-lobby to the Oracle host, replacing MComm.
# Copies the static binary + systemd unit + production config over SSH,
# installs them, stops the MComm compose stack (image and compose file
# stay on the host so MComm is restorable), restarts the unit, attests
# the running binary hash, then health-checks all three TCP listeners.
# MComm data (/srv/ssx3-lobby/data) is untouched.
# Usage: infra/deploy-lobby-rust.sh [--dry-run] [--host=IP] [--user=ssx3]
#        [--binary=PATH] [--unit=PATH] [--config=PATH]
set -eu

HOST=""
USER="ssx3"
# Build the lobby from source (see README); pass the static binary explicitly.
BINARY="${BINARY:-}"
UNIT=""
CONFIG=""
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"
DRY_RUN=0
RELAY_UDP="3658:3757"

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --host=*) HOST="${arg#--host=}" ;;
    --user=*) USER="${arg#--user=}" ;;
    --binary=*) BINARY="${arg#--binary=}" ;;
    --unit=*) UNIT="${arg#--unit=}" ;;
    --config=*) CONFIG="${arg#--config=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -z "$UNIT" ] && UNIT="$SCRIPT_DIR/lobby-rust/ssx3-lobby.service"
[ -z "$CONFIG" ] && CONFIG="$SCRIPT_DIR/lobby-rust/lobby.toml"

if [ -z "$HOST" ]; then
  HOST="$(tofu -chdir="$SCRIPT_DIR/oci" output -raw lobby_address)"
fi
TARGET="$USER@$HOST"
REMOTE_DIR="/srv/ssx3-lobby"
SERVICE="ssx3-lobby.service"
APP_USER="ssx3-lobby"

if [ -z "$BINARY" ]; then echo "missing: pass --binary=PATH to the built lobby binary" >&2; exit 2; fi
for f in "$BINARY" "$UNIT" "$CONFIG"; do
  if [ ! -f "$f" ]; then echo "missing: $f" >&2; exit 2; fi
done
EXPECTED_HASH="$(shasum -a 256 "$BINARY" | awk '{print $1}')"

step() { printf '%s\n' "$*"; }

ssh_run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    step "would run (ssh $TARGET, sudo): $*"
  else
    step "run (ssh $TARGET, sudo): $*"
    ssh $SSH_OPTS "$TARGET" sudo "$@"
  fi
}

step "deploy Rust ssx3-lobby to $TARGET (binary: $BINARY)"
step "1. create dedicated user $APP_USER (idempotent)"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run (ssh $TARGET, sudo): id $APP_USER || useradd --system --no-create-home --shell /usr/sbin/nologin $APP_USER"
else
  ssh $SSH_OPTS "$TARGET" "sudo sh -c 'id $APP_USER || useradd --system --no-create-home --shell /usr/sbin/nologin $APP_USER'"
fi

step "2. ship binary + unit + config to host"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run: scp $BINARY $UNIT $CONFIG to $TARGET:/tmp/"
else
  scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$BINARY" "$TARGET:/tmp/ssx3-lobby.new"
  scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$UNIT" "$TARGET:/tmp/ssx3-lobby.service.new"
  scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$CONFIG" "$TARGET:/tmp/lobby.toml.new"
fi

step "3. install binary, unit and production config"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run (ssh $TARGET, sudo): preserve current lobby binary when present"
else
  ssh $SSH_OPTS "$TARGET" "sudo sh -c 'if [ -x /usr/local/bin/ssx3-lobby ]; then cp -p /usr/local/bin/ssx3-lobby /usr/local/bin/ssx3-lobby.previous; fi'"
fi
ssh_run install -m 0755 /tmp/ssx3-lobby.new "/usr/local/bin/ssx3-lobby.$EXPECTED_HASH"
ssh_run ln -sfn "/usr/local/bin/ssx3-lobby.$EXPECTED_HASH" /usr/local/bin/ssx3-lobby
ssh_run install -m 0644 /tmp/ssx3-lobby.service.new /etc/systemd/system/ssx3-lobby.service
ssh_run mkdir -p /etc/ssx3-lobby
ssh_run install -m 0644 -o root -g root /tmp/lobby.toml.new /etc/ssx3-lobby/lobby.toml
if [ "$DRY_RUN" -eq 0 ]; then
  ssh $SSH_OPTS "$TARGET" "rm -f /tmp/ssx3-lobby.new /tmp/ssx3-lobby.service.new /tmp/lobby.toml.new"
fi

step "4. open relay UDP range $RELAY_UDP/udp in the host firewall (idempotent)"
ssh_run ufw allow "$RELAY_UDP/udp"

step "5. daemon-reload, stop MComm (image + compose file stay for rollback)"
ssh_run systemctl daemon-reload
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run (ssh $TARGET): cd $REMOTE_DIR && docker compose down (only if the stack exists)"
else
  ssh $SSH_OPTS "$TARGET" "cd $REMOTE_DIR && docker compose down || echo 'no MComm stack running'"
fi

step "6. enable, restart and attest $SERVICE"
ssh_run systemctl enable "$SERVICE"
ssh_run systemctl restart "$SERVICE"
if [ "$DRY_RUN" -eq 0 ]; then
  sleep 3
  ssh $SSH_OPTS "$TARGET" sudo "systemctl is-active $SERVICE"
  RUNNING_HASH="$(ssh $SSH_OPTS "$TARGET" 'pid=$(sudo systemctl show ssx3-lobby.service --property=MainPID --value); test "$pid" -gt 0 && sudo sha256sum "/proc/$pid/exe"' | awk '{print $1}')"
  if [ "$RUNNING_HASH" != "$EXPECTED_HASH" ]; then
    step "FAIL: running binary hash differs from deployed binary"
    exit 1
  fi
  step "running binary SHA-256: $RUNNING_HASH"
else
  step "would attest /proc/MainPID/exe SHA-256 against $EXPECTED_HASH"
fi

# All three TCP listeners bind at startup. Each is retried for ~60 s.
step "7. health check TCP 11000/10901/13505 on $HOST (retry up to 60 s each)"
for port in 11000 10901 13505; do
  if [ "$DRY_RUN" -eq 1 ]; then
    step "would run: python3 tcp connect $HOST:$port (retry up to 60 s)"
  else
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
  fi
done

if [ "$DRY_RUN" -eq 0 ]; then
  step "8. @dir probe on 11000 (lobby speaks the login flow)"
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
