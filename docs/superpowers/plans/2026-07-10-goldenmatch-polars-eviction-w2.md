# GoldenMatch Polars eviction — Wave 2 plan (batched)

Program spec: `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`
(W2 row + section 4). Predecessors: W0 merged #1616 (lazy-import proxy + seam scaffold
+ op audit), W1 merged #1622 (Arrow IO front + ArrowFrame + `GOLDENMATCH_FRAME=arrow`
ingest lane + differential harness + frozen anchors).

## Goal

Wave 2 = the classic-engine glue port: blocker/scorer/cluster/golden/standardize/
matchkey onto seam ops, PLUS the item recon moved here from W1 — the fused-kernel
prep derivation (`_build_block_key_expr`, `_get_transformed_values`, `fused_match.py`'s
internal `pl.DataFrame`) so the covered-config spine runs polars-free end-to-end.

W2 is far too large for one PR (194 `.join` calls, 50 `group_by`, 121 `with_columns`,
six engine files). Per the spec's risk register ("wave/batch decomposition with
per-batch merges ... no wave stacks on an unmerged predecessor"), W2 ships as
**five batches, each its own PR off fresh main**:

| Batch | Content | PR shape |
| --- | --- | --- |
| **W2a** | Fused-prep derivation goes dual-backend behind two new seam ops; polars-free spine when input is Arrow | this plan, full detail |
| **W2b** | Relational seam ops (named joins, filter/group/partition/sort/concat/frame-constructors) + join-semantics fixtures; NO engine call-site changes | this plan, full detail |
| **W2c** | Columnar pair-stream spine port (scorer columnar -> ClusterFrames -> golden-from-frames) onto W2b ops; arrow lane flows past ingest for covered configs | scoped here, planned per-batch |
| **W2d** | Blocker static path (group_by loop, sentinel filter, sorted-neighborhood) + `load_file` returns Frame | scoped here, planned per-batch |
| **W2e** | Expression long tail: standardize `_native_*` twins, matchkey chains, golden fast-path aggregations | scoped here, planned per-batch |

## Non-goals (this wave)

- No default flip: `GOLDENMATCH_FRAME` default stays `polars`; arrow stays experimental.
- No distributed port (`distributed/clustering.py` is W4 by the W0 audit).
- No controller/autoconfig port (W3).
- No public-API change (v3.0.0 is W5).
- No new Rust kernels unless an op measures >10% slower (spec 4.3 binding rule);
  W2a/W2b start as `pyarrow.compute` + existing native symbols.

## Recon findings the plan is built on (2026-07-10)

1. **Fused prep is the only thing between a covered config and a polars-free spine.**
   `fused_match.py:147/150/154` (and FS twin at 305/308/327) wrap incoming
   `pa.Array`s back into `pl.DataFrame().cast(pl.Utf8)`, evaluate
   `_build_block_key_expr` (blocker.py:278-311) and `_get_transformed_values`
   (scorer.py:407-432), then `.to_arrow()` back. The kernel FFI itself is already
   Arrow (`list[pa.Array]` in, clusters out).
2. **The transform vocabulary splits cleanly.** Native-expressible chains live in
   `matchkey._try_native_chain` (cast/lowercase/uppercase/strip/substring/
   normalize_whitespace/strip_all/digits_only/alpha_only + gated address_normalize);
   everything else (soundex/metaphone/qgram/token_sort/first_token/last_token/
   bloom_filter/plugins) falls back to `map_elements(apply_transforms)` — and
   `utils/transforms.py::apply_transforms` is **pure Python + jellyfish, zero
   Polars**. So a polars-free derivation needs: Utf8 cast parity, a pc-based
   native-chain twin, `pc.binary_join_element_wise` for the `"||"` composite, and
   the existing pure-Python fallback per column.
3. **Byte-parity hazards for W2a** (each gets a dedicated fixture):
   - Polars casts to **LargeUtf8**; pc must target `pa.large_string()`.
   - Non-string source columns: `pl.cast(Utf8)` stringification of ints/floats/
     NaN/bools/dates must match exactly (the blocker sentinel-drop
     `nan/null/none` depends on it).
   - Polars `str.slice(start, length)` is length-based; pc
     `utf8_slice_codeunits(start, stop)` is stop-based.
   - Polars regex = Rust `regex` crate; pc = RE2. The four regexes in
     `_try_native_transform` (`\s+`, `[^0-9]`, `[^a-zA-Z]`) are engine-agnostic,
     but this is asserted by fixture, not assumed. `address_normalize`'s
     `list.eval`/backref chains are NOT ported in W2a — that transform routes to
     the pure-Python fallback on the arrow backend (parity oracle is
     `apply_transforms`, which is the reference implementation anyway).
   - `map_elements` null propagation: Polars skips nulls (UDF never sees them);
     the arrow fallback must preserve nulls, not stringify them.
   - **Float/int stringification WILL diverge under plain `pc.cast`** (reviewer
     finding): Polars renders `1.0 -> "1.0"`, `NaN -> "NaN"`; Arrow's cast
     renders shortest-repr `"1"` and `"nan"`. The sentinel drop lowercases so
     block keys mask the NaN case, but SCORE fields don't (different strings ->
     different jaro scores). `_cast_utf8_arrow` is pre-authorized to use a
     per-dtype Python-side formatter where `pc.cast` fails the fixture
     (expected for float64) — the batch does not stall on this.
   - Composite null propagation: Polars `concat_str(ignore_nulls=False)` nulls
     the whole key when ANY field is null; `pc.binary_join_element_wise`
     `null_handling="emit_null"` matches — pinned by a NAMED fixture, and the
     single-field key skips concat entirely (blocker.py:308-311), mirrored.
   - Corpus additions: dictionary-encoded arrays (parquet produces them;
     explicit decode before cast), ChunkedArrays with unequal chunking into
     the join kernel, timestamp/time/decimal128 alongside date32.
4. **The seam has no relational ops yet.** `frame.py` = 5 Frame ops + 5 Column ops.
   Every join/group_by/filter/with_columns in the engine is a new op. Five join
   call sites need named variants: inner self-join with suffix + `<` dedupe
   (scorer.py:391), left x2 on id_a/id_b (scorer.py:1377/1846), inner on
   cluster_id (golden.py:1302), inner with left_on/right_on (golden.py:1306).
5. **Gates.** Standing per-PR perf gate is `throughput-gate` (cost-based:
   candidate_pairs +15% tol, reduction_ratio, recall — no wall-clock). Wall
   benches (`bench-zero-config.yml`, `scale-audit.yml`) are dispatch-only; the
   spec's 1M self-join suspect is measured via dispatch before W2c merges, not
   in-PR. The differential harness (`scripts/diff_frame_backends.py` + frozen
   anchors) already drives `run_dedupe` end-to-end under both backends.
6. **Import hygiene is already done.** All six engine files defer Polars via
   `_polars_lazy`/TYPE_CHECKING/local imports; no eager dtype literals remain.
   W2 is runtime op-set work only.

---

## Batch W2a — fused-prep derivation dual-backend (polars-free covered spine)

### Design

Two new **seam-level derivation ops** on `Frame` (frame.py), following the
established discipline (semantic op, both backends, delegation-parity test):

```python
def derive_block_key(self, key_fields: Sequence[tuple[str, Sequence[str]]], sep: str = "||") -> "Column":
    """Transformed composite block key: per-field transform chain, then
    concat with `sep`. Mirrors blocker._build_block_key_expr semantics."""

def derive_transformed_column(self, field: str, transforms: Sequence[str]) -> "Column":
    """One field's transform chain applied to its Utf8-cast column.
    Mirrors scorer._get_transformed_values semantics (no precomputed
    __xform__ read at the seam level; that fast path stays in scorer)."""
```

- **PolarsFrame impl**: delegates to the EXISTING expressions —
  `_build_block_key_expr` / `_try_native_chain` + `map_elements` fallback —
  byte-identical by construction. Imports happen at CALL time inside the method
  body (reviewer-verified cycle-safe: frame.py has no engine imports and is
  fully initialized before any method runs); if layering gets noisy, a sibling
  `core/polars_derive.py` mirroring `arrow_derive.py` is the sanctioned shape.
- **ArrowFrame impl**: new `core/arrow_derive.py`:
  - `_cast_utf8_arrow(arr) -> pa.LargeStringArray` — parity-pinned
    stringification (fixture-driven; see hazards above).
  - `_native_chain_arrow(arr, transforms) -> arr | None` — pc twin of
    `_try_native_chain` for the SAME covered set (lowercase, uppercase, strip,
    substring, normalize_whitespace, strip_all, digits_only, alpha_only);
    returns None for anything else INCLUDING address_normalize (falls back).
  - fallback: `arr.to_pylist()` -> per-value `apply_transforms` (nulls stay
    None) -> `pa.array(..., type=pa.large_string())`.
  - composite: `pc.binary_join_element_wise(*cols, sep)`.
- **fused_match.py port**: `run_match_fused_arrow` / `run_match_fused_fs_arrow`
  branch EXPLICITLY on `resolve_frame_backend()`: `polars` (the default) wraps
  the incoming dict into a PolarsFrame exactly as today (`pl.DataFrame(...)
  .cast(pl.Utf8)` round-trip — zero behavior change for every current user);
  `arrow` builds an ArrowFrame via `pa.table(dict)`. `to_frame(dict)` must NOT
  be the selector — an unconditional dict->ArrowFrame coercion would silently
  flip the default fused path to the new derivation (reviewer finding; violates
  the no-default-flip non-goal).
- **FS path is NOT wire-through** (reviewer finding): `_field_values_for_block`
  (probabilistic.py:1312-1333) does Polars value EXTRACTION (`block_df[f].to_list()`
  on the pre-cast frame) before its pure-Python transform loop. Refactor it to
  accept `list[str | None]` per field; the fused FS prep feeds it via a third,
  smaller seam op `utf8_values(field) -> list[str | None]` (Utf8-cast + to_list,
  both backends). The classic probabilistic caller keeps its Polars extraction
  unchanged (ports in W2c/W2e).
- **Op contract: cast-then-chain.** `derive_transformed_column` pins "cast the
  column to Utf8 FIRST, then apply the chain" — the fused caller relies on the
  frame-wide pre-cast today, and `_get_transformed_values`' raw fallback
  (`scorer.py:431`, no cast) would hand ints to `apply_transforms` otherwise.
  PolarsFrame's delegation pre-casts before the fallback path.
- `to_frame()` gains `dict[str, pa.Array]` acceptance (builds `pa.table` ->
  ArrowFrame) for EXPLICIT arrow-lane callers; and `to_frame`'s isinstance
  order is fixed: Frame/pa.Table/dict checks FIRST, the `pl.DataFrame` check
  LAST and guarded by `"polars" in sys.modules` (a real pl.DataFrame input
  implies polars is already imported) — otherwise the `_LazyPolars` proxy's
  `__getattr__` imports polars during the isinstance and the task-5 tripwire
  trips on our own plumbing (reviewer BLOCKER).

### Tasks

1. **Parity fixtures first** (`tests/test_arrow_derive_parity.py`):
   corpus of columns (utf8 with nulls/empties, int64, float64 with NaN/inf,
   bool, date32, large lists of mixed case/whitespace) x transform chains
   (each native op alone, chained pairs, soundex/metaphone/qgram/token_sort
   fallbacks, empty chain). Assert ArrowFrame derivation == PolarsFrame
   derivation VALUE-FOR-VALUE (nulls aligned), and both == per-value
   `apply_transforms` oracle where defined. Every hazard in recon-finding 3 is
   a named test.
2. `core/arrow_derive.py` implementation to green those fixtures.
3. `frame.py`: the two ops on the protocol + both backends + delegation-parity
   tests in `test_frame_seam.py` / `test_frame_seam_arrow.py`.
4. `fused_match.py` port (both entry points + multipass wire-through +
   `_field_values_for_block` list-in refactor); existing tests in
   `test_fused_match.py` unedited and green (they pin the brute oracle);
   NEW arrow-lane twins of the three brute-oracle tests appended (each run
   under `GOLDENMATCH_FRAME=arrow` via monkeypatched env).
5. **Spine proof test**: covered config, input as `pa.Table`,
   `GOLDENMATCH_FRAME=arrow`, assert `polars` absent from the call: run in a
   subprocess with an import-tripwire (`sys.modules` check after run) proving
   pyarrow-in -> match_fused -> clusters-out touches zero Polars. (The pipeline
   CALLER still materializes via Polars today — the tripwire scopes the
   fused-prep call itself, documented in the test.)
6. Differential harness: add a third fixture dataset whose config uses a
   soundex block key + lowercase/strip field transforms THROUGH the fused path
   (current two datasets run classic); re-freeze anchors deliberately.
7. Docs: spec W2 row annotated (W2a shipped), tuning.mdx note if any env
   behavior changed (none expected).

### W2a acceptance

- All existing tests green (fused suite unedited).
- New parity fixtures green on both backends.
- Spine tripwire test proves the covered fused prep is polars-free under arrow.
- Differential lane green incl. the new fused-path dataset.
- `throughput-gate` green (cost-based; fused prep does not change candidates).

## Batch W2b — relational seam ops + join-semantics fixtures

### Design

Grow `frame.py` with the ops the engine port (W2c/W2d) needs, each with both
backends + delegation parity + null-semantics fixtures. NO engine call sites
change in W2b — this keeps the PR pure-additive and independently mergeable.

Op set (from the classic-engine recon, highest-leverage first):

| Op | Semantics pinned by fixture | Polars impl | Arrow impl |
| --- | --- | --- | --- |
| `self_join_on(key, id_col, suffix="_right")` | THE spec-4.1 named op: inner self-join on `key` + `id < id_right` one-direction dedupe baked in (scorer.py:391-393's exact shape — the generic join + a mask can't express the `<` without Column comparison ops, reviewer finding) | `.join` + filter expr | Acero join + pc.less on the two id cols |
| `join_inner(other, on=None, left_on=None, right_on=None, suffix="_right")` | null keys DON'T match (both engines default); suffix on collision; right key dropped on left_on/right_on | `.join(how="inner")` | `pa.Table.join` (Acero) + suffix normalization |
| `join_left(other, on, suffix)` | unmatched -> nulls; null keys don't match | `.join(how="left")` | `pa.Table.join("left outer")` |
| `rename(mapping)` / `drop(cols)` | the join call sites chain these (golden.py:1304 rename-after-join, scorer.py:1377/1846 rename-before + drop-after — reviewer finding) | `.rename`/`.drop` | `rename_columns`/`drop_columns` |
| `filter_mask(mask: Column)` | boolean take; NULL mask rows DROP (Polars `.filter` and `pc.filter(null_selection_behavior="drop")` agree — pinned because scorer.py:1381/1850's `src_a != src_b` produces nulls on unmatched left-join rows and relies on the drop) | `.filter` | `pc.filter` |
| `filter_valid_key(col)` | drop null + sentinel (`strip.lower in {"", "nan","null","none"}`) — the blocker/scorer guard as ONE op | existing expr | pc chain |
| `group_len(keys)` | key(s) -> counts frame | `group_by.agg(pl.len())` | `pa.TableGroupBy.aggregate([("", "count_all")])` |
| `partition_by_key(key)` | iterate (key, subframe); ORDER pinned as **key-sorted, callers pre-sort** — the one engine call site already sorts by `__cluster_id__` first (golden.py:906-914), and first-appearance order can't fall out of the sort_indices Arrow impl (reviewer finding: the earlier first-appearance pin contradicted the impl) | `partition_by(maintain_order=True)` over pre-sorted input | `pc.sort_indices(stable)` + slice runs |
| `sort(keys)` / `slice(offset, length)` / `take_rows(indices)` | stable sort | `.sort` | `pc.sort_indices(stable)` + `take` |
| `concat_frames(frames)` | vertical, schema-checked | `pl.concat` | `pa.concat_tables` |
| `unique_column(col)` / `Column.max()` / `Column.to_numpy()` | | | |
| `frame_from_columns(dict[str, np.ndarray | pa.Array], schema)` / `empty_frame(schema)` | dtype mapping table pinned | `pl.DataFrame` | `pa.table` |

Join-semantics fixtures (spec risk #1) land BEFORE the arrow impls: null keys,
duplicate keys (row multiplication), suffix collision, left_on/right_on rename
behavior, empty inputs — asserted identical across backends after
canonicalization (row order is NOT part of the contract; callers that need
order sort explicitly, which the fixtures document).

### W2b acceptance

- Delegation-parity tests: every op, both backends, PolarsFrame output ==
  raw-Polars output byte-identical.
- Cross-backend fixtures: canonicalized equality on the semantics corpus.
- No engine file touched -> full suite trivially green.

## Batches W2c / W2d / W2e — scoped, planned per-batch

- **W2c (columnar spine port)**: `find_fuzzy_matches_columnar` /
  `score_blocks_columnar` (scorer), `build_cluster_frames` + `_columnar_presplit`
  (cluster), `build_golden_records_from_frames` (golden) route through W2b ops;
  the 4 scorer/golden joins move to `join_inner`/`join_left`.
  [W2c-execution amendments: `_columnar_pipeline_enabled` is NOT touched (the
  flag stays 0 per the do-not-flip verdict; W2c only ports call sites), and
  the 1M `bench-zero-config.yml` exit gate runs the DEFAULT backend only --
  an arrow e2e 1M is meaningless before W2d moves the ingest boundary; the
  both-backends 1M is W2d's exit gate. Detailed batch plan:
  2026-07-10-goldenmatch-polars-eviction-w2c.md.]
  >10% on a ported op -> kernel per spec 4.3.
- **W2d (blocker + ingest Frame return)**: `_build_static_blocks` group_by loop,
  sentinel filter, sorted-neighborhood sort/slice onto seam ops; `load_file`
  returns a Frame (callers `load_files`/`apply_column_map`/`validate_columns`
  ported); `ingest.py:116`'s `pl.from_arrow` moves INTO PolarsFrame coercion.
- **W2e (expression long tail)**: standardize `_native_*` twins as arrow chains
  (sharing `arrow_derive` vocabulary), matchkey `compute_matchkeys` /
  `precompute_matchkey_transforms`, golden fast-path group-agg survivorship
  (hardest; may justify extending `golden_fused` coverage instead of porting the
  Polars expressions — decided by measurement in-batch, per spec 4.3's
  "widening fused coverage shrinks classic glue").

Each gets its own plan doc + review before execution, mirroring W0/W1.

## Risks

| Risk | Mitigation |
| --- | --- |
| Utf8-cast stringification drift (floats/dates) breaks sentinel drop | Hazard fixtures FIRST (W2a task 1); every dtype x null/NaN pinned |
| Acero join semantics differ from Polars (nulls, dup keys, suffix) | W2b fixtures land before impls; canonicalized contract excludes row order |
| Fallback `to_pylist()` per-column slower than `map_elements` | Same asymptotic shape (both per-value Python); measured by the differential harness's wall/RSS record; >10% -> `goldenflow._native` arrow chain kernels (`apply_chain_ops_arrow`, `soundex_arrow`) are the ready escalation, gated on `fusable_kernel_names` coverage checks |
| Two derivation implementations drift over time | PolarsFrame impl DELEGATES to the existing expressions (no fork); parity fixtures run in CI on every goldenmatch change |
| Frozen-anchor churn | Anchors re-frozen only in W2a task 6, with the diff shown in the PR |

## Verification (every batch)

- Full goldenmatch pytest lanes green (sharded + heavy + fallback).
- `goldenmatch_frame_diff` advisory lane green.
- `throughput-gate` green.
- ruff + pyright + `check_map_elements.py` clean (new fallback code paths must
  carry the `GM-MAP-ELEMENTS` discipline where applicable).
- Local targeted runs via main venv + worktree PYTHONPATH
  (`POLARS_SKIP_CPU_CHECK=1`, `GOLDENMATCH_NATIVE=0` where kernels not needed).
