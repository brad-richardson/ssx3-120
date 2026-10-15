#!/bin/sh
# Harden the live lobby host to the SEC2 (M10) settings. cloud-init's
# user_data is ignored after creation, so rebuild settings in
# oci/cloud-init.yaml are mirrored here for the running host.
# Idempotent: re-running writes the same files and restarts the same
# services. Only touches sshd config and unattended-upgrades; never the
# firewall or the admin_cidrs/player_cidrs security list.
# Usage: infra/harden-host.sh [--host=IP] [--user=ssx3]
set -eu

HOST=""
USER="ssx3"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/oci_ssx3}"
SSH_OPTS="-i $SSH_KEY -o IdentitiesOnly=yes -o ConnectTimeout=10"

for arg in "$@"; do
  case "$arg" in
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

echo "harden $TARGET (sshd + unattended-upgrades)"
ssh $SSH_OPTS "$TARGET" 'set -eu
  printf "%s\n" "PasswordAuthentication no" "PermitRootLogin no" | sudo tee /etc/ssh/sshd_config.d/60-ssx3-lobby.conf > /dev/null
  sudo sshd -T | grep -Ei "^(passwordauthentication|permitrootlogin) "
  sudo systemctl restart sshd
  sudo apt-get install -y unattended-upgrades
  printf "%s\n" "APT::Periodic::Update-Package-Lists \"1\";" "APT::Periodic::Unattended-Upgrade \"1\";" | sudo tee /etc/apt/apt.conf.d/20auto-upgrades > /dev/null
  printf "%s\n" "Unattended-Upgrade::Automatic-Reboot \"true\";" "Unattended-Upgrade::Automatic-Reboot-Time \"03:00\";" | sudo tee /etc/apt/apt.conf.d/50unattended-upgrades-local > /dev/null
  sudo systemctl enable unattended-upgrades
  systemctl is-enabled unattended-upgrades
  echo "harden ok"'
