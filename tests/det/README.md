# Public determinism gate (`tests/det/`)

Four det keys against your own disc. After any guest-affecting change all
four must be IDENTICAL; new behavior needs a new key, never a silent
re-mint. Guest hashes don't prove graphics or cost: pair them with fixed
captures (`docs/facts.md`).

## One command (Mac host)

```sh
./tests/det/mac_gate.sh --work ~/ssx3-work --iso ~/SSX3.iso
```

`--work` holds prepare/mac-libs/mac outputs plus the gate dir
(`det-gate-<engine7>/` with the PASS/FAIL table, logs and run dirs). Steps:
knob checks, build (prepare unless `inputs.json` exists, mac-libs, mac with
suite), engine-root suite, runner-dir invariant, one det boot per profile
(`reference`, `play`, `full120events`, `play120`) compared against the
shipped keys.

## Keys (`keys/<key>/`)

Each key: `manifest.json` (pins: backend, route, stop/sound/coverage/hash
cadence, codegen register, lib SHAs, disc SHAs, engine ancestry) plus the
extracted comparison artifacts (`hashes.bin` guest-hash lines,
`coverage.txt`, `snd.txt` tick rows). Full key dirs (runner binaries,
frames) are not shipped. Resolution: newest key per profile whose `fork`
(engine) commit is an ancestor of the checked-out engine.

Comparison (`compare.py`) ports the canonical hash/snd/coverage logic, so a
public IDENTICAL equals a canonical IDENTICAL on the same bytes.

Recorded, not gated (build-environment bytes vary by host): backend dylib
SHAs and the codegen registration-table SHA. Registration ownership can
move without guest change; only guest hashes, snd rows and coverage fail
the gate. All three are recorded per profile in the gate dir.

## Tools

- `boot/ssx3_boot.py` — bounded Mac boot (det/speed) on a route; needs
  `--runner/--iso/--elf/--codegen` (+ `--ge1-lib/--microvu-lib` for the
  external/microVU path). Uses the lab build-slot lease when importable,
  otherwise runs unleased.
- `boot/baseline.py` — key store ops (`put/get/key-for/list/compare/make/pins`)
  against `--store` (default: the shipped keys).
- `boot/pm1_macthreads.c` — Mac thread-count helper for the harness.
- `knob_matrix.py --check` — the signed-off play set must be effective in
  every shipped preset layer (`--rev` pins the engine).
- `knob_registry.py --check` — every runtime `PS2X_/GE1_` literal registered
  with class and owner (`knob_registry.tsv`).
- `hashdiff.py` — `[det-hash:v1]` stream comparator (verbatim lab logic).

## Key management

Mint a candidate with `baseline.py make --pins <pins.json> --runner <path>
--iso/--elf/--codegen/--ge1-lib/--microvu-lib ...`, compare with `compare.py`,
and ship the extracted artifacts (hashes/coverage/snd + manifest) after the
gate passes at the pinned engine. Key pins must name the engine commit, the
generator, and the codegen register SHA.
