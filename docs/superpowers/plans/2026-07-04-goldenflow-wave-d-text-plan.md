# Wave D text — owned goldenflow-core kernels (cross-surface)

**Program:** GoldenFlow owned-kernel + cross-surface (Rust-is-the-reference).
**Predecessors:** Wave 0/A/B, D1 email, D2 url, sweep (url/numeric/categorical),
names-remainder (#1431), address-simple (#1432 — this branch stacks on it).
**Thesis (Ben, locked):** commit fully — ALL transforms become owned Rust
kernels; language surfaces fall out of Rust; kernel = spec under reference-mode.
No "keep it Python" escape hatches. Migration (not addition): count stays 92,
minor version bumps.

## Scope: the 18 `text` transforms — split into TWO stacked PRs by parity risk

`goldenflow-core::text` ALREADY has `strip` + `collapse_whitespace` kernels
(P9 #1423, wired for the SQL de-bridge only — NOT yet the native wheel / wasm /
Python-native dispatch). This wave surfaces those two AND migrates the other 16.

### PR text-1 — mechanical / ASCII-bound (12 transforms + surface the 2 existing)
All hand-rolled, NO regex dep (JS/Py/Rust regex diverge on `\s`/`\d`/greedy —
the address/email precedent), ASCII-bounded where a Unicode char-class would
otherwise leak runtime differences (documented boundary, reference-mode).

| transform | mode | auto | shape / kernel |
|---|---|---|---|
| `strip` | expr | **True** | EXISTS `text::strip` — wire surfaces |
| `collapse_whitespace` | expr | **True** | EXISTS `text::collapse_whitespace` — wire surfaces |
| `normalize_quotes` | expr | **True** | 6 explicit smart-quote→ASCII replaces |
| `normalize_line_endings` | expr | False | `\r\n`→`\n` then `\r`→`\n` (literal, order matters) |
| `truncate(n=255)` | expr | False | char slice `[0,n)` (param) |
| `pad_left(width=10,char="0")` | expr | False | left-pad to width (param) |
| `pad_right(width=10,char=" ")` | expr | False | right-pad to width (param) |
| `remove_html_tags` | expr | False | strip `<...>` spans (hand-rolled scan) |
| `remove_urls` | expr | False | strip `https?://` + non-whitespace run |
| `remove_digits` | expr | False | remove ASCII digits (bound; document) |
| `remove_punctuation` | expr | False | keep ASCII-alnum + whitespace, drop rest |
| `extract_numbers` | expr | False | extract `\d+\.?\d*` runs, join " " |

- **Parameterized transforms** (`truncate`/`pad_left`/`pad_right`): the kernel is
  a plain fn `(s, n[, char]) -> String`; the arrow shim/native runner take the
  params. These are per-column-constant params (like numeric `round(n)`/
  `clamp(min,max)`), so the runner is `fn(**params) -> Callable[[Series],Series]`
  — mirror `round_native`/`clamp_native`.
- **`remove_digits`/`remove_punctuation`/`extract_numbers` char-class boundary:**
  the old Polars used Rust-regex `\d`/`\s` (Unicode-aware). Owned kernels use
  ASCII digit (`0-9`) and ASCII-or-Unicode whitespace via `char::is_whitespace`
  (matches across surfaces for the ASCII whitespace that appears in real data);
  document that exotic Unicode digits/whitespace are out of the bounded set.
  Corpus pins realistic ASCII + a couple non-ASCII rows to lock the boundary.
- All 12 are scalar `string→string` → fit the shared corpus. `truncate`/`pad_*`
  are parameterized: corpus rows use the DEFAULT params (the harness feeds
  length-1 arrays through the default-param path); dedicated pinned-vector tests
  cover non-default params (numeric `round`/`clamp` precedent).

### PR text-2 — Unicode-heavy (6 transforms; explicit-map / bounded)
The load-bearing parity risk. Casing + NFKD normalization diverge across
Rust std / Python `str` / JS by Unicode-DB version. Strategy per the
`name_transliterate` precedent: **explicit curated maps in the crate, replicated
char-for-char to Py/TS, corpus pinned to UCD-STABLE inputs** (common Latin
diacritics/ligatures whose casing + NFKD have been fixed for decades). Kernel =
spec; the rare exotic input is the documented reference-mode boundary.

| transform | mode | auto | strategy |
|---|---|---|---|
| `lowercase` | expr | False | Rust `to_lowercase` = spec; corpus = agreeing inputs; document |
| `uppercase` | expr | False | Rust `to_uppercase` = spec; ß→SS etc. verified equal on Py/JS |
| `title_case` | expr | False | ASCII-title (reuse `name_proper`'s `ascii_title`) + document boundary |
| `normalize_unicode` | series | **True** | EXPLICIT NFKD-decompose map (extend the transliterate approach) + strip combining; NO `unicode-normalization` crate dep (its UCD wouldn't match Py/JS runtime) |
| `fix_mojibake` | series | False | latin-1 encode → utf-8 decode round-trip (deterministic byte op; portable) |

- **`normalize_unicode` is `auto_apply=True`** — highest-risk. Keep the existing
  ASCII-fast-path (pure-ASCII column → no-op) so zero-config runs on ASCII data
  are byte-identical AND fast. The owned kernel decomposes via an explicit
  precomposed→base+combining map (curated superset of the transliterate map,
  covering the Latin-1 + Latin-Extended-A precomposed range) then drops the
  combining marks. Bounded + documented; corpus pins the common diacritics.
- **`lowercase`/`uppercase`:** measure first — Rust `char::to_lowercase`/
  `to_uppercase` vs Python `str.lower`/`upper` vs JS agree on the FULL BMP for
  the vast majority; the corpus generator asserts native==python per row, so any
  divergence surfaces as a corpus-build failure to investigate, not silent drift.
- **`fix_mojibake`:** port `val.encode("latin-1").decode("utf-8")` exactly —
  Rust: map each char (must be ≤ U+00FF) to a byte, then `str::from_utf8`; on any
  failure (char >255, or invalid utf-8) return the original. Deterministic; no
  Unicode-DB dependency. Verify byte-identical to the Python round-trip.

## Cross-surface fan-out (established recipe, per PR)
For each kernel: **goldenflow-core::text** (pure Rust + `#[cfg(test)]` tests) →
**native-flow** `*_arrow` shim → **`_native.py`** `*_native()` runner →
**Python transform** native-first + byte-matched `_*_py` fallback →
**goldenflow-wasm** export → **TS** pure-fallback + wasm dispatch →
**`_native_loader`** `_COMPONENT_SYMBOLS["text"]` (floor symbol `strip_arrow`) →
**corpus/parity**. All `mode="expr"` transforms keep `func(column)->Expr` via
`map_batches`.

## Tasks (per PR: TDD, sequential — shared text.rs/text.py/lib.rs/loader/corpus)
1. goldenflow-core `text.rs` kernels + unit tests (locally testable).
2. native-flow `text.rs` shim (+ any param-passing arrow fns).
3. `_native.py` runners + Python migration + `text` loader component.
4. goldenflow-wasm exports + TS `text.ts` + backend/loader.
5. Corpus + parity (scalar rows; pinned-vector tests for parameterized/Unicode).
6. Versions + docs (text-1 → 1.11.0/0.11.0/0.9.0; text-2 → 1.12.0/0.12.0/0.10.0).

## Landmines
- **Three auto_apply transforms** (`strip`, `collapse_whitespace`,
  `normalize_quotes` in text-1; `normalize_unicode` in text-2) — a zero-config
  regression is user-visible. Kernels MUST reproduce the existing output
  byte-for-byte on realistic inputs; corpus pins them.
- **`strip`/`collapse_whitespace` already exist** — do NOT re-add the kernels;
  add the arrow shim + native runner + wasm export + Python native dispatch + TS.
  Confirm the existing kernel signatures (`strip(&str)->&str`,
  `collapse_whitespace(&str)->String`) match what the shim needs.
- **NO regex dep** — hand-roll html-tag/url/number scans + char-class filters.
- **CI fmt/tsc are the ONLY signal for native-flow fmt + TS** (box OOMs): run
  `cargo fmt --check` on native-flow AND grep TS for `[A-Za-z0-9]\*/` before push
  (the names #1431 lesson). Statically cross-check corpus==PURE_TS_FN==wasm keys.
- **P-series overlap (#1423):** shares `goldenflow-core::text`. Expect lib.rs
  module-list already has `text`; APPEND kernels, keep both P-series + our fns.

## Base / merge
text-1 stacks on `feat/goldenflow-wave-d-address` (#1432). text-2 stacks on
text-1. After each base merges, `git rebase --onto origin/main <base-tip>` then
open the PR + arm auto-merge (squash).
