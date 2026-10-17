# ssx3-lobby

Quick-play matchmaker for SSX 3 plus an optional UDP relay ("proxy
only" scope). Clean-room phase-1 server: login, the buddy subset and
quick-play pairing, per `docs/research/ssx3-online-protocol.md` in the
ssx3 repo and PRO1's fixtures. **No upstream contact. Never point this
at EA's, ssxor's or any public server.**

Licence: GPL-3.0-or-later, the same as the rest of this repository
(see the top-level `LICENSE`).

## Layout

- `crates/proto`: framing, message types, encode/decode (no deps).
- `crates/lobby`: the `lobby` binary (tokio, one task per connection).
- `crates/lobby/tests`: PRO1 fixture replays.
- `tests/` unit + property tests live next to the code they cover;
  the two-client pairing/failure tests are `#[cfg(test)]` in
  `crates/lobby/src/main.rs` (in-process server, ephemeral ports).

## Run (shipped: static binary + systemd; Brad 10-13, no Docker)

```sh
# cross-build static musl binaries (needs cargo-zigbuild + zig)
cargo zigbuild --release --target aarch64-unknown-linux-musl   # Oracle
cargo zigbuild --release --target x86_64-unknown-linux-musl    # self-hosted x86_64 tests
```

Deploy (the Oracle step runs with the orchestrator):

```sh
./deploy-lobby.sh user@host   # scp binary+unit, enable+restart, health-check
```

The unit (`ssx3-lobby.service`, the single canonical unit) runs as a dedicated
`ssx3-lobby` user (created by `deploy-lobby-rust.sh`) with
`ProtectSystem=strict`, `NoNewPrivileges=yes`, an empty
`CapabilityBoundingSet`, `RestrictAddressFamilies=AF_INET AF_INET6`,
`MemoryMax=512M`, `TasksMax=256`, and journald logging. Phase 1 keeps
everything in memory, so no `StateDirectory` is declared. Config lives at
`/etc/ssx3-lobby/lobby.toml` (read-only to the service); set
`advertise_ip` there (or via `LOBBY_ADVERTISE_IP`).

Dev loop on the Mac:

```sh
cp lobby.example.toml lobby.toml   # set advertise_ip
cargo run -p ssx3-lobby -- lobby.toml
```

Ports 11000/10901/13505 (TCP); relay UDP range 3658-3756 when
`relay.mode = "always"` (the default); Quick Play netplay on UDP 3757
when `[state_sync] enable = true` (120<->120 pairing at the race card, a
shared GO instant, STATE relay, canonical results; see
`crates/lobby/src/state_sync.rs`). The client HLE sends a random
per-install DID, routes to the assigned relay port, and proves its
relay role. Names are throwaway and receive numeric suffixes on
collision. The journal contains health events and counters only;
names, addresses, passwords, device IDs and tokens stay in memory.

## Docker (tests only)

```sh
docker build -t ssx3-lobby:phase1 .
LOBBY_ADVERTISE_IP=192.0.2.1 docker compose -f lobby-compose.yaml up -d
```

`lobby-compose.yaml` is a drop-in for ssx3 `infra/lobby-compose.yaml`
(same TCP ports, plus the relay UDP range). The arm64/amd64 images are
test artifacts; the shipped artifact is the static binary above.
