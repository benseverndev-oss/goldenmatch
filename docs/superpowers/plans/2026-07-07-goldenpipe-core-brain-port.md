# goldenpipe-core brain port — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the auto-config brain (`plan_pipeline` + rules + `apply_scale_hints` + `band_of` + structs) into the existing `goldenpipe-core` Rust crate, gated by the hand-authored-vector parity harness (Rust `cargo test` authoritative; Python Leg A box-runnable).

**Architecture:** New `planner.rs` module in `goldenpipe-core` mirrors the Python decision core exactly; three JSON faces in `json.rs`; three native `#[pyfunction]` exports; three hand-authored vector files; three Python bridge fns in `_planner_json.py`; parity cases in both legs. Python side is fully box-verifiable; Rust side is written against spec+vectors and gated by CI `cargo test`.

**Tech Stack:** Rust (serde_json, `JsonMap` with `preserve_order`), Python 3.12, pytest, ruff. **Box CANNOT `cargo build`** — Rust is CI-only.

**Spec:** `docs/superpowers/specs/2026-07-07-goldenpipe-core-brain-port-design.md`

---

## Environment

```bash
cd "D:/show_case/gg-local-llm"
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/goldenpipe;packages/python/infermap;packages/python/goldencheck-types"
export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
```
(`;` separator, native Windows.) Branch `feat/goldenpipe-core-brain-port` (off fresh main, spec committed). Rust tasks: verify by grep/eye only — do NOT attempt `cargo build`.

## Ordering rationale

Task 1 (Python bridge + vectors + Leg A) is done FIRST and fully verified on the box — it produces the validated vector contract and proves Python conforms. Tasks 2-4 (Rust) are written against the spec + those committed vectors; they can't compile locally, so they're validated by CI `cargo test` in Task 5. The vectors are the shared contract both sides meet.

---

### Task 1: Python bridge fns + hand-authored vectors + Leg A (box-verified)

**Files:**
- Modify: `packages/python/goldenpipe/goldenpipe/core/_planner_json.py`
- Modify: `packages/python/goldenpipe/goldenpipe/core/_native_loader.py` (Leg B pass-throughs)
- Create: `packages/rust/extensions/goldenpipe-core/tests/vectors/plan_pipeline.json`, `apply_scale_hints.json`, `band_of.json`
- Modify: `packages/python/goldenpipe/tests/core/test_planner_parity.py`

- [ ] **Step 1: Add bridge fns to `_planner_json.py`**

Add the aliased import (the module already imports the ENGINE `PlannedStage` — alias the brain's to avoid collision) near the top imports:
```python
from goldenpipe.autoconfig_planner import (
    ComplexityProfile,
    PipePlan,
    PipeProfile,
    PlannedStage as PlanStage,
    PlannerInput,
    apply_scale_hints,
    band_of,
    plan_pipeline,
)
```

Add helpers + three fns at the end of the file:
```python
def _profile_from_dict(d: dict) -> PipeProfile:
    return PipeProfile(
        n_rows=d["n_rows"], n_cols=d["n_cols"],
        column_names=tuple(d["column_names"]), dtypes=tuple(d["dtypes"]),
        inferred_domain=d["inferred_domain"], domain_confidence=d["domain_confidence"],
    )


def _plan_to_dict(plan: PipePlan) -> dict:
    return {
        "stages": [{"name": s.name, "config": s.config} for s in plan.stages],
        "rule_name": plan.rule_name,
        "confidence": plan.confidence,
        "evidence": plan.evidence,
    }


def _plan_from_dict(d: dict) -> PipePlan:
    return PipePlan(
        stages=tuple(PlanStage(s["name"], s["config"]) for s in d["stages"]),
        rule_name=d["rule_name"], confidence=d["confidence"], evidence=d["evidence"],
    )


def plan_pipeline_json(input_str: str) -> str:
    arg = json.loads(input_str)
    inp = PlannerInput(
        runtime=_profile_from_dict(arg["runtime"]),
        complexity=ComplexityProfile(**arg["complexity"]),
    )
    return json.dumps(_plan_to_dict(plan_pipeline(inp)))


def apply_scale_hints_json(input_str: str) -> str:
    arg = json.loads(input_str)
    plan = _plan_from_dict(arg["plan"])
    runtime = _profile_from_dict(arg["runtime"])
    return json.dumps(_plan_to_dict(apply_scale_hints(plan, runtime)))


def band_of_json(input_str: str) -> str:
    return json.dumps(band_of(json.loads(input_str)))  # bare float in, band string out
```

- [ ] **Step 2: Author the vectors** — create the three files with `[{comment, input, expected}]` arrays. **Every confidence/density value MUST be a float literal (`1.0`, `0.0`, `0.95` — never `1`/`0`).** Every `runtime` object MUST include all six PipeProfile fields.

To get each `expected` correct, run the bridge fn on the box and match its output (then hand-write it with float literals + a comment). Example for one plan_pipeline case:
```bash
"$INTERP" -c "import json; from goldenpipe.core._planner_json import plan_pipeline_json
print(plan_pipeline_json(json.dumps({'runtime':{'n_rows':1,'n_cols':3,'column_names':['a','b','c'],'dtypes':['String','Int64','String'],'inferred_domain':None,'domain_confidence':0.0},'complexity':{'max_null_density':0.0,'mean_null_density':0.0}})))"
```

`plan_pipeline.json` cases (one per rule):
- pathological: `runtime.n_rows=1` (+ full profile), `complexity {0.0,0.0}` → `rule_name:"pathological"`, stages `[scan, transform]`, `confidence:1.0`.
- confident_schema: `inferred_domain:"finance", domain_confidence:0.8` → stages `[infer_schema{"domain":"finance"}, scan, transform, dedupe]`, `confidence:0.8`.
- low_confidence: `inferred_domain:null, complexity.max_null_density:0.7, n_rows:200000` → stages `[scan, transform, dedupe]`, `confidence:0.3`.
- default: `inferred_domain:null, max_null_density:0.0` → stages `[scan, transform, dedupe]`, `confidence:0.7`.
- weak_domain: `inferred_domain:"finance", domain_confidence:0.4` → default plan, `confidence:0.7` (evidence still carries `inferred_domain:"finance", domain_confidence:0.4`).

`apply_scale_hints.json` cases (input is `{plan, runtime}`, both full):
- below threshold: `runtime.n_rows:999999`, plan = a default plan WITH dedupe → expected == input plan (unchanged).
- at scale: `runtime.n_rows:1000000`, plan = default plan with dedupe → dedupe stage config becomes `{"_dedupe_hints":{"throughput":{"recall_target":0.95}}}`, evidence gains `"scale_hinted":true`.
- no dedupe: `runtime.n_rows:5000000`, plan = pathological plan `[scan, transform]` → unchanged.

`band_of.json` cases: `0.7→"green"`, `0.71→"green"`, `0.69→"amber"`, `0.4→"amber"`, `0.39→"red"`, `0.0→"red"`. (input is a bare float; expected is the string.)

- [ ] **Step 3: Add Leg B native pass-throughs to `_native_loader.py`** — REQUIRED, or CI's `goldenpipe_native` lane (which builds the wheel + runs Leg B with `GOLDENPIPE_NATIVE=1`, bypassing the skip) hits `getattr(NL, "plan_pipeline_json")` → `AttributeError` → gate fails. The box never catches this (Leg A only). Mirror the existing five pass-throughs (`_native_loader.py:69-87`), appending:
```python
def plan_pipeline_json(input: str) -> str:
    return _native.plan_pipeline_json(input)


def apply_scale_hints_json(input: str) -> str:
    return _native.apply_scale_hints_json(input)


def band_of_json(input: str) -> str:
    return _native.band_of_json(input)
```
(The wheel symbols these call are added in Task 3 Step 4; `_COMPONENT_SYMBOLS` needs no change — Leg B keys off `native_available`, not `native_enabled`.)

- [ ] **Step 4: Wire Leg A + Leg B cases** in `test_planner_parity.py` — add to the Leg-A `_CASES` list:
```python
("plan_pipeline", PJ.plan_pipeline_json),
("apply_scale_hints", PJ.apply_scale_hints_json),
("band_of", PJ.band_of_json),
```
and the same three `(name, fn_name)` pairs to the Leg-B native `parametrize` list (`("plan_pipeline","plan_pipeline_json")`, etc.).

- [ ] **Step 5: Run Leg A — verify PASS (box)**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/core/test_planner_parity.py -q -k "pure_python"
```
Expected: the three new `test_pure_python_matches_core_vectors[...]` cases PASS (Leg B skips — no wheel on the box). If a case FAILS, Python is telling you your hand-authored `expected` is wrong — fix the vector (NOT the bridge) until green. This is the self-correcting contract validation.

- [ ] **Step 6: Ruff + commit**
```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/goldenpipe/core/_native_loader.py packages/python/goldenpipe/tests/core/test_planner_parity.py
git add packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/goldenpipe/core/_native_loader.py packages/python/goldenpipe/tests/core/test_planner_parity.py packages/rust/extensions/goldenpipe-core/tests/vectors/plan_pipeline.json packages/rust/extensions/goldenpipe-core/tests/vectors/apply_scale_hints.json packages/rust/extensions/goldenpipe-core/tests/vectors/band_of.json
git commit -m "feat(goldenpipe-core): Python bridge + loader pass-throughs + hand-authored brain vectors (Leg A)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 2: Rust `planner.rs` (CI-gated — no local build)

**Files:**
- Create: `packages/rust/extensions/goldenpipe-core/src/planner.rs`
- Modify: `packages/rust/extensions/goldenpipe-core/src/lib.rs` (add `pub mod planner;`)

- [ ] **Step 1: Create `planner.rs`** with structs + logic mirroring the Python EXACTLY:

```rust
//! GoldenPipe auto-config BRAIN (the decision core), ported from
//! autoconfig_planner.py + autoconfig_planner_rules.py. Pure; the pure-Python
//! brain is the non-authoritative fallback proven to reproduce these bytes.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::model::JsonMap;

const RED_NULL_DENSITY: f64 = 0.6;
const CONFIDENT_DOMAIN_THRESHOLD: f64 = 0.5;
const SCALE_ROUTE_MIN_ROWS: i64 = 1_000_000;
const THROUGHPUT_RECALL_TARGET: f64 = 0.95;
const GREEN_THRESHOLD: f64 = 0.7;
const AMBER_THRESHOLD: f64 = 0.4;

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

pub fn band_of(confidence: f64) -> &'static str {
    if confidence >= GREEN_THRESHOLD {
        "green"
    } else if confidence >= AMBER_THRESHOLD {
        "amber"
    } else {
        "red"
    }
}

fn stage(name: &str, config: JsonMap) -> PlannedStage {
    PlannedStage { name: name.to_string(), config }
}

fn default_evidence(inp: &PlannerInput) -> JsonMap {
    // Insertion order MUST match Python default_evidence: n_rows, n_cols,
    // inferred_domain, domain_confidence, max_null_density, mean_null_density.
    let mut m = JsonMap::new();
    m.insert("n_rows".into(), json!(inp.runtime.n_rows));
    m.insert("n_cols".into(), json!(inp.runtime.n_cols));
    m.insert("inferred_domain".into(), json!(inp.runtime.inferred_domain));
    m.insert("domain_confidence".into(), json!(inp.runtime.domain_confidence));
    m.insert("max_null_density".into(), json!(inp.complexity.max_null_density));
    m.insert("mean_null_density".into(), json!(inp.complexity.mean_null_density));
    m
}

pub fn plan_pipeline(inp: &PlannerInput) -> PipePlan {
    let r = &inp.runtime;
    // 1. pathological
    if r.n_rows <= 1 {
        return PipePlan {
            stages: vec![stage("goldencheck.scan", JsonMap::new()), stage("goldenflow.transform", JsonMap::new())],
            rule_name: "pathological".into(),
            confidence: 1.0,
            evidence: default_evidence(inp),
        };
    }
    // 2. confident_schema
    if r.inferred_domain.is_some() && r.domain_confidence >= CONFIDENT_DOMAIN_THRESHOLD {
        let mut cfg = JsonMap::new();
        cfg.insert("domain".into(), json!(r.inferred_domain));
        return PipePlan {
            stages: vec![
                stage("infer_schema", cfg),
                stage("goldencheck.scan", JsonMap::new()),
                stage("goldenflow.transform", JsonMap::new()),
                stage("goldenmatch.dedupe", JsonMap::new()),
            ],
            rule_name: "confident_schema".into(),
            confidence: r.domain_confidence,
            evidence: default_evidence(inp),
        };
    }
    // 3. low_confidence (the sole RED source)
    if r.inferred_domain.is_none() && inp.complexity.max_null_density > RED_NULL_DENSITY {
        return PipePlan {
            stages: default_dedupe_stages(),
            rule_name: "low_confidence".into(),
            confidence: 0.3,
            evidence: default_evidence(inp),
        };
    }
    // 4. default
    PipePlan {
        stages: default_dedupe_stages(),
        rule_name: "default".into(),
        confidence: 0.7,
        evidence: default_evidence(inp),
    }
}

fn default_dedupe_stages() -> Vec<PlannedStage> {
    vec![
        stage("goldencheck.scan", JsonMap::new()),
        stage("goldenflow.transform", JsonMap::new()),
        stage("goldenmatch.dedupe", JsonMap::new()),
    ]
}

pub fn apply_scale_hints(plan: &PipePlan, runtime: &PipeProfile) -> PipePlan {
    if runtime.n_rows < SCALE_ROUTE_MIN_ROWS
        || !plan.stages.iter().any(|s| s.name == "goldenmatch.dedupe")
    {
        return plan.clone();
    }
    let stages = plan
        .stages
        .iter()
        .map(|s| {
            if s.name == "goldenmatch.dedupe" {
                let mut cfg = s.config.clone();
                cfg.insert(
                    "_dedupe_hints".into(),
                    json!({"throughput": {"recall_target": THROUGHPUT_RECALL_TARGET}}),
                );
                PlannedStage { name: s.name.clone(), config: cfg }
            } else {
                s.clone()
            }
        })
        .collect();
    let mut evidence = plan.evidence.clone();
    evidence.insert("scale_hinted".into(), Value::Bool(true));
    PipePlan { stages, rule_name: plan.rule_name.clone(), confidence: plan.confidence, evidence }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inp(n_rows: i64, domain: Option<&str>, dc: f64, max_null: f64) -> PlannerInput {
        PlannerInput {
            runtime: PipeProfile {
                n_rows, n_cols: 2, column_names: vec!["a".into(), "b".into()],
                dtypes: vec!["String".into(), "String".into()],
                inferred_domain: domain.map(|s| s.to_string()), domain_confidence: dc,
            },
            complexity: ComplexityProfile { max_null_density: max_null, mean_null_density: 0.0 },
        }
    }

    #[test]
    fn band_thresholds() {
        assert_eq!(band_of(0.7), "green");
        assert_eq!(band_of(0.69), "amber");
        assert_eq!(band_of(0.39), "red");
    }

    #[test]
    fn rules_fire_in_order() {
        assert_eq!(plan_pipeline(&inp(1, None, 0.0, 0.0)).rule_name, "pathological");
        assert_eq!(plan_pipeline(&inp(100, Some("finance"), 0.8, 0.0)).rule_name, "confident_schema");
        assert_eq!(plan_pipeline(&inp(200000, None, 0.0, 0.7)).rule_name, "low_confidence");
        assert_eq!(plan_pipeline(&inp(100, None, 0.0, 0.0)).rule_name, "default");
        assert_eq!(plan_pipeline(&inp(100, Some("finance"), 0.4, 0.0)).rule_name, "default");
    }

    #[test]
    fn scale_hint_applies_and_noops() {
        let plan = plan_pipeline(&inp(100, None, 0.0, 0.0)); // default, has dedupe
        let hinted = apply_scale_hints(&plan, &inp(1_000_000, None, 0.0, 0.0).runtime);
        assert_eq!(hinted.evidence.get("scale_hinted"), Some(&Value::Bool(true)));
        let below = apply_scale_hints(&plan, &inp(999_999, None, 0.0, 0.0).runtime);
        assert!(below.evidence.get("scale_hinted").is_none());
    }
}
```

- [ ] **Step 2: Add `pub mod planner;`** to `lib.rs` (after `pub mod model;` or in the existing alpha order).

- [ ] **Step 3: Verify by eye/grep (NO cargo build on box)** — confirm: struct field names match the Python/vector JSON keys; `default_evidence` insertion order is the six keys in the exact order; no obvious syntax error (balanced braces, `use` covers `json!`/`Value`/`JsonMap`). Grep for the module: `grep -n "pub mod planner" packages/rust/extensions/goldenpipe-core/src/lib.rs`.

- [ ] **Step 4: Commit**
```bash
git add packages/rust/extensions/goldenpipe-core/src/planner.rs packages/rust/extensions/goldenpipe-core/src/lib.rs
git commit -m "feat(goldenpipe-core): planner.rs — Rust brain (plan_pipeline/apply_scale_hints/band_of)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 3: Rust JSON faces + native exports + parity replay (CI-gated)

**Files:**
- Modify: `packages/rust/extensions/goldenpipe-core/src/json.rs`
- Modify: `packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs`
- Modify: `packages/rust/extensions/goldenpipe-native/src/lib.rs`

- [ ] **Step 1: Add the three JSON faces to `json.rs`.** Add `use crate::planner::{apply_scale_hints, band_of, plan_pipeline, PipePlan, PipeProfile, PlannerInput};` and:
```rust
pub fn plan_pipeline_json(input: &str) -> String {
    let inp: PlannerInput = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(&plan_pipeline(&inp)).unwrap()
}

#[derive(Deserialize)]
struct ScaleHintsIn {
    plan: PipePlan,
    runtime: PipeProfile,
}

pub fn apply_scale_hints_json(input: &str) -> String {
    let arg: ScaleHintsIn = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(&apply_scale_hints(&arg.plan, &arg.runtime)).unwrap()
}

pub fn band_of_json(input: &str) -> String {
    let x: f64 = match serde_json::from_str(input) {
        Ok(a) => a,
        Err(e) => return parse_err(e),
    };
    serde_json::to_string(band_of(x)).unwrap()
}
```

- [ ] **Step 2: Add the REQUIRED evidence key-order test** to `json.rs` `#[cfg(test)] mod tests` (mirror `resolve_json_config_echoes_insertion_order`):
```rust
    #[test]
    fn plan_pipeline_json_evidence_key_order() {
        let out = plan_pipeline_json(
            r#"{"runtime":{"n_rows":100,"n_cols":2,"column_names":["a","b"],
                "dtypes":["String","String"],"inferred_domain":null,"domain_confidence":0.0},
                "complexity":{"max_null_density":0.0,"mean_null_density":0.0}}"#,
        );
        let ev = out.split("\"evidence\":{").nth(1).unwrap();
        assert!(
            ev.starts_with("\"n_rows\":100,\"n_cols\":2,\"inferred_domain\":null,\"domain_confidence\":0.0,\"max_null_density\":0.0,\"mean_null_density\":0.0"),
            "got {ev}"
        );
    }
```

- [ ] **Step 3: Add the replay tests to `golden_vectors.rs`:**
```rust
#[test]
fn vec_plan_pipeline() {
    run("plan_pipeline", plan_pipeline_json);
}
#[test]
fn vec_apply_scale_hints() {
    run("apply_scale_hints", apply_scale_hints_json);
}
#[test]
fn vec_band_of() {
    run("band_of", band_of_json);
}
```

- [ ] **Step 4: Add the three native exports to `goldenpipe-native/src/lib.rs`** (shim + registration, mirroring the existing five):
```rust
#[pyfunction]
fn plan_pipeline_json(input: &str) -> String { goldenpipe_core::json::plan_pipeline_json(input) }
#[pyfunction]
fn apply_scale_hints_json(input: &str) -> String { goldenpipe_core::json::apply_scale_hints_json(input) }
#[pyfunction]
fn band_of_json(input: &str) -> String { goldenpipe_core::json::band_of_json(input) }
```
and in the `_native` pymodule: `m.add_function(wrap_pyfunction!(plan_pipeline_json, m)?)?;` (×3).

- [ ] **Step 5: Verify by eye/grep (no cargo build).** Confirm the `use crate::planner::...` covers every name used; the `ScaleHintsIn` derives `Deserialize`; the three `run(...)` calls reference fns imported by `use goldenpipe_core::json::*;` (already at the top of golden_vectors.rs). Grep the three native registrations exist.

- [ ] **Step 6: Commit**
```bash
git add packages/rust/extensions/goldenpipe-core/src/json.rs packages/rust/extensions/goldenpipe-core/tests/golden_vectors.rs packages/rust/extensions/goldenpipe-native/src/lib.rs
git commit -m "feat(goldenpipe-core): brain JSON faces + native exports + vector replay + key-order test

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

---

### Task 4: Ship (box verify Python; CI gates Rust)

**Files:** none (verification + PR)

- [ ] **Step 1: Box — Python Leg A full + ruff**
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/core/test_planner_parity.py -q -k "pure_python"
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/tests/core/test_planner_parity.py
```
Expected: all pure-python parity cases pass; ruff clean.

- [ ] **Step 2: Rebase + push**
```bash
unset GH_TOKEN; gh auth switch --user benzsevern >/dev/null 2>&1; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q && git rebase origin/main
git push -u origin feat/goldenpipe-core-brain-port --force-with-lease
```

- [ ] **Step 3: Open PR**
```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldenpipe-core-brain-port \
  --title "feat(goldenpipe-core): port the auto-config brain to Rust (parity-gated)" \
  --body "<summary: planner.rs ports plan_pipeline/apply_scale_hints/band_of + structs; three JSON faces + native exports; hand-authored vectors gated by golden_vectors.rs (cargo test, authoritative) + Python Leg A (box) + required evidence key-order test. Python side verified on box; Rust gated by CI. WASM/TS parity + Rust-as-runtime deferred.>

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

- [ ] **Step 4: Watch the FIRST CI run for the Rust build/test result** (this is the one place polling is warranted — the box can't compile Rust, so the cargo build/test result is only visible in CI). Check the rust job:
```bash
gh pr checks <PR#> --repo benseverndev-oss/goldenmatch
# if the rust/cargo job fails, get the error:
gh run view <run-id> --repo benseverndev-oss/goldenmatch --log | grep -nE "^error|error\[|cannot find|mismatched|golden_vectors|panicked|assertion" | head -30
```
If red: fix `planner.rs`/`json.rs`/vectors per the compiler/test error (common: serde field mismatch, a vector `expected` with an integer where the Rust emits a float, a rule value typo), commit, push, re-check. Iterate until the rust job is green. Per `feedback_verify_rust_builds_explicitly`, grep for `^error` explicitly — don't trust a tailing summary.

- [ ] **Step 5: Arm auto-merge + STOP**
```bash
gh pr merge <PR#> --auto --squash   # WITHOUT --delete-branch; if 'strategy set by queue', run: gh pr merge <PR#> --auto
```
Then STOP.

---

## Cross-cutting reminders
- **Box CANNOT `cargo build`.** Rust tasks verified by eye/grep; CI `cargo test` is the gate (watch the first run, Task 4 Step 4).
- **Float literals in every vector** confidence/density (serde_json `1 != 1.0`; the Rust gate is strict).
- **`default_evidence` key order** = `n_rows, n_cols, inferred_domain, domain_confidence, max_null_density, mean_null_density` — pinned by the required json.rs order test.
- **Aliased `PlannedStage as PlanStage`** in the Python bridge (engine `PlannedStage` already imported there).
- Python commit (Task 1) is fully box-green; Rust commits (2,3) gate in CI.
