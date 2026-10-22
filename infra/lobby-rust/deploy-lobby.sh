#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Deploy the static lobby binary + systemd unit to a host (e.g. Oracle).
# Does NOT pick a host by itself: pass user@host explicitly. The Oracle
# step runs with the orchestrator; do not point this at anything else.
#
# Usage: deploy-lobby.sh user@host [binary] [unit] [config]
#   binary defaults to target/aarch64-unknown-linux-musl/release/lobby
#   unit   defaults to ssx3-lobby.service (the single canonical unit)
#   config defaults to lobby.example.toml (installed only if the host has
#          no /etc/ssx3-lobby/lobby.toml yet; edit advertise_ip there)
set -euo pipefail
DEST=${1:?usage: deploy-lobby.sh user@host [binary] [unit] [config]}
BIN=${2:-target/aarch64-unknown-linux-musl/release/lobby}
UNIT=${3:-ssx3-lobby.service}
CFG=${4:-lobby.example.toml}
for f in "$BIN" "$UNIT" "$CFG"; do
  [ -f "$f" ] || { echo "missing: $f"; exit 2; }
done
HASH=$(shasum -a 256 "$BIN" | awk '{print $1}')
echo "== copy binary + unit to $DEST"
scp "$BIN" "$DEST:/tmp/lobby.new"
scp "$UNIT" "$DEST:/tmp/ssx3-lobby.service.new"
scp "$CFG" "$DEST:/tmp/lobby.toml.example"
ssh "$DEST" "LOBBY_EXPECTED_HASH=$HASH sh -s" <<'REMOTE'
  set -e
  if [ -x /usr/local/bin/ssx3-lobby ]; then sudo cp -p /usr/local/bin/ssx3-lobby /usr/local/bin/ssx3-lobby.previous; fi
  sudo install -m 0755 /tmp/lobby.new "/usr/local/bin/ssx3-lobby.$LOBBY_EXPECTED_HASH"
  sudo ln -sfn "/usr/local/bin/ssx3-lobby.$LOBBY_EXPECTED_HASH" /usr/local/bin/ssx3-lobby
  sudo install -m 0644 /tmp/ssx3-lobby.service.new /etc/systemd/system/ssx3-lobby.service
  sudo mkdir -p /etc/ssx3-lobby
  if [ ! -f /etc/ssx3-lobby/lobby.toml ]; then
    sudo install -m 0644 /tmp/lobby.toml.example /etc/ssx3-lobby/lobby.toml
    echo "installed example config; EDIT /etc/ssx3-lobby/lobby.toml (advertise_ip) before starting"
  fi
  rm -f /tmp/lobby.new /tmp/ssx3-lobby.service.new /tmp/lobby.toml.example
  sudo systemctl daemon-reload
  sudo systemctl enable ssx3-lobby.service
  sudo systemctl restart ssx3-lobby.service
  sleep 3
  sudo systemctl is-active ssx3-lobby.service
  pid=$(sudo systemctl show ssx3-lobby.service --property=MainPID --value)
  test "$pid" -gt 0
  actual=$(sudo sha256sum "/proc/$pid/exe" | awk '{print $1}')
  test "$actual" = "$LOBBY_EXPECTED_HASH"
  echo "running binary SHA-256: $actual"
REMOTE
echo "== health check: @dir on 11000"
python3 -c "
import socket, sys
s = socket.create_connection(('$DEST'.split('@')[1], 11000), timeout=10)
body = b'PROD=SSX-PS2-2004\nLANG=en\nSLUS=SLUS_20772\n\x00'
s.sendall(b'@dir' + b'\x00\x00\x00\x00' + (12 + len(body)).to_bytes(4, 'big') + body)
d = s.recv(512)
ok = d[:4] == b'@dir' and b'PORT=10901' in d
print('healthy' if ok else 'UNHEALTHY: ' + repr(d[:60]))
sys.exit(0 if ok else 1)
"
