#!/bin/sh
# SPDX-License-Identifier: GPL-3.0-or-later OR MIT
# Static guard for the INF1 first-boot fixes plus the SEC2 (M10/M9)
# hardening. No cloud access needed. Fails loudly if cloud-init regresses:
# wrong compose package, firewall ordering that would wipe Docker's chains,
# a missing deploy preflight, or missing sshd/auto-upgrade/compose-pin config.
# Usage: infra/check-cloud-init.sh
set -eu

DIR="$(cd "$(dirname "$0")" && pwd)"
CLOUD="$DIR/oci/cloud-init.yaml"
DEPLOY="$DIR/deploy-lobby.sh"
COMPOSE="$DIR/lobby-compose.yaml"
FAIL=0

fail() { echo "FAIL: $*"; FAIL=1; }
ok() { echo "ok: $*"; }

if grep -q 'docker-compose-plugin' "$CLOUD"; then
  fail "cloud-init still names docker-compose-plugin (not in Ubuntu archive)"
else
  ok "no docker-compose-plugin"
fi
if grep -q -- '- docker-compose-v2' "$CLOUD"; then
  ok "uses Ubuntu docker-compose-v2 package"
else
  fail "cloud-init missing docker-compose-v2 package"
fi

FLUSH_LINE=$(grep -n 'iptables, -F' "$CLOUD" | head -1 | cut -d: -f1)
START_LINE=$(grep -n 'enable, --now, docker' "$CLOUD" | head -1 | cut -d: -f1)
if [ -n "$FLUSH_LINE" ] && [ -n "$START_LINE" ] && [ "$FLUSH_LINE" -lt "$START_LINE" ]; then
  ok "iptables flush (line $FLUSH_LINE) precedes first docker start (line $START_LINE)"
else
  fail "iptables flush must precede first docker start"
fi

LAST_RUNCMD=$(grep -n -- '- \[' "$CLOUD" | tail -1 | cut -d: -f1)
if sed -n "${LAST_RUNCMD}p" "$CLOUD" | grep -q 'restart, docker'; then
  ok "systemctl restart docker is the last runcmd entry (line $LAST_RUNCMD)"
else
  fail "systemctl restart docker must be the last runcmd entry"
fi

if grep -q 'docker compose version' "$DEPLOY" && grep -q 'docker compose missing' "$DEPLOY"; then
  ok "deploy script has a loud docker-compose preflight"
else
  fail "deploy script missing loud docker-compose preflight"
fi

if grep -v '^#' "$CLOUD" | grep -q 'allowed_cidrs'; then
  fail "cloud-init must not render per-CIDR rules (security list owns sources)"
else
  ok "cloud-init renders no per-CIDR rules"
fi

# SEC2 (M10): explicit SSH hardening (no reliance on image defaults).
if grep -v '^#' "$CLOUD" | grep -q '^ssh_pwauth: false'; then
  ok "ssh_pwauth explicitly false"
else
  fail "cloud-init must set ssh_pwauth: false explicitly"
fi
if grep -v '^#' "$CLOUD" | grep -q '^disable_root: true'; then
  ok "disable_root explicitly true"
else
  fail "cloud-init must set disable_root: true explicitly"
fi
if grep -q 'sshd -T' "$CLOUD"; then
  ok "first-boot sshd audit present"
else
  fail "cloud-init missing first-boot sshd audit"
fi

# SEC2 (M10): unattended-upgrades pinned, not just installed.
if grep -q '20auto-upgrades' "$CLOUD" && grep -q 'Unattended-Upgrade' "$CLOUD"; then
  ok "unattended-upgrades config pinned"
else
  fail "cloud-init must pin unattended-upgrades config"
fi

# SEC2 (M9): compose resource caps beyond memory.
if [ -f "$COMPOSE" ]; then
  if grep -q 'pids_limit' "$COMPOSE" && grep -q 'cpus' "$COMPOSE"; then
    ok "compose sets cpus and pids_limit"
  else
    fail "compose must set cpus and pids_limit"
  fi
else
  fail "compose file missing: $COMPOSE"
fi

exit "$FAIL"
