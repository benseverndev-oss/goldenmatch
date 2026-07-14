# Scope: bridge core-API arrow-port → drop the `#1747 [polars]` stopgap

> **Status:** scoped 2026-07-14. This is Wave 1 Stage 5 of
> `2026-07-14-goldenmatch-zero-config-arrow-polars-free.md`, expanded after the
> "just port convert.rs" prototype was reverted — the port is NOT one file. It's
> `convert.rs::json_to_polars_df` + the bridge's whole Python core-API surface.
> Scope only; do NOT implement without approval.

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

Remaining for the stopgap drop (**PR-2**): the Rust side — `convert.rs`
`json_to_polars_df`→`json_to_arrow_df` + `polars_df_to_json` arrow-first +
`api.rs::build_probabilistic_frame`→arrow, then drop `[polars]` from the
`rust`/`rust_pgrx`/coverage lanes.

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
