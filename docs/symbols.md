# SSX 3 symbol map

`games/ssx3/ssx3-symbols.tsv` is the project's guest function/data map. It
supersedes an earlier internal labels file (not shipped here). One row per address,
sorted by address (numeric ascending, then symbolic `R+0x…`/`P+0x…` offsets
alphabetically). Names, addresses and roles only — no game bytes.

## Columns

| Column | Meaning |
| --- | --- |
| address | EE RAM address (`0x…`, lowercase, no leading zeros) or struct offset (`R+0x…`, `P+0x…`, `S+0x…`, `A+0x…`, `C+0x…`) |
| kind | `code` (function or hook/call PC) or `data` (global, table, struct field, constant word) |
| name | `snake_case` identifier; alternate names live in the role text as `alt:` / `disputed:` |
| role | One-line role. May end with `engine: <const> (<file:line>)` naming the engine hook/const that uses it |
| source | origin tag: `engine`, a research `§Labels` section, `labels.tsv:<id>`, `nm1:<id>`, or `decomp:…` |
| evidence | what established the row: an in-repo `file:line`, `<id> notes` (internal research attribution, not shipped here), or `prior labels/candidates/map` (a superseded input, not shipped here) |
| confidence | See below |

Sub-`0x100000` `code` rows are VU micro-memory program PCs (a separate
address space), not EE addresses. `mmio_site_*` rows are MMIO override
points from `games/ssx3/ssx3.toml`; `resume_entry_*` rows are boot-proven
indirect-call targets from its `extra_function_starts`.

## Confidence levels

- `verified` — the role was established by codegen read, trace, watch or
  boot evidence. Engine-hook rows confirmed the same way also land here.
- `decomp-unverified` — an ssxdecomp name not yet confirmed by research.
  Prefer the researched name on conflicts; the decomp name stays as `disputed:`.
- `inferred` — a single-observation candidate (NM1 harvests, engine-derived
  hook sites and stub names). Good enough to hook, not a confirmed identity.

## How contributors add rows

Record the function, hook PC or RAM global (address, name, evidence) and
add the map row in the same commit series. The merge keeps the
better-evidenced name on conflicts and records the other as `disputed:`
with both citations. `tests/test_symbols_map.py`
(plus `tests/symbols_allowlist.tsv` for proven non-guest constants) runs in
`product-checks.yml` and fails if any engine hook PC / `k*` constant lacks
a row, so a change that adds a hook must add the row with it.
