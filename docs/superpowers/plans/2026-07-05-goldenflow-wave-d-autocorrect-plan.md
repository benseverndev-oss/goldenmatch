# Wave D category_auto_correct — owned fuzzy kernel (cross-surface)

**Program:** GoldenFlow owned-kernel + cross-surface (Rust-is-the-reference).
**Predecessors:** the whole text family (text-1 #1439 + text-2 #1443, MERGED).
This is the last non-date family — the FUZZY, data-dependent one Ben flagged.
**Decision (Ben, 2026-07-05):** OWN THE WHOLE ALGORITHM in Rust (not just the
`fuzz_ratio` primitive), so the corrections are byte-identical across all three
surfaces — fixing the CURRENT Py/TS divergence (Python uses rapidfuzz Indel/LCS
ratio; TS used a Levenshtein ratio; they also differ on whitespace stripping).

## Owned kernel: `goldenflow-core::autocorrect`
Two functions:
- **`fuzz_ratio(a, b) -> f64`** — the rapidfuzz `fuzz.ratio` primitive:
  `100 * (1 - indel/(len_a+len_b))`, `indel = len_a+len_b-2*LCS` (LCS over
  chars). Special case `("","") -> 100`. Pinned vs rapidfuzz: active/actve=90.909,
  aaa/aa=80, kitten/sitting=61.538, abc/""=0, ""/""=100. (Threshold decisions
  only depend on which side of 85.0 a value falls, so last-ULP float differences
  are immaterial; the kernel returns STRINGS, not the float.)
- **`build_canonical_map(values, counts, freq_threshold, match_threshold) ->
  Vec<(from, to)>`** — the whole `_build_canonical_map` algorithm, ORDER-
  DETERMINISTIC (mirrors Python's Counter/dict insertion order = the
  `value_counts(sort=True)` order): frequency count (case-insensitive) ->
  canonical determination (>= freq_threshold; best casing = most_common, ties
  broken by INSERTION ORDER, i.e. first-max/`c > best` strictly) -> exact
  case-insensitive corrections -> fuzzy corrections for low-freq (`score >
  best_score` strictly = first-wins tie, `>= match_threshold`). Returns the
  corrections as (from_casing, to_canonical) pairs keyed by the STRIPPED casing.

## Data-dependent shape (host owns value_counts + apply, kernel owns the algo)
`category_auto_correct` is `mode="series"`, whole-column. The host computes
`value_counts` (polars / JS) -> passes (values[], counts[]) to the kernel ->
gets the corrections map -> applies per-element `corrections.get(v.strip(),
v.strip())` (this STRIPS every value, a documented side effect). value_counts +
apply are orchestration (stay host); the correction-map algorithm is the kernel.

## New marshaling: (str[], i64[]) -> (str[], str[])
`build_canonical_map_arrow(values, counts, freq_threshold, match_threshold) ->
(from_arr, to_arr)`. Read a Utf8 array + an Int64 array, call the kernel, return
two Utf8 arrays. New `util.rs` helper `zip_str_i64_to_str_pair` (or inline in the
shim). `_native.py` `build_canonical_map_native()` returns a
`Callable[[Series, Series], tuple[Series, Series]]`.

## Parity: pinned-vector (data-dependent, doesn't fit the string->scalar corpus)
- `fuzz_ratio` (two-input -> float): pinned-vector `test_autocorrect_kernels.py`
  (native + fallback; value-parity like numeric).
- `category_auto_correct` (column -> column): pinned-vector cases with a clear
  canonical + typo variants + case variants + a below-threshold non-match; assert
  the corrected column on BOTH the fallback (GOLDENFLOW_NATIVE=0, which uses the
  existing rapidfuzz-based `_build_canonical_map`) and native paths.
- The Python fallback (`_build_canonical_map` using `rapidfuzz.fuzz.ratio`) IS the
  byte-match reference: my `fuzz_ratio` replicates rapidfuzz, and my Rust ordering
  replicates Python's insertion order, so native == fallback on non-tie inputs.

## Cross-surface fan-out
goldenflow-core::autocorrect (fuzz_ratio + build_canonical_map + tests) ->
native-flow shim (build_canonical_map_arrow) -> `_native.py` runner + Python
migration (category_auto_correct calls native build_canonical_map, falls back to
the rapidfuzz `_build_canonical_map`) -> `_native_loader` `autocorrect` component
(floor `build_canonical_map_arrow`) -> goldenflow-wasm export (buildCanonicalMap
+ fuzzRatio) -> TS auto-correct.ts (REWRITE to call wasm build_canonical_map with
a pure-TS fallback that faithfully ports the Rust algorithm: LCS ratio + ordered
map + STRIP on apply -- unifies TS with Python) -> pinned-vector tests.

## Tasks
1. goldenflow-core `autocorrect.rs` (fuzz_ratio + build_canonical_map) + unit
   tests (pin the rapidfuzz values + a full build_canonical_map scenario).
   `pub mod autocorrect` in lib.rs.
2. native-flow shim + util helper (str[]+i64[] -> str[]+str[]).
3. `_native.py` runner + Python migration; `autocorrect` loader component.
4. goldenflow-wasm exports + TS rewrite (subagent) + backend/loader.
5. Pinned-vector parity: `test_autocorrect_kernels.py` (Python) +
   `autocorrect-kernels.test.ts` (TS). NOT the shared corpus.
6. Versions: goldenflow 1.13.0 / native 0.11.0 / npm 0.13.0. goldenflow-core:
   NEW module + lib.rs edit forces a rebuild (unlike text's existing-module
   edit), so the gotcha-5 core version bump is NOT strictly needed — but bump
   0.3.0 -> 0.4.0 anyway as cheap insurance if the maturin lane is uncertain.

## Landmines
- **Ordering determinism**: Python Counter.most_common + dict insertion order.
  Rust MUST use insertion-ordered structures + first-max-on-ties (`c > best`
  strictly, `score > best_score` strictly). Feed the kernel the value_counts in
  the SAME order Python iterates (value_counts sort=True). Ties are inherently
  ambiguous -> keep pinned-vector inputs tie-free.
- **STRIP on apply**: category_auto_correct strips every value (`corrections.get(
  v.strip(), v.strip())`). The unified TS apply MUST strip too (it currently
  does NOT -- a real fix).
- **auto_apply=True** -> user-visible; but it's suppressed for high-cardinality
  columns by selector.py (>10% unique). Pin the corpus/vectors to low-card.
- **rapidfuzz stays a Python dep** (the fallback uses it); the native path
  replaces it. Do NOT drop rapidfuzz from pyproject.
- **pre-push routine** (all 5): whole-pkg ruff + native-flow fmt + core clippy +
  TS */-grep + (no corpus, but keep the pinned-vector tests green).

## Base / merge
Off origin/main (text family merged). PR + arm auto-merge (squash).
