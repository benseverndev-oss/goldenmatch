# Wave D names-remainder — owned goldenflow-core kernels (cross-surface)

**Program:** GoldenFlow owned-kernel + cross-surface (Rust-is-the-reference).
**Predecessors:** Wave 0 (identifiers), A (swift/aba/imei), B (name_transliterate/
name_script), D1 email, D-sweep-pt1 (url/numeric/categorical, PR #1426 — this
branch stacks on it).
**Thesis (Ben, 2026-07-04, locked):** commit fully — ALL transforms become owned
Rust kernels; language surfaces fall out of Rust; kernel = spec under
reference-mode. No "keep it Python" escape hatches. Migration (not addition), so
the transform count stays 92 and version bumps are minor.

## Scope: the 8 remaining `names` transforms

`name_transliterate` + `name_script` already landed in Wave B. Remaining:

| transform | mode | auto_apply | shape | owned kernel |
|---|---|---|---|---|
| `strip_titles` | expr | **True** | string→string | `names::strip_titles` |
| `strip_suffixes` | expr | False | string→string | `names::strip_suffixes` |
| `name_proper` | series | False | string→string | `names::name_proper` |
| `nickname_standardize` | series | False | string→string (dict) | `names::nickname_standardize` |
| `initial_expand` | series | False | series+flag-mask | `names::has_initial(&str)->bool` |
| `split_name` | dataframe | False | 1 col → (first,last) | `names::split_name(&str)->(Option,Option)` |
| `split_name_reverse` | dataframe | False | 1 col → (first,last) | `names::split_name_reverse` |
| `merge_name` | dataframe | False | (first,last) → full | `names::merge_name(f,l)->Option` |

**Reality check vs the original framing:** `split_name` returns **first_name +
last_name only** (`rsplit(" ", 1)`), NOT first/middle/last. The struct is a
2-field struct, not 3. `merge_name` is the inverse (multi-input → one column).

## Two shape-buckets, two parity mechanisms (both have precedent)

### Bucket 1 — scalar `string→string` → the existing string corpus
`strip_titles`, `strip_suffixes`, `name_proper`, `nickname_standardize`, and
`initial_expand`'s predicate all reduce to a scalar `(&str)->Option<String>`
(or `->bool` for `has_initial`). They fit `tests/parity/identifiers_corpus.jsonl`
exactly, like every prior family. No corpus-shape change needed.

- **`strip_titles`/`strip_suffixes`:** currently `mode="expr"` pure-Polars regex.
  Keep `mode="expr"` and the public `func(column)->Expr` signature by dispatching
  through `pl.col(column).map_batches(...)` when native — the same trick numeric
  `currency_strip` used to stay `expr`. Kernel = explicit bounded title/suffix
  word-list match (documented), replicated char-for-char to TS. The regexes in
  `names.py` (`_TITLES`, `_SUFFIXES`) are the spec to port.
- **`name_proper`:** ASCII title-case + the `Mc*`/`O'*` capitalization fixups
  (`_MC_PATTERN`, `_O_PATTERN`). **Unicode hazard:** Python `str.title()` and Rust
  char casing diverge on non-ASCII. Bound the kernel to ASCII-aware title-casing +
  the two explicit fixups; document the boundary (reference-mode "resolved in
  Rust's favor"). Corpus rows must include non-ASCII inputs to pin the behavior.
- **`nickname_standardize`:** the ~70-entry `_NICKNAMES` map moves into the crate
  as in-crate data (exactly like the transliterate map in Wave B) and is
  replicated to TS. Lookup key = `val.strip().lower()`, fallback = original value.
- **`initial_expand`:** the value output is the series UNCHANGED; the only logic is
  the flag predicate `\b[A-Z]\.\s`. Owned kernel = `names::has_initial(&str)->bool`
  in the corpus. Python/TS `initial_expand` calls the kernel per row to build the
  flagged-rows list; the identity pass-through needs no kernel. (The engine
  consumes the flag list separately; that plumbing is unchanged.)

### Bucket 2 — multi-output / dataframe → dedicated pinned-vector parity
`split_name`, `split_name_reverse` (1→2 cols) and `merge_name` (2→1 col) do NOT
fit a string→scalar corpus. Follow the **numeric-array-ops precedent** (`round`/
`clamp`/… live in `test_numeric_kernels.py`, not the string corpus): a new
`tests/transforms/test_name_kernels.py` with pinned input→output vectors asserted
on BOTH the native path and the pure-Python fallback, plus a TS unit test
(`tests/unit/name-kernels.test.ts`). The kernel is still the per-row scalar:
- `split_name(&str) -> (Option<String>, Option<String>)` — `rsplit(' ',1)`; 2
  parts → (first, last); 1 part → (first, ""); empty/None → (None, None).
- `split_name_reverse(&str) -> (Option<String>, Option<String>)` — `split(',',1)`;
  2 parts → (last.trim → last, first.trim → first); else (whole.trim, "").
- `merge_name(first: Option<&str>, last: Option<&str>) -> Option<String>` — join
  the non-empty trimmed parts with a space; None if both empty.

## Cross-surface fan-out (per the established migration recipe)

For each kernel: **goldenflow-core** (pure Rust + `#[cfg(test)]` unit tests) →
**native-flow** `*_arrow` shim → **`_native.py`** `*_native()` runner →
**Python transform** imports the runner + keeps a byte-matched `_*_py` reference →
**goldenflow-wasm** export → **TS** pure-fallback + wasm dispatch (backend +
loader) → **`_native_loader`** `_COMPONENT_SYMBOLS["names_ext"]` (floor symbol,
e.g. `strip_titles_arrow`) → **corpus/parity**.

**New plumbing (the only genuinely new part):** multi-output arrow marshaling.
- native `split_name_arrow(arr) -> (arr, arr)` returns a tuple of two Arrow
  string arrays; `split_name_native()` returns `Callable[[Series], tuple[Series,
  Series]]`; the dataframe-mode `split_name` builds `first_name`/`last_name` from
  the pair. `merge_name_arrow(first_arr, last_arr) -> arr`.
- wasm `split_name(&str) -> Vec<Option<String>>` (2-element `[first, last]`) via
  wasm-bindgen (or a small `#[wasm_bindgen] struct NameParts`); TS unpacks the
  pair. Keep it a 2-element array for the simplest marshaling.
- Reuse Wave B's `names` module — EXTEND `goldenflow-core/src/names.rs`,
  `native-flow/src/names.rs` (or a `names_ext.rs`), and the existing TS
  `transforms/names.ts`.

## Tasks (each TDD, two-stage reviewed, sequential — all touch names.rs/names.py/
mod lists so parallel would conflict; the sweep learned this)

1. **goldenflow-core kernels** (pyo3-free, LOCALLY TESTABLE via `cargo test`):
   extend `names.rs` with `strip_titles`, `strip_suffixes`, `name_proper`,
   `nickname_standardize`, `has_initial`, `split_name`, `split_name_reverse`,
   `merge_name` + unit tests covering the pinned vectors + Unicode edges. This is
   the load-bearing novel piece; prove it here.
2. **native-flow shim**: `*_arrow` pyfunctions incl. the tuple-returning
   `split_name_arrow`/`split_name_reverse_arrow` and two-input `merge_name_arrow`.
3. **`_native.py` runners** + Python transforms migrated to native-first with
   byte-matched `_*_py` references; `_native_loader` `names_ext` component.
4. **goldenflow-wasm** exports + TS backend/loader wiring + pure-TS ports.
5. **Corpus + parity**: scalar rows appended to `identifiers_corpus.jsonl`
   (regen via `scripts/gen_identifiers_corpus.py`, sync-check to TS copy);
   `test_name_kernels.py` + `name-kernels.test.ts` for the multi-output three.
   Wire numeric-style value/tuple asserts.
6. **Versions + docs**: goldenflow 1.9.0 / npm 0.9.0 / native 0.7.0; CHANGELOG;
   `goldenflow/CLAUDE.md` names-family note; API-reference names list already
   lists all 8 (migration, count stays 92).

## Landmines
- **strip_titles is `auto_apply=True`** — a zero-config regression here is
  user-visible. The kernel MUST reproduce the existing regex byte-for-byte;
  corpus must cover the title list + the trailing `.strip_chars()`.
- **`name_proper` Unicode title-casing** — the one real parity risk; bound to
  ASCII + explicit fixups, pin with non-ASCII corpus rows, document the boundary.
- **P-series overlap:** #1423 (P9, SQL de-bridge) is independently adding
  `goldenflow-core::text` kernels. It does NOT touch `names`, so no collision
  this family — but expect the same lib.rs module-list conflict on rebase; keep
  both modules.
- **CI-only TS gate** (box OOMs): the D-sweep bug (numeric fns never wired into
  the parity test) was invisible locally. For the TS side, statically verify the
  parity registry covers every new corpus/kernel transform BEFORE push (a
  `corpus == PURE_TS_FN == wasm map` key cross-check script caught it for numeric).

## Base / merge
Stacked on `feat/goldenflow-wave-d-sweep` (PR #1426). After #1426 squash-merges,
`git rebase --onto origin/main <sweep-tip> feat/goldenflow-wave-d-names` (the
A→B→D1 pattern), then open the PR + arm auto-merge (squash).
