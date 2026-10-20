# AGENTS.md — for coding agents and contributors

Setup runbook: [docs/agent-setup.md](docs/agent-setup.md). It covers the
host toolchain, the engine submodule, doctor, and build + install. The
README's agent prompt points there; follow it exactly and do not improvise
toolchains, flags, or signing.

## Repo layout

- `build` — one-command wrapper (needs only `uv`; `make android ISO=…` wraps it).
- `build.py` — product entrypoint (forwards to `tools/ssx3/` with the pinned engine).
- `tools/ssx3/` — builder, doctor, host recipes, font/shader transforms.
- `games/ssx3/` — game metadata (TOML + function map) for code generation.
- `presets/` — stock/120/platform env layers staged onto device.
- `apps/android/` — complete Gradle app shell (own package id, own keystore).
- `apps/` — iOS/Desktop platform glue arrives with the resource-ownership step.
- `deps/PS2Recomp` — pinned engine submodule (runtime, recompiler, tests).
- `deps.lock.json` — every pin: engine, generator, vendor, toolchains, archive identity.
- `tests/det/` — the public determinism gate: four keys checked against your own disc.
- `tests/` — no-disc product checks (run on every push).
- `tools/replay/`, `tools/bench/` — pad record/replay scripts, generic Android bench.
- `infra/` — operator-side lobby infra (terraform, deploy/harden/backup scripts, service files).
- `docs/` — build/input/troubleshooting guides, verified facts, engine timing research.

This repo is the product home with a pinned engine; the game-module seam
lands after v0.

## Where the build steps live

- `tools/ssx3/build.py` — doctor, prepare, android-libs, android, ios-libs,
  ios, tricky, stage, install. The `./build` wrapper runs it through `uv`.
- `presets/` — shipped env layers (`ps2x.env`, `full120.env`, iOS variants,
  `online.env`) staged onto the device.
- `apps/android/` — Gradle app shell; `apps/` holds the other platform glue.
- Full builder reference: [tools/ssx3](tools/ssx3/README.md). Platform
  quirks: [docs/android.md](docs/android.md),
  [docs/ios.md](docs/ios.md).

## How to run the tests

CI runs no-disc product checks on every push (see
`.github/workflows/product-checks.yml`). Run the same set locally from the
repo root, with the engine submodule checked out
(`git submodule update --init -- deps/PS2Recomp`):

```sh
pip install numpy pillow
test "$(git -C deps/PS2Recomp rev-parse HEAD)" = "$(python3 -c "import json; print(json.load(open('deps.lock.json'))['engine']['pin'])")"
for t in tools/ssx3/tests/test_*.py; do python3 "$t" || exit 1; done
python3 tools/ssx3/test_prebuilt.py
python3 tests/test_contract_stable.py
python3 tests/provenance/check_manifest.py
python3 tests/test_symbols_map.py
for s in $(git ls-files '*.sh'); do
  if head -1 "$s" | grep -q bash; then bash -n "$s"; else sh -n "$s"; fi
done
python3 -m py_compile build.py tools/ssx3/build.py
```

Every test file signals by exit code: 0 is pass, nonzero is fail. After any
guest-affecting change the four keys must be IDENTICAL (`reference`,
`play`, `full120events`, `play120`):
`./tests/det/mac_gate.sh --work ~/ssx3-work --iso ~/SSX3.iso`. New
behavior needs a new key and sign-off — never a silent re-mint.

## Rules

- Bring your own disc: the user supplies their USA `SSX 3 (USA).iso`
  (`SLUS_207.72`). Never commit game data, ISOs, generated code, cards,
  keystores, or provisioning profiles. The work directory stays outside
  this checkout and private.
- Do not commit disc bytes, ISOs/ELFs, extracted or converted packs,
  generated game code (`--work/codegen`), memory cards, keystores, or
  built apps.
- On iPhone/iPad, never uninstall the app — that wipes saves too. Upgrade
  with a plain install.
- Back up your Android signing key
  (`~/.config/ssx3-120/android-signing.keystore`): a different key forces
  an uninstall, which wipes saves.
- The builder resumes: `prepare` and the `*-libs` steps are skipped when
  their outputs are current. Re-run the same command to continue — and do
  not stack a build and a build+install: one `--install` command builds
  (or resumes) and installs.
- If a step fails, read its error to the end and fix the named cause.
  Do not retry a failed Xcode/Gradle step with different flags hoping it
  passes.
