#!/bin/bash
# Create only the lobby instance, retrying across the region's ADs until
# Always Free A1 capacity appears ("Out of host capacity" is routine on the
# free tier). The network must already exist (a first `tofu apply` creates it
# even when the instance fails). Usage: infra/retry-instance.sh [hours] [sleep-s]
set -u
cd "$(dirname "$0")/oci" || exit 2
HOURS=${1:-12} PAUSE=${2:-100}
REGION=$(sed -nE 's/^region *= *"([^"]+)".*/\1/p' terraform.tfvars)
PREFIX=$(sed -nE 's/^availability_domain *= *"([^"]+)-AD-[0-9]+".*/\1/p' terraform.tfvars)
[ -n "$PREFIX" ] || { echo "availability_domain not set in terraform.tfvars"; exit 2; }
end=$(( $(date +%s) + HOURS*3600 )); n=0
while [ "$(date +%s)" -lt "$end" ]; do
  for ad in 1 2 3; do
    n=$((n+1))
    out=$(tofu apply -no-color -auto-approve -target=oci_core_instance.lobby \
          -var "availability_domain=$PREFIX-AD-$ad" 2>&1)
    if tofu state list | grep -q '^oci_core_instance.lobby$'; then
      sed -i '' -E "s#^(availability_domain *= *\"$PREFIX-AD-)[0-9]+#\1$ad#" terraform.tfvars
      echo "$(date '+%H:%M:%S') attempt $n: created in $REGION AD-$ad"
      tofu output -no-color; exit 0
    fi
    if echo "$out" | grep -qE "Out of host capacity|TooManyRequests"; then
      echo "$(date '+%H:%M:%S') attempt $n AD-$ad: no capacity"
    else
      echo "$out" | tail -25; exit 1
    fi
    sleep "$PAUSE"
  done
done
echo "no capacity after ${HOURS} h"; exit 3
