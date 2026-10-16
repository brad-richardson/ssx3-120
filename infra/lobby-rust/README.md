# ssx3-lobby (LICENSE-TBD)

Quick-play matchmaker for SSX 3 plus an optional UDP relay ("proxy
only" scope). Clean-room phase-1 server: login, the buddy subset and
quick-play pairing, per `docs/research/ssx3-online-protocol.md` in the
ssx3 repo and PRO1's fixtures. **No upstream contact. Never point this
at EA's, ssxor's or any public server.**

Publishing and the licence are Brad's call later; this file reserves
the decision (`LICENSE-TBD`).

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
cargo zigbuild --release --target x86_64-unknown-linux-musl    # bradflix tests
```

Deploy (the Oracle step runs with the orchestrator):

```sh
./deploy-lobby.sh user@host   # scp binary+unit, enable+restart, health-check
```

The unit (`systemd/ssx3-lobby.service`) runs as `DynamicUser=yes` with
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

Ports 11000/10901/13505 (TCP); relay UDP range 3658-3757 when
`relay.mode = "always"` (the default). The client HLE sends a random
per-install DID, routes to the assigned relay port, and proves its
relay role. Names are throwaway and receive numeric suffixes on
collision. The journal contains health events and counters only;
names, addresses, passwords, device IDs and tokens stay in memory.

## Docker (tests only)

```sh
docker build -t ssx3-lobby:phase1 .
LOBBY_ADVERTISE_IP=127.0.0.1 docker compose -f lobby-compose.yaml up -d
```

`lobby-compose.yaml` is a drop-in for ssx3 `infra/lobby-compose.yaml`
(same TCP ports, plus the relay UDP range). The arm64/amd64 images are
test artifacts; the shipped artifact is the static binary above.
