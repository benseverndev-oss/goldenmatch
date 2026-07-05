# Wave D text-2 — Unicode text kernels (cross-surface)

**Program:** GoldenFlow owned-kernel + cross-surface (Rust-is-the-reference).
**Predecessors:** text-1 (#1439, mechanical text kernels — MERGED). This is the
Unicode-heavy follow-up.
**Thesis (Ben, locked):** commit fully — ALL transforms become owned Rust
kernels; kernel = spec under reference-mode; explicit maps (NOT runtime Unicode
DBs) for guaranteed cross-surface byte-parity. Migration, count stays 92.

## Scope: the 5 Unicode-heavy `text` transforms

| transform | mode | auto | strategy |
|---|---|---|---|
| `lowercase` | expr | False | Rust `str::to_lowercase` = spec; corpus bounded to inputs where Rust==Py==JS (Latin/ASCII); gen asserts native==python per row |
| `uppercase` | expr | False | Rust `str::to_uppercase` = spec (ß→SS agrees across surfaces) |
| `title_case` | expr | False | ASCII-title (reuse names.rs `ascii_title`, make it `pub(crate)`); documented ASCII boundary vs polars Unicode `to_titlecase` |
| `normalize_unicode` | series | **True** | EXPLICIT NFKD-decompose+strip-combining map (GENERATED, 413 entries over U+00C0–017F + U+1E00–1EFF); NOT the `unicode-normalization` crate (runtime-UCD drift). Keep the ASCII fast-path. |
| `fix_mojibake` | series | False | latin-1 encode → utf-8 decode round-trip; deterministic byte op, portable (no Unicode DB) |

## Per-transform parity design

### lowercase / uppercase (trivial kernels, bounded corpus)
`pub fn lowercase(s) -> String { s.to_lowercase() }` / `to_uppercase`. These are
what polars already calls (Rust std), so "owning" them is moving the SAME Rust
fn into goldenflow-core. Python fallback `val.lower()`/`val.upper()`, TS
`.toLowerCase()`/`.toUpperCase()`. **Parity boundary:** Greek final sigma
(ΟΔΟΣ→οδος vs οδοσ) and Turkish İ/ı can diverge across Rust/Py/JS. Bound the
corpus to Latin + common diacritics where all three agree; the corpus generator
already asserts native==python per row, so any divergence FAILS the build (not
silent drift). Document the boundary.

### title_case (ASCII-title, documented boundary)
Reuse `names.rs::ascii_title` (make `pub(crate)`): first alphabetic char of each
word upper, rest lower, non-alpha resets the word. Byte-identical to the
`name_proper` ASCII path. Bounded to ASCII (polars `to_titlecase` is Unicode-aware;
documented reference-mode boundary). Python/TS fallbacks port `ascii_title`.

### normalize_unicode (the load-bearing piece — GENERATED explicit map)
The existing transform: NFKD-normalize then drop combining marks. NET effect =
strip diacritics from precomposed chars that DECOMPOSE (é→e, ñ→n, …), leaving
NON-decomposing chars (ß, æ, œ, ø, ł, đ, þ, ð) UNCHANGED — distinct from
`name_transliterate` (which maps ß→ss etc.). So it needs its OWN map.
- **Generated map** (`scripts/gen_normalize_unicode_map.py`, a one-off that
  emits the Rust match arms + the Python dict + the TS map from Python's
  `unicodedata` NFKD): covers U+00C0–U+017F (Latin-1 Supp + Latin Ext-A, 167
  changed) + U+1E00–U+1EFF (Latin Ext Additional / Vietnamese, 246). ~413 entries,
  identical bytes on every surface (same generated data) — STRONGER parity than
  the old runtime-`unicodedata` (which could differ by Python version). Values may
  be multi-char (Ĳ→"IJ") or contain a non-ASCII modifier (ŉ→"ʼn"); replicated verbatim.
- **ASCII fast-path KEPT** (pure-ASCII column → no-op) so zero-config runs on
  ASCII data stay byte-identical AND fast. The kernel: for each char, ASCII →
  passthrough; else map-lookup (emit replacement) or, if not in the map (CJK,
  rare), passthrough unchanged. Documented boundary: chars outside the generated
  ranges pass through (the old code decomposed a few more rare precomposed chars).
- **auto_apply=True** → highest regression risk; corpus pins the common diacritics
  + the non-decomposing chars (ß/æ/ø stay) + multi-char (Ĳ→IJ) + ASCII fast-path.

### fix_mojibake (portable byte round-trip)
Port `val.encode("latin-1").decode("utf-8")`: Rust — map each char to a byte
(each must be ≤ U+00FF, else the encode fails → return original), then
`str::from_utf8` (invalid → return original). Deterministic, no Unicode DB;
byte-identical across surfaces. `mode="series"`.

## Cross-surface fan-out (established recipe)
goldenflow-core::text (extend) → native-flow shim → `_native.py` runners +
Python migration (text component ALREADY exists — floor `strip_arrow`; these add
to it) → goldenflow-wasm exports → TS text.ts + backend/loader → corpus/parity.
`lowercase`/`uppercase`/`title_case` are `mode="expr"` (map_batches);
`normalize_unicode`/`fix_mojibake` are `mode="series"`.

## Tasks
1. goldenflow-core text.rs kernels (lowercase/uppercase/title_case/fix_mojibake +
   the generated `normalize_unicode` map) + unit tests. **Make ascii_title
   pub(crate) in names.rs.** Commit the generator script too.
2. native-flow shim (5 `*_arrow`).
3. `_native.py` runners + Python migration (the 5 dispatch native-first;
   `_*_py` fallbacks byte-match the kernel — `lower()`/`upper()`, ascii_title
   port, the SAME generated normalize map, latin-1 round-trip).
4. goldenflow-wasm exports + TS text.ts (migrate the 5) + backend/loader.
5. Corpus + parity: scalar rows for all 5 (they fit the string→scalar corpus).
   Bound casing/normalize rows to cross-surface-stable inputs.
6. Versions + docs: goldenflow 1.12.0 / native 0.10.0 / npm 0.12.0. **BUMP
   goldenflow-core 0.2.0 → 0.3.0** (gotcha 5: text.rs is an existing module, so
   the version bump is REQUIRED to bust the stale rust-cache in the maturin lane).

## Landmines
- **gotcha 5 (stale core cache):** editing existing text.rs → MUST bump
  goldenflow-core version (0.2.0 → 0.3.0) or the `python (goldenflow)` maturin
  lane links a stale core (E0425). Already burned text-1.
- **gotcha 4 (unreachable_patterns):** re-run core clippy after EVERY edit.
- **normalize_unicode auto_apply** — a zero-config regression is user-visible;
  keep the ASCII fast-path; pin the corpus thoroughly.
- **casing cross-surface divergence** (Greek/Turkish) — bound corpus; the
  gen-time native==python assert is the safety net.
- **pre-push routine:** whole-package `ruff` + native-flow `cargo fmt --check` +
  core clippy + TS `*/`-grep + corpus sync — all four before push.

## Base / merge
Off origin/main (text-1 merged). Open PR + arm auto-merge (squash).
