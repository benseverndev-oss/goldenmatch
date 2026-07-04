# Wave D address-simple — owned goldenflow-core kernels (cross-surface)

**Program:** GoldenFlow owned-kernel + cross-surface (Rust-is-the-reference).
**Predecessors:** Wave 0 (identifiers), A (swift/aba/imei), B (name_transliterate/
name_script), D1 email, D-sweep-pt1 (url/numeric/categorical, #1426), names-remainder
(#1431 — this branch stacks on it).
**Thesis (Ben, 2026-07-04, locked):** commit fully — ALL transforms become owned
Rust kernels; language surfaces fall out of Rust; kernel = spec under reference-mode.
No "keep it Python" escape hatches. Migration (not addition), so the transform count
stays 92 and version bumps are minor.

## Scope: the 8 `address` transforms (US-simple; i18n stays Wave C deferred)

| transform | mode | auto_apply | shape | owned kernel |
|---|---|---|---|---|
| `address_standardize` | expr | False | string→string | `address::address_standardize` |
| `address_expand` | expr | False | string→string | `address::address_expand` |
| `state_abbreviate` | expr | False | string→string | `address::state_abbreviate` |
| `state_expand` | expr | False | string→string | `address::state_expand` |
| `zip_normalize` | expr | **True** | string→string | `address::zip_normalize` |
| `country_standardize` | series | False | string→string (dict) | `address::country_standardize` |
| `unit_normalize` | series | False | string→string | `address::unit_normalize` |
| `split_address` | dataframe | False | 1 col → (street,city,state,zip) | `address::split_address` |

All 8 are US-scoped: US street-suffix map, US state map, US ZIP, a bounded country
lookup, US unit designators. i18n addresses remain Wave C (deferred).

## Two shape-buckets, two parity mechanisms (both have precedent)

### Bucket 1 — scalar `string→string` → the existing string corpus
`address_standardize`, `address_expand`, `state_abbreviate`, `state_expand`,
`zip_normalize`, `country_standardize`, `unit_normalize` all reduce to a scalar
`(&str)->Option<String>`. They fit `tests/parity/identifiers_corpus.jsonl` exactly,
like every prior scalar family. No corpus-shape change needed.

- **`address_standardize`/`address_expand`:** currently `mode="expr"` — a chain of
  case-insensitive **word-boundary** regex replaces, one per (full,abbr) pair. Keep
  `mode="expr"` and the public `func(column)->Expr` signature by dispatching through
  `pl.col(column).map_batches(...)` when native (the trick strip_titles/currency_strip
  used). **Hand-roll the word-boundary replace in Rust — NO regex dep** (JS/Python/Rust
  regex `\b` + replace-all semantics differ → parity hazard; email.rs set the no-regex
  precedent). `\b{word}\b` = the match must be bounded by non-word chars (`[A-Za-z0-9_]`)
  on both sides; case-insensitive compare; preserve the surrounding text. The `_STREET_ABBREV`
  map (15 entries) is in-crate data replicated to TS. **Order matters** (dict iteration
  order = insertion order in py3.7+; replicate that ordering in the Rust Vec and the TS
  array). Note `"Way"→"Way"` is an identity entry — preserve it (harmless, keeps parity).
- **`state_abbreviate`:** 3-way fallback — (1) 2-char input that upper-cases to a valid
  abbreviation → uppercase; (2) full name (case-insensitive) → the `_STATES_LOWER` lookup;
  (3) neither → **original column value unchanged** (NOT the stripped value — the Polars
  `.otherwise(pl.col(column))` returns the raw input). Kernel must replicate that: strip
  for the tests, but the fallback returns the ORIGINAL. 51 states incl DC.
- **`state_expand`:** strip → upper → `_STATES_REVERSE` lookup, default = **original
  column value** (again the raw, un-stripped input on no-match).
- **`zip_normalize`** (**auto_apply=True** — user-visible): strip → take first `-`-split
  segment → if all-digits (`^\d+$`) zero-pad to width 5 (`zfill(5)`), else return the
  base segment unchanged. Note zfill only left-pads; a >5-digit all-digit base is returned
  as-is. Empty base → zfill→"00000"? Check: `"".zfill(5)=="00000"` but base after strip of
  empty is "" and `"^\d+$"` does NOT match empty → returns "" unchanged. Pin this in corpus.
- **`country_standardize`:** the `_COUNTRIES` map (~60 entries) → in-crate data replicated
  to TS. Key = `val.strip().lower()`, fallback = original value. (Same shape as
  nickname_standardize from names.)
- **`unit_normalize`:** strip → apply 3 anchored prefix substitutions in order:
  `^(?:Apt|Apartment)\.?\s+`→`"Unit "`, `^(?:Ste|Suite)\.?\s+`→`"Ste "`, `^#\s*`→`"Unit "`,
  case-insensitive. Anchored at start only (`^`), applied sequentially to the result.
  Hand-roll (no regex): match a known prefix token (case-insensitive) optionally followed
  by `.`, then `\s+` (or for `#`, `\s*`), replace with the target. Sequential application
  matters — pin a corpus row where two could chain.

### Bucket 2 — multi-output / dataframe → dedicated pinned-vector parity
`split_address` (1 col → 4 cols: street/city/state/zip) does NOT fit a string→scalar
corpus. Follow the **split_name precedent** (`test_name_kernels.py`): extend it (or add
`test_address_kernels.py`) with pinned input→output vectors on BOTH the native path and
the pure-Python fallback, plus a TS unit test. The kernel is the per-row scalar:
- `split_address(&str) -> (Option, Option, Option, Option)` — hand-rolled parse of
  `"street, city, state zip"`: the Python regex is
  `^(.+?),\s*(.+?),\s*([A-Za-z]{2})\s+(\d{5}(?:-\d{4})?)$` (non-greedy first two captures,
  2-letter state, 5-or-9 ZIP). On match → (street, city, state, zip). On no-match →
  **(whole_input, None, None, None)** (street = the raw value, others null). None → all None.
  Hand-roll: the non-greedy `.+?,` means split on the FIRST comma for street, the SECOND
  for city; then the remainder must be `<2 alpha> <5 or 5-4 digits>`. Implement as: find
  first comma → street; find next comma → city; trim remainder; the tail must match
  `[A-Za-z]{2}\s+\d{5}(-\d{4})?` anchored+full → state+zip; any structural miss → no-match
  fallback. Pin +4 ZIP, no-match, null, and extra-comma cases.

## New plumbing (the genuinely new part): 1→4 array marshaling
names-remainder added `map_str_to_str_pair` (1→2) and `zip_str_to_str` (2→1).
`split_address` needs **1→4**: add `map_str_to_str_quad(arr) -> (arr, arr, arr, arr)`
to `native-flow/src/util.rs`, and `split_address_arrow(arr) -> (arr, arr, arr, arr)`.
- `_native.py`: `split_address_native()` returns `Callable[[Series], tuple[Series×4]]`;
  the dataframe-mode `split_address` builds street/city/state/zip from the quad.
- wasm: `split_address(&str) -> Vec<Option<String>>` (4-element `[street,city,state,zip]`);
  TS unpacks. Keep it a 4-element array (simplest marshaling, mirrors split_name's 2-elem).

## Cross-surface fan-out (established migration recipe)
For each kernel: **goldenflow-core** (pure Rust + `#[cfg(test)]` tests) → **native-flow**
`*_arrow` shim → **`_native.py`** `*_native()` runner → **Python transform** imports the
runner + keeps a byte-matched `_*_py` reference → **goldenflow-wasm** export → **TS**
pure-fallback + wasm dispatch → **`_native_loader`** `_COMPONENT_SYMBOLS["address"]`
(floor symbol `address_standardize_arrow`) → **corpus/parity**.

New crate module: `goldenflow-core/src/address.rs` (+ `pub mod address;` in lib.rs) and
`native-flow/src/address.rs` (+ registration in native-flow lib.rs). New TS
`src/core/transforms/address.ts`. wasm exports appended to goldenflow-wasm lib.rs.

## Tasks (each TDD, two-stage reviewed, sequential — shared lib.rs/loader/corpus)
1. **goldenflow-core `address.rs`** (pyo3-free, LOCALLY TESTABLE via `cargo test`): 8
   kernels — the 7 scalar + `split_address` tuple — one-for-one ports of `address.py`,
   in-crate `_STREET_ABBREV`/`_STATES`/`_COUNTRIES` data, hand-rolled word-boundary +
   anchored-prefix + split parsing (NO regex dep). Unit tests covering the pinned vectors
   + fallback (no-match returns original) + Unicode/edge cases. Load-bearing novel piece.
2. **native-flow shim** `address.rs`: `*_arrow` pyfunctions incl. `map_str_to_str_quad` +
   `split_address_arrow` (4-tuple). Register in native-flow lib.rs.
3. **`_native.py` runners** + Python transforms migrated native-first with byte-matched
   `_*_py` references; `_native_loader` `address` component (floor `address_standardize_arrow`).
   `mode="expr"` transforms keep the `func(column)->Expr` API via `map_batches`.
4. **goldenflow-wasm** exports + TS `address.ts` + backend/loader wiring + pure-TS ports
   (in-crate maps replicated to TS char-for-char).
5. **Corpus + parity**: scalar rows appended to `identifiers_corpus.jsonl` (regen via
   `scripts/gen_identifiers_corpus.py`, sync-check to TS copy); `test_address_kernels.py`
   (or extend test_name_kernels) + TS unit test for `split_address`.
6. **Versions + docs**: goldenflow 1.10.0 / npm 0.10.0 / native 0.8.0; CHANGELOG;
   `goldenflow/CLAUDE.md` address-family note (count stays 92; migration).

## Landmines
- **zip_normalize is `auto_apply=True`** — a zero-config regression is user-visible. Kernel
  MUST reproduce the strip→first-segment→conditional-zfill byte-for-byte. Pin the "already
  5", "+4 stripped", ">5 all-digit returned as-is", "non-numeric passthrough", empty cases.
- **state_abbreviate / state_expand fallback returns the ORIGINAL (un-stripped) value** on
  no-match, not the stripped one. Easy to get wrong — pin a `"  xx  "`-style no-match row.
- **Word-boundary hand-roll** (address_standardize/expand): `\b` semantics — a match inside
  a longer word must NOT replace (`"Streets"` must not become `"Sts"`). Test that. Case-
  insensitive match but the REPLACEMENT is the canonical-cased abbr. Dict ordering preserved.
- **No regex dep** — hand-roll word-boundary, anchored-prefix, and the split parse. JS/Py/Rust
  regex engines differ on `\b`/greedy/`replace_all`; hand-rolling is the parity guarantee.
- **P-series overlap:** #1423 (P-series SQL de-bridge) adds `goldenflow-core::text` kernels;
  does NOT touch `address`, so no collision — but expect the lib.rs module-list conflict on
  rebase; keep both modules.
- **CI-only TS gate** (box OOMs): statically verify `corpus == PURE_TS_FN == wasm map` keys
  BEFORE push (the D-sweep numeric bug was invisible locally).

## Base / merge
Stacked on `feat/goldenflow-wave-d-names` (#1431). After #1431 squash-merges,
`git rebase --onto origin/main <names-tip> feat/goldenflow-wave-d-address`, then open the
PR + arm auto-merge (squash).
