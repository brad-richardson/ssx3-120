#!/bin/sh
# Deploy the MComm quickplay lobby to the OCI host.
# Builds the image on this Mac for linux/arm64, ships it over SSH,
# then starts the compose stack on the host. Idempotent: re-running
# rebuilds, reloads and recreates the container with the same config.
# Usage: infra/deploy-lobby.sh [--dry-run] [--host=IP] [--user=ssx3]
#        [--mcomm-dir=PATH]
# SSHes in as the ssx3 user that cloud-init sets up (docker group, owns
# /srv/ssx3-lobby, carries the same SSH key as ubuntu).
set -eu

HOST=""
USER="ssx3"
# MComm is never published; operators with their own checkout pass --mcomm-dir.
MCOMM_DIR="${MCOMM_DIR:-}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"
DRY_RUN=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --host=*) HOST="${arg#--host=}" ;;
    --user=*) USER="${arg#--user=}" ;;
    --mcomm-dir=*) MCOMM_DIR="${arg#--mcomm-dir=}" ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

if [ -z "$HOST" ]; then
  HOST="$(tofu -chdir="$SCRIPT_DIR/oci" output -raw lobby_address)"
fi
if [ -z "$MCOMM_DIR" ]; then
  echo "missing MComm checkout: pass --mcomm-dir=PATH" >&2; exit 2
fi
TARGET="$USER@$HOST"
IMAGE="ssx3-onl6-mcomm:quickplay"
REMOTE_DIR="/srv/ssx3-lobby"

step() { printf '%s\n' "$*"; }

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    step "would run: $*"
  else
    step "run: $*"
    "$@"
  fi
}

ssh_run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    step "would run (ssh $TARGET): $*"
  else
    step "run (ssh $TARGET): $*"
    ssh $SSH_OPTS "$TARGET" "$@"
  fi
}

step "deploy MComm quickplay to $TARGET (mcomm: $MCOMM_DIR)"
step "1. build image $IMAGE for linux/arm64"
run sh "$MCOMM_DIR/docker/build.sh"

step "2. ship image to host"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run: docker save $IMAGE | ssh $TARGET docker load"
else
  step "run: docker save $IMAGE | ssh $TARGET docker load"
  docker save "$IMAGE" | ssh $SSH_OPTS "$TARGET" docker load
fi

step "3. copy compose file and config (advertise address $HOST)"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run: ssh $TARGET mkdir -p $REMOTE_DIR"
  step "would run: scp $SCRIPT_DIR/lobby-compose.yaml $TARGET:$REMOTE_DIR/compose.yaml"
  step "would run (ssh $TARGET): printf MCOMM_ADVERTISE_IP=$HOST > $REMOTE_DIR/.env"
else
  ssh $SSH_OPTS "$TARGET" "mkdir -p $REMOTE_DIR"
  scp -i "$SSH_KEY" -o IdentitiesOnly=yes "$SCRIPT_DIR/lobby-compose.yaml" "$TARGET:$REMOTE_DIR/compose.yaml"
  ssh $SSH_OPTS "$TARGET" "printf 'MCOMM_ADVERTISE_IP=$HOST\n' > $REMOTE_DIR/.env"
fi

step "4. preflight: docker compose plugin on host"
if [ "$DRY_RUN" -eq 1 ]; then
  step "would run (ssh $TARGET): docker compose version (fails loudly if missing)"
else
  if ssh $SSH_OPTS "$TARGET" docker compose version; then
    step "ok: docker compose present on host"
  else
    step "FAIL: docker compose missing on $TARGET (cloud-init installs docker-compose-v2)"
    exit 1
  fi
fi

step "5. start stack"
ssh_run sh -c "'cd $REMOTE_DIR && docker compose up -d'"

# 11000 (lobby) and 13505 (buddy) listen from boot. 10901 (game) is opened by
# MComm only for ~5 s after a client's @dir on 11000, so it is not probed
# directly; a refused 10901 is expected when idle. The lobby needs a few
# seconds to start after `compose up`, so each port is retried for ~60 s.
step "6. health check TCP 11000/13505 on $HOST (10901 opens on demand)"
for port in 11000 13505; do
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

step "done."
