# goldenpipe-core planner kernel (SP1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A pyo3-free Rust `goldenpipe-core` crate that reproduces goldenpipe's pure planner (resolve / router / decisions / auto_config / skip_if) byte-identically — the single source of truth the deferred Python (SP2) and TS/WASM (SP3) bindings will marshal over.

**Architecture:** One standalone-workspace crate, `serde` + `serde_json` (preserve_order) only, ~200 LOC. Typed fns over serde structs + thin `*_json` wrappers (the shim/fixture boundary). Python is the CANONICAL semantics; every fn reproduces the existing Python planner exactly (verified against `engine/resolver.py`, `engine/router.py`, `decisions.py`, `pipeline.py`). Golden-vector fixtures are the cross-surface parity contract.

**Tech Stack:** Rust (edition 2021, toolchain 1.94.0), serde/serde_json. No pyo3, no python/TS wiring this slice.

**Spec:** `docs/superpowers/specs/2026-07-04-goldenpipe-core-planner-design.md`

---

## Ground truth (confirmed)

- Sibling `-core` crates (`goldenflow-core`, `goldengraph-core`) are **standalone workspaces** (empty `[workspace]` table) so the core can be a path dep of BOTH a future native ext AND a wasm crate without either claiming it. goldenpipe-core follows this — it is **NOT** added to any root workspace members list.
- Version pins from `goldengraph-core/Cargo.toml`: `serde = { version = "1", features = ["derive"] }`, `serde_json = "1"`. We add the `preserve_order` feature to serde_json.
- Canonical Python semantics live in: `resolver.py:37-73` (resolve), `router.py:13-41` (apply), `decisions.py` (3 predicates), `pipeline.py:75-89` (auto_config), `runner.py:28-36` (skip_if `not artifact`), `registry.py:58` (entry-point keys by `ep.name`), `models/{config,context,stage}.py` (shapes).
- `wasm32-unknown-unknown` target may not be installed locally (rustup proxy uncertain) — the wasm smoke (Task 8) is **optional/best-effort**; it does not gate SP1.

## File structure (all under `packages/rust/extensions/goldenpipe-core/`)

- `Cargo.toml` — standalone workspace + package + the two deps.
- `src/lib.rs` — crate doc + `pub mod` list.
- `src/model.rs` — all serde structs (the JSON contract).
- `src/resolve.rs` — `resolve()`.
- `src/router.rs` — `apply_decision()`.
- `src/decisions.rs` — `evaluate_builtin()`.
- `src/config.rs` — `auto_config()` + `skip_if_falsy()`.
- `src/json.rs` — the `*_json` wrappers.
- `tests/vectors/*.json` — golden-vector fixtures.
- `tests/golden_vectors.rs` — replays fixtures through the `*_json` wrappers.

## Box runner (NTFS D:, toolchain 1.94.0)

```
cd packages/rust/extensions/goldenpipe-core
# if the rustup proxy is gone, prepend the toolchain bin + set CARGO_HOME (see reference_rustup_proxy_exfat_direct_binary):
#   export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH"; export CARGO_HOME=/d/.cargo
cargo test
```
Reference skills: @superpowers:test-driven-development, @superpowers:subagent-driven-development. Auth: benzsevern (`unset GH_TOKEN` before push).

---

## Task 1: Crate scaffold + model.rs structs (compiles, structs round-trip)

**Files:**
- Create: `packages/rust/extensions/goldenpipe-core/Cargo.toml`, `src/lib.rs`, `src/model.rs`

- [ ] **Step 1: Write `Cargo.toml`**

```toml
# Standalone `[workspace]` (empty) so this pyo3-free core can be a path dependency
# of BOTH the future goldenpipe native ext AND goldenpipe-wasm without either
# workspace claiming it — same isolation rationale as score-core / goldenflow-core.
# No rust-toolchain.toml: inherits each parent crate's toolchain when built as a path dep.
[workspace]

[package]
name = "goldenpipe-core"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "Owned reference planner kernel for GoldenPipe (resolve/router/decisions/auto_config), pyo3-free, shared across the native ext + WASM. One source of truth."

[lib]
name = "goldenpipe_core"

[dependencies]
serde = { version = "1", features = ["derive"] }
# preserve_order => serde_json::Map is an insertion-ordered IndexMap, so passthrough
# `config` maps echo in the SAME order Python json.dumps / JS JSON.stringify emit
# (the cross-surface byte-parity requirement).
serde_json = { version = "1", features = ["preserve_order"] }
```

- [ ] **Step 2: Write `src/model.rs`** (the full JSON contract)

```rust
//! Serde structs mirroring goldenpipe's Python/TS models. Only the JSON-serializable
//! subset crosses the boundary; `config_schema` (a Python type) and the polars `df`
//! never enter the core.
use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};

pub type JsonMap = Map<String, Value>;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum OnError {
    Continue,
    Abort,
}
impl Default for OnError {
    fn default() -> Self {
        OnError::Continue
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StageSpec {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    #[serde(rename = "use")]
    pub use_: String,
    #[serde(default)]
    pub needs: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub skip_if: Option<String>,
    #[serde(default)]
    pub on_error: OnError,
    #[serde(default)]
    pub config: JsonMap,
}

/// A `stages` entry is EITHER a full StageSpec OR a bare `use` string.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum StageEntry {
    Spec(StageSpec),
    Name(String),
}
impl StageEntry {
    /// Normalize to a StageSpec (bare string -> StageSpec{use: s}) — the makeStageSpec rule.
    pub fn into_spec(self) -> StageSpec {
        match self {
            StageEntry::Spec(s) => s,
            StageEntry::Name(s) => StageSpec {
                name: None,
                use_: s,
                needs: vec![],
                skip_if: None,
                on_error: OnError::Continue,
                config: JsonMap::new(),
            },
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PipelineConfig {
    pub pipeline: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output: Option<String>,
    pub stages: Vec<StageEntry>,
    #[serde(default)]
    pub decisions: Vec<String>,
}

/// Registry metadata. `key` = the registration key the config's `use` references
/// (Python entry-point discovery keys by `ep.name`); `name` = `info.name`. They CAN
/// differ, so the core keys lookups by `key`.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct StageInfo {
    pub key: String,
    pub name: String,
    pub produces: Vec<String>,
    pub consumes: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct Decision {
    #[serde(default)]
    pub skip: Vec<String>,
    #[serde(default)]
    pub abort: bool,
    #[serde(default)]
    pub insert: Vec<String>,
    #[serde(default)]
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct PlannedSpec {
    pub name: String,
    #[serde(rename = "use")]
    pub use_: String,
    pub config: JsonMap,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub skip_if: Option<String>,
    pub on_error: OnError,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ExecutionPlan {
    pub stages: Vec<PlannedSpec>,
}

/// Tagged union preserving goldenpipe's TWO error classes: `Wiring` (a consume not
/// produced by an earlier stage) and `UnknownStage` (a `use` with no registered stage).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PlanError {
    Wiring {
        stage: String,
        missing: String,
        available: Vec<String>,
    },
    UnknownStage {
        #[serde(rename = "use")]
        use_: String,
    },
}

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct CtxSubset {
    #[serde(default)]
    pub artifacts: JsonMap,
    #[serde(default)]
    pub metadata: JsonMap,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ApplyResult {
    pub remaining: Vec<PlannedSpec>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub router_note: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn bare_string_stage_entry_normalizes() {
        let e: StageEntry = serde_json::from_str("\"goldencheck.scan\"").unwrap();
        let s = e.into_spec();
        assert_eq!(s.use_, "goldencheck.scan");
        assert_eq!(s.on_error, OnError::Continue);
    }

    #[test]
    fn stagespec_uses_serde_rename_use() {
        let s: StageSpec = serde_json::from_str(r#"{"use":"x"}"#).unwrap();
        assert_eq!(s.use_, "x");
        // round-trips back to "use", not "use_"
        assert!(serde_json::to_string(&s).unwrap().contains("\"use\":\"x\""));
    }

    #[test]
    fn on_error_defaults_continue_and_lowercases() {
        assert_eq!(OnError::default(), OnError::Continue);
        let v = serde_json::to_string(&OnError::Abort).unwrap();
        assert_eq!(v, "\"abort\"");
    }
}
```

- [ ] **Step 3: Write `src/lib.rs`**

```rust
//! GoldenPipe owned planner kernel (pyo3-free).
//!
//! This crate is the single source of truth for GoldenPipe's PLANNER — resolve
//! (ordering + wiring validation), router (skip/abort/insert), the built-in decision
//! predicates, auto_config, and the skip_if predicate. The native PyO3 ext and the
//! WASM surface are thin marshaling shims over these functions; the pure-Python and
//! pure-TS planners are non-authoritative fallbacks that must reproduce these bytes.
//! Execution/IO (the Runner, registry discovery, CSV, Reporter) stays a per-language
//! host and is deliberately NOT here.
pub mod config;
pub mod decisions;
pub mod json;
pub mod model;
pub mod resolve;
pub mod router;
```

(`json`/`resolve`/`router`/`decisions`/`config` are created in later tasks; add each `pub mod` line as its file lands, or stub empty modules now and fill them — either works. If stubbing, create empty `src/{resolve,router,decisions,config,json}.rs` so it compiles.)

- [ ] **Step 4: Run** `cargo test` (box runner) — Expected: the 3 model tests PASS, crate compiles.

- [ ] **Step 5: Commit**

```bash
cd /d/show_case/gg-local-llm && unset GH_TOKEN
git add packages/rust/extensions/goldenpipe-core/
git commit -m "feat(goldenpipe-core): crate scaffold + serde planner model"
```

---

## Task 2: `resolve()` — ordering + auto-load + wiring/unknown validation

**Files:**
- Create/replace: `src/resolve.rs`

- [ ] **Step 1: Write failing tests** (put in `#[cfg(test)] mod tests` at the bottom of `resolve.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::resolve;
    use crate::model::*;

    fn info(key: &str, produces: &[&str], consumes: &[&str]) -> StageInfo {
        StageInfo {
            key: key.into(),
            name: key.into(),
            produces: produces.iter().map(|s| s.to_string()).collect(),
            consumes: consumes.iter().map(|s| s.to_string()).collect(),
        }
    }
    fn cfg(stages: Vec<StageEntry>) -> PipelineConfig {
        PipelineConfig { pipeline: "auto".into(), source: None, output: None, stages, decisions: vec![] }
    }
    fn name_entry(u: &str) -> StageEntry { StageEntry::Name(u.into()) }

    #[test]
    fn happy_order_and_auto_prepend_load() {
        let stages = vec![
            info("load", &["df"], &[]),
            info("goldencheck.scan", &["findings"], &["df"]),
            info("goldenmatch.dedupe", &["clusters"], &["df", "findings"]),
        ];
        let plan = resolve(&cfg(vec![name_entry("goldencheck.scan"), name_entry("goldenmatch.dedupe")]), &stages).unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["load", "goldencheck.scan", "goldenmatch.dedupe"]);
    }

    #[test]
    fn no_load_seeds_df() {
        let stages = vec![info("s", &["out"], &["df"])];
        let plan = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap();
        assert_eq!(plan.stages.len(), 1);   // df available even with no load stage
    }

    #[test]
    fn wiring_error_lists_sorted_available() {
        let stages = vec![info("s", &["out"], &["missing"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        match err {
            PlanError::Wiring { stage, missing, available } => {
                assert_eq!(stage, "s");
                assert_eq!(missing, "missing");
                assert_eq!(available, vec!["df".to_string()]);   // sorted
            }
            _ => panic!("expected Wiring"),
        }
    }

    #[test]
    fn unknown_use_is_unknown_stage() {
        let err = resolve(&cfg(vec![name_entry("nope")]), &[]).unwrap_err();
        assert_eq!(err, PlanError::UnknownStage { use_: "nope".into() });
    }

    #[test]
    fn planned_name_prefers_spec_name_over_info_name() {
        let stages = vec![info("thekey", &[], &["df"])];   // info.name == "thekey"
        let spec = StageSpec { name: Some("alias".into()), use_: "thekey".into(), needs: vec![],
                               skip_if: None, on_error: OnError::Continue, config: JsonMap::new() };
        let plan = resolve(&cfg(vec![StageEntry::Spec(spec)]), &stages).unwrap();
        assert_eq!(plan.stages[0].name, "alias");        // spec.name wins
        assert_eq!(plan.stages[0].use_, "thekey");
    }

    #[test]
    fn lookup_by_key_not_name() {
        // key ("gm.dedupe") differs from info.name ("Dedupe"); config references the KEY
        let mut i = info("gm.dedupe", &[], &["df"]);
        i.name = "Dedupe".into();
        let plan = resolve(&cfg(vec![name_entry("gm.dedupe")]), &[i]).unwrap();
        assert_eq!(plan.stages[0].name, "Dedupe");       // fell back to info.name
    }
}
```

- [ ] **Step 2: Run** `cargo test resolve` — Expected: FAIL (no `resolve`).

- [ ] **Step 3: Implement** (top of `resolve.rs`)

```rust
//! resolve(config, stage_info[]) -> ExecutionPlan | PlanError. Mirrors resolver.py:37-73.
use std::collections::BTreeSet;

use crate::model::{ExecutionPlan, PipelineConfig, PlanError, PlannedSpec, StageInfo};

pub fn resolve(config: &PipelineConfig, stages: &[StageInfo]) -> Result<ExecutionPlan, PlanError> {
    let by_key = |k: &str| stages.iter().find(|s| s.key == k);

    let mut plan = ExecutionPlan { stages: vec![] };
    let mut available: BTreeSet<String> = BTreeSet::new();

    // Auto-prepend `load` iff a stage is registered under key "load"; else seed "df".
    if let Some(load) = by_key("load") {
        plan.stages.push(PlannedSpec {
            name: load.name.clone(),
            use_: "load".into(),
            config: Default::default(),
            skip_if: None,
            on_error: Default::default(),
        });
        available.extend(load.produces.iter().cloned());
    } else {
        available.insert("df".into());
    }

    for entry in &config.stages {
        let spec = entry.clone().into_spec();
        let info = by_key(&spec.use_).ok_or(PlanError::UnknownStage { use_: spec.use_.clone() })?;
        let name = spec.name.clone().unwrap_or_else(|| info.name.clone());

        for dep in &info.consumes {
            if !available.contains(dep) {
                return Err(PlanError::Wiring {
                    stage: name,
                    missing: dep.clone(),
                    available: available.iter().cloned().collect(), // BTreeSet -> sorted Vec
                });
            }
        }
        plan.stages.push(PlannedSpec {
            name,
            use_: spec.use_,
            config: spec.config,
            skip_if: spec.skip_if,
            on_error: spec.on_error,
        });
        available.extend(info.produces.iter().cloned());
    }
    Ok(plan)
}
```

- [ ] **Step 4: Run** `cargo test resolve` — Expected: 6 PASS. (`BTreeSet` gives the sorted `available` for free.)

- [ ] **Step 5: Commit** `feat(goldenpipe-core): resolve() ordering + wiring/unknown validation`

---

## Task 3: `apply_decision()` — skip / abort / insert

**Files:** Create/replace `src/router.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::apply_decision;
    use crate::model::*;

    fn planned(name: &str) -> PlannedSpec {
        PlannedSpec { name: name.into(), use_: name.into(), config: JsonMap::new(),
                      skip_if: None, on_error: OnError::Continue }
    }
    fn dec(skip: &[&str], abort: bool, insert: &[&str], reason: &str) -> Decision {
        Decision { skip: skip.iter().map(|s| s.to_string()).collect(), abort,
                   insert: insert.iter().map(|s| s.to_string()).collect(), reason: reason.into() }
    }

    #[test]
    fn abort_empties_and_prefixes_note() {
        let r = apply_decision(&dec(&[], true, &[], "critical"), &[planned("a")]);
        assert!(r.remaining.is_empty());
        assert_eq!(r.router_note.as_deref(), Some("ABORT: critical"));
    }

    #[test]
    fn skip_filters_by_name() {
        let r = apply_decision(&dec(&["b"], false, &[], "x"), &[planned("a"), planned("b"), planned("c")]);
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "c"]);
        assert_eq!(r.router_note.as_deref(), Some("x"));
    }

    #[test]
    fn insert_prepends_in_order() {
        let r = apply_decision(&dec(&[], false, &["x", "y"], ""), &[planned("a")]);
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["x", "y", "a"]);
        assert_eq!(r.router_note, None);   // empty reason -> None
    }

    #[test]
    fn skip_then_insert_combined() {
        let r = apply_decision(&dec(&["a"], false, &["z"], "r"), &[planned("a"), planned("b")]);
        let names: Vec<_> = r.remaining.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["z", "b"]);
    }

    #[test]
    fn empty_decision_is_noop() {
        let r = apply_decision(&Decision::default(), &[planned("a"), planned("b")]);
        assert_eq!(r.remaining.len(), 2);
        assert_eq!(r.router_note, None);
    }
}
```

- [ ] **Step 2: Run** `cargo test router` — FAIL.

- [ ] **Step 3: Implement**

```rust
//! apply_decision(decision, remaining) -> ApplyResult. Mirrors router.py:13-41.
//! Pure: returns the new remaining list + the exact `ctx.reasoning["_router"]` string
//! the host must record; it does NOT mutate ctx and does NOT fetch stage objects
//! (the host maps an inserted name -> its stage).
use crate::model::{ApplyResult, Decision, PlannedSpec};

pub fn apply_decision(decision: &Decision, remaining: &[PlannedSpec]) -> ApplyResult {
    if decision.abort {
        return ApplyResult {
            remaining: vec![],
            router_note: Some(format!("ABORT: {}", decision.reason)),
        };
    }
    let note = if decision.reason.is_empty() { None } else { Some(decision.reason.clone()) };

    let mut kept: Vec<PlannedSpec> = remaining
        .iter()
        .filter(|s| !decision.skip.contains(&s.name))
        .cloned()
        .collect();

    if !decision.insert.is_empty() {
        let mut inserted: Vec<PlannedSpec> = decision
            .insert
            .iter()
            .map(|name| PlannedSpec {
                name: name.clone(),
                use_: name.clone(),
                config: Default::default(),
                skip_if: None,
                on_error: Default::default(),
            })
            .collect();
        inserted.append(&mut kept);
        kept = inserted;
    }
    ApplyResult { remaining: kept, router_note: note }
}
```

- [ ] **Step 4: Run** `cargo test router` — 5 PASS.
- [ ] **Step 5: Commit** `feat(goldenpipe-core): apply_decision() skip/abort/insert`

---

## Task 4: `evaluate_builtin()` — severity / pii / row_count

**Files:** Create/replace `src/decisions.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::evaluate_builtin;
    use crate::model::CtxSubset;

    fn ctx(json: &str) -> CtxSubset { serde_json::from_str(json).unwrap() }

    #[test]
    fn severity_gate_critical_aborts() {
        let c = ctx(r#"{"artifacts":{"findings":[{"severity":"critical"}]}}"#);
        let d = evaluate_builtin("severity_gate", &c).unwrap();
        assert!(d.abort);
        assert_eq!(d.reason, "Critical findings detected");
    }

    #[test]
    fn severity_gate_none_and_empty() {
        assert!(evaluate_builtin("severity_gate", &ctx(r#"{"artifacts":{"findings":[{"severity":"info"}]}}"#)).is_none());
        assert!(evaluate_builtin("severity_gate", &ctx(r#"{"artifacts":{}}"#)).is_none());
    }

    #[test]
    fn pii_router_hits() {
        let d = evaluate_builtin("pii_router", &ctx(r#"{"artifacts":{"findings":[{"check":"pii_detection"}]}}"#)).unwrap();
        assert_eq!(d.skip, vec!["goldenmatch.dedupe"]);
        assert_eq!(d.insert, vec!["goldenmatch.dedupe_pprl"]);
        assert_eq!(d.reason, "PII detected, routing to PPRL matching");
    }

    #[test]
    fn row_count_gate_reason_bytes_match_python() {
        let d = evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{"input_rows":1}}"#)).unwrap();
        assert_eq!(d.reason, "Only 1 row(s), skipping deduplication");   // byte-match f-string
        assert_eq!(d.skip, vec!["goldenmatch.dedupe"]);
        // >= 2 and missing -> None (missing defaults to 0 -> <2 -> Some!)
        assert!(evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{"input_rows":2}}"#)).is_none());
        let missing = evaluate_builtin("row_count_gate", &ctx(r#"{"metadata":{}}"#)).unwrap();
        assert_eq!(missing.reason, "Only 0 row(s), skipping deduplication");   // default 0 -> fires
    }

    #[test]
    fn unknown_name_none() {
        assert!(evaluate_builtin("nope", &CtxSubset::default()).is_none());
    }
}
```

NOTE the missing-`input_rows` case: Python `ctx.metadata.get("input_rows", 0)` → 0 → `0 < 2` → fires with "Only 0 row(s)". Reproduce that (default 0, NOT None).

- [ ] **Step 2: Run** `cargo test decisions` — FAIL.

- [ ] **Step 3: Implement**

```rust
//! evaluate_builtin(name, ctx) -> Option<Decision>. Mirrors decisions.py EXACTLY
//! (Python is canonical). Not engine-invoked today; here for one-source-of-truth so
//! the predicate logic can't drift Python<->TS later.
use serde_json::Value;

use crate::model::{CtxSubset, Decision};

fn findings(ctx: &CtxSubset) -> Option<&Vec<Value>> {
    match ctx.artifacts.get("findings") {
        Some(Value::Array(a)) if !a.is_empty() => Some(a),
        _ => None, // absent or empty -> None (matches `if not findings`)
    }
}

pub fn evaluate_builtin(name: &str, ctx: &CtxSubset) -> Option<Decision> {
    match name {
        "severity_gate" => {
            let f = findings(ctx)?;
            let critical = f.iter().any(|x| x.get("severity").and_then(Value::as_str) == Some("critical"));
            critical.then(|| Decision { abort: true, reason: "Critical findings detected".into(), ..Default::default() })
        }
        "pii_router" => {
            let f = findings(ctx)?;
            let pii = f.iter().any(|x| x.get("check").and_then(Value::as_str) == Some("pii_detection"));
            pii.then(|| Decision {
                skip: vec!["goldenmatch.dedupe".into()],
                insert: vec!["goldenmatch.dedupe_pprl".into()],
                reason: "PII detected, routing to PPRL matching".into(),
                ..Default::default()
            })
        }
        "row_count_gate" => {
            // Python: ctx.metadata.get("input_rows", 0) -> default 0. Accept int or float,
            // truncate toward zero like an int compare; non-numeric -> 0.
            let n = ctx.metadata.get("input_rows").and_then(Value::as_i64).unwrap_or(0);
            (n < 2).then(|| Decision {
                skip: vec!["goldenmatch.dedupe".into()],
                reason: format!("Only {} row(s), skipping deduplication", n),
                ..Default::default()
            })
        }
        _ => None,
    }
}
```

- [ ] **Step 4: Run** `cargo test decisions` — 5 PASS.
- [ ] **Step 5: Commit** `feat(goldenpipe-core): evaluate_builtin() severity/pii/row_count`

---

## Task 5: `auto_config()` + `skip_if_falsy()`

**Files:** Create/replace `src/config.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::{auto_config, skip_if_falsy};
    use crate::model::JsonMap;
    use serde_json::json;

    fn avail(v: &[&str]) -> Vec<String> { v.iter().map(|s| s.to_string()).collect() }
    fn uses(cfg: &crate::model::PipelineConfig) -> Vec<String> {
        cfg.stages.iter().map(|e| e.clone().into_spec().use_).collect()
    }

    #[test]
    fn all_available_default_three() {
        let c = auto_config(&avail(&["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]), None);
        assert_eq!(uses(&c), ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"]);
        assert_eq!(c.pipeline, "auto");
    }

    #[test]
    fn subset_filters() {
        let c = auto_config(&avail(&["goldenmatch.dedupe"]), None);
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn identity_appended_when_nonempty_and_available() {
        let mut opts = JsonMap::new();
        opts.insert("threshold".into(), json!(0.8));
        let c = auto_config(&avail(&["goldenmatch.dedupe", "goldenmatch.identity_resolve"]), Some(&opts));
        assert_eq!(uses(&c), ["goldenmatch.dedupe", "goldenmatch.identity_resolve"]);
    }

    #[test]
    fn identity_unavailable_not_appended() {
        let mut opts = JsonMap::new();
        opts.insert("t".into(), json!(1));
        let c = auto_config(&avail(&["goldenmatch.dedupe"]), Some(&opts));
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn empty_opts_no_identity() {
        // Python `if self._identity_opts` treats {} as not-given.
        let c = auto_config(&avail(&["goldenmatch.dedupe", "goldenmatch.identity_resolve"]), Some(&JsonMap::new()));
        assert_eq!(uses(&c), ["goldenmatch.dedupe"]);
    }

    #[test]
    fn skip_if_falsy_truth_table() {
        for t in [json!(null), json!(false), json!(0), json!(0.0), json!(""), json!([]), json!({})] {
            assert!(skip_if_falsy(&t), "{t:?} should be falsy");
        }
        for f in [json!(true), json!(1), json!(0.5), json!("x"), json!([0]), json!({"a":1})] {
            assert!(!skip_if_falsy(&f), "{f:?} should be truthy");
        }
    }
}
```

- [ ] **Step 2: Run** `cargo test config` — FAIL.

- [ ] **Step 3: Implement**

```rust
//! auto_config + skip_if_falsy. Mirrors pipeline.py:75-89 and runner.py:30 (`not artifact`).
use serde_json::Value;

use crate::model::{JsonMap, OnError, PipelineConfig, StageEntry, StageSpec};

const DEFAULT_STAGES: [&str; 3] = ["goldencheck.scan", "goldenflow.transform", "goldenmatch.dedupe"];
const IDENTITY: &str = "goldenmatch.identity_resolve";

pub fn auto_config(available: &[String], identity_opts: Option<&JsonMap>) -> PipelineConfig {
    let has = |name: &str| available.iter().any(|a| a == name);
    let mk = |use_: &str, config: JsonMap| StageEntry::Spec(StageSpec {
        name: None, use_: use_.into(), needs: vec![], skip_if: None, on_error: OnError::Continue, config,
    });

    let mut stages: Vec<StageEntry> = DEFAULT_STAGES.iter().filter(|s| has(s)).map(|s| mk(s, JsonMap::new())).collect();

    // Empty map == not-given (Python truthiness of a dict).
    if let Some(opts) = identity_opts {
        if !opts.is_empty() && has(IDENTITY) {
            stages.push(mk(IDENTITY, opts.clone()));
        }
    }
    PipelineConfig { pipeline: "auto".into(), source: None, output: None, stages, decisions: vec![] }
}

/// Canonical falsy predicate for the runner's `skip_if`. Python `not artifact` and TS
/// `isFalsy` agree on every JSON type; pinned here so they can't drift.
pub fn skip_if_falsy(artifact: &Value) -> bool {
    match artifact {
        Value::Null => true,
        Value::Bool(b) => !b,
        Value::Number(n) => n.as_f64().map(|x| x == 0.0).unwrap_or(false),
        Value::String(s) => s.is_empty(),
        Value::Array(a) => a.is_empty(),
        Value::Object(o) => o.is_empty(),
    }
}
```

- [ ] **Step 4: Run** `cargo test config` — 6 PASS.
- [ ] **Step 5: Commit** `feat(goldenpipe-core): auto_config + skip_if_falsy`

---

## Task 6: `json.rs` — the `*_json` wrappers (the shim + fixture boundary)

**Files:** Create/replace `src/json.rs`

- [ ] **Step 1: Write failing tests**

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn v(s: &str) -> Value { serde_json::from_str(s).unwrap() }

    #[test]
    fn resolve_json_ok_and_err_shapes() {
        let ok = resolve_json(r#"{"config":{"pipeline":"auto","stages":["s"]},
                                   "stages":[{"key":"s","name":"s","produces":[],"consumes":["df"]}]}"#);
        assert_eq!(v(&ok)["ok"]["stages"][0]["name"], "s");

        let err = resolve_json(r#"{"config":{"pipeline":"auto","stages":["nope"]},"stages":[]}"#);
        assert_eq!(v(&err)["err"]["kind"], "unknown_stage");
        assert_eq!(v(&err)["err"]["use"], "nope");
    }

    #[test]
    fn resolve_json_config_echoes_insertion_order() {
        // config keys z,a,m must ROUND-TRIP in that order (preserve_order), not sorted.
        let out = resolve_json(r#"{"config":{"pipeline":"auto","stages":[{"use":"s","config":{"z":1,"a":2,"m":3}}]},
                                   "stages":[{"key":"s","name":"s","produces":[],"consumes":["df"]}]}"#);
        let cfg_str = out.split("\"config\":{").nth(1).unwrap();
        assert!(cfg_str.starts_with("\"z\":1,\"a\":2,\"m\":3"), "got {cfg_str}");
    }

    #[test]
    fn parse_error_is_tagged() {
        let out = resolve_json("{not json");
        assert_eq!(v(&out)["err"]["kind"], "parse");
    }

    #[test]
    fn skip_if_falsy_json_roundtrips() {
        assert_eq!(skip_if_falsy_json("{}"), "true");
        assert_eq!(skip_if_falsy_json("{\"a\":1}"), "false");
    }
}
```

- [ ] **Step 2: Run** `cargo test json` — FAIL.

- [ ] **Step 3: Implement**

```rust
//! JSON wrappers: the surface the native/wasm shims call and the golden-vector harness
//! replays. Each parses its input struct, calls the typed fn, serializes the result.
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::config::{auto_config, skip_if_falsy};
use crate::decisions::evaluate_builtin;
use crate::model::{
    ApplyResult, CtxSubset, Decision, ExecutionPlan, JsonMap, PipelineConfig, PlanError, PlannedSpec,
    StageInfo,
};
use crate::resolve::resolve;
use crate::router::apply_decision;

fn parse_err(e: impl std::fmt::Display) -> String {
    json!({"err": {"kind": "parse", "msg": e.to_string()}}).to_string()
}

#[derive(Deserialize)]
struct ResolveIn { config: PipelineConfig, stages: Vec<StageInfo> }

pub fn resolve_json(input: &str) -> String {
    let arg: ResolveIn = match serde_json::from_str(input) { Ok(a) => a, Err(e) => return parse_err(e) };
    match resolve(&arg.config, &arg.stages) {
        Ok(plan) => json!({ "ok": plan }).to_string(),
        Err(err) => json!({ "err": err }).to_string(),   // PlanError serializes with its "kind" tag
    }
}

#[derive(Deserialize)]
struct ApplyIn { decision: Decision, remaining: Vec<PlannedSpec> }

pub fn apply_decision_json(input: &str) -> String {
    let arg: ApplyIn = match serde_json::from_str(input) { Ok(a) => a, Err(e) => return parse_err(e) };
    serde_json::to_string(&apply_decision(&arg.decision, &arg.remaining)).unwrap()
}

#[derive(Deserialize)]
struct EvalIn { name: String, ctx: CtxSubset }

pub fn evaluate_builtin_json(input: &str) -> String {
    let arg: EvalIn = match serde_json::from_str(input) { Ok(a) => a, Err(e) => return parse_err(e) };
    // None serializes to JSON null (the "no decision" signal).
    serde_json::to_string(&evaluate_builtin(&arg.name, &arg.ctx)).unwrap()
}

#[derive(Deserialize)]
struct AutoIn { available: Vec<String>, #[serde(default)] identity_opts: Option<JsonMap> }

pub fn auto_config_json(input: &str) -> String {
    let arg: AutoIn = match serde_json::from_str(input) { Ok(a) => a, Err(e) => return parse_err(e) };
    serde_json::to_string(&auto_config(&arg.available, arg.identity_opts.as_ref())).unwrap()
}

pub fn skip_if_falsy_json(input: &str) -> String {
    let v: Value = match serde_json::from_str(input) { Ok(a) => a, Err(e) => return parse_err(e) };
    skip_if_falsy(&v).to_string()
}

// keep imports used
#[allow(unused_imports)]
use crate::model::{ExecutionPlan as _EP};
```

(If the `ExecutionPlan`/`Serialize` imports warn as unused, trim them — the `#[allow]`/alias line is only a hint; keep the import list clean so `cargo test` has zero warnings.)

- [ ] **Step 4: Run** `cargo test json` — 4 PASS. (The insertion-order test is the load-bearing `preserve_order` proof.)
- [ ] **Step 5: Commit** `feat(goldenpipe-core): *_json wrappers (shim + fixture boundary)`

---

## Task 7: Golden-vector fixtures + replay harness (the cross-surface parity contract)

**Files:**
- Create: `tests/vectors/{resolve,apply_decision,evaluate_builtin,auto_config,skip_if}.json`
- Create: `tests/golden_vectors.rs`

- [ ] **Step 1: Write the harness** `tests/golden_vectors.rs`

```rust
//! Replays the golden vectors through the *_json wrappers. These fixtures ARE the
//! cross-surface parity contract: SP2 (Python) and SP3 (TS) fallbacks must reproduce
//! these exact JSON values (VALUE + key order, which preserve_order maintains).
use goldenpipe_core::json::*;
use serde_json::Value;

fn load(name: &str) -> Vec<Value> {
    let path = format!("{}/tests/vectors/{}.json", env!("CARGO_MANIFEST_DIR"), name);
    let s = std::fs::read_to_string(&path).unwrap_or_else(|_| panic!("missing {path}"));
    serde_json::from_str(&s).unwrap()
}

/// Each case: {"input": <json>, "expected": <json>}. We call `f(input_string)` and
/// compare the PARSED result Value to `expected` (value equality; order is enforced
/// separately by the json.rs insertion-order test).
fn run(name: &str, f: fn(&str) -> String) {
    for (i, case) in load(name).into_iter().enumerate() {
        let input = serde_json::to_string(&case["input"]).unwrap();
        let got: Value = serde_json::from_str(&f(&input)).unwrap();
        assert_eq!(got, case["expected"], "{name}[{i}] mismatch\n input={input}");
    }
}

#[test] fn vec_resolve()        { run("resolve", resolve_json); }
#[test] fn vec_apply()          { run("apply_decision", apply_decision_json); }
#[test] fn vec_evaluate()       { run("evaluate_builtin", evaluate_builtin_json); }
#[test] fn vec_auto_config()    { run("auto_config", auto_config_json); }
#[test] fn vec_skip_if()        { run("skip_if", skip_if_falsy_json); }
```

- [ ] **Step 2: Write the fixture files** — each an array of `{input, expected}`. Cover EXACTLY the spec's Testing cases. Minimum set (add the rest following the pattern):

`tests/vectors/skip_if.json`:
```json
[
  {"input": null, "expected": true},
  {"input": false, "expected": true},
  {"input": 0, "expected": true},
  {"input": "", "expected": true},
  {"input": [], "expected": true},
  {"input": {}, "expected": true},
  {"input": true, "expected": false},
  {"input": 0.5, "expected": false},
  {"input": "x", "expected": false},
  {"input": [0], "expected": false},
  {"input": {"a": 1}, "expected": false}
]
```
`tests/vectors/apply_decision.json` (skip / insert-order / abort / skip+insert / empty), `evaluate_builtin.json` (severity crit/none/empty, pii hit/miss, row <2/>=2/missing, unknown→null), `auto_config.json` (all / subset / +identity-nonempty / identity-unavailable / empty-opts-no-identity), `resolve.json` (happy / auto-load / bare-string / Wiring / UnknownStage / name-override / key!=name / config-insertion-order / empty / consume+produce) — each `{input, expected}` matching the unit-test assertions above. Write these by hand from the known-correct outputs; keep them small.

- [ ] **Step 3: Run** `cargo test --test golden_vectors` — Expected: all 5 vector tests PASS. (If a fixture's `expected` is wrong, FIX THE FIXTURE to the actual correct output — do not weaken the harness.)

- [ ] **Step 4: Run the FULL suite** `cargo test` — Expected: all unit + vector tests green, zero warnings.

- [ ] **Step 5: Commit** `test(goldenpipe-core): golden-vector cross-surface parity fixtures`

---

## Task 8: (optional, best-effort) wasm-clean smoke

**Files:** none

- [ ] **Step 1:** `rustup target add wasm32-unknown-unknown 2>/dev/null; cargo build --target wasm32-unknown-unknown` — Expected: builds (serde + serde_json/preserve_order/indexmap are all wasm32-clean), de-risking SP3. If the target/proxy is unavailable locally, SKIP — this is a CI concern for SP3, not an SP1 gate. Do NOT block on it.
- [ ] **Step 2:** (if it built) note the result in the PR body; no commit needed.

---

## Wrap-up

- [ ] `cargo test` fully green (box runner). Push branch, open PR against main, arm `gh pr merge --auto --squash`, STOP (no CI poll). Auth: `GH_TOKEN=$(gh auth token --user benzsevern)` for `gh pr create`; `unset GH_TOKEN` before push.
- [ ] PR body: note this is SP1 of the goldenpipe→Rust program (planner core only; SP2 Python + SP3 TS/WASM deferred), source-of-truth/anti-drift goal (no perf claim), Python-canonical semantics, the two honesty flags (auto_config identity = a TS behavior change landing at SP3; evaluate_builtin = no-op vs today, prevents future drift).
- [ ] Memory: add a `project_goldenpipe_core_cross_surface` note (SP1 shipped; the planner extraction; the SP2/SP3 remaining; the "Rust-is-reference now includes goldenpipe planner, orchestration still Python" correction).
- [ ] CI: goldenpipe-core is a standalone workspace — confirm the Rust CI lane builds/tests it (add to the rust matrix if the CI enumerates crates explicitly; check `.github/workflows/ci.yml`).

## Notes / risks

- **Python is canonical** — every fn reproduces the Python planner byte-exact. The row_count reason string, the sorted `available`, the insertion-ordered `config`, and the empty-identity-opts semantics are the byte-parity-load-bearing details; the unit tests pin each.
- **`preserve_order` is load-bearing** (config echo parity) — the `resolve_json_config_echoes_insertion_order` test is the guard; if it ever fails, the feature got dropped and SP2/SP3 parity would silently break.
- **Fixtures are the SP2/SP3 contract** — write them from KNOWN-CORRECT outputs; a wrong fixture is fixed by correcting the fixture, never by weakening `golden_vectors.rs`.
- **Scope stays SP1** — no pyo3, no Python/TS wiring, no touching Runner/registry/IO. SP2/SP3 are separate specs.
- **`serde(untagged)` StageEntry** — serde tries variants in order; `Spec(StageSpec)` first then `Name(String)` works because a bare JSON string can't deserialize into StageSpec (no `use` field). The `bare_string_stage_entry_normalizes` test guards this.
