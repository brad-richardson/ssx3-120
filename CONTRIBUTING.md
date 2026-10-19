# Contributing

Short version: bring your own disc, keep game bytes out of the repo, run the checks, keep PRs small.

## Bring your own disc

This repo contains no game data. Build from your own USA SSX 3 disc (`SLUS_207.72`; see `docs/ssx3-inputs.md`), plus your own USA SSX Tricky disc (`SLUS-20326`; see `docs/tricky-inputs.md`) only when you want the optional Tricky layer. Keep your ISO, your private `--work` directory (disc image, generated game code, cards, signing keys), and all logs outside this checkout.

## Never commit game data or derived files

Do not commit disc bytes, ISOs/ELFs, extracted or converted packs, generated game code (`--work/codegen`), memory cards, keystores, or built apps. Build outputs, caches, and OS noise are already ignored (see `.gitignore`); generated sources live outside the repo via the private work directory.

## How to run the checks

CI runs no-disc product checks on every push (see `.github/workflows/product-checks.yml`). Run the same set locally from the repo root:

```sh
for t in tools/ssx3/tests/test_*.py; do python3 "$t"; done
python3 tools/ssx3/test_prebuilt.py
python3 tests/test_contract_stable.py
python3 tests/provenance/check_manifest.py
python3 tests/test_symbols_map.py
```

All of them must pass with no disc and no device. Name new tests `tools/ssx3/tests/test_*.py` (builder) or extend the matching suite so CI picks them up.

## PR flow

Small focused PRs are easiest to review. Fill in `.github/pull_request_template.md` (what/why, how tested, device). CI must pass; a maintainer merges.
