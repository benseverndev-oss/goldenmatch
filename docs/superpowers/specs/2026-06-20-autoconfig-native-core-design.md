# Auto-Config Native Core — design

- **Date:** 2026-06-20
- **Status:** Approved (brainstorm), pre-plan
- **Topic:** Port the deterministic auto-config decision logic to a pyo3-free Rust
  core (`goldenmatch-autoconfig-core`) consumed by Python, WASM/TS, and (free)
  the SQL surfaces, for cross-surface parity and speed.

## Context

Auto-config is a ~10K LOC Python subsystem (12 files under
`packages/python/goldenmatch/goldenmatch/core/autoconfig*.py` +
`complexity_profile.py` / `execution_plan.py` / `runtime_profile.py`). Most of it
is orchestration that is inherently surface-specific: data sampling, the iterative
controller refit loop (which re-runs the *pipeline*), LLM refit policies,
remote-asset verification, and a cross-run memory store. The TypeScript port
(`packages/typescript/goldenmatch/`) re-implements the planner and classifier *by
hand*, which is a live parity-drift source.

The monorepo already has a battle-tested pattern for sharing logic across surfaces:
pyo3-free `*-core` crates (`fingerprint-core`, `graph-core`, `score-core`) wrapped
thinly by `goldenmatch-native` (abi3 wheel), `postgres` (pgrx), and `datafusion-udf`
— all computing byte-identical results without re-entering CPython. This design
extends that pattern to the **deterministic decision layer** of auto-config.

### Goal

One compiled source of truth for the deterministic auto-config decisions, bound to
every language surface (Python `-native` wheel, a WASM build for the JS/TS port,
and pgrx/datafusion for free), all making **byte-identical** decisions. Rust is the
target language (the entire native stack; the only core that already compiles
cleanly to WASM + abi3 + pgrx).

Native speed also **changes the calculus** of auto-config: today the controller
samples small on purpose (`ControllerBudget.for_dataset` sqrt-scales, caps at 20K)
and the entire Chao1 mark-recapture apparatus exists *because* small samples read
cardinality unreliably. Cheap native profiling relaxes that constraint — bigger
samples mean cardinality converges and `estimated_pair_count` can be measured
rather than extrapolated, improving config quality. This is treated as a
**speed-to-quality lever gated behind a bench**, not an assumed win (see §Bigger
samples).

### Non-goals (out of scope for this spec)

- The iterative controller refit loop (`AutoConfigController.run`) — drives the
  pipeline, surface-specific.
- LLM refit / classification policies, remote-asset verification, cross-run memory.
- Data sampling / IO under path A (stays per-surface; path B can absorb it).
- Raising the controller's default sample size — a **downstream, bench-gated**
  change to `ControllerBudget`, not this slice.
- Wiring pgrx / datafusion call sites — the crate becomes *available* to them;
  actually calling it from SQL is an opportunistic follow-up.

## Findings that shaped the design

1. **The planner is almost pure, but reads the environment directly.** The 8 rules
   in `autoconfig_planner_rules.py` read exactly one `ComplexityProfile` field —
   `profile.blocking.estimated_pair_count` — plus `n_rows`, `RuntimeProfile`
   (ram/cpu/disk), and *live environment probes*: `native_enabled("block_scoring")`
   (+ `GOLDENMATCH_PLANNER_BUCKET`), `find_spec("ray")`,
   `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY`, and the `user_backend` override. A
   language-agnostic core cannot probe a Python import or env var, so these become
   **explicit `Capabilities` inputs** — strictly better (testable, no hidden state).

2. **Regex-engine parity is a trap with a clean escape.** Classification is driven
   by name-pattern regexes (`_GEO_PATTERNS`, `_ID_PATTERNS`, …). Two of them use
   lookbehind (`(?<![a-z])city`, `(?<=[a-zA-Z])(?:ID|Id)$`), which the fast
   linear-time Rust `regex` crate deliberately omits. Since the goal is parity with
   *today's Python output*, rewriting the patterns risks the exact drift we are
   killing. Resolution: the **core owns the regexes** via the **`fancy-regex`** crate
   (lookaround support, compiles to WASM), one engine for all surfaces. Perf is a
   non-issue — at most `max_columns=40` names plus a handful of sample values, not a
   hot loop.

3. **The LLM boundary is crisp.** Classification is fully deterministic through
   `profile_columns` line 380; the LLM pass (line 382) only fires when a provider is
   passed AND a column is "ambiguous" (`confidence < 0.8 or col_type in
   {string, numeric}`, excluding already-high-confidence types). The core
   reproduces everything up to 380 and emits a `needs_llm_escalation` flag using that
   exact predicate; the surface decides whether to act on it.

4. **Sampling-parity boundary.** The core is decision-parity *given identical
   inputs* (golden vectors prove this). End-to-end cross-surface parity (same CSV →
   same config in Python vs TS) *additionally* requires the surfaces to sample
   identically (`seed=42`, size 1000). Under path A that sampling is a surface
   concern; **path B can absorb it** by taking the full Arrow column + a
   `SamplePolicy` and sampling deterministically in Rust with a fixed PRNG, so every
   surface gets the identical sample for free.

## Architecture

A new pyo3-free crate `goldenmatch-autoconfig-core` (sibling to the other `-core`
crates, standalone workspace) holds two pure layers.

### Layer 1 — Planner (parity-only)

`decide_plan(input) -> ExecutionPlan`. Ports the 8-rule registry from
`autoconfig_planner_rules.py` verbatim, including `auto_chunk_size`. Microsecond
cost; its value is that every surface picks the same backend from the same logic.

### Layer 2 — Classification (parity + modest speed; the bigger-samples enabler)

`classify_columns(cols) -> Vec<ColumnProfile>`. The core owns the `fancy-regex`
pattern set + the data heuristics + the exact name-vs-data merge precedence. Two
entry points share the same return type:

- **A (default), `stats-in`:** the surface profiles its own frame (polars in
  Python computes `cardinality_ratio` / `null_rate` / `avg_len`, pulls
  `sample_values`) and passes plain serde structs. No Arrow, no dataframe dep →
  **WASM-trivial**.
- **B (opt-in, `feature = "arrow"`), `arrow-in`:** the surface passes an Arrow
  column + a `SamplePolicy`; the core samples + computes the stats itself in one
  native pass. This is the big-sample enabler and unifies sampling across surfaces.

The LLM classification fallback stays per-surface; the core only flags
low-confidence columns via `needs_llm_escalation`.

## Data contracts

### Layer 1

```rust
struct RuntimeProfile { available_ram_gb: f64, cpu_count: u32, disk_free_gb: f64 }

enum BackendName { PolarsDirect, Chunked, Duckdb, Ray, Bucket }
enum ClusteringStrategy { InMemory, PartitionedUnionFind, StreamingCc }
enum SpillThreshold { Ram, Duckdb, DiskPerWorker }   // absence (Python None) modeled Option-side, NOT a variant

struct Capabilities {
    bucket_available: bool,   // surface folds native_enabled("block_scoring") + GOLDENMATCH_PLANNER_BUCKET opt-out
    ray_available: bool,      // find_spec("ray")
    ray_auto_select: bool,    // GOLDENMATCH_ENABLE_DISTRIBUTED_RAY
    user_backend: Option<BackendName>,
}

struct PlannerInput {
    n_rows_full: u64,
    estimated_pair_count: u64,
    runtime: RuntimeProfile,
    caps: Capabilities,
}

struct ExecutionPlan {
    backend: BackendName,
    chunk_size: Option<u64>,
    max_workers: u32,
    pair_spill_threshold: Option<SpillThreshold>,   // Python None -> JSON null (5 of 8 rules); a None enum variant would serialize "none" and break parity
    clustering_strategy: ClusteringStrategy,
    rule_name: String,
}

fn decide_plan(input: &PlannerInput) -> ExecutionPlan
```

`_scoring_backend()` collapses to `if caps.bucket_available { Bucket } else
{ PolarsDirect }`. Rule registry order ports verbatim: `user_override` (first),
`pathological`, `simple` (< 100K rows AND < 50M pairs), `fast_box` (>= 100K rows,
< 50M pairs, >= 32GB RAM), `bucket_suggested` (sub-32GB, <= 750K rows, RAM-safe),
`chunked` (50M–5B pairs, >= 16GB RAM), `ray` (>= 50M rows + caps), `duckdb`
(>= 5B pairs OR < 16GB RAM). Named threshold constants and the `auto_chunk_size`
math (`_CHUNKED_BYTES_PER_ROW=1024`, target 60% RAM, clamp `[10_000, 1_000_000]`)
port byte-for-byte.

### Layer 2

```rust
enum ColType { Email, Name, Phone, Zip, Address, Geo, Identifier,
               Description, Numeric, Date, String, Year, MultiName }

struct ColumnStats {   // path A payload, per column
    name: String,
    dtype: String,
    sample_values: Vec<String>,
    null_rate: f64,
    cardinality_ratio: f64,
    avg_len: f64,
}

struct ColumnProfile {
    name: String,
    dtype: String,
    col_type: ColType,
    confidence: f64,
    null_rate: f64,
    cardinality_ratio: f64,
    avg_len: f64,
    needs_llm_escalation: bool,   // = the line-401 "ambiguous" predicate
}

fn classify_columns(cols: &[ColumnStats]) -> Vec<ColumnProfile>             // A, always
#[cfg(feature = "arrow")]
fn classify_columns_arrow(cols, sample: SamplePolicy) -> Vec<ColumnProfile> // B, opt-in
```

**Verbatim merge precedence** (`profile_columns` lines 350-368) — captured exactly,
note the authoritative set is `{date, geo, identifier, numeric, year}`:

```
name_authoritative = {date, geo, identifier, numeric, year}
if name_type in name_authoritative:        col_type = name_type;  confidence = 0.9
elif name_type and data_type != "string":
    if name_type == data_type:             col_type = name_type;  confidence = min(data_conf + 0.2, 1.0)
    else:                                  col_type = data_type;  confidence = data_conf
elif name_type:                            col_type = name_type;  confidence = 0.6
else:                                      col_type = data_type;  confidence = data_conf
```

Ports of `_classify_by_name`, `_classify_by_data`, and `_guess_type` (the value
heuristic, in `core/profiler.py` ~line 27).

**Two parity subtleties the port must NOT unify** (golden vectors guard both, but
flagged so the porter does not "clean them up"):

1. **Two different cardinality denominators.** `ColumnProfile.cardinality_ratio`
   (autoconfig.py:337) is `len(set(values)) / total_rows` (sample height *incl.*
   nulls). But the internal cardinality *guard* inside `_classify_by_data`
   (autoconfig.py:214) is `len(set(values)) / len(values)` (non-null count), and it
   additionally gates on `len(values) >= 10`. These are deliberately different;
   keep both.
2. **Branch evaluation order inside `_classify_by_data` is load-bearing.** Order:
   numeric-shaped cardinality guard → year detection (`all(_is_year(v))`) →
   `type_map` lookup → `multi_name` (avg_len > 30, delim_ratio >= 0.7,
   avg_delims >= 2) → `description` (avg_len > 50). Year is checked *before* the
   generic `type_map`; `multi_name` is checked *before* `description`. Preserve the
   short-circuit precedence exactly.

## Bindings

This spec delivers **two** bindings so cross-surface parity is actually demonstrated:

- **Python:** `#[pyfunction]` shims added to the *existing* `goldenmatch-native`
  crate (already depends on the `-core` crates). Python dispatches via the existing
  `_native_loader` + a new `native_enabled("autoconfig")` gate;
  `apply_planner_rules` / `profile_columns` keep pure-Python as the
  `try-native / except` fallback. Republish discipline applies: bump
  `Cargo.toml` AND `pyproject.toml` in lockstep; confirm the new symbols are in the
  *published* wheel (per the documented stale-wheel footgun).
- **WASM/TS:** a `wasm-bindgen` wrapper builds the core to WASM; the TS port
  consumes it and **deletes its hand-maintained planner/classifier**. Must honor the
  TS edge-safety rule (`src/core/**`, no `node:*`) so it loads in edge runtimes.
  The npm build pipeline gains a wasm-pack/wasm-bindgen step.

**pgrx / datafusion:** available for free (pyo3-free core); not wired in this slice.

## Parity harness (the anti-drift contract)

A shared `golden/` directory of JSON vectors `input → expected {ExecutionPlan |
ColumnProfile}`, **generated from the current Python implementation as the oracle**.
Coverage:

- **Planner:** every threshold boundary (100K / 50M / 5B rows-or-pairs, 16GB / 32GB
  RAM, 750K bucket ceiling), `user_override`, ray/duckdb fall-through (caps on/off),
  `auto_chunk_size` clamp edges, pathological `n_rows <= 1`.
- **Classifier:** lookbehind names (`city`, `municipality`, `recordID`, `account_no`),
  date/geo precedence, the cardinality guard (numeric-shaped >= 0.95 → identifier),
  multi_name (comma/semicolon delimited), description (avg_len > 50), year detection.

Every binding (Rust unit test, Python, WASM/TS) loads the **same** JSON and asserts.
CI runs all three; drift fails the build. This harness is the load-bearing
verifier — static review will not catch a subtle threshold/precedence divergence.

## Bigger samples (speed-to-quality lever, gated)

A bench (`scripts/bench_autoconfig_native.py` + a `workflow_dispatch` job on
`large-new-64GB`) measures:

1. Wall of native `classify` + `decide_plan` vs Python on representative frames.
2. The actual lever: `estimated_pair_count` / cardinality **error vs ground truth as
   a function of sample size**, with native vs Python wall at each size.

The downstream change "raise the controller's default sample size" lands **only if**
this bench shows a real quality lift at affordable wall. Until then the core just
makes big-sample profiling cheap; `ControllerBudget` is untouched. (Per the
performance-audit measure-first lesson + `feedback_verify_perf_not_just_ship`.)

## Rollout — validate-then-cutover

Matches the #663 Arrow-native posture:

- Native auto-config ships **default-off** behind `GOLDENMATCH_AUTOCONFIG_NATIVE`.
- Pure-Python stays the default **and** the parity oracle — never deleted.
- Flip the default only after golden parity is green on **every** surface.
- `GOLDENMATCH_NATIVE=0` remains the master off-switch.

## Deliverables

1. `goldenmatch-autoconfig-core` crate (Layers 1+2, `fancy-regex`, optional `arrow`).
2. Python binding via `goldenmatch-native` + loader gate + try/except dispatch.
3. WASM/TS binding; TS port's parallel planner/classifier deleted.
4. Golden-vector parity harness, run by all three surfaces in CI.
5. The bigger-samples bench + workflow.

**Staging (for the plan):** core + Python + harness land first; WASM/TS second.

## Testing strategy

- Rust unit tests: rule table per-rule, classifier per-pattern, `auto_chunk_size`.
- Shared golden-vector parity tests in Rust, Python, and WASM/TS.
- Python: existing `tests/test_autoconfig_regressions.py` stays green with the
  native path forced on.
- TS: `tests/parity/` gains auto-config cases.

## Open questions / risks

- **`_guess_type` exact body** — located at `core/profiler.py` ~line 27; a
  threshold-ratio value heuristic with no hidden deps. Port verbatim in the plan.
- **WASM packaging in the npm build** — wasm-pack output, edge-runtime load path,
  bundle size; the highest-uncertainty piece.
- **Float determinism** across Rust/Python/JS for the confidence arithmetic and
  `auto_chunk_size` — all integer/`f64` with simple ops; golden vectors guard it.
- **Wheel republish discipline** — adding symbols requires republishing
  `goldenmatch-native` with lockstep version bumps.

## References

- `packages/python/goldenmatch/goldenmatch/core/autoconfig_planner_rules.py`,
  `autoconfig_planner.py`, `execution_plan.py`, `runtime_profile.py`,
  `autoconfig.py` (`profile_columns`, `_classify_by_*`, pattern constants).
- `docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md` (the planner).
- `_native_loader.py` discovery order + `native_enabled` gating.
- Prior `-core` split: `fingerprint-core` + `native` + `postgres`.
- Memory: `project_663_arrow_kernels` (validate-then-cutover), `project_688_*` +
  `feedback_verify_perf_not_just_ship` (measure the wall), `reference_pgrx_test_incompatible`.
