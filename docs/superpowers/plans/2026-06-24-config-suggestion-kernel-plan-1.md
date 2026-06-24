# Config-Suggestion Kernel — Plan 1: Kernel + Python API + Benchmark

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Arrow-in, pyo3-free `suggest-core` kernel that reviews a finished dedupe run and emits ranked, explainable config-edit suggestions, expose it through a `goldenmatch[native]` Python API, and ship a benchmark harness that scores the suggester against an oracle ranking of real F1 lifts.

**Architecture:** A new pyo3-free Rust crate (`packages/rust/extensions/suggest-core`) ingests the run's Arrow artifacts (scored pairs, clusters, per-column signals), reduces them (reusing `analysis-core` histogram/quantile), runs three v1 rules (threshold, scorer-swap, negative-evidence), generates rationale text, and ranks. A thin `#[pyfunction]` shim in the existing `goldenmatch-native` crate exposes it; a Python adapter (`review_config`) assembles the Arrow inputs from a run result and `apply_suggestion` patches the Pydantic config. The benchmark (`scripts/suggest_quality`, mirroring `scripts/autoconfig_quality`) measures suggestion intelligence against an oracle.

**Tech Stack:** Rust (arrow, serde, pyo3 via the native crate), Python 3.11+ (polars, pyarrow, Pydantic), maturin build (`scripts/build_native.py`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-24-config-suggestion-kernel-design.md`

**Out of scope (Plan 2):** accept/reject persistence (`suggestion_feedback` table, `MemoryStore`), priors-driven learning at runtime, and CLI/MCP/TUI surfaces. This plan implements the kernel's `AcceptancePriors` *input* and ranking math, but the persistence/loading that feeds it lands in Plan 2. Until then the Python API passes empty priors.

---

## Conventions for the implementing engineer

- **Build the native wheel** after Rust changes: `python scripts/build_native.py` from the repo root (this rebuilds `goldenmatch._native` in-tree; see root CLAUDE.md "goldenmatch-native"). In-tree builds pick up new symbols immediately — no republish needed for local dev.
- **Rust unit tests:** `cargo test -p goldenmatch-suggest-core` from `packages/rust/extensions/`. Set `CARGO_HOME="C:/Users/bsevern/.cargo"` on Windows (root CLAUDE.md gotcha).
- **Python tests:** `.venv/Scripts/python.exe -m pytest <path>` (per packages/python/CLAUDE.md — `uv run` is flaky for workspace members on Windows). Set `POLARS_SKIP_CPU_CHECK=1` and `PYTHONIOENCODING=utf-8` (memory `reference_polars_wmi_hang_windows`).
- **Never run the full pytest suite locally** (OOMs the box — memory `feedback_avoid_full_suite_oom`); run targeted files. The full suite runs in CI.
- **Arrow version:** `suggest-core` MUST pin the same arrow version as `native/Cargo.toml` (currently **59**, after the #1003/#1005 arrow 55→59 bump — confirm in Task 0). A mismatched arrow version will not unify with `native`'s `PyArrowType<RecordBatch>` at the Task 10 shim and won't compile. `analysis-core` carries no arrow dependency, so ignore it for the version decision.
- **Commit after every green step.** Use `feat(suggest):` / `test(suggest):` prefixes. End commit messages with the Co-Authored-By + Claude-Session trailers per repo convention.
- Each rule emits a `Suggestion`; follow the fully-worked Rule 1 (Task 5) as the template for Rules 2–3.

---

## Phase 0 — De-risk: freeze the input schema

### Task 0: Resolve the three open questions and freeze the kernel input schema

No code change — this is an investigation that produces a short findings note appended to this plan and locks the Arrow input columns before any kernel code is written.

**Files:**
- Modify: `docs/superpowers/plans/2026-06-24-config-suggestion-kernel-plan-1.md` (append a `## Task 0 findings` section)

- [ ] **Step 1: Does a pyo3-free autoconfig decision crate already exist?**

Run:
```bash
cd /d/show_case/goldenmatch/packages/rust/extensions
ls */Cargo.toml
grep -rl "autoconfig\|Lever\|RefitPolicy\|Decision" --include=*.rs . | grep -v target
```
Expected: no crate owns autoconfig decision logic (the bridge delegates to Python). Record the verdict. If a crate *does* exist, STOP and revisit whether `suggest-core` should extend it.

- [ ] **Step 2: Where do scored-pair scores come from post-run?**

Read `packages/python/goldenmatch/goldenmatch/tui/engine.py` (look for `EngineResult.scored_pairs`) and `packages/python/goldenmatch/goldenmatch/core/cluster.py` (`build_clusters` returns a dict whose per-cluster `pair_scores` carries `(id_a,id_b)->score`). Confirm at least one reliably-populated source of `(id_a, id_b, score)` after a dedupe run. Record which the adapter will use (prefer `EngineResult.scored_pairs`; fall back to flattening `clusters[*]["pair_scores"]`).

- [ ] **Step 3: Where do per-column signals come from?**

Read `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` and `core/indicators.py` (`compute_column_priors` gives per-column `identity_score` + `corruption_score`; `core/autoconfig_rules.py::compute_identity_collision_signal` gives collision rate). Confirm `cardinality_ratio`, `null_rate`, and a `variant_rate` (from `core.quality.blocking_risk`) are obtainable. Record the source artifact for each `column_signals` field.

- [ ] **Step 4: Freeze the input schema**

Append the final Arrow schemas to this plan as `## Task 0 findings`. The three batches:

```
scored_pairs:   id_a:int64, id_b:int64, score:float64
clusters:       cluster_id:int64, size:int64, confidence:float64,
                quality:utf8 ("strong"|"weak"|"split"), oversized:bool
column_signals: field:utf8, col_type:utf8, scorer:utf8, in_blocking:bool,
                in_negative_evidence:bool, identity_score:float64,
                corruption_score:float64, collision_rate:float64,
                cardinality_ratio:float64, null_rate:float64, variant_rate:float64
```
If Step 2/3 show a field is unavailable, record the substitute or mark the rule that depends on it as degraded (still ship the rule; it simply emits nothing without its signal). **Most likely degradation:** if `EngineResult.scored_pairs` is not reliably populated on the `backend=bucket` path, the threshold rule (which needs the score distribution) emits nothing for bucket runs — note this explicitly so it isn't a surprise mid-build; the fallback is flattening `clusters[*]["pair_scores"]`.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-06-24-config-suggestion-kernel-plan-1.md
git commit -m "docs(suggest): Task 0 findings — freeze kernel input schema"
```

---

## Phase 1 — The `suggest-core` crate (pure data contract + reductions)

### Task 1: Scaffold the crate

**Files:**
- Create: `packages/rust/extensions/suggest-core/Cargo.toml`
- Create: `packages/rust/extensions/suggest-core/src/lib.rs`
- Do **NOT** modify the bridge `packages/rust/extensions/Cargo.toml` — see Step 1 note.

- [ ] **Step 1: Write `Cargo.toml`**

`suggest-core` follows the **`score-core` pattern**: it carries its own standalone empty `[workspace]` block so it can be a path dependency of BOTH `native` (pyo3) and later `datafusion-udf` (FFI) without either workspace claiming it. Read `score-core/Cargo.toml` first and mirror its `[workspace]` rationale. Do **NOT** add `suggest-core` to the bridge workspace `members` list (that list is `["bridge"]` with a long `exclude`; `native` and the `-core` crates are deliberately outside it).

```toml
[package]
name = "goldenmatch-suggest-core"
version = "0.1.0"
edition = "2021"

# Standalone workspace so this pyo3-free core can be a path dep of both `native`
# and `datafusion-udf` without either workspace claiming it (mirrors score-core).
[workspace]

[lib]
# pyo3-free: plain lib, no cdylib. Consumed by `native` (pyo3 shim) and later
# datafusion-udf (FFI). Mirrors score-core / analysis-core.

[dependencies]
# MUST equal the arrow version `native/Cargo.toml` pins (currently 59, per the
# #1003/#1005 arrow 55->59 bump — confirm in Task 0). A different arrow version
# will NOT unify with the `PyArrowType<RecordBatch>` coming from `native` in
# Task 10 and the shim will fail to compile. analysis-core carries NO arrow dep
# (it's a pure &[f64] crate) — do not try to "match" it for arrow.
arrow = { version = "59", default-features = false }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
# Reuse the existing reduction kernels — do NOT reimplement histogram/quantile.
# analysis-core is pure (&[f64] in, no arrow), so it imposes no arrow version.
analysis-core = { path = "../analysis-core" }
```

- [ ] **Step 2: Write a trivial `lib.rs` and a compile-check test**

```rust
//! `goldenmatch-suggest-core` — pyo3-free config-suggestion kernel.
//!
//! Canonical source of truth for config suggestions: ingests a finished run's
//! Arrow artifacts, reduces them, runs the suggestion rules, generates rationale
//! text, and ranks. Shared by construction across the `goldenmatch-native` pyo3
//! shim and (later) the datafusion-udf FFI + TS/WASM surfaces. No I/O, no pyo3.

#[cfg(test)]
mod tests {
    #[test]
    fn crate_builds() {
        assert_eq!(2 + 2, 4);
    }
}
```

- [ ] **Step 3: Verify it builds**

Run: `cd packages/rust/extensions && CARGO_HOME="C:/Users/bsevern/.cargo" cargo test -p goldenmatch-suggest-core`
Expected: PASS (`crate_builds`).

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/suggest-core
git commit -m "feat(suggest): scaffold goldenmatch-suggest-core crate"
```
(Do not `git add` the bridge `Cargo.toml` — `suggest-core` is its own standalone workspace.)

### Task 2: The data contract types

**Files:**
- Create: `packages/rust/extensions/suggest-core/src/contract.rs`
- Modify: `packages/rust/extensions/suggest-core/src/lib.rs` (add `pub mod contract;`)

- [ ] **Step 1: Write the failing serde round-trip test**

In `contract.rs`, after the types, add:

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn suggestion_json_roundtrip() {
        let s = Suggestion {
            id: "thr:name:raise".into(),
            kind: SuggestionKind::RaiseThreshold,
            target: "name".into(),
            current_value: "0.80".into(),
            proposed_value: "0.88".into(),
            rationale: "placeholder".into(),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.7,
            patch: ConfigPatch::SetThreshold { matchkey: "name".into(), value: 0.88 },
            evidence: serde_json::json!({"dip": 0.86}),
        };
        let txt = serde_json::to_string(&s).unwrap();
        let back: Suggestion = serde_json::from_str(&txt).unwrap();
        assert_eq!(back.kind, SuggestionKind::RaiseThreshold);
        assert_eq!(back.patch, ConfigPatch::SetThreshold { matchkey: "name".into(), value: 0.88 });
    }
}
```

- [ ] **Step 2: Run, verify it fails to compile** (types undefined).

Run: `cargo test -p goldenmatch-suggest-core suggestion_json_roundtrip`
Expected: compile error.

- [ ] **Step 3: Write the contract types**

```rust
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SuggestionKind {
    RaiseThreshold,
    LowerThreshold,
    SwapScorer,
    AddNegativeEvidence,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum PredictedEffect { PrecisionUp, RecallUp }

/// Declarative config edit. The kernel defines WHAT changes once; each language
/// applies it to its own native config object.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum ConfigPatch {
    SetThreshold { matchkey: String, value: f64 },
    SetScorer { matchkey: String, field: String, scorer: String },
    AddNegativeEvidence { field: String },
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Suggestion {
    pub id: String,
    pub kind: SuggestionKind,
    pub target: String,
    pub current_value: String,
    pub proposed_value: String,
    pub rationale: String,
    pub predicted_effect: PredictedEffect,
    pub confidence: f64,
    pub patch: ConfigPatch,
    pub evidence: serde_json::Value,
}

/// Reduced, frame-free view of the config (what the rules need to read).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConfigSummary {
    pub matchkeys: Vec<MatchkeySummary>,
    pub negative_evidence: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MatchkeySummary {
    pub name: String,
    pub kind: String,            // "weighted" | "fuzzy" | "exact" | "probabilistic"
    pub threshold: Option<f64>,
    pub fields: Vec<FieldSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FieldSummary {
    pub field: String,
    pub scorer: Option<String>,
    pub weight: Option<f64>,
}

/// Accept/reject history folded into ranking. Plan 2 fills this from MemoryStore;
/// Plan 1 always passes an empty map.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct AcceptancePriors {
    /// key = "{snake_case kind}:{target}" -> (accepts, rejects). The key is
    /// produced by `rank::prior_key` (Task 8) — Plan 2's persistence MUST use
    /// the same helper so the loop binds.
    pub counts: std::collections::HashMap<String, (u32, u32)>,
}
```

- [ ] **Step 4: Run, verify PASS.** `cargo test -p goldenmatch-suggest-core suggestion_json_roundtrip` → PASS.
- [ ] **Step 5: Commit** `feat(suggest): contract types (Suggestion, ConfigPatch, ConfigSummary, priors)`

### Task 3: Diagnostics reductions from Arrow (scored_pairs, clusters)

**Files:**
- Create: `packages/rust/extensions/suggest-core/src/diagnostics.rs`
- Modify: `src/lib.rs` (`pub mod diagnostics;`)

This computes the reduced `RunDiagnostics` struct from the Arrow batches. It reuses `analysis-core::histogram`. The arrow extraction follows `native/src/score.rs` (downcast `RecordBatch` columns to typed arrays).

- [ ] **Step 1: Write the failing test** (build a small `scored_pairs` RecordBatch in-test, assert histogram + mass bands)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Float64Array, Int64Array};
    use arrow::record_batch::RecordBatch;
    use arrow::datatypes::{Field, Schema, DataType};
    use std::sync::Arc;

    fn pairs_batch(scores: &[f64]) -> RecordBatch {
        let n = scores.len();
        let schema = Arc::new(Schema::new(vec![
            Field::new("id_a", DataType::Int64, false),
            Field::new("id_b", DataType::Int64, false),
            Field::new("score", DataType::Float64, false),
        ]));
        RecordBatch::try_new(schema, vec![
            Arc::new(Int64Array::from((0..n as i64).collect::<Vec<_>>())),
            Arc::new(Int64Array::from((0..n as i64).map(|x| x+1).collect::<Vec<_>>())),
            Arc::new(Float64Array::from(scores.to_vec())),
        ]).unwrap()
    }

    #[test]
    fn mass_bands_split_by_threshold() {
        // 6 scores: 3 above 0.8, 1 in [0.7,0.8), 2 below 0.7
        let b = pairs_batch(&[0.95, 0.9, 0.85, 0.75, 0.6, 0.5]);
        let d = ScoreDiagnostics::from_batch(&b, 0.80, 24).unwrap();
        assert!((d.mass_above - 3.0/6.0).abs() < 1e-9);
        assert!((d.mass_just_below - 1.0/6.0).abs() < 1e-9); // [0.70,0.80)
        assert_eq!(d.histogram.len(), 24);
    }
}
```

- [ ] **Step 2: Run → fail** (`ScoreDiagnostics` undefined).

- [ ] **Step 3: Implement**

```rust
use arrow::array::{Array, Float64Array, Int64Array, BooleanArray, StringArray};
use arrow::record_batch::RecordBatch;

pub struct ScoreDiagnostics {
    pub histogram: Vec<(f64, i64)>,
    pub mass_above: f64,       // fraction of pairs with score >= threshold
    pub mass_just_below: f64,  // fraction in [threshold-0.10, threshold)
    pub n_pairs: usize,
}

impl ScoreDiagnostics {
    pub fn from_batch(batch: &RecordBatch, threshold: f64, bins: i64) -> Result<Self, String> {
        let col = batch.column_by_name("score").ok_or("missing score column")?;
        let scores = col.as_any().downcast_ref::<Float64Array>().ok_or("score not f64")?;
        let vals: Vec<f64> = scores.iter().flatten().collect();
        let n = vals.len();
        if n == 0 {
            return Ok(Self { histogram: vec![], mass_above: 0.0, mass_just_below: 0.0, n_pairs: 0 });
        }
        let above = vals.iter().filter(|&&s| s >= threshold).count();
        let band_lo = threshold - 0.10;
        let just_below = vals.iter().filter(|&&s| s >= band_lo && s < threshold).count();
        // Reuse analysis-core histogram (no second implementation).
        let histogram = analysis_core::histogram(&vals, bins);
        Ok(Self {
            histogram,
            mass_above: above as f64 / n as f64,
            mass_just_below: just_below as f64 / n as f64,
            n_pairs: n,
        })
    }

    /// Lowest-count bin strictly between the two highest-mass regions — the
    /// bimodality "dip". Returns the bin's left edge, or None if no clear valley.
    pub fn dip(&self) -> Option<f64> {
        if self.histogram.len() < 3 { return None; }
        let counts: Vec<i64> = self.histogram.iter().map(|(_, c)| *c).collect();
        let peak = *counts.iter().max().unwrap();
        // find the global min that has a higher-count bin on BOTH sides
        let mut best: Option<(usize, i64)> = None;
        for i in 1..counts.len()-1 {
            let left_max = counts[..i].iter().max().copied().unwrap_or(0);
            let right_max = counts[i+1..].iter().max().copied().unwrap_or(0);
            if left_max > counts[i] && right_max > counts[i] {
                if best.map_or(true, |(_, c)| counts[i] < c) {
                    best = Some((i, counts[i]));
                }
            }
        }
        // require the valley to be a real dip (< 25% of peak) to avoid noise
        best.filter(|&(_, c)| (c as f64) < 0.25 * peak as f64)
            .map(|(i, _)| self.histogram[i].0)
    }
}
```

Add a `ClusterDiagnostics::from_batch` that counts `weak`/`oversized`/`split` from the clusters batch (same downcast pattern, `quality` is a `StringArray`, `oversized` a `BooleanArray`). Add a unit test for it.

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): Arrow score+cluster diagnostics reductions`

### Task 4: Column-signals extraction

**Files:** `src/diagnostics.rs` (extend)

- [ ] **Step 1: Failing test** — build a `column_signals` batch, assert a `Vec<ColumnSignal>` round-trips the rows (field, scorer, corruption_score, collision_rate, identity_score, cardinality_ratio, in_blocking, in_negative_evidence).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `pub struct ColumnSignal { ... }` + `pub fn column_signals_from_batch(&RecordBatch) -> Result<Vec<ColumnSignal>, String>` using the downcast pattern.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): column-signals extraction`

---

## Phase 2 — The rules

### Task 5: Rule 1 — threshold raise/lower (the template rule)

**Files:**
- Create: `packages/rust/extensions/suggest-core/src/rules.rs`
- Modify: `src/lib.rs` (`pub mod rules;`)

This is the fully-worked rule; Rules 2–3 follow its shape (a pure fn returning `Vec<Suggestion>`).

- [ ] **Step 1: Failing tests** (three behaviors)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::diagnostics::ScoreDiagnostics;
    use crate::contract::*;

    fn sd(mass_above: f64, mass_just_below: f64, dip: Option<f64>) -> ScoreDiagnostics {
        // construct directly for unit isolation
        ScoreDiagnostics {
            histogram: dip.map(|_| vec![(0.0,100),(0.5,2),(0.9,100)]).unwrap_or(vec![(0.0,50),(0.5,50)]),
            mass_above, mass_just_below, n_pairs: 1000,
        }
    }

    #[test]
    fn raises_on_everything_matches() {
        let out = threshold_rule("name", 0.80, &sd(0.95, 0.0, None), 0, 0);
        assert!(out.iter().any(|s| s.kind == SuggestionKind::RaiseThreshold));
    }

    #[test]
    fn lowers_on_recall_risk() {
        // lots of mass just below + weak/oversized clusters present
        let out = threshold_rule("name", 0.80, &sd(0.10, 0.30, None), 5, 2);
        assert!(out.iter().any(|s| s.kind == SuggestionKind::LowerThreshold));
    }

    #[test]
    fn moves_to_dip_when_threshold_off_valley() {
        let out = threshold_rule("name", 0.80, &sd(0.4, 0.05, Some(0.5)), 0, 0);
        let s = out.iter().find(|s| matches!(s.patch, ConfigPatch::SetThreshold{..})).unwrap();
        // dip at 0.5 is below current 0.80 → suggest lowering toward the valley
        assert!(matches!(&s.patch, ConfigPatch::SetThreshold{value, ..} if (*value-0.5).abs() < 0.11));
    }
}
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement**

```rust
use crate::contract::*;
use crate::diagnostics::ScoreDiagnostics;

const EVERYTHING_MATCHES: f64 = 0.90;   // mirrors controller precision_collapse_floor
const RECALL_RISK_BAND: f64 = 0.20;     // fraction just-below to call recall risk

/// `weak_clusters` / `oversized_clusters` are counts from ClusterDiagnostics.
pub fn threshold_rule(
    matchkey: &str,
    current: f64,
    sd: &ScoreDiagnostics,
    weak_clusters: usize,
    oversized_clusters: usize,
) -> Vec<Suggestion> {
    let mut out = Vec::new();

    // (a) bimodality dip not aligned with current threshold
    if let Some(dip) = sd.dip() {
        if (dip - current).abs() > 0.05 {
            let kind = if dip > current { SuggestionKind::RaiseThreshold } else { SuggestionKind::LowerThreshold };
            let effect = if dip > current { PredictedEffect::PrecisionUp } else { PredictedEffect::RecallUp };
            out.push(Suggestion {
                id: format!("thr:dip:{matchkey}"),
                kind: kind.clone(),
                target: matchkey.into(),
                current_value: format!("{current:.2}"),
                proposed_value: format!("{dip:.2}"),
                rationale: format!(
                    "Pair scores split into two groups with a gap near {dip:.2}, but the \
                     `{matchkey}` threshold sits at {current:.2}. Moving it to {dip:.2} \
                     separates the two groups cleanly."),
                predicted_effect: effect,
                confidence: 0.7,
                patch: ConfigPatch::SetThreshold { matchkey: matchkey.into(), value: round2(dip) },
                evidence: serde_json::json!({"dip": dip, "current": current}),
            });
        }
    }

    // (b) "everything matches" → raise
    if sd.mass_above > EVERYTHING_MATCHES {
        let proposed = round2((current + 1.0) / 2.0); // halfway to 1.0
        out.push(Suggestion {
            id: format!("thr:raise:{matchkey}"),
            kind: SuggestionKind::RaiseThreshold,
            target: matchkey.into(),
            current_value: format!("{current:.2}"),
            proposed_value: format!("{proposed:.2}"),
            rationale: format!(
                "{:.0}% of scored pairs clear the `{matchkey}` threshold of {current:.2} — \
                 almost everything is matching, which usually means false merges. Raising it \
                 to {proposed:.2} tightens the match.", sd.mass_above * 100.0),
            predicted_effect: PredictedEffect::PrecisionUp,
            confidence: 0.6,
            patch: ConfigPatch::SetThreshold { matchkey: matchkey.into(), value: proposed },
            evidence: serde_json::json!({"mass_above": sd.mass_above}),
        });
    }

    // (c) recall risk: mass just below + weak/oversized clusters → lower
    if sd.mass_just_below > RECALL_RISK_BAND && (weak_clusters + oversized_clusters) > 0 {
        let proposed = round2(current - 0.05);
        out.push(Suggestion {
            id: format!("thr:lower:{matchkey}"),
            kind: SuggestionKind::LowerThreshold,
            target: matchkey.into(),
            current_value: format!("{current:.2}"),
            proposed_value: format!("{proposed:.2}"),
            rationale: format!(
                "{:.0}% of pairs score just below the `{matchkey}` threshold ({current:.2}), \
                 and there are weak/oversized clusters nearby — likely missed matches. \
                 Lowering it to {proposed:.2} recovers them.", sd.mass_just_below * 100.0),
            predicted_effect: PredictedEffect::RecallUp,
            confidence: 0.5,
            patch: ConfigPatch::SetThreshold { matchkey: matchkey.into(), value: proposed },
            evidence: serde_json::json!({"mass_just_below": sd.mass_just_below,
                                         "weak": weak_clusters, "oversized": oversized_clusters}),
        });
    }

    out
}

fn round2(x: f64) -> f64 { (x * 100.0).round() / 100.0 }
```

- [ ] **Step 4: Run → PASS** (all three behaviors). Fix the dip test expectation if (a)/(c) both fire — dedup is the ranker's job (Task 8); for the unit test assert the dip suggestion is *present*.
- [ ] **Step 5: Commit** `feat(suggest): rule 1 threshold raise/lower`

### Task 6: Rule 2 — scorer swap (noise-aware)

**Files:** `src/rules.rs` (extend)

- [ ] **Step 1: Failing test** — a `ColumnSignal` for an `address`/`name`/free-text column, `scorer == "token_sort"`, `corruption_score >= 0.3` (or `variant_rate >= 0.02`) → emits a `SwapScorer` to `jaro_winkler`; a clean column or a `qgram`-coded column emits nothing. (Use a fixture value clearly inside the band, e.g. `corruption_score = 0.5`, so the `>=` boundary is unambiguous.)
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `pub fn scorer_swap_rule(matchkey: &str, signals: &[ColumnSignal]) -> Vec<Suggestion>`. Gate: `col_type in {address, string, name}` AND `scorer == token_sort` AND (`corruption_score >= 0.3` OR `variant_rate >= 0.02`). Propose `jaro_winkler`. Rationale cites the corruption/variant number. `confidence: 0.65`. Patch `SetScorer`. (Mirror the #662 noise-aware default and `core/autoconfig.py` guard logic — read it to match thresholds.)
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): rule 2 scorer swap`

### Task 7: Rule 3 — add negative evidence

**Files:** `src/rules.rs` (extend)

- [ ] **Step 1: Failing test** — a `ColumnSignal` with `identity_score >= 0.75`, `cardinality_ratio >= 0.5`, `in_negative_evidence == false`, `collision_rate >= 0.5` → emits `AddNegativeEvidence`; a column already in NE, or low identity/collision, emits nothing.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `pub fn negative_evidence_rule(signals: &[ColumnSignal]) -> Vec<Suggestion>` mirroring `compute_identity_collision_signal` / `promote_negative_evidence` thresholds (read `core/autoconfig_rules.py` to match). `confidence: 0.55`. Patch `AddNegativeEvidence`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): rule 3 add negative evidence`

---

## Phase 3 — Rank, suppress, and the top-level entry

### Task 8: Ranking + priors + dedup

**Files:**
- Create: `packages/rust/extensions/suggest-core/src/rank.rs`
- Modify: `src/lib.rs`

- [ ] **Step 1: Failing tests**
  - Two suggestions with different confidence → higher confidence ranks first.
  - A `(kind, target)` with priors `(0 accepts, 3 rejects)` → suppressed (absent from output).
  - A `(kind, target)` with priors `(3 accepts, 0 rejects)` → ranked above an equal-confidence one with no history.
  - Duplicate `id` collapses to one.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement**

```rust
use crate::contract::{Suggestion, SuggestionKind, AcceptancePriors};

const SUPPRESS_AFTER_NET_REJECTS: i64 = 2;

/// Canonical priors-map key: `"{snake_case kind}:{target}"`. Pin this here so
/// Plan 2's MemoryStore persistence writes the SAME key — otherwise the
/// accept/reject loop silently won't bind. Uses the snake_case serde names
/// (NOT Debug-format, which would drop the underscores).
pub fn prior_key(kind: &SuggestionKind, target: &str) -> String {
    let k = match kind {
        SuggestionKind::RaiseThreshold => "raise_threshold",
        SuggestionKind::LowerThreshold => "lower_threshold",
        SuggestionKind::SwapScorer => "swap_scorer",
        SuggestionKind::AddNegativeEvidence => "add_negative_evidence",
    };
    format!("{k}:{target}")
}

pub fn rank(mut suggestions: Vec<Suggestion>, priors: &AcceptancePriors) -> Vec<Suggestion> {
    // dedup by id (keep first)
    let mut seen = std::collections::HashSet::new();
    suggestions.retain(|s| seen.insert(s.id.clone()));

    // suppress repeatedly-rejected (kind,target)
    suggestions.retain(|s| {
        match priors.counts.get(&prior_key(&s.kind, &s.target)) {
            Some((acc, rej)) => (*rej as i64 - *acc as i64) < SUPPRESS_AFTER_NET_REJECTS,
            None => true,
        }
    });

    // score = confidence + acceptance nudge
    suggestions.sort_by(|a, b| {
        score(b, priors).partial_cmp(&score(a, priors)).unwrap_or(std::cmp::Ordering::Equal)
    });
    suggestions
}

fn score(s: &Suggestion, priors: &AcceptancePriors) -> f64 {
    let nudge = match priors.counts.get(&prior_key(&s.kind, &s.target)) {
        Some((acc, rej)) => 0.05 * (*acc as f64 - *rej as f64),
        None => 0.0,
    };
    s.confidence + nudge
}
```

- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): ranking + priors suppression`

### Task 9: Top-level `suggest()` over Arrow + golden vectors

**Files:**
- Create: `packages/rust/extensions/suggest-core/src/api.rs`
- Create: `packages/rust/extensions/suggest-core/tests/golden/*.json` (fixtures)
- Modify: `src/lib.rs` (`pub mod api; pub use api::suggest;`)

- [ ] **Step 1: Failing test** — call `suggest(scored_pairs, clusters, column_signals, config_json, priors_json)` with a fixture that should produce a known scorer-swap as #1, assert the top suggestion's `kind`/`target`.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `pub fn suggest(scored_pairs: &RecordBatch, clusters: &RecordBatch, column_signals: &RecordBatch, config_json: &str, priors_json: &str) -> Result<String, String>` that: parses `ConfigSummary`/`AcceptancePriors` from JSON, builds diagnostics, calls each rule per matchkey, ranks, and returns `serde_json::to_string(&RankedSuggestions)`. Returns a JSON string (the FFI/pyo3-friendly boundary).

  **v1 cluster-count simplification:** `ClusterDiagnostics` (Task 3) counts `weak`/`oversized` **globally**, not per-matchkey. Pass those same global counts to every matchkey's `threshold_rule`. This is an accepted v1 simplification — zero-config datasets typically have one weighted matchkey, so global == per-matchkey in practice. Note it in a code comment so a future multi-matchkey extension knows to revisit.
- [ ] **Step 4: Add a golden-vector test** that loads `tests/golden/ncvr_address.json` (inputs + expected top suggestion) and asserts the kernel output matches. This is the determinism pin.
- [ ] **Step 5: Run → PASS.**
- [ ] **Step 6: Commit** `feat(suggest): top-level suggest() over Arrow + golden vectors`

---

## Phase 4 — Python binding

### Task 10: `suggest.rs` pyo3 shim in `goldenmatch-native`

**Files:**
- Create: `packages/rust/extensions/native/src/suggest.rs`
- Modify: `packages/rust/extensions/native/src/lib.rs` (add `mod suggest;` + register `suggest::suggest_config`)
- Modify: `packages/rust/extensions/native/Cargo.toml` (add `goldenmatch-suggest-core = { path = "../suggest-core" }`)

- [ ] **Step 1: Write the shim** (delegates to the core; receives pyarrow batches via `PyArrowType`, like `score.rs::score_block_pairs_arrow`)

```rust
use arrow::pyarrow::PyArrowType;
use arrow::record_batch::RecordBatch;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

#[pyfunction]
pub fn suggest_config(
    scored_pairs: PyArrowType<RecordBatch>,
    clusters: PyArrowType<RecordBatch>,
    column_signals: PyArrowType<RecordBatch>,
    config_json: &str,
    priors_json: &str,
) -> PyResult<String> {
    goldenmatch_suggest_core::suggest(
        &scored_pairs.0, &clusters.0, &column_signals.0, config_json, priors_json,
    ).map_err(|e| PyValueError::new_err(e))
}
```

- [ ] **Step 2: Register** in `_native` pymodule: `m.add_function(wrap_pyfunction!(suggest::suggest_config, m)?)?;`
- [ ] **Step 3: Build the wheel.** Run: `python scripts/build_native.py`. Expected: builds, no errors.
- [ ] **Step 4: Smoke test** in Python:

```python
import goldenmatch._native as n
assert hasattr(n, "suggest_config")
```

Run: `.venv/Scripts/python.exe -c "import goldenmatch._native as n; print(hasattr(n,'suggest_config'))"`
Expected: `True`.

- [ ] **Step 5: Commit** `feat(suggest): native pyo3 shim for suggest_config`

### Task 11: Python adapter — `review_config`

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/suggest/__init__.py`
- Create: `packages/python/goldenmatch/goldenmatch/core/suggest/adapter.py`
- Create: `packages/python/goldenmatch/goldenmatch/core/suggest/types.py`
- Test: `packages/python/goldenmatch/tests/test_suggest_adapter.py`

`adapter.review_config(result, config)`:
1. Build the three pyarrow tables from the run result (per Task 0 findings): scored pairs `(id_a,id_b,score)`, clusters `(cluster_id,size,confidence,quality,oversized)`, column signals (one row per column from the profile/indicators).
2. Serialize `config` → `ConfigSummary` JSON via a small `_config_summary(config)` mapper (read `config.get_matchkeys()`; `MatchkeyConfig.negative_evidence`).
3. Call `goldenmatch._native.suggest_config(...)`; parse the JSON back into `Suggestion` dataclasses (`types.py`).
4. If native is absent → raise `SuggestionsNativeRequired` with the "install goldenmatch[native]" message.

- [ ] **Step 1: Failing test** — a tiny synthetic dedupe result (use `_person_df` from `tests/test_autoconfig_regressions.py`, run `dedupe_df` with a known-loose threshold) → `review_config` returns a non-empty list whose items are `Suggestion` dataclasses with a `rationale` string. Guard the test with `pytest.importorskip` on `goldenmatch._native` + a `hasattr(..., "suggest_config")` skip.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** `types.py` (`@dataclass Suggestion`, `ConfigPatch` union mirror), `adapter.py` (the four steps), `__init__.py` re-exports.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): python review_config adapter`

### Task 12: `apply_suggestion`

**Files:** `core/suggest/apply.py`; test `tests/test_suggest_apply.py`

- [ ] **Step 1: Failing tests** — `apply_suggestion(config, suggestion)` returns a NEW `GoldenMatchConfig` (Pydantic `model_copy(deep=True)`) with: SetThreshold → the named matchkey's `threshold` updated; SetScorer → the named field's `scorer` updated; AddNegativeEvidence → the field appended to the matchkey's `negative_evidence`. Original config unmutated.
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement** a pure function dispatching on `suggestion.patch.op`. Use the typed-accessor pattern; write through `model_copy`.
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Commit** `feat(suggest): apply_suggestion config patcher`

### Task 13: Native-absent degradation

**Files:** `core/suggest/adapter.py` (already raises); test `tests/test_suggest_native_required.py`

- [ ] **Step 1: Failing test** — monkeypatch the native loader to simulate absence; assert `review_config` raises `SuggestionsNativeRequired` and the message contains `goldenmatch[native]`.
- [ ] **Step 2: Run → fail / Step 3: implement the guard / Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(suggest): clean native-required degradation`

---

## Phase 5 — Benchmark harness

### Task 14: `scripts/suggest_quality` scaffold + dataset loaders

**Files:**
- Create: `scripts/suggest_quality/__init__.py`
- Create: `scripts/suggest_quality/datasets.py` (reuse loaders from `scripts/autoconfig_quality` + `tests/benchmarks/`)
- Create: `scripts/suggest_quality/cli.py` (`report` / `gate` / `bless` subcommands — mirror `scripts/autoconfig_quality`)

- [ ] **Step 1:** Read `scripts/autoconfig_quality/` end to end; mirror its CLI shape, determinism env (`GOLDENMATCH_AUTOCONFIG_MEMORY=0`, fixed seed), and dataset loaders (Febrl3/4, DBLP-ACM, NCVR sample, historical_50k, synthetic). Skip datasets whose files are absent (gitignored) with a logged note (memory `feedback`: no silent caps).
- [ ] **Step 2:** `report` runs nothing yet — prints "0 datasets" — but the CLI wires up. Smoke: `python -m scripts.suggest_quality.cli report --datasets synthetic`.
- [ ] **Step 3: Commit** `feat(suggest): suggest_quality harness scaffold`

### Task 15: Oracle enumeration + metrics

**Files:**
- Create: `scripts/suggest_quality/oracle.py`
- Create: `scripts/suggest_quality/metrics.py`
- Test: `packages/python/goldenmatch/tests/test_suggest_metrics.py`

- [ ] **Step 1: Failing unit tests for the metrics** (pure functions, no dedupe):
  - `rank_correlation(suggested_order, oracle_lifts)` → Spearman; identical order → 1.0, reversed → -1.0.
  - `suggester_precision(applied_lifts)` → fraction with `lift >= 0`.
  - `convergence(steps)` → final F1 + step count from a list of (suggestion, f1_after).
- [ ] **Step 2: Run → fail.**
- [ ] **Step 3: Implement metrics.py** (use `scipy.stats.spearmanr`; it's already a dep via sklearn/scipy).
- [ ] **Step 4: Run → PASS.**
- [ ] **Step 5: Implement `oracle.py`** — per dataset: zero-config → baseline F1 (use `core/evaluate.py::evaluate_pairs` + `load_ground_truth_csv`); `review_config` → suggestions; for each candidate edit, `apply_suggestion` → re-run `dedupe_df` → F1; assemble the per-dataset record (baseline, suggestions, oracle lifts, applied lifts, convergence). This is integration code (no unit test; exercised by `report`).
- [ ] **Step 6: Wire `report`** to print the per-dataset table + headline suggester-score (mean rank-correlation across datasets). Run on `synthetic` locally; full set in CI.
- [ ] **Step 7: Commit** `feat(suggest): oracle enumeration + suggester metrics`

### Task 16: Regression anchors, baseline, CI gate

**Files:**
- Create: `scripts/suggest_quality/baseline.json` (blessed scores; created by `bless`)
- Create: `.github/workflows/bench-suggest-quality.yml`
- Test: `packages/python/goldenmatch/tests/test_suggest_anchors.py`

- [ ] **Step 1: Anchor test (the hard contract)** — on NCVR (skip if dataset absent), `review_config` after zero-config must rank the `res_street_address` token_sort→jaro_winkler `SwapScorer` as the #1 suggestion. Assert `suggestions[0].kind == SwapScorer and suggestions[0].target == "res_street_address"`. Mark `@pytest.mark.benchmark` (excluded from default CI; runs in the bench workflow).
- [ ] **Step 2: Run** the anchor in the bench context; if it fails, the rule thresholds (Task 6) need tuning — iterate there, not in the test. This is the loop the whole plan exists to enable.
- [ ] **Step 3: `bless`** writes `baseline.json` (per-dataset rank-correlation, suggester-precision, convergence F1). `gate` loads it and fails if any metric regresses beyond a tolerance (e.g. rank-corr drops > 0.05).
- [ ] **Step 4: CI workflow** — `workflow_dispatch` + `on: push` paths covering ALL code the harness depends on, not just the core: `packages/rust/extensions/suggest-core/**`, `packages/rust/extensions/native/src/suggest.rs`, `packages/python/goldenmatch/goldenmatch/core/suggest/**`, and `scripts/suggest_quality/**`. `runs-on: large-new-64GB` (memory `feedback_bench_default_runner`); builds the native wheel, runs `python -m scripts.suggest_quality.cli gate`. Default datasets = those available in CI.
- [ ] **Step 5: Commit** `feat(suggest): anchors + blessed baseline + bench-suggest-quality CI`

---

## Done criteria for Plan 1

- `cargo test -p goldenmatch-suggest-core` green; golden-vector fixture pins the kernel.
- `goldenmatch._native.suggest_config` present in the in-tree wheel.
- `review_config(result, config)` returns ranked `Suggestion`s with rationale text; `apply_suggestion` round-trips a new config; native-absent raises the clean message.
- `python -m scripts.suggest_quality.cli report` produces the per-dataset table; the NCVR address-swap anchor passes; `bless` + `gate` wired in CI.
- Feature stays default-off (no auto-config or pipeline path calls `review_config`); it is opt-in via the Python API only. Surfaces + the default-on flip are Plan 2 + its benchmark sign-off.

## Task 0 findings

_(Appended during Task 0 — records the resolved input schema and artifact sources.)_
