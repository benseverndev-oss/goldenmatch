# Scope: bridge core-API arrow-port → drop the `#1747 [polars]` stopgap

> **Status:** scoped 2026-07-14. This is Wave 1 Stage 5 of
> `2026-07-14-goldenmatch-zero-config-arrow-polars-free.md`, expanded after the
> "just port convert.rs" prototype was reverted — the port is NOT one file. It's
> `convert.rs::json_to_polars_df` + the bridge's whole Python core-API surface.
> Scope only; do NOT implement without approval.

## STATUS 2026-07-14 — aux fixes LANDED (PR #1770); DROP blocked on cross-source match arrow-port

**Merged/merging (PR #1770):** the aux SOURCE fixes — validate / anomaly /
autoconfig_memory / profiler discriminators + seam routing. Byte-identical on
every polars-present lane; `native=0` arrow tripwire green.

**The `[polars]` DROP itself is blocked — now diagnosed to exact lines** (the
initial "native-present" reading was only half right; the real blockers are the
bridge GLUE + a deliberate cross-source-match polars gap, both reproducible on a
native-less box once you go through the bridge wrappers). PR #1770's first
revision (convert.rs→arrow + local polars-free install) failed the `rust` lane on
5 tests. Root causes, precisely:

1. **Bridge Rust GLUE reads polars-only accessors on the returned frame — TRIVIAL.**
   `validate_table` (api.rs:1513-1515) + `autofix_table` (api.rs:1536-1537) call
   `.getattr("height")` + `.call_method0("to_dicts")`; the match path
   (api.rs:476) calls `target_df.getattr("height")`. With convert.rs feeding a
   `pa.Table`, these functions RETURN a `pa.Table` (`ArrowFrame.native`), which
   has `.num_rows`/`.to_pylist()` — not `.height`/`.to_dicts()`. FIX: a robust
   Rust helper (`hasattr num_rows -> num_rows else height`, same for
   to_pylist/to_dicts) at those 4 sites. (My local aux probe called the Python
   fns directly, bypassing the glue — that's why it missed these.)

2. **The WHOLE MATCH PIPELINE is polars — a large parallel feature port.**
   Traced from the CI failure through the layers: `autoconfig.py:3787-3792`
   force-coerces the target to polars when a reference is present + `:3841`
   requires a `pl.DataFrame` reference (both easy to relax). But that only exposes
   the real wall: `pipeline.py::run_match_df` (line ~4114) casts via `pl.Utf8`,
   `.lazy()`, `pl.lit`, `_add_row_ids`, `pl.concat` into a **combined
   `pl.LazyFrame`**, and `pipeline.py::_run_match_pipeline` (line 3816) takes that
   `combined_lf: pl.LazyFrame` and runs the entire match on it (`.collect()`/
   `.lazy()`). **The match pipeline is a SEPARATE pipeline from the arrow-ported
   `_run_dedupe_pipeline` and was never included in the dedupe arrow eviction
   (W1–W5 + the D-descent, dozens of PRs).** Verified locally: relaxing the
   autoconfig coerce just moves the crash into `run_match_df`'s `pl.Utf8` cast on
   a `pa.Table`. Arrow-porting match = replicating the dedupe-pipeline arrow
   effort for `run_match_df` + `_run_match_pipeline` — a large, recall-correctness-
   sensitive feature with its own design + linkage-parity harness, NOT a
   bridge-stopgap change. **This is the true, non-negotiable blocker: `[polars]`
   cannot drop while the bridge exposes `match` and the match pipeline is polars.**

3. **Probabilistic autoconfig v0 history** — `autoconfig_controller.py:390`
   `_run_pipeline_sample` on a probabilistic config imports polars building the
   v0 virtual history entry (`ModuleNotFoundError` in CI). Same class as #2 (the
   FS sample pipeline isn't arrow-native for that path).

**Verdict:** #1 is trivial; #2 (+#3) is the substantial blocker — the cross-source
match / reference controller must go arrow-native first, which is its own scoped
feature (recall-correctness sensitive, out of this plan's clean scope). The
convert.rs→arrow + ci.yml drop are held for the follow-up that lands #1 + the
match arrow-port together, verified in CI.

### CORRECTED SCOPE (feasibility proven 2026-07-14) — the match port is FEASIBLE, not multi-week

The needed arrow primitives ALREADY EXIST (verified by probe on this box, native=0):
- `Frame.derive_matchkey([(field, transforms)…])` — arrow-native matchkey derivation
  (the dedupe eager path uses it; `compute_matchkeys` is the OLD polars-only twin).
- `Frame.derive_standardized_column(col, names)` — arrow standardize.
- `find_exact_matches(frame, mk)` — DUAL-REP (accepts a seam Frame OR a `pa.Table`).
- `build_blocks(pa.Table, blocking)` — arrow ✓; `score_buckets` — arrow ✓.
- `_apply_domain_extraction` — already dual-rep.
- Combined-frame build: `pyarrow.concat_tables` (the seam has no vstack).

So the drop is NOT "replicate the multi-week dedupe eviction." It is a **focused
arrow port of `run_match_df` + `_run_match_pipeline`** (~275 lines, pipeline.py
3816-4092): build the combined frame as a `pa.Table`; thread it through the stages
above (swap `compute_matchkeys`→`derive_matchkey`, `apply_standardization`→
`derive_standardized_column`, and the output stage's `combined_df.filter(pl.col)`
/`.to_dicts()`/`pl.DataFrame(rows)` → pyarrow-compute filters / `.to_pylist()` /
`pa.Table.from_pylist`). Best done as a NEW frame-lane-gated arrow path (mirroring
`_run_dedupe_pipeline`'s `_frame_lane_eligible` gate at pipeline.py:1000) so the
existing polars path stays byte-identical, with the arrow path proven by a
**linkage-parity harness** (arrow-input `match_df` must produce byte-identical
matched pairs to polars-input — the eviction's own standard). Then #1 (bridge
glue) + convert.rs→arrow + ci.yml drop land alongside. A partial exact-only port
that only greens the two bridge match tests is NOT acceptable — it would leave
fuzzy/weighted `match_df` broken polars-absent in production.

Est: one focused session (design + parity harness + staged port + CI). Not this
session's scope, but a well-defined next step — not the open-ended blocker the
earlier notes implied.

## The central fact

The `rust` / `rust_pgrx` / coverage lanes carry a `[polars]` extra (the #1747
stopgap) purely so the bridge's Python side has polars at runtime. Dropping it
**uninstalls polars**, so every place the bridge or the Python API it calls does
`import polars` breaks.

`packages/rust/extensions/bridge/src/convert.rs::json_to_polars_df` (line 14) is
the ONE conversion helper every bridge entrypoint uses — 15 call sites in
`api.rs` (172, 260, 335, 381, 382, 471, 472, 630, 681, 1256, 1480, 1530, 1552,
1577, 1611) all call it. It does `py.import("polars")` → `pl.read_json`. So this
is not an aux-only problem: **the primary dedupe/autoconfig/match path builds a
polars frame here too**, then hands it to `dedupe_df`/`auto_configure_df` (which
now ACCEPT arrow, but are being fed polars).

So the port is two linked moves:

1. **`convert.rs`**: `json_to_polars_df` → `json_to_arrow_df` (build a `pa.Table`
   via `json.loads` + `pyarrow.Table.from_pylist`); `polars_df_to_json` →
   arrow-first (`to_pylist` + `json.dumps`, keep the `write_json` branch for a
   genuine polars frame passing through). No `import polars` anywhere in the
   file.
2. **Every Python API the 15 call sites feed** must accept a `pa.Table`. The
   primary path already does; the aux core-API surface is the remaining work.

The reverted prototype did (1) but not (2), so the aux fns got a `pa.Table` and
died on `'pyarrow.lib.Table' has no attribute 'height'`.

## P0 empirical findings (2026-07-14, polars-blocked probe on this box)

Probed every bridge-invoked Python API with a `pa.Table` + `import polars`
blocked. Result: the aux surface was SMALLER than the scope feared — most were
already seam-ported. Leaks found + fixed in **PR-1**:

- `validate_dataframe` — `isinstance(df, pl.DataFrame)` discriminator (flipped to
  `isinstance(df, pa.Table)`; rest already seamed).
- `detect_anomalies` — raw `df.columns`/`df.to_dicts()` → Arrow seam
  (`.to_arrow().column_names` / `.to_pylist()`).
- `autoconfig_memory.profile_signature` — raw `df.columns`/`df.schema[c]`
  (the auto_configure primary path hit it) → arrow-schema branch, polars branch
  byte-identical.
- `profiler.profile_dataframe` — `_columns_as_polars_series` wrapped arrow cols
  as `pl.Series`; now wraps only when polars importable (byte-identical for every
  polars-present env, incl. bridge-with-polars) and threads name + semantic dtype
  on the polars-free path. `profile_column` discriminator made polars-safe.

Already arrow-safe (NO port): `auto_fix_dataframe`, `evaluate_clusters`,
`compare_clusters`, `preflight`, `postflight`, `train_em`, `score_probabilistic`
(the FS pair only builds polars on the Rust side via `build_probabilistic_frame`).

Arrow tripwire: `tests/test_coreapi_aux_no_polars.py` (subprocess, polars blocked,
runs all aux fns on a `pa.Table`, asserts `polars not in sys.modules`).

The stopgap drop (**PR-2**, self-contained) — the Rust side + the CI flip:

- `convert.rs`: `json_to_polars_df`→`json_to_arrow_df` (`json.loads` +
  `pyarrow.Table.from_pylist`) + `polars_df_to_json`→`arrow_df_to_json`
  (`to_pylist`+`json.dumps`; polars `write_json` kept for a genuine polars frame).
  Dead polars-only IPC converters + their broken test deleted.
- `api.rs`: `build_probabilistic_frame`→arrow (append an Int64 `__row_id__`);
  the MatchResult column-extract (api.rs:500) `pl.from_arrow`→`table.column().to_pylist()`.
- `ci.yml`: the `rust` + `rust_coverage` bridge lanes install the LOCAL base
  goldenmatch (`pip install ${GITHUB_WORKSPACE}/packages/python/goldenmatch`) with
  NO `[polars]` extra — so the drop is **self-validating against current source**
  (no PyPI release needed). Base deps carry pyarrow but NOT polars / goldencheck /
  goldenflow (the latter two were never in `[polars]` either, only `quality` /
  `transform`), so the quality/transform steps degrade gracefully and the whole
  bridge path runs polars-free. The `cargo test --workspace` bridge suite (dedupe
  / match / autoconfig / validate / profile / autofix / anomaly / preflight /
  postflight / FS) is the tripwire — `GOLDENMATCH_BRIDGE_REQUIRE_PY=1` makes any
  polars ImportError a hard fail.

Local validation: bridge builds + clippy/fmt clean; match/convert/probabilistic
tests green (arrow column extract works, byte-identical). The full polars-absent +
native-present run is CI-only (this box lacks native `golden_fused`).

## Per-function classification (the bridge's Python core-API surface)

Mapped bridge fn → Python API → polars footprint (from source inspection):

| Bridge fn (api.rs) | Python API | Location | Arrow status | Port size |
|---|---|---|---|---|
| `run_dedupe`/`auto_configure`/`match_sources` (172/260/335/…) | `dedupe_df`/`auto_configure_df`/`match_df` | `_api.py` | **arrow-native already** (Wave-1 flag flip) | none (just feed arrow) |
| `evaluate` (1367) | `evaluate_clusters` | `core/evaluate.py` | 0 `pl.*` — takes pairs/clusters JSON, not a row-df | none |
| `compare_clusters` (1431) | `compare_clusters` | `cli/compare.py` | 0 `pl.*` — takes clusters JSON, not a row-df | none |
| `validate_table` (1469→df@1480) | `validate_dataframe` | `core/validate.py` | **partially seamed** (has `isinstance(df, pl.DataFrame)`@84, `frame.height`/`.columns`; residual `pl.Series`/`pl.Utf8`@164) | **S** (discriminator flip + degrade 1 pl.Series) |
| `autofix_table` (1522→df@1530) | `auto_fix_dataframe` | `core/autofix.py` | polars, unseamed (`df: pl.DataFrame`) | **M** (real port) |
| `detect_anomalies` (1544→df@1552) | `detect_anomalies` | `core/anomaly.py` | polars, unseamed (`df.to_dicts()`@80, `df.columns`@70) | **S** (`.column_names`/`.to_pylist()`) |
| `preflight` (1568→df@1577) | `autoconfig_verify.preflight` | `core/autoconfig_verify.py` | UNVERIFIED — inspect | ? |
| `postflight` (1601→df@1611) | `autoconfig_verify` + `dedupe_df` | `core/autoconfig_verify.py` | `dedupe_df` arrow-native; wrapper UNVERIFIED | ? |
| `profile_table` (1248→df@1256) | `profile_dataframe` | `core/profiler.py` | 3 `pl.*` sites — inspect (may be seamed by PR-2) | S–M |
| `train_em` (1641) | FS EM (`config.schemas`) | `core/probabilistic.py` | UNVERIFIED — FS math, likely takes rows | ? |
| `score_probabilistic` (1692) | FS scorer | `core/probabilistic.py` | UNVERIFIED | ? |

**Confirmed-failing (measured this session):** `validate_table`, `autofix_table`,
`detect_anomalies`. **Confirmed-clean (0 polars):** `evaluate`, `compare_clusters`.
**Primary path:** already arrow-native. **Still to inspect (5):** `preflight`,
`postflight`, `profile_table`, `train_em`, `score_probabilistic`.

## Port pattern (established this session — reuse it verbatim)

Every one of these follows the `transform.py`/`quality.py`/`blocker.py` fixes
already merged this wave:

1. Discriminator: `import pyarrow as _pa; _is_arrow = isinstance(df, _pa.Table)`
   — NEVER `isinstance(df, pl.DataFrame)` (that triggers the lazy `pl` import and
   fails when polars is uninstalled).
2. Use the frame seam (`goldenmatch.core.frame.to_frame`) for `.height` /
   `.columns` / `.column(c)` / `.filter_mask` so both lanes share one code path.
3. Any residual raw-polars construction (`pl.Series`, `pl.Utf8`,
   `pl.from_arrow`) either routes through the seam or is guarded with
   `try: import polars except ImportError: <degrade + one-time WARNING>`.
4. `.to_dicts()` → arrow `.to_pylist()`; `.columns` → `.column_names`.

## Staging (each = one PR, parity gate + arrow-blocked tripwire + ruff)

- **P0 — inspect the 5 unknowns.** Read `autoconfig_verify.preflight/postflight`,
  `profiler.profile_dataframe`, `probabilistic.train_em`/`score_probabilistic`;
  confirm arrow status + size. Cheap; do first so the train is fully sized.
- **P1 — `convert.rs` arrow flip + primary-path proof.** `json_to_arrow_df` +
  `arrow_df_to_json`; confirm the 9 primary call sites (dedupe/autoconfig/match)
  run green with the arrow converter (they already accept arrow). Gate: bridge
  primary tests green.
- **P2 — port `validate_dataframe`** (S: discriminator + degrade the empty
  `pl.Series` quarantine column).
- **P3 — port `detect_anomalies`** (S: `.column_names` + `.to_pylist()`).
- **P4 — port `auto_fix_dataframe`** (M: the real unseamed one).
- **P5 — port the P0 unknowns** (profile/preflight/postflight/FS) — likely 1–2
  PRs depending on P0 findings.
- **P6 — drop the stopgap.** Remove `[polars]` from `rust`/`rust_pgrx`/coverage
  lanes in `.github/workflows/ci.yml`; add a bridge tripwire (pip-installed
  goldenmatch, native present, `import polars` blocked → `run_dedupe` +
  `auto_configure` + each aux fn complete). Gate: bridge lanes green with no
  `[polars]`; flip the tracker item to resolved.

## Effort estimate

~6–8 PRs. The Rust change (P1) is mechanical and prototyped. The Python ports
are all the SAME small pattern already applied 4× this wave (transform/quality/
blocker/block_analyzer). `evaluate`/`compare_clusters`/primary are free. The only
unknown is the 5 P0 functions — most likely S each, but FS (`train_em`/
`score_probabilistic`) could be M if they iterate rows in polars.

## Risk / notes

- Local box lacks native `match_fused`/`golden_fused`, so it exercises FALLBACK
  paths — a local green is NOT proof for the full-native `rust` lane. Every stage
  needs the arrow-blocked subprocess tripwire, and P6 must confirm in CI.
- Bridge builds+tests locally on Windows (per extensions CLAUDE.md): `PYO3_PYTHON`
  = venv python, python313.dll dir on PATH, venv `Lib/site-packages` on
  PYTHONPATH, `ARROW_DEFAULT_MEMORY_POOL=system`.
- `arrow_df_to_json` output must byte-match the current `polars_df_to_json`
  (column order, null rendering, float formatting) or the bridge's JSON-contract
  tests drift — parity-gate the round-trip.
