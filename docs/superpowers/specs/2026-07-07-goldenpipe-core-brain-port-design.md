# goldenpipe-core brain port — design

**Status:** approved (design gate)
**Date:** 2026-07-07
**Builds on:** the auto-config brain (slices 1 #1526, 2 #1536) + scale-hint merge (#1541), all merged. The complete Python brain (`plan_pipeline`, rules, `apply_scale_hints`, `band_of`, structs) is on `main`.

## 1. Goal

Port the auto-config **brain** (the decision core) to the existing
`goldenpipe-core` Rust crate as the cross-surface source of truth, with the
pure-Python brain proven to reproduce it byte-for-byte via the established
two-leg parity harness. `goldenpipe-core` already ports the *engine* layer
(`resolve`/`router`/`decisions`/`auto_config`); this adds the *decision* layer.

Going forward Rust is canonical; today it stays a parity-checked port — Python
still executes at runtime (same as the shipped engine-layer port). Making the
Rust core the runtime path, and the WASM/TS-consumer parity, are separate slices.

## 2. Architecture

Extend `packages/rust/extensions/goldenpipe-core` with a new `planner.rs` module
(the brain), three JSON faces in `json.rs`, and native exports in
`goldenpipe-native`. Add three hand-authored vector files. The Python
`goldenpipe/core/_planner_json.py` gains matching bridge fns that call the
**real** `goldenpipe.autoconfig_planner`. The parity harness gates all three
against the shared vectors:

- **Rust `golden_vectors.rs`** (`cargo test`, CI) — the authoritative
  cross-language gate: the Rust `*_json` must reproduce the vectors.
- **Python Leg A** (`test_planner_parity.py`, box-runnable) — the pure-Python
  brain (via `_planner_json`) must reproduce the same vectors.
- **Python Leg B** (native wheel, CI, skip-guarded) — the wheel must reproduce
  them.

Because the vectors are **hand-authored** (a human-specified contract, matching
the existing `auto_config.json`/`skip_if.json` style — not a Python dump), Leg A
is a genuine gate, not a tautology: Python must independently match the
contract. Convenient box property: hand-author the expecteds, run Leg A against
the real Python brain, and Python surfaces **most** hand-authoring errors (wrong
stages, wrong `rule_name`, wrong numeric values) immediately.

**Two byte-shape properties Leg A does NOT catch — the Rust `cargo test` is the
strict gate for both:**
- **Numeric type.** Python `==` treats `1 == 1.0` as true; serde_json `Value`
  equality treats integer `1` and float `1.0` as **unequal**. So a vector with
  `"confidence": 1` passes Leg A but fails `golden_vectors.rs`. **Author every
  confidence/density field as a float literal** (`1.0`, `0.7`, `0.3`, `0.95`,
  `0.0` — never `1` or `0`).
- **Key order.** Both legs compare maps/dicts order-insensitively (serde_json's
  `preserve_order` `IndexMap` PartialEq and Python `dict` `==` both ignore key
  order), so the vectors do NOT pin evidence key order. A dedicated order test
  enforces it (§10, required).

### Why a new `planner.rs` module

The brain's `PlannedStage`/`PipePlan` are distinct from the engine's
`PlannedSpec`/`ExecutionPlan` in `model.rs`. A separate module keeps the decision
structs from colliding with the engine structs and mirrors the Python split
(`autoconfig_planner.py` vs the engine modules).

## 3. Rust — `goldenpipe-core/src/planner.rs` (new)

Structs (serde; `JsonMap = serde_json::Map<String, Value>` with `preserve_order`,
already the crate's convention). `config`/`evidence` are `JsonMap`.

```rust
use serde::{Deserialize, Serialize};
use crate::model::JsonMap;

#[derive(Deserialize)]
pub struct PipeProfile {
    pub n_rows: i64,
    pub n_cols: i64,
    pub column_names: Vec<String>,
    pub dtypes: Vec<String>,
    pub inferred_domain: Option<String>,
    pub domain_confidence: f64,
}

#[derive(Deserialize)]
pub struct ComplexityProfile {
    pub max_null_density: f64,
    pub mean_null_density: f64,
}

#[derive(Deserialize)]
pub struct PlannerInput {
    pub runtime: PipeProfile,
    pub complexity: ComplexityProfile,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct PlannedStage {
    pub name: String,
    pub config: JsonMap,
}

#[derive(Serialize, Deserialize, Clone)]
pub struct PipePlan {
    pub stages: Vec<PlannedStage>,
    pub rule_name: String,
    pub confidence: f64,
    pub evidence: JsonMap,
}
```

Logic — mirror `autoconfig_planner.py` + `autoconfig_planner_rules.py` exactly
(constants, thresholds, first-match order, evidence keys/order):

- `band_of(confidence: f64) -> &'static str` — `>= 0.7` green, `>= 0.4` amber,
  else red.
- `default_evidence(inp: &PlannerInput) -> JsonMap` — inserts, IN ORDER:
  `n_rows, n_cols, inferred_domain, domain_confidence, max_null_density,
  mean_null_density` (matches Python dict insertion order; `preserve_order`
  keeps it, so the key-order test stays green).
- `plan_pipeline(inp: &PlannerInput) -> PipePlan` — an ordered **if-chain**
  (equivalent to Python's first-match `DEFAULT_RULES`): `pathological`
  (`n_rows <= 1` → `[scan, transform]`, conf 1.0) → `confident_schema`
  (`inferred_domain.is_some() && domain_confidence >= 0.5` → `[infer_schema{domain},
  scan, transform, dedupe]`, conf = domain_confidence) → `low_confidence`
  (`inferred_domain.is_none() && max_null_density > 0.6` → `[scan, transform,
  dedupe]`, conf 0.3) → default (`[scan, transform, dedupe]`, conf 0.7).
- `apply_scale_hints(plan: &PipePlan, runtime: &PipeProfile) -> PipePlan` —
  `n_rows < SCALE_ROUTE_MIN_ROWS (1_000_000)` or no `goldenmatch.dedupe` stage →
  clone-return unchanged; else clone stages, and for the `goldenmatch.dedupe`
  stage insert `_dedupe_hints = {"throughput": {"recall_target": 0.95}}` into a
  cloned config, and insert `scale_hinted = true` into cloned evidence.
- Constants: `RED_NULL_DENSITY = 0.6`, `SCALE_ROUTE_MIN_ROWS = 1_000_000`,
  `_THROUGHPUT_RECALL_TARGET = 0.95`, `GREEN/AMBER` thresholds.

`infer_schema{domain}` config: `{"domain": <inferred_domain>}` — the domain
string, matching Python's `{"domain": p.inferred_domain}`.

## 4. Rust — JSON faces in `goldenpipe-core/src/json.rs`

Mirror the existing `*_json` pattern (deserialize In struct → call typed fn →
serialize; parse errors → `{"err":{"kind":"parse",...}}`):

```rust
pub fn plan_pipeline_json(input: &str) -> String {
    // input: PlannerInput -> output: PipePlan
}
pub fn apply_scale_hints_json(input: &str) -> String {
    // input: {plan: PipePlan, runtime: PipeProfile} -> output: PipePlan
}
pub fn band_of_json(input: &str) -> String {
    // input: bare float -> output: JSON string band (like skip_if_falsy_json)
}
```

- `plan_pipeline_json` deserializes a `PlannerInput` and returns
  `serde_json::to_string(&plan_pipeline(&inp))`.
- `apply_scale_hints_json` deserializes an `{plan, runtime}` In struct and
  returns `serde_json::to_string(&apply_scale_hints(&plan, &runtime))`.
- `band_of_json` parses a bare `f64` (like `skip_if_falsy_json` parses a bare
  `Value`) and returns `serde_json::to_string(&band_of(x))` (a quoted JSON
  string).

Add module `pub mod planner;` to `lib.rs` and the three fns' `use`.

## 5. Native exports — `goldenpipe-native/src/lib.rs`

Add three `#[pyfunction]` shims (identical marshaling pattern to the existing
five) and register them in the `_native` pymodule:

```rust
#[pyfunction]
fn plan_pipeline_json(input: &str) -> String { goldenpipe_core::json::plan_pipeline_json(input) }
#[pyfunction]
fn apply_scale_hints_json(input: &str) -> String { goldenpipe_core::json::apply_scale_hints_json(input) }
#[pyfunction]
fn band_of_json(input: &str) -> String { goldenpipe_core::json::band_of_json(input) }
// + m.add_function(wrap_pyfunction!(...)) x3
```

**WASM (`goldenpipe-wasm`) deferred** — that's the TS-consumer slice.

## 6. Vectors — `goldenpipe-core/tests/vectors/{plan_pipeline,apply_scale_hints,band_of}.json`

Hand-authored `[{comment, input, expected}]` arrays, same style as
`auto_config.json`. Coverage:

- **`band_of.json`** — boundaries: 0.7→green, 0.71→green, 0.69→amber, 0.4→amber,
  0.39→red, 0.0→red.
- **`plan_pipeline.json`** — one case per rule: pathological (`n_rows=1`),
  confident_schema (`inferred_domain="finance", domain_confidence=0.8`),
  low_confidence (`inferred_domain=null, max_null_density=0.7, n_rows` large),
  default (`inferred_domain=null, max_null_density=0.0`), and a
  confident-but-weak (`domain_confidence=0.4` → default) case. Each `expected` is
  the full `PipePlan` (stages with name+config, rule_name, confidence, evidence
  with all six keys in order).
- **`apply_scale_hints.json`** — below threshold (`n_rows=999_999` →
  unchanged), at threshold with dedupe (`n_rows=1_000_000` → dedupe gains
  `_dedupe_hints`, evidence gains `scale_hinted:true`), no-dedupe plan
  (pathological shape at scale → unchanged). Input is `{plan, runtime}`.

Every `input` `runtime`/`PlannerInput` object MUST include ALL `PipeProfile`
fields — `n_rows, n_cols, column_names, dtypes, inferred_domain,
domain_confidence` — even though `column_names`/`dtypes` never reach the output
(the Rust `PipeProfile` has no `#[serde(default)]`, so an omitted field fails to
deserialize; Python `PipeProfile(**runtime)` likewise raises). All
confidence/density values are **float literals** (`1.0`, `0.95`, `0.0` — see §2).

Authoring workflow (box): write the expecteds, run Leg A
(`test_planner_parity.py`) against the real Python brain, fix any mismatch
Python reports, commit. This validates most of the contract against Python before
Rust exists — but NOT numeric type or key order (§2), which only the Rust
`cargo test` (and the §10 order test) enforce.

## 7. Python bridge — `goldenpipe/core/_planner_json.py`

Add three `*_json(input: str) -> str` fns that call the **real**
`goldenpipe.autoconfig_planner` and serialize to the exact vector shapes:

- `plan_pipeline_json` — parse input dict → build `PlannerInput(PipeProfile(**runtime),
  ComplexityProfile(**complexity))` → `plan_pipeline(inp)` → serialize the
  `PipePlan` to `{"stages": [{"name","config"}...], "rule_name", "confidence",
  "evidence"}` → `json.dumps`.
- `apply_scale_hints_json` — parse `{plan, runtime}` → reconstruct `PipePlan`
  (stages → `PlannedStage(name, config)`, plus rule_name/confidence/evidence) +
  `PipeProfile(**runtime)` → `apply_scale_hints(plan, runtime)` → serialize.
- `band_of_json` — parse bare float → `band_of(x)` → `json.dumps` (a quoted
  string).

Small serialize/deserialize helpers (`_plan_to_dict`, `_plan_from_dict`,
`_profile_from_dict`) local to the bridge, mirroring the existing
`_planned_to_dict` helper. `PipePlan.evidence` is already a dict — emit as-is
(insertion order preserved by Python dict).

**Import-collision caveat:** `_planner_json.py` already imports `PlannedStage`
from `goldenpipe.engine.resolver` (the engine's planned-stage). The brain's
`PlannedStage` is a DIFFERENT class from `goldenpipe.autoconfig_planner` — import
it under an alias to avoid shadowing, e.g.
`from goldenpipe.autoconfig_planner import PlannedStage as PlanStage, PipePlan,
PipeProfile, ComplexityProfile, PlannerInput, plan_pipeline, apply_scale_hints,
band_of`.

## 8. Parity wiring

- **`goldenpipe-core/tests/golden_vectors.rs`** — add `vec_plan_pipeline`,
  `vec_apply_scale_hints`, `vec_band_of` `#[test]`s calling `run(name, fn)`.
- **`test_planner_parity.py`** — add `("plan_pipeline", PJ.plan_pipeline_json)`,
  `("apply_scale_hints", PJ.apply_scale_hints_json)`, `("band_of",
  PJ.band_of_json)` to BOTH the Leg-A `_CASES` list and the Leg-B native list.
- Native version: bumping `goldenpipe-native` to expose new symbols follows the
  existing republish discipline (Cargo + pyproject in lockstep) — but Leg B is
  skip-guarded, so a stale wheel just skips, it does not fail. The authoritative
  gate is the Rust `cargo test` + Python Leg A.

## 9. Box constraints & gate

- **Box CANNOT `cargo build`** (Rust CI-only, exFAT/toolchain). The Rust
  (`planner.rs`, `json.rs`, native shims, `golden_vectors.rs`) is written against
  this spec + the vectors and validated by CI `cargo test`. Iterate via CI if red
  (grep `^error`, per `feedback_verify_rust_builds_explicitly`).
- **Box-runnable now:** the Python bridge fns + Leg A parity + hand-authoring
  validation (`test_planner_parity.py` Leg A) + `ruff`. So the Python side is
  fully proven locally; only the Rust side waits on CI.

## 10. Testing

- Rust unit tests in `planner.rs` `#[cfg(test)]` (a couple of direct
  `plan_pipeline`/`apply_scale_hints`/`band_of` asserts) + the vector replay in
  `golden_vectors.rs` (CI).
- Python Leg A (box): all three new cases green against the real brain.
- **REQUIRED evidence key-order test** in `json.rs` `#[cfg(test)]` — mirror the
  existing `resolve_json_config_echoes_insertion_order` (json.rs): assert that
  `plan_pipeline_json`'s serialized `evidence` object emits its keys in exactly
  `n_rows, n_cols, inferred_domain, domain_confidence, max_null_density,
  mean_null_density` order (substring/`starts_with` check on the raw JSON, since
  `Value` equality is order-insensitive). Without this, the six-key order — the
  byte-shape contract this port exists to pin — is unguarded by the vectors.

## 11. Non-goals

- WASM export + TS-consumer parity (own slice).
- Porting the glue (`profile_context`/`profile_complexity`/`enforce_confidence`/
  `build_planner_input`) — those are impure (Polars/InferMap/raises) and stay
  Python; only the pure decision core ports.
- Making the Rust core the runtime execution path — stays a parity-checked port.
- Republishing the native wheel is not required for the gate (Leg B is
  skip-guarded); do it as normal release hygiene when convenient.

## 12. File touch list

- `packages/rust/extensions/goldenpipe-core/src/planner.rs` — **new** (structs +
  logic + unit tests).
- `packages/rust/extensions/goldenpipe-core/src/lib.rs` — `pub mod planner;`.
- `packages/rust/extensions/goldenpipe-core/src/json.rs` — three `*_json` faces.
- `packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs` — three
  `#[test]`s.
- `packages/rust/extensions/goldenpipe-core/tests/vectors/{plan_pipeline,apply_scale_hints,band_of}.json`
  — **new** hand-authored.
- `packages/rust/extensions/goldenpipe-native/src/lib.rs` — three `#[pyfunction]`
  + registrations.
- `packages/python/goldenpipe/goldenpipe/core/_planner_json.py` — three bridge
  fns + helpers.
- `packages/python/goldenpipe/tests/core/test_planner_parity.py` — three cases in
  both legs.
