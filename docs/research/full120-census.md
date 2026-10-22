# Full-120 census: what each column means and how to use it

`full120-census.tsv` is a static census of every EE instruction that reads a
guest pool word holding (or neighbouring) a 1/60-period constant, plus the
direct consumers those readers imply. It is the map the 120 Hz conversion
was built from: one row per reader PC, each with a status. It is a static
scan, not a runtime coverage claim — a row says "this instruction reads this
word", not "this system runs 2×".

## Columns

| Column | Meaning |
| --- | --- |
| `address` | Reader PC (EE virtual address, hex). The instruction that loads the word. |
| `function` | Guest function containing the reader (symbol name where known). |
| `kind` | Conversion rule class: `integer_lead` (integer cadence/count), `gp_seed` (pool-word data reference), and similar static classes. |
| `target` | The word or value the rule acts on (pool address or rate). |
| `census_class` | Analyst judgement: `a?`/`b?`/`e?`/`k?` prefix = hypothesized conversion class (multiplicative rate, additive step, clock/ordinal, hardware-time), `converted:<group>` = handled by that FIX group, `stock:*` = intentionally left at stock cadence, `idle`/`open`/`unconverted` = disposition at census time. |
| `status` | Disposition: `converted:<group>` (fixed by that FIX group), `stock:<reason>` (kept at stock: `root`, `per-time`, `event`, `not-time`, `k`), `idle` (not called in any lab window), `open` (live in events, per-update — needs a group), `unconverted`, `fh12` (covered by a post-call hook). |
| `system` | Subsystem tag where assigned (e.g. `9 trails`, `9 particles`, `9 rider FX`, `14 streaming`, `12 HUD`, `7 camera`, `10 anim`, `8 world`). Empty = unassigned. |
| `reader` | Extra reader context (a second PC or a note on dormant callers). Mostly empty. |
| `live_calls_per_stock_s(stock/events)` | Measured call rate per stock second at 60 Hz vs in 120 Hz events mode (e.g. `59.9/119.9` = runs every update in both modes, so it needs conversion; `fr`/`ap` prefixes = measured route). Empty = not measured. |
| `note` | One-line analyst note: the word's role, the FIX group, or why it is stock/idle. |

## Status values

- `converted:<group>` — a named FIX group converts this reader (e.g.
  `converted:anim`, `converted:boost`, `converted:camera`, `converted:clock`,
  `converted:countdown`, `converted:emitter`, `converted:rng`,
  `converted:rider`, `converted:steer`, `converted:stick`,
  `converted:timers`, `converted:aigate`, `converted:meter`,
  `converted:rail`, `converted:flags`, `converted:event`,
  `converted:slew`, `converted:session`, `converted:launch`,
  `converted:drag`, `converted:fx`). The group name matches the `FIX`
  group in the engine's full-120 header.
- `stock:<reason>` — deliberately not converted: `stock:root` (mathematical
  constant, not time), `stock:per-time` (already per-second units),
  `stock:event` (event impulse, not a rate), `stock:not-time` (dimensionless
  tuning), `stock:k` (hardware/audio-time domain), `stock:per-time` etc.
  Do not "fix" these by halving.
- `idle` — the reader never fired in any measurement window (588 rows).
  Harmless until the code path goes live; re-check before touching that
  system.
- `open` — fires per-update in events mode but has no FIX group (263 rows at
  census time). Each is a candidate, not a verdict: confirm the unit and the
  caller cadence before converting.
- `unconverted` — reviewed and left at stock (5 rows).
- `fh12` — handled by a post-call hook rather than a pool-word edit.

## How the census was built (method only)

1. Decode the EE executable's text sections and find every memory-operand
   instruction with a GP-relative address falling in the allocated-data pool
   holding 1/60-family constants.
2. Record each reader PC, its function, and the referenced word.
3. Classify statically (`kind`, `census_class`) by the arithmetic around the
   reader (multiply vs add, dt present or not, integer vs float domain).
4. Overlay runtime call counts from scripted deterministic boots at 60 Hz and
   in 120 Hz events mode (`live_calls_per_stock_s`).
5. Assign `status` per row as FIX groups land; `open`/`idle` rows are the
  remainder.

## How to use it in another codebase

- To port the conversion: filter `status = converted:<group>` for the group
  you are implementing; each row gives the exact reader PC, function, word,
  and note. Verify the word's float value in your own ELF before editing —
  pool layout shifts between builds.
- To audit completeness: filter `status = open` with non-empty
  `live_calls_per_stock_s` — those fire every update and have no group.
  Confirm each one's unit (force already integrated by a converted dt is the
  classic double-scale trap: see `ground` in `docs/facts.md`).
- To extend coverage: `idle` rows need a measurement window that reaches
  their code path before any verdict.
- Do not treat the TSV as executable: indirect callers, aliases, computed
  constants and subsystem ownership can hide readers the static scan cannot
  see.
