# Contributing

Short version: bring your own disc, keep game bytes out of the repo, run the checks, keep PRs small.

## Bring your own disc

This repo contains no game data. Build from your own USA SSX 3 disc (`SLUS_207.72`; see `docs/ssx3-inputs.md`), plus your own USA SSX Tricky disc (`SLUS-20326`; see `docs/tricky-inputs.md`) only when you want the optional Tricky layer. Keep your ISO, your private `--work` directory (disc image, generated game code, cards, signing keys), and all logs outside this checkout.

## Never commit game data or derived files

Do not commit disc bytes, ISOs/ELFs, extracted or converted packs, generated game code (`--work/codegen`), memory cards, keystores, or built apps. Build outputs, caches, and OS noise are already ignored (see `.gitignore`); generated sources live outside the repo via the private work directory.

## How to run the checks

CI runs no-disc product checks on every push (see `.github/workflows/product-checks.yml`). Run the same set locally from the repo root, with the engine submodule checked out (`git submodule update --init -- deps/PS2Recomp`):

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

All of them must pass with no disc and no device. The `|| exit 1` keeps
the loop fail-fast: without it a passing last test hides an earlier
failure. Name new tests `tools/ssx3/tests/test_*.py` (builder) or extend the matching suite so CI picks them up. New shell scripts must pass the syntax loop; new Python entry points belong in the `py_compile` list.

## Licensing new files

<!-- REUSE-IgnoreStart -->
Your contribution is licensed MIT OR GPL-3.0-or-later, like the rest of the repo (README → License). Start new source files with an SPDX header (`// SPDX-License-Identifier: GPL-3.0-or-later OR MIT`, or `#` in scripts); data and config types are covered by `REUSE.toml`. Code from another project goes under `third_party/` with its own licence and notice. `pipx run reuse lint` must report no issues.
<!-- REUSE-IgnoreEnd -->

## PR flow

Small focused PRs are easiest to review. Fill in `.github/pull_request_template.md` (what/why, how tested, device). CI must pass; a maintainer merges.
