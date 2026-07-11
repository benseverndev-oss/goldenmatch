# GoldenMatch Polars eviction — W3 plan (controller/autoconfig dual-backend)

Spec: `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`
W3 row: "Controller/autoconfig front: profiling, indicators, complexity
signals — mostly column reductions (goldencheck-shaped)" -> Controller
dual-backend. Predecessors: W0-W2 all merged (#1616/#1622/#1632/#1633/#1642/
#1650/#1651/#1655). Recon 2026-07-11 (line refs verified against this tree).

## Invariants (unchanged from W2)

- NO default flip: `GOLDENMATCH_FRAME` stays `polars`; every surface keeps
  receiving `pl.DataFrame` today (ingest converts at the boundary until W5).
  W3 is dual-backend PREP: seam ops + parity fixtures + call sites routed so
  an ArrowFrame COULD flow.
- PolarsColumn/PolarsFrame impls delegate to the exact Polars call each site
  uses (byte-identical); Arrow impls parity-pinned by fixtures FIRST.
- `complexity_profile.py` needs NO port (pure dataclasses — recon-verified).
- Every batch is its own PR off the predecessor's merged main (the W2
  fold-and-checkout-ours drill when parallelizing).
- pytest-split shard-shift discipline: new test files added with the
  rootdir-relative deselect lesson in mind; watch for clobber reds.
- MERGE ORDER GUARD: #1654 (zero-config fix) touches autoconfig_rules +
  _profile_helpers — fold origin/main into this branch when it merges,
  BEFORE any call-site batch ships.

## Batches

### W3a — seam ops, fixtures-first, pure-additive (mirrors W2b)

Column protocol + both backends (goldencheck-proven shapes marked GC):
- `drop_nulls() -> Column` (GC), `cast_str(strict: bool = True) -> Column`
  (GC `cast` — W3 needs only the Utf8 flavor; arrow twin = arrow_derive
  cast_utf8 for strict=False parity, pc.cast for strict),
  `fill_null(value) -> Column` (GC), `sum()`, `mean()`, `min()`, `std()`
  (GC; std ddof=1 pinned — polars default), `value_counts_desc() ->
  list[tuple[value, int]]` (GC — count-desc order pinned; ties by value for
  cross-backend determinism), `str_len_chars() -> Column` (net-new; polars
  `.str.len_chars()` vs `pc.utf8_length` — CODEPOINT semantics pinned by
  fixture with multibyte corpus).
- Frame protocol + both backends:
  - `sample(n, seed, shuffle=True) -> Frame` — THE controller op.
    CONTRACT: polars `df.sample(n=…, seed=…, shuffle=…)` rows are the
    REFERENCE; the arrow impl CANNOT reproduce polars' RNG, so the contract
    is statistical, not byte: same n, deterministic for a given seed on the
    same backend, no duplicates. Cross-backend fixtures assert SHAPE +
    determinism-per-backend, NOT row identity. Downstream parity across
    backends is therefore config-level (the controller's verdicts on ITS
    backend's sample), documented loudly — this is the one W3 op where
    byte-parity is impossible by construction.
  - `with_row_index(name="__row__") -> Frame`.
  - `joint_n_unique(cols) -> int` (autoconfig:3203 composite cardinality).
  - `group_nunique(key, value) -> Frame[key, n_unique]` (_source_disjoint).
  - `head(n) -> Frame` (indicators/profilers `df.head`; alias of
    slice(0, n) — thin, but named for call-site fidelity).
  - `coverage_ratio(pass_field_lists: Sequence[Sequence[str]]) -> float`
    (_union_coverage:1436-1451 as ONE semantic op: fraction of rows covered
    by at least one pass's all-non-null fields; polars impl = the exact
    pl.repeat/mask loop; arrow = pc.is_valid + and/or folds).
- `semantic_dtype() -> str` Column op (reviewer SHOULD-FIX, the strongest
  finding): a pinned cross-backend mapping to {text, numeric, date, bool,
  unknown}. Four sites branch on Polars dtype strings/objects
  (autoconfig:88, controller:1875, profiler:104/114,
  indicators._compute_identity_score:95) and Arrow stringifies float64 as
  "double" — contains neither "int" nor "float" — so float columns would
  silently classify "unknown" and skew controller verdicts. Raw
  `str(dtype)` stays polars-side only where byte-fidelity matters
  (ColumnProfile.dtype pins the polars spelling; the native classify JSON
  gets the normalized tag — decided at W3c port time with a fixture).
- W3c's previously-unnamed ops, added HERE per fixtures-first (reviewer):
  Frame `distinct_row_count()` (profiler:173 df.unique().height), Column
  `blank_count()` (strip=="" count, profiler:131), and the all-empty-row
  fold (profiler:176-191) declared PYTHON-SIDE over to_list (cold path,
  explicit decision, not an op).
- `sample` default is `shuffle=False` (reviewer NIT: 3 of 5 call sites use
  polars' default; the controller passes shuffle=True explicitly).
- `group_nunique` contract pins the either-col-null row DROP
  (_source_disjoint's frame-level drop_nulls on the 2-col selection) —
  an arrow impl that groups nulls would flip the disjoint verdict.
- Fixtures in test_frame_relational_ops.py (existing file — no shard-shift)
  covering: null/empty columns, multibyte str_len, value_counts tie order
  (+ null-exclusion pinned), std ddof, sample determinism-per-backend +
  n>height behavior (pinned to polars' actual), joint vs single-col
  n_unique null semantics, semantic_dtype across
  utf8/large_utf8/int/float/double/date/bool/null, coverage_ratio's
  edge cases (missing column -> pass contributes nothing; empty
  pass_field_lists -> 0.0; empty fields-list INSIDE a pass -> pinned
  (all-True mask covers everything -> 1.0, unreachable today, asserted);
  float-NaN counts as non-null on BOTH backends; height==0 -> 0.0).

### W3b — indicators port (lowest risk)

`indicators.py`: compute_column_priors (head + n_unique + cast/fill_null/
to_list), estimate_sparse_match_signal + estimate_full_pop_hits (group_len +
Python pair math — the `.select((n*(n-1)/2).sum())` expression moves to
Python over group_len output: SAME values, pinned by test_indicators.py
unedited), corruption score, compute_cross_blocking_overlap
(with_row_index + the list-agg stays Python over group_partitions if the
expression shape resists a clean op — recon flags this as the one candidate
to leave partially Polars with a W5 note). Budget semantics unchanged
(test_indicators_budget.py unedited).

### W3c — profiling port

`autoconfig._emit_data_profile`, `autoconfig_controller.
_compute_data_profile_from_df` (twins — port BOTH, consider extracting the
shared body like W2c's _cross_source_filter_df), `autoconfig.
profile_columns`, `profiler.py` (profile_column/profile_dataframe), and the
W2e-2-deferred `matchkey._emit_matchkey_profile` — its Chao1
value_counts["count"]-probing replaced by value_counts_desc + Python f1/f2
(cleaner AND backend-neutral). Gates: test_profiler.py, test_matchkey.py,
test_profile_emitter.py unedited.

### W3d — autoconfig blocking measurement + block_analyzer

build_blocking's null_rate/_max_block_size/_nonnull_ratio (group_len + max +
drop_nulls), joint cardinality -> joint_n_unique, _union_coverage ->
coverage_ratio, _check_source_overlap/_source_disjoint -> unique/filter_eq/
group_nunique, block_analyzer.score_candidate — NOT a derive_block_key reuse
(reviewer): its compound candidates carry PER-FIELD transforms and its
empty-transform path is bare cast+concat_str, not blocker's expr. Port via
per-field derive_transformed_column composition + the concat op, with the
polars impl delegating to block_analyzer's own _apply_candidate_transforms
for byte-fidelity; group_len + mean/std/max for stats. estimate_recall
(sample + cast_str + fill_null). Gates: test_autoconfig.py,
test_block_analyzer.py unedited.

### W3e — controller plumbing + entry boundary (NO default flip)

_take_sample/_sample_one/_stratified_sample/_pick_stratification_key onto
Frame.sample/concat_frames/head; reference merge stays pl.concat
vertical_relaxed (NEW op only if a call site needs it on arrow — it can't
until W5; leave with a W5 note). auto_configure_df's isinstance gate
(3546-3644) additionally accepts a Frame — unwrap `.native` AT THE TOP,
before the throughput early-check (3593-3606) and the exclusions gate
(both isinstance-DataFrame-guarded; a wrapped Frame would silently skip
them — reviewer). PolarsFrame -> today's path; ArrowFrame ->
pl.from_arrow shim with the W5 removal note (the W2d explicit-boundary
pattern). The `reference` param stays Polars-only (deliberate: match-mode
arrow can't flow until W5). _stratified_sample's partition uses the
existing group_partitions (first-appearance order == partition_by
maintain_order — already pinned by its W2d fixture). Gates:
test_autoconfig_controller*.py, the serial heavy test_controller_adaptive_
e2e.py, goldenmatch_frame_diff lane green.

## Exit gates (whole wave)

- Full goldenmatch shards + heavy + fallback green; differential harness
  green. W5 IMPLICATION, stated now (reviewer): the harness stays green in
  W3 only because ingest converts to polars in BOTH lanes, so both sample
  identically. Once arrow flows past ingest (W5), per-backend sampling
  means controller OUTPUT may legitimately differ across lanes — the
  harness's controller expectations must become per-backend then; that is
  the documented `sample` contract, not a bug.
- throughput-gate green (cost metrics unchanged — the port is delegation).
- No wall gate: the W2c precedent (both-refs-identical under the same
  workload) + zero-config fix's bench provide the baseline; a W3-specific
  wall run is meaningless while the tiny-block perf item is open (tracked
  separately). State this in each PR.

## Risks

| Risk | Mitigation |
| --- | --- |
| `sample` cross-backend non-parity misread as a bug later | The op docstring + fixtures pin the statistical contract explicitly; controller verdicts are per-backend by design |
| Twins drift (_emit_data_profile vs _compute_data_profile_from_df) | Extract shared body in W3c (the W2c retire-the-mirror pattern) |
| value_counts_desc tie order differs polars-vs-arrow | Secondary sort by value pinned in the op (both backends), fixture with ties |
| Indicator budget checks time out differently after seam wrapping | Budgets are wall-clock guards, not outputs; test_indicators_budget.py pins the sentinel behavior unedited |
| #1654 conflict (autoconfig_rules/_profile_helpers) | Fold main on its merge before W3b+ ship |
