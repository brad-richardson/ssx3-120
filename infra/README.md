# Self-hosted SSX 3 quick-play lobby (public infra tree)

This tree builds one small Oracle Cloud Always Free Ampere A1 (arm64)
server that runs the Rust `ssx3-lobby` matchmaker + UDP relay as a
systemd unit. Game clients connect to it, get matched, then race
through the relay (no direct peer IPs). Nothing here points at any
third-party server.

What you need: an Oracle Cloud tenancy (Always Free), `opentofu`,
`oci-cli`, an SSH key, and a Rust toolchain to build the lobby.

## 1. One-time auth

```
brew install opentofu oci-cli
oci setup bootstrap
```

`oci setup bootstrap` writes `~/.oci/config` (`DEFAULT` profile). The
OpenTofu files in `oci/` read that profile; no passwords or keys go
into the repo.

## 2. Configure

```
cd oci
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: region, compartment_ocid, availability_domain,
# ssh_public_key_path, admin_cidrs (your home/VPN only) and player_cidrs
# (empty = no player ingress until you stage it in)
```

## 3. Create the host

```
tofu init
tofu plan
tofu apply
```

Note the `lobby_address` it prints. Back up the state file (it can
hold sensitive values and is gitignored):

```
./backup-state.sh
```

Out of host capacity is routine on the free tier: the network is
created even when the instance fails. Retry with
`./retry-instance.sh [hours]`, which tries each availability domain.

## 4. Build the lobby

Build the static lobby binary from the vendored source tree
(`lobby-rust/`; run `cargo test --locked --workspace` there first),
then deploy:

```
cd lobby-rust
cargo zigbuild --release --target aarch64-unknown-linux-musl
cd ..
./deploy-lobby-rust.sh --host=<lobby_address> --binary=<path-to-lobby>
```

`deploy-lobby-rust.sh` creates the dedicated `ssx3-lobby` user,
installs the binary + unit + `lobby-rust/lobby.toml` (edit
`advertise_ip` to your host's public IPv4 first), opens the relay UDP
range in ufw, enables/starts the unit, attests the running binary
hash, and health-checks TCP 11000/10901/13505. Dry run:

```
./deploy-lobby-rust.sh --dry-run --host=<lobby_address> --binary=<path>
```

Point clients at the host (hostname or IPv4 literal). Roll back to a
previous binary by re-running with that binary; the prior install is
kept as `/usr/local/bin/ssx3-lobby.previous`.

## 5. Operate

- Status: `systemctl is-active ssx3-lobby.service`; TCP listeners
  11000/10901/13505 and the Quick Play UDP listener 3657 (a fixed port just
  below the relay range; 3757 stays open during the transition for installed
  clients). Relay UDP ports bind per pair on demand, so no other
  UDP listener when idle is normal.
- Logs: `journalctl -u ssx3-lobby.service` (health events and relay
  counters only; names, IPs and tokens stay in memory).
- Harden SSH + auto-upgrades: `./harden-host.sh --host=<addr>`.
- Back up state: `./backup-state.sh` (the Rust lobby keeps no data
  dir; a unit restart drops logins and active races).
- Failing closed: with empty `player_cidrs`, nothing is open.
  `admin_cidrs` stays restricted to your home/VPN; it can never be
  world-open (validation rejects it).

## 6. Opening stages

Who can connect is decided in exactly one place: the security list
(`player_cidrs` for game ports, `admin_cidrs` for SSH). Suggested
stages: your home IP only, then named friend CIDRs for a scheduled
pilot, then — after rate limits, alerts and a rollback rehearsal —
public `0.0.0.0/0` on `player_cidrs` only. SSH stays restricted.

## 7. Cost guard

`oci/budget.tf` alerts at the configured monthly amount (default
$10). The recipient address lives only in your local, gitignored
`terraform.tfvars`. An alert is not a spend cap: monitor egress and
be ready to close ingress.

## Files

| Path | Purpose |
| --- | --- |
| `oci/` | VCN, instance, budget, cloud-init; `terraform.tfvars.example` template |
| `deploy-lobby-rust.sh` | Ship binary + unit + config, attest, health-check |
| `rollback-lobby-mcomm.sh` | Legacy MComm fallback (only if you run MComm; MComm itself is never published) |
| `deploy-lobby.sh` | Legacy MComm deploy (needs your own MComm checkout via `--mcomm-dir`) |
| `harden-host.sh` | sshd + unattended-upgrades on the live host |
| `backup-lobby.sh`, `backup-state.sh` | Data/state backups to `./lobby-backups`, `./infra-state-backup` |
| `retry-instance.sh`, `check-cloud-init.sh` | Capacity retry; first-boot static guard |
| `lobby-compose.yaml` | Legacy MComm compose (reference only) |
| `lobby-rust/` | Vendored lobby source + service unit + production config template (set `advertise_ip`) |
