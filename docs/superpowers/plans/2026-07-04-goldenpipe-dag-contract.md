# goldenpipe-core Dependency-DAG Planner Contract — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Harden the `goldenpipe-core` planner from a linear "validate an ordered list" resolver into a dependency-DAG resolver that activates the dead `needs` field, reorders minimally to satisfy declared dependencies, and detects cycles / missing producers / ambiguous co-production — Rust is the reference, Python + TS re-conform, all locked byte-identical by the SP2/SP3 golden-vector parity gates.

**Architecture:** Rewrite `resolve.rs` to the spec's §3.1 algorithm (config-order availability `AVAIL(i)`, a virtual `df` seed, `needs` + guarded sole-producer edges, stable Kahn topo-sort). Rename the `Wiring` PlanError variant to `MissingProducer` (dropping `available`) and add `AmbiguousProducer` / `Cycle` / `UnknownNeed`. Rewrite the one existing wiring golden vector and add new cross-surface cases; the pure-Python (`resolver.py` + `_planner_json.py`) and pure-TS (`resolvePure` + `plannerJsonPure`/`plannerJson`) fallbacks re-conform to those vectors.

**Tech Stack:** Rust (`goldenpipe-core`, serde/serde_json, `BTreeSet`/`BinaryHeap`), Python 3.11 (pure planner), TypeScript (pure planner). No pyo3/wasm changes — the native/wasm shims already delegate to `goldenpipe_core::json::*`, so the algorithm rewrite flows to every surface for free.

**Spec:** `docs/superpowers/specs/2026-07-04-goldenpipe-dag-contract-design.md`

**Branch/worktree:** `feat/goldenpipe-dag` in `D:\show_case\gg-local-llm` (stacked on the unmerged SP3 #1427 — rebase `--onto origin/main` once SP3 squash-merges; see Task 8).

---

## Box / execution constraints (read once, applies to every task)

- **Rust is box-safe.** Run in the crate dir with the NTFS toolchain:
  ```bash
  cd /d/show_case/gg-local-llm/packages/rust/extensions/goldenpipe-core
  PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo cargo test
  PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo cargo fmt --check
  PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo cargo clippy --all-targets -- -D warnings
  ```
- **Python parity (Leg A only, no wheel) is box-safe.** goldenpipe imports polars, so set `POLARS_SKIP_CPU_CHECK=1`; the pipeline path pulls goldenmatch, so `GOLDENMATCH_NATIVE=0` (avoids the stale-native-wheel skew — see `reference_py_worktree_test_native_skew`). Run the worktree code via the main venv with a `PYTHONPATH` shadow:
  ```bash
  cd /d/show_case/gg-local-llm/packages/python/goldenpipe
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONPATH="/d/show_case/gg-local-llm/packages/python/goldenpipe" \
    /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/core/test_planner_parity.py -q
  ```
  (`test_planner_parity.py` loads the vectors from `packages/rust/extensions/goldenpipe-core/tests/vectors/*.json` and replays Leg A = pure-Python == core. It auto-picks up new vector cases — no test-file edit needed.)
- **TS is CI-ONLY.** vitest/tsc OOM-kill the box (exit 137, see `feedback_box_memory_oom_ts`). Do NOT run `pnpm`/`vitest` locally. TS changes are verified by the `goldenpipe_wasm` CI lane (Leg A pure-TS == core + Leg B wasm == core, both replay the same vectors). Typecheck-in-head only.
- **GitHub auth:** `benzsevern` account for this repo. Unset `GH_TOKEN` before `gh auth switch --user benzsevern` (see `feedback_github_auth_switch`).
- **No perf gate** (planner over ~5 stages, no hot loop — consistent with SP1–SP3).

---

## File structure (what changes and why)

| File | Change | Responsibility |
|------|--------|----------------|
| `packages/rust/extensions/goldenpipe-core/src/model.rs` | Modify (`PlanError` enum + its unit tests) | The tagged error union — rename `Wiring`→`MissingProducer` (drop `available`), add `AmbiguousProducer`/`Cycle`/`UnknownNeed` |
| `packages/rust/extensions/goldenpipe-core/src/resolve.rs` | Rewrite `resolve()` + tests | The §3.1 DAG algorithm (reference impl) |
| `packages/rust/extensions/goldenpipe-core/tests/vectors/resolve.json` | Modify (rewrite 1 case, add ~7) | The cross-surface parity contract |
| `packages/python/goldenpipe/goldenpipe/engine/resolver.py` | Rewrite `Resolver.resolve` + error classes | Pure-Python re-conform |
| `packages/python/goldenpipe/goldenpipe/core/_planner_json.py` | Modify `resolve_json` | Python JSON face — emit new error kinds |
| `packages/typescript/goldenpipe/src/core/engine/resolver.ts` | Rewrite `resolvePure` + error classes | Pure-TS re-conform |
| `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts` | Modify `resolveJsonPure` | TS Leg-A JSON face — emit new error kinds |
| `packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts` | Modify `throwFromErr` | TS reroute — rehydrate new error kinds |
| `packages/typescript/goldenpipe/README.md` | Modify (planner section) | Doc the DAG/`needs` behavior |

No CI-workflow edit: the `goldenpipe_wasm` / `goldenpipe_native` lanes already replay `tests/vectors/*.json`; new cases are covered automatically. No `json.rs` change: `resolve_json` is shape-stable (the `ok`/`err` envelope). No pyo3/wasm crate change: they delegate to `goldenpipe_core::json::resolve_json`.

---

## The algorithm (spec §3.1, restated for implementers)

Stages `S = [s_0 … s_{n-1}]` in config order, `load` prepended when a stage is registered under key `load`. `SEED = {"df"}` iff no `load` stage present (else `load` produces `df` at index 0). `pname(s_i)` = the planned name = `spec.name or info.name` (literal `"load"` for the prepended stage). `needs`/producer lookup match by **registry key** (= `use`).

1. **Build node list** (prepend load; look up each config stage's `StageInfo` by key → `UnknownStage` if absent). Each node carries `pname`, `use`, `info` (produces/consumes), `needs` (from the config spec; `[]` for load), and a pre-built `PlannedSpec` output row.
2. **`needs` edges** (phase order: before sole-producer). For each node `i`, each `need`: find the node `j` whose `use == need` (earliest config index); none → `UnknownNeed { stage: pname(i), needs: [need] }`; else add edge `(j → i)` (a self-edge `j==i` is kept and caught as `Cycle` in step 4). Duplicate needs collapse (edge set is a `BTreeSet`).
3. **Guarded sole-producer edges.** For each node `i`, each `dep` in `consumes`:
   - `dep ∈ AVAIL(i)` (i.e. `dep=="df" && SEED` **or** some `j<i` produces `dep`) → satisfied by config order: **no edge, no error**.
   - else `L = { j>i : dep ∈ produces(j) }`:
     - `|L|=0` → `MissingProducer { stage: pname(i), artifact: dep }`
     - `|L|=1` → edge `(L[0] → i)`
     - `|L|≥2` → if the `needs` edges pin **exactly one** member of `L` before `i` (an edge `(j→i)` already in the set with `j∈L`), use it (no error, no extra edge); otherwise `AmbiguousProducer { artifact: dep, producers: [use of L in ascending config index] }`.
   - **First violation wins**: iterate `i` ascending, then `consumes` order; `return` on the first error.
4. **Stable Kahn topo-sort** keyed by config index (min-heap of indices). A self-edge, or any node left with in-degree > 0, → `Cycle { stages: [pname of unscheduled nodes in ascending config index] }`.
5. **Emit** `ExecutionPlan` = the nodes' `PlannedSpec` rows in the sorted order. Zero edges → order is `0..n` = config order = **byte-identical to today**.

---

## Task 1: Rust — rename `Wiring` → `MissingProducer`, add the three new PlanError variants

Keep the resolver *linear* for now (behavior-preserving except dropping `available`); this isolates the enum change from the algorithm rewrite so each commit is green.

**Files:**
- Modify: `packages/rust/extensions/goldenpipe-core/src/model.rs:116-128` (the `PlanError` enum) and its tests (none reference `PlanError` today, but confirm).
- Modify: `packages/rust/extensions/goldenpipe-core/src/resolve.rs:37-42` (the construction site) and `resolve.rs:108-124` (the `wiring_error_lists_sorted_available` test).

- [ ] **Step 1: Replace the `PlanError` enum** (`model.rs`, replacing lines 114-128)

```rust
/// Tagged union of the planner's failure classes.
/// - `MissingProducer`: a consumed artifact no stage (nor the `df` seed) produces.
/// - `AmbiguousProducer`: an unsatisfied consumer with >=2 later producers, no `needs` tiebreak.
/// - `Cycle`: the declared edges (`needs` + sole-producer) contain a cycle.
/// - `UnknownNeed`: a `needs` entry naming a stage/key not in the pipeline.
/// - `UnknownStage`: a `use` with no registered stage.
/// The error `stage` field carries the PLANNED NAME (`spec.name or info.name`), matching
/// the pre-DAG `Wiring` error; only `available` was dropped.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum PlanError {
    MissingProducer {
        stage: String,
        artifact: String,
    },
    AmbiguousProducer {
        artifact: String,
        producers: Vec<String>,
    },
    Cycle {
        stages: Vec<String>,
    },
    UnknownNeed {
        stage: String,
        needs: Vec<String>,
    },
    UnknownStage {
        #[serde(rename = "use")]
        use_: String,
    },
}
```

- [ ] **Step 2: Update the `resolve.rs` construction site** (replace lines 35-43)

```rust
        for dep in &info.consumes {
            if !available.contains(dep) {
                return Err(PlanError::MissingProducer {
                    stage: name,
                    artifact: dep.clone(),
                });
            }
        }
```

- [ ] **Step 3: Update the `resolve.rs` error test** (replace `wiring_error_lists_sorted_available`, lines 108-124)

```rust
    #[test]
    fn missing_producer_when_no_stage_produces_dep() {
        let stages = vec![info("s", &["out"], &["missing"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        assert_eq!(
            err,
            PlanError::MissingProducer {
                stage: "s".into(),
                artifact: "missing".into(),
            }
        );
    }
```

- [ ] **Step 4: Add a serialization test for each new variant** (append inside `model.rs` `mod tests`)

```rust
    #[test]
    fn plan_error_new_variants_serialize_with_kind_tag() {
        let ambig = PlanError::AmbiguousProducer {
            artifact: "df".into(),
            producers: vec!["a".into(), "b".into()],
        };
        assert_eq!(
            serde_json::to_value(&ambig).unwrap(),
            serde_json::json!({"kind": "ambiguous_producer", "artifact": "df", "producers": ["a", "b"]})
        );
        let cyc = PlanError::Cycle { stages: vec!["a".into(), "b".into()] };
        assert_eq!(
            serde_json::to_value(&cyc).unwrap(),
            serde_json::json!({"kind": "cycle", "stages": ["a", "b"]})
        );
        let un = PlanError::UnknownNeed { stage: "s".into(), needs: vec!["ghost".into()] };
        assert_eq!(
            serde_json::to_value(&un).unwrap(),
            serde_json::json!({"kind": "unknown_need", "stage": "s", "needs": ["ghost"]})
        );
        let mp = PlanError::MissingProducer { stage: "s".into(), artifact: "x".into() };
        assert_eq!(
            serde_json::to_value(&mp).unwrap(),
            serde_json::json!({"kind": "missing_producer", "stage": "s", "artifact": "x"})
        );
    }
```

- [ ] **Step 5: Build + test + lint** (box-safe commands from the top of this doc)

Run: `cargo test` in the crate dir.
Expected: all pass (the `wiring_error_*` test is gone, replaced by `missing_producer_*`; the golden-vector test `vec_resolve` will FAIL — the `resolve.json` wiring case still says `kind: "wiring"`. That is expected and fixed in Task 2. To keep this step green, run only the unit tests: `cargo test --lib`).

Run: `cargo test --lib` → PASS. `cargo fmt --check` → clean. `cargo clippy --all-targets -- -D warnings` → clean.

- [ ] **Step 6: Commit**

```bash
git add packages/rust/extensions/goldenpipe-core/src/model.rs packages/rust/extensions/goldenpipe-core/src/resolve.rs
git commit -m "refactor(goldenpipe-core): rename Wiring->MissingProducer, add DAG error variants"
```

---

## Task 2: Rust — rewrite the wiring golden vector + re-conform the Python/TS JSON faces (rename only)

Land the `wiring`→`missing_producer` payload change across the shared vector and all three JSON faces, so `vec_resolve` and the Python Leg A go green again *before* the algorithm changes behavior.

**Files:**
- Modify: `packages/rust/extensions/goldenpipe-core/tests/vectors/resolve.json` (the `"wiring error lists sorted available"` case, currently lines ~18-21).
- Modify: `packages/python/goldenpipe/goldenpipe/core/_planner_json.py:66-69` (the `WiringError` mapping).
- Modify: `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts` (the `WiringError` catch branch) and `packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts:32-41` (`throwFromErr` wiring branch).

- [ ] **Step 1: Rewrite the wiring vector case** (`resolve.json` — replace the `"wiring error lists sorted available"` object)

```json
  {"comment": "missing producer: no stage nor seed produces the consumed artifact",
   "input": {"config": {"pipeline": "auto", "stages": ["s"]},
             "stages": [{"key": "s", "name": "s", "produces": ["out"], "consumes": ["missing"]}]},
   "expected": {"err": {"kind": "missing_producer", "stage": "s", "artifact": "missing"}}},
```

- [ ] **Step 2: Run the Rust golden-vector test** — Run: `cargo test --test golden_vectors vec_resolve`. Expected: PASS (the linear resolver from Task 1 now emits `missing_producer` matching the rewritten vector).

- [ ] **Step 3: Update the Python JSON face** (`_planner_json.py` — replace the `except WiringError` block, lines 66-69)

```python
    except WiringError as e:
        return json.dumps(
            {"err": {"kind": "missing_producer", "stage": e.stage, "artifact": e.missing}}
        )
```

(`WiringError` still carries `.missing`; we surface it as `artifact` and drop `available`. Task 5 replaces `WiringError` internals and adds the new kinds.)

- [ ] **Step 4: Run the Python Leg A parity** — Run the box-safe pytest command from the top. Expected: PASS (pure-Python `resolver.py` still raises the linear `WiringError`; the shim now emits `missing_producer` matching the vector).

- [ ] **Step 5: Update the TS Leg-A JSON face** (`plannerJsonPure.ts` — the `WiringError` catch branch; change the emitted object to)

```ts
    if (e instanceof WiringError) {
      return JSON.stringify({
        err: { kind: "missing_producer", stage: e.stage, artifact: e.missing },
      });
    }
```

(TS `WiringError` currently exposes `missing`; keep reading it here. Task 6 repurposes it to `artifact` + adds the new kinds.)

- [ ] **Step 6: Update the TS reroute error rehydration** (`plannerJson.ts` `throwFromErr`, lines 32-41 — replace the `wiring` branch)

```ts
  if (err.kind === "missing_producer") {
    throw new WiringError(
      `Stage '${String(err.stage)}' consumes '${String(err.artifact)}' but no prior stage produces it.`,
      {
        stage: String(err.stage),
        missing: String(err.artifact),
        available: [],
      },
    );
  }
```

(Keeps the `WiringError` shape stable for host `catch (WiringError)` sites; `available` is now always `[]`. Task 6 tidies the `WiringError` constructor.)

- [ ] **Step 7: Commit** (TS verified in CI, not on the box)

```bash
git add packages/rust/extensions/goldenpipe-core/tests/vectors/resolve.json \
        packages/python/goldenpipe/goldenpipe/core/_planner_json.py \
        packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts \
        packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts
git commit -m "refactor(goldenpipe): rewrite wiring vector -> missing_producer across all surfaces"
```

---

## Task 3: Rust — rewrite `resolve()` to the §3.1 DAG algorithm (the reference impl)

**Files:**
- Rewrite: `packages/rust/extensions/goldenpipe-core/src/resolve.rs` (the `resolve` fn body + add tests).

- [ ] **Step 1: Add the new-behavior failing tests FIRST** (append to `resolve.rs` `mod tests`)

```rust
    fn spec_entry(u: &str, needs: &[&str]) -> StageEntry {
        StageEntry::Spec(StageSpec {
            name: None,
            use_: u.into(),
            needs: needs.iter().map(|s| s.to_string()).collect(),
            skip_if: None,
            on_error: OnError::Continue,
            config: JsonMap::new(),
        })
    }

    #[test]
    fn already_valid_pipeline_is_byte_identical() {
        // The flagship: zero edges -> config order unchanged.
        let stages = vec![
            info("load", &["df"], &[]),
            info("goldenflow.transform", &["df", "manifest"], &["df"]),
            info("goldenmatch.dedupe", &["clusters"], &["df"]),
        ];
        let plan = resolve(
            &cfg(vec![name_entry("goldenflow.transform"), name_entry("goldenmatch.dedupe")]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["load", "goldenflow.transform", "goldenmatch.dedupe"]);
    }

    #[test]
    fn reorders_consumer_before_its_sole_producer() {
        // config lists b (consumes out) before a (produces out); both consume df (seeded).
        let stages = vec![
            info("a", &["out"], &["df"]),
            info("b", &[], &["out", "df"]),
        ];
        let plan = resolve(&cfg(vec![name_entry("b"), name_entry("a")]), &stages).unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "b"]); // a pulled before b
    }

    #[test]
    fn needs_reorders_against_config_order() {
        // config [b, a]; b needs a -> a must precede b.
        let stages = vec![info("a", &[], &["df"]), info("b", &[], &["df"])];
        let plan = resolve(
            &cfg(vec![spec_entry("b", &["a"]), spec_entry("a", &[])]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["a", "b"]);
    }

    #[test]
    fn reproduction_chain_stays_config_order() {
        // load -> t1 (re-produces df) -> t2 (re-produces df): all satisfied, zero edges.
        let stages = vec![
            info("load", &["df"], &[]),
            info("t1", &["df"], &["df"]),
            info("t2", &["df"], &["df"]),
        ];
        let plan = resolve(&cfg(vec![name_entry("t1"), name_entry("t2")]), &stages).unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["load", "t1", "t2"]);
    }

    #[test]
    fn missing_producer_when_absent_and_unseeded() {
        let stages = vec![info("s", &["out"], &["ghost"])];
        let err = resolve(&cfg(vec![name_entry("s")]), &stages).unwrap_err();
        assert_eq!(err, PlanError::MissingProducer { stage: "s".into(), artifact: "ghost".into() });
    }

    #[test]
    fn ambiguous_producer_two_later_producers_no_needs() {
        // c consumes x; p1 and p2 (both later) produce x; no needs tiebreak.
        let stages = vec![
            info("c", &[], &["x", "df"]),
            info("p1", &["x"], &["df"]),
            info("p2", &["x"], &["df"]),
        ];
        let err = resolve(
            &cfg(vec![name_entry("c"), name_entry("p1"), name_entry("p2")]),
            &stages,
        )
        .unwrap_err();
        assert_eq!(
            err,
            PlanError::AmbiguousProducer { artifact: "x".into(), producers: vec!["p1".into(), "p2".into()] }
        );
    }

    #[test]
    fn ambiguous_resolved_when_needs_pins_exactly_one() {
        let stages = vec![
            info("c", &[], &["x", "df"]),
            info("p1", &["x"], &["df"]),
            info("p2", &["x"], &["df"]),
        ];
        // c needs p2 -> pins exactly one of {p1,p2}; resolves (p2 before c).
        let plan = resolve(
            &cfg(vec![spec_entry("c", &["p2"]), name_entry("p1"), name_entry("p2")]),
            &stages,
        )
        .unwrap();
        let names: Vec<_> = plan.stages.iter().map(|s| s.name.as_str()).collect();
        assert_eq!(names, ["p2", "c", "p1"]); // p2 pulled before c; p1 stays after
    }

    #[test]
    fn cycle_when_needs_are_mutual() {
        let stages = vec![info("a", &[], &["df"]), info("b", &[], &["df"])];
        let err = resolve(
            &cfg(vec![spec_entry("a", &["b"]), spec_entry("b", &["a"])]),
            &stages,
        )
        .unwrap_err();
        assert_eq!(err, PlanError::Cycle { stages: vec!["a".into(), "b".into()] });
    }

    #[test]
    fn unknown_need_names_absent_stage() {
        let stages = vec![info("s", &[], &["df"])];
        let err = resolve(&cfg(vec![spec_entry("s", &["ghost"])]), &stages).unwrap_err();
        assert_eq!(err, PlanError::UnknownNeed { stage: "s".into(), needs: vec!["ghost".into()] });
    }

    #[test]
    fn resolve_is_deterministic_across_runs() {
        let stages = vec![info("a", &["out"], &["df"]), info("b", &[], &["out", "df"])];
        let c = cfg(vec![name_entry("b"), name_entry("a")]);
        let r1 = resolve(&c, &stages).unwrap();
        let r2 = resolve(&c, &stages).unwrap();
        assert_eq!(r1, r2);
    }
```

- [ ] **Step 2: Run the new tests — verify they FAIL** — Run: `cargo test --lib resolve`. Expected: the new tests FAIL (linear resolver errors on the reorder cases / doesn't emit the new variants). The four pre-existing OK tests still pass.

- [ ] **Step 3: Replace the `resolve()` fn body** (`resolve.rs` lines 1-54 — keep the `//!` header, replace imports + fn)

```rust
//! resolve(config, stage_info[]) -> ExecutionPlan | PlanError.
//! Dependency-DAG planner (spec §3.1): config-order-authoritative with a virtual `df`
//! seed, `needs` + guarded sole-producer edges, and a stable Kahn topo-sort. An
//! already-valid pipeline produces zero edges -> byte-identical to config order.
use std::cmp::Reverse;
use std::collections::{BTreeSet, BinaryHeap};

use crate::model::{ExecutionPlan, PipelineConfig, PlanError, PlannedSpec, StageInfo};

struct Node<'a> {
    pname: String,
    use_: String,
    info: &'a StageInfo,
    needs: Vec<String>,
    spec: PlannedSpec,
}

pub fn resolve(config: &PipelineConfig, stages: &[StageInfo]) -> Result<ExecutionPlan, PlanError> {
    let by_key = |k: &str| stages.iter().find(|s| s.key == k);

    // 1. Build the ordered node list (load prepended per SP1; UnknownStage on bad `use`).
    let mut nodes: Vec<Node> = Vec::new();
    let has_load = by_key("load").is_some();
    if let Some(load) = by_key("load") {
        nodes.push(Node {
            pname: "load".into(),
            use_: "load".into(),
            info: load,
            needs: vec![],
            spec: PlannedSpec {
                name: "load".into(),
                use_: "load".into(),
                config: Default::default(),
                skip_if: None,
                on_error: Default::default(),
            },
        });
    }
    for entry in &config.stages {
        let spec = entry.clone().into_spec();
        let info = by_key(&spec.use_).ok_or(PlanError::UnknownStage { use_: spec.use_.clone() })?;
        let pname = spec.name.clone().unwrap_or_else(|| info.name.clone());
        nodes.push(Node {
            pname: pname.clone(),
            use_: spec.use_.clone(),
            info,
            needs: spec.needs.clone(),
            spec: PlannedSpec {
                name: pname,
                use_: spec.use_,
                config: spec.config,
                skip_if: spec.skip_if,
                on_error: spec.on_error,
            },
        });
    }
    let n = nodes.len();
    let seed_df = !has_load; // df seeded iff no load stage produces it at index 0

    // First node (by config index) whose `use` == k — the match key space for needs/producers.
    let key_to_idx = |k: &str| nodes.iter().position(|nd| nd.use_ == k);

    let mut edges: BTreeSet<(usize, usize)> = BTreeSet::new();

    // 2. needs edges (reported before missing/ambiguous — phase order).
    for (i, nd) in nodes.iter().enumerate() {
        for need in &nd.needs {
            match key_to_idx(need) {
                None => {
                    return Err(PlanError::UnknownNeed {
                        stage: nd.pname.clone(),
                        needs: vec![need.clone()],
                    })
                }
                Some(j) => {
                    edges.insert((j, i)); // self-edge (j==i) kept -> caught as Cycle in step 4
                }
            }
        }
    }

    // 3. Guarded sole-producer edges. First violation (by config index, then consumes order) wins.
    let produced_before = |i: usize, x: &str| -> bool {
        (seed_df && x == "df") || nodes[..i].iter().any(|nd| nd.info.produces.iter().any(|p| p == x))
    };
    for i in 0..n {
        for dep in &nodes[i].info.consumes {
            if produced_before(i, dep) {
                continue; // satisfied by seed or an earlier stage -> no edge, no error
            }
            let later: Vec<usize> = ((i + 1)..n)
                .filter(|&j| nodes[j].info.produces.iter().any(|p| p == dep))
                .collect();
            match later.len() {
                0 => {
                    return Err(PlanError::MissingProducer {
                        stage: nodes[i].pname.clone(),
                        artifact: dep.clone(),
                    })
                }
                1 => {
                    edges.insert((later[0], i));
                }
                _ => {
                    let pinned = later.iter().filter(|&&j| edges.contains(&(j, i))).count();
                    if pinned != 1 {
                        return Err(PlanError::AmbiguousProducer {
                            artifact: dep.clone(),
                            producers: later.iter().map(|&j| nodes[j].use_.clone()).collect(),
                        });
                    }
                    // exactly one needs-pinned producer: it already has an edge; nothing to add.
                }
            }
        }
    }

    // 4. Stable Kahn topo-sort keyed by config index (min-heap of indices).
    let mut indeg = vec![0usize; n];
    let mut adj: Vec<Vec<usize>> = vec![vec![]; n];
    for &(a, b) in &edges {
        if a == b {
            return Err(PlanError::Cycle { stages: vec![nodes[a].pname.clone()] });
        }
        adj[a].push(b);
        indeg[b] += 1;
    }
    let mut heap: BinaryHeap<Reverse<usize>> = BinaryHeap::new();
    for (i, &d) in indeg.iter().enumerate() {
        if d == 0 {
            heap.push(Reverse(i));
        }
    }
    let mut order: Vec<usize> = Vec::with_capacity(n);
    while let Some(Reverse(u)) = heap.pop() {
        order.push(u);
        for &v in &adj[u] {
            indeg[v] -= 1;
            if indeg[v] == 0 {
                heap.push(Reverse(v));
            }
        }
    }
    if order.len() != n {
        let cyc: Vec<String> = (0..n)
            .filter(|&i| indeg[i] > 0)
            .map(|i| nodes[i].pname.clone())
            .collect();
        return Err(PlanError::Cycle { stages: cyc });
    }

    // 5. Emit in sorted order (zero edges -> 0..n -> config order, byte-identical).
    Ok(ExecutionPlan {
        stages: order.into_iter().map(|i| nodes[i].spec.clone()).collect(),
    })
}
```

- [ ] **Step 4: Run the full crate test suite** — Run: `cargo test`. Expected: all `resolve.rs` unit tests pass (old OK cases + new behavior cases), `model.rs` tests pass, and `golden_vectors::vec_resolve` still passes (all existing vectors are already-valid → zero edges → unchanged; the rewritten missing-producer vector matches). `cargo fmt --check` clean; `cargo clippy --all-targets -- -D warnings` clean.

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/goldenpipe-core/src/resolve.rs
git commit -m "feat(goldenpipe-core): dependency-DAG resolver (needs + sole-producer reorder, cycle/ambiguous/missing detection)"
```

---

## Task 4: Rust — add the new cross-surface golden vectors

These vectors become the failing tests that drive the Python (Task 5) and TS (Task 6) re-conform.

**Files:**
- Modify: `packages/rust/extensions/goldenpipe-core/tests/vectors/resolve.json` (append cases before the closing `]`).

- [ ] **Step 1: Append the new vector cases** (insert as new array elements; mind the trailing comma on the prior last element)

```json
  {"comment": "DAG: needs reorders against config order (b needs a; config lists b first)",
   "input": {"config": {"pipeline": "auto", "stages": [{"use": "b", "needs": ["a"]}, {"use": "a"}]},
             "stages": [{"key": "a", "name": "a", "produces": [], "consumes": ["df"]},
                        {"key": "b", "name": "b", "produces": [], "consumes": ["df"]}]},
   "expected": {"ok": {"stages": [{"name": "a", "use": "a", "config": {}, "on_error": "continue"},
                                  {"name": "b", "use": "b", "config": {}, "on_error": "continue"}]}}},
  {"comment": "DAG: sole-producer reorder (b consumes out, listed before its only producer a)",
   "input": {"config": {"pipeline": "auto", "stages": ["b", "a"]},
             "stages": [{"key": "a", "name": "a", "produces": ["out"], "consumes": ["df"]},
                        {"key": "b", "name": "b", "produces": [], "consumes": ["out", "df"]}]},
   "expected": {"ok": {"stages": [{"name": "a", "use": "a", "config": {}, "on_error": "continue"},
                                  {"name": "b", "use": "b", "config": {}, "on_error": "continue"}]}}},
  {"comment": "DAG: re-production chain stays config order (byte-identical regression pin)",
   "input": {"config": {"pipeline": "auto", "stages": ["t1", "t2"]},
             "stages": [{"key": "load", "name": "load", "produces": ["df"], "consumes": []},
                        {"key": "t1", "name": "t1", "produces": ["df"], "consumes": ["df"]},
                        {"key": "t2", "name": "t2", "produces": ["df"], "consumes": ["df"]}]},
   "expected": {"ok": {"stages": [{"name": "load", "use": "load", "config": {}, "on_error": "continue"},
                                  {"name": "t1", "use": "t1", "config": {}, "on_error": "continue"},
                                  {"name": "t2", "use": "t2", "config": {}, "on_error": "continue"}]}}},
  {"comment": "DAG: ambiguous producer (c consumes x; p1 and p2 both produce it later, no needs)",
   "input": {"config": {"pipeline": "auto", "stages": ["c", "p1", "p2"]},
             "stages": [{"key": "c", "name": "c", "produces": [], "consumes": ["x", "df"]},
                        {"key": "p1", "name": "p1", "produces": ["x"], "consumes": ["df"]},
                        {"key": "p2", "name": "p2", "produces": ["x"], "consumes": ["df"]}]},
   "expected": {"err": {"kind": "ambiguous_producer", "artifact": "x", "producers": ["p1", "p2"]}}},
  {"comment": "DAG: cycle (mutual needs)",
   "input": {"config": {"pipeline": "auto", "stages": [{"use": "a", "needs": ["b"]}, {"use": "b", "needs": ["a"]}]},
             "stages": [{"key": "a", "name": "a", "produces": [], "consumes": ["df"]},
                        {"key": "b", "name": "b", "produces": [], "consumes": ["df"]}]},
   "expected": {"err": {"kind": "cycle", "stages": ["a", "b"]}}},
  {"comment": "DAG: unknown need",
   "input": {"config": {"pipeline": "auto", "stages": [{"use": "s", "needs": ["ghost"]}]},
             "stages": [{"key": "s", "name": "s", "produces": [], "consumes": ["df"]}]},
   "expected": {"err": {"kind": "unknown_need", "stage": "s", "needs": ["ghost"]}}}
```

- [ ] **Step 2: Run the Rust golden-vector test** — Run: `cargo test --test golden_vectors vec_resolve`. Expected: PASS (the Task 3 algorithm produces exactly these values).

- [ ] **Step 3: Commit**

```bash
git add packages/rust/extensions/goldenpipe-core/tests/vectors/resolve.json
git commit -m "test(goldenpipe-core): add DAG golden vectors (needs/sole-producer reorder, ambiguous/cycle/unknown_need)"
```

---

## Task 5: Python — re-conform `resolver.py` to the DAG algorithm + emit new error kinds

The new vectors (Task 4) now make the Python Leg A FAIL — that is the failing test. Port the algorithm and error kinds until it passes.

**Files:**
- Rewrite: `packages/python/goldenpipe/goldenpipe/engine/resolver.py` (`Resolver.resolve` + error classes).
- Modify: `packages/python/goldenpipe/goldenpipe/core/_planner_json.py` (`resolve_json` error mapping).

- [ ] **Step 1: Run Python Leg A — verify it FAILS** — Run the box-safe pytest command. Expected: FAIL on the new DAG cases (linear resolver can't reorder / emits no `ambiguous_producer`/`cycle`/`unknown_need`).

- [ ] **Step 2: Rewrite `resolver.py`** (replace the whole file body below the module docstring)

```python
"""Pipeline resolver -- dependency-DAG planner (spec §3.1). Config-order-authoritative
with a virtual `df` seed, `needs` + guarded sole-producer edges, stable Kahn sort."""
from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Any

from goldenpipe.engine.registry import StageRegistry
from goldenpipe.models.config import PipelineConfig, StageSpec


class WiringError(Exception):
    """A consumed artifact no stage (nor the `df` seed) produces (spec: MissingProducer).
    Name retained for back-compat with existing `except WiringError` sites. Carries
    optional structured attrs for the parity helper; `str(e)` still works."""

    def __init__(self, message: str, *, stage: str | None = None, artifact: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.artifact = artifact
        self.missing = artifact  # legacy alias (old attr name)


class AmbiguousProducerError(Exception):
    def __init__(self, artifact: str, producers: list[str]) -> None:
        super().__init__(f"Artifact '{artifact}' has ambiguous producers: {producers}")
        self.artifact = artifact
        self.producers = producers


class CycleError(Exception):
    def __init__(self, stages: list[str]) -> None:
        super().__init__(f"Dependency cycle among stages: {stages}")
        self.stages = stages


class UnknownNeedError(Exception):
    def __init__(self, stage: str, needs: list[str]) -> None:
        super().__init__(f"Stage '{stage}' needs unknown stage(s): {needs}")
        self.stage = stage
        self.needs = needs


@dataclass
class PlannedStage:
    """A resolved stage ready for execution."""
    name: str
    stage: Any
    spec: StageSpec
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionPlan:
    """Ordered list of stages to execute."""
    stages: list[PlannedStage] = field(default_factory=list)


@dataclass
class _Node:
    pname: str
    use: str
    produces: list[str]
    consumes: list[str]
    needs: list[str]
    planned: PlannedStage


class Resolver:
    """Builds and validates an ExecutionPlan from config + registry (DAG contract)."""

    @staticmethod
    def resolve(config: PipelineConfig, registry: StageRegistry) -> ExecutionPlan:
        # 1. Build the ordered node list (load prepended; KeyError on bad `use`).
        nodes: list[_Node] = []
        has_load = True
        try:
            load = registry.get("load")
        except KeyError:
            has_load = False
        if has_load:
            nodes.append(_Node(
                pname="load", use="load", produces=list(load.info.produces), consumes=[],
                needs=[], planned=PlannedStage(name="load", stage=load, spec=StageSpec(use="load")),
            ))
        for raw in config.stages:
            spec = StageSpec(use=raw) if isinstance(raw, str) else raw
            stage_obj = registry.get(spec.use)  # KeyError -> unknown_stage (mapped by the shim)
            pname = spec.name or stage_obj.info.name
            nodes.append(_Node(
                pname=pname, use=spec.use,
                produces=list(stage_obj.info.produces), consumes=list(stage_obj.info.consumes),
                needs=list(spec.needs),
                planned=PlannedStage(name=pname, stage=stage_obj, spec=spec, config=spec.config),
            ))
        n = len(nodes)
        seed_df = not has_load

        def key_to_idx(k: str) -> int | None:
            for idx, nd in enumerate(nodes):
                if nd.use == k:
                    return idx
            return None

        edges: set[tuple[int, int]] = set()

        # 2. needs edges (reported before missing/ambiguous).
        for i, nd in enumerate(nodes):
            for need in nd.needs:
                j = key_to_idx(need)
                if j is None:
                    raise UnknownNeedError(nd.pname, [need])
                edges.add((j, i))  # self-edge -> Cycle in step 4

        # 3. Guarded sole-producer edges. First violation (by index, then consumes order) wins.
        def produced_before(i: int, x: str) -> bool:
            if seed_df and x == "df":
                return True
            return any(x in nodes[j].produces for j in range(i))

        for i in range(n):
            for dep in nodes[i].consumes:
                if produced_before(i, dep):
                    continue
                later = [j for j in range(i + 1, n) if dep in nodes[j].produces]
                if len(later) == 0:
                    raise WiringError(
                        f"Stage '{nodes[i].pname}' consumes '{dep}' but no prior stage produces it.",
                        stage=nodes[i].pname, artifact=dep,
                    )
                if len(later) == 1:
                    edges.add((later[0], i))
                else:
                    pinned = sum(1 for j in later if (j, i) in edges)
                    if pinned != 1:
                        raise AmbiguousProducerError(dep, [nodes[j].use for j in later])
                    # exactly one needs-pinned producer: edge already present.

        # 4. Stable Kahn topo-sort keyed by config index (min-heap).
        indeg = [0] * n
        adj: list[list[int]] = [[] for _ in range(n)]
        for a, b in edges:
            if a == b:
                raise CycleError([nodes[a].pname])
            adj[a].append(b)
            indeg[b] += 1
        heap = [i for i in range(n) if indeg[i] == 0]
        heapq.heapify(heap)
        order: list[int] = []
        while heap:
            u = heapq.heappop(heap)
            order.append(u)
            for v in adj[u]:
                indeg[v] -= 1
                if indeg[v] == 0:
                    heapq.heappush(heap, v)
        if len(order) != n:
            raise CycleError([nodes[i].pname for i in range(n) if indeg[i] > 0])

        # 5. Emit in sorted order.
        return ExecutionPlan(stages=[nodes[i].planned for i in order])
```

- [ ] **Step 3: Update `_planner_json.py` `resolve_json`** (replace the try/except block, lines 64-78, imports at top too)

Add to the import on line 14:
```python
from goldenpipe.engine.resolver import (
    AmbiguousProducerError, CycleError, PlannedStage, Resolver, UnknownNeedError, WiringError,
)
```

Replace the resolve body:
```python
    config = PipelineConfig(**arg["config"])
    try:
        plan = Resolver.resolve(config, reg)
    except WiringError as e:
        return json.dumps({"err": {"kind": "missing_producer", "stage": e.stage, "artifact": e.artifact}})
    except AmbiguousProducerError as e:
        return json.dumps({"err": {"kind": "ambiguous_producer", "artifact": e.artifact, "producers": e.producers}})
    except CycleError as e:
        return json.dumps({"err": {"kind": "cycle", "stages": e.stages}})
    except UnknownNeedError as e:
        return json.dumps({"err": {"kind": "unknown_need", "stage": e.stage, "needs": e.needs}})
    except KeyError:
        for raw in config.stages:
            use = raw if isinstance(raw, str) else raw.use
            if use not in reg._stages:
                return json.dumps({"err": {"kind": "unknown_stage", "use": use}})
        raise  # unreachable
    return json.dumps({"ok": {"stages": [_planned_to_dict(p) for p in plan.stages]}})
```

- [ ] **Step 4: Run Python Leg A — verify it PASSES** — Run the box-safe pytest command. Expected: PASS on ALL resolve vectors incl. the new DAG cases.

- [ ] **Step 5: Run the broader goldenpipe engine tests** (guard against a resolver-consumer regression) — Run:
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenpipe
POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONPATH="/d/show_case/gg-local-llm/packages/python/goldenpipe" \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/ -q -k "resolver or planner or pipeline"
```
Expected: PASS (existing valid pipelines unchanged; `except WiringError` sites still catch the missing-producer case).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenpipe/goldenpipe/engine/resolver.py packages/python/goldenpipe/goldenpipe/core/_planner_json.py
git commit -m "feat(goldenpipe): re-conform Python resolver to the DAG contract"
```

---

## Task 6: TypeScript — re-conform `resolvePure` to the DAG algorithm + emit new error kinds

CI-only verification (box OOMs vitest). Port precisely; typecheck-in-head.

**Files:**
- Rewrite: `packages/typescript/goldenpipe/src/core/engine/resolver.ts` (`resolvePure` + `WiringError` + new error classes).
- Modify: `packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts` (`resolveJsonPure` catch branches).
- Modify: `packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts` (`throwFromErr` new kinds).

- [ ] **Step 1: Rewrite `resolver.ts` error classes + `resolvePure`** (replace lines 14-86: the `WiringError` class through the end of `resolvePure`; leave the `Resolver` object below untouched)

```ts
/** A consumed artifact no stage (nor the `df` seed) produces (spec: MissingProducer).
 * Name retained for back-compat with host `catch (WiringError)` sites. */
export class WiringError extends Error {
  stage?: string;
  artifact?: string;
  /** legacy alias for {@link artifact}. */
  get missing(): string | undefined {
    return this.artifact;
  }
  constructor(message: string, extra?: { stage: string; artifact: string }) {
    super(message);
    this.name = "WiringError";
    if (extra) {
      this.stage = extra.stage;
      this.artifact = extra.artifact;
    }
  }
}

export class AmbiguousProducerError extends Error {
  constructor(public artifact: string, public producers: string[]) {
    super(`Artifact '${artifact}' has ambiguous producers: ${producers.join(", ")}`);
    this.name = "AmbiguousProducerError";
  }
}

export class CycleError extends Error {
  constructor(public stages: string[]) {
    super(`Dependency cycle among stages: ${stages.join(", ")}`);
    this.name = "CycleError";
  }
}

export class UnknownNeedError extends Error {
  constructor(public stage: string, public needs: string[]) {
    super(`Stage '${stage}' needs unknown stage(s): ${needs.join(", ")}`);
    this.name = "UnknownNeedError";
  }
}

export interface PlannedStage {
  name: string;
  stage: Stage;
  spec: StageSpec;
  config: Record<string, unknown>;
}

export interface ExecutionPlan {
  stages: PlannedStage[];
}

interface ResolveNode {
  pname: string;
  use: string;
  produces: string[];
  consumes: string[];
  needs: string[];
  planned: PlannedStage;
}

/**
 * Pure-TS core of {@link Resolver.resolve} — the dependency-DAG planner (spec §3.1),
 * guard-free so plannerJsonPure can call it without re-entering the WASM reroute guard.
 */
export function resolvePure(config: PipelineConfig, registry: StageRegistry): ExecutionPlan {
  // 1. Build the ordered node list (load prepended; registry.get throws -> unknown_stage).
  const nodes: ResolveNode[] = [];
  const hasLoad = registry.has("load");
  if (hasLoad) {
    const load = registry.get("load");
    nodes.push({
      pname: "load",
      use: "load",
      produces: [...load.info.produces],
      consumes: [],
      needs: [],
      planned: { name: "load", stage: load, spec: makeStageSpec("load"), config: {} },
    });
  }
  for (const rawSpec of config.stages) {
    const spec = makeStageSpec(rawSpec);
    const stageObj = registry.get(spec.use);
    const pname = spec.name ?? stageObj.info.name;
    nodes.push({
      pname,
      use: spec.use,
      produces: [...stageObj.info.produces],
      consumes: [...stageObj.info.consumes],
      needs: [...spec.needs],
      planned: { name: pname, stage: stageObj, spec, config: spec.config },
    });
  }
  const n = nodes.length;
  const seedDf = !hasLoad;

  const keyToIdx = (k: string): number => nodes.findIndex((nd) => nd.use === k);

  const edges = new Set<string>(); // "a>b"
  const addEdge = (a: number, b: number) => edges.add(`${a}>${b}`);
  const hasEdge = (a: number, b: number) => edges.has(`${a}>${b}`);

  // 2. needs edges (reported before missing/ambiguous).
  for (let i = 0; i < n; i++) {
    for (const need of nodes[i].needs) {
      const j = keyToIdx(need);
      if (j < 0) throw new UnknownNeedError(nodes[i].pname, [need]);
      addEdge(j, i); // self-edge -> Cycle in step 4
    }
  }

  // 3. Guarded sole-producer edges. First violation (by index, then consumes order) wins.
  const producedBefore = (i: number, x: string): boolean => {
    if (seedDf && x === "df") return true;
    for (let j = 0; j < i; j++) if (nodes[j].produces.includes(x)) return true;
    return false;
  };
  for (let i = 0; i < n; i++) {
    for (const dep of nodes[i].consumes) {
      if (producedBefore(i, dep)) continue;
      const later: number[] = [];
      for (let j = i + 1; j < n; j++) if (nodes[j].produces.includes(dep)) later.push(j);
      if (later.length === 0) {
        throw new WiringError(
          `Stage '${nodes[i].pname}' consumes '${dep}' but no prior stage produces it.`,
          { stage: nodes[i].pname, artifact: dep },
        );
      } else if (later.length === 1) {
        addEdge(later[0], i);
      } else {
        const pinned = later.filter((j) => hasEdge(j, i)).length;
        if (pinned !== 1) {
          throw new AmbiguousProducerError(dep, later.map((j) => nodes[j].use));
        }
      }
    }
  }

  // 4. Stable Kahn topo-sort keyed by config index (min-heap emulated by sorted array).
  const indeg = new Array(n).fill(0);
  const adj: number[][] = Array.from({ length: n }, () => []);
  for (const e of edges) {
    const [a, b] = e.split(">").map(Number);
    if (a === b) throw new CycleError([nodes[a].pname]);
    adj[a].push(b);
    indeg[b] += 1;
  }
  const ready: number[] = [];
  for (let i = 0; i < n; i++) if (indeg[i] === 0) ready.push(i);
  ready.sort((x, y) => x - y);
  const order: number[] = [];
  while (ready.length > 0) {
    const u = ready.shift() as number; // smallest config index
    order.push(u);
    for (const v of adj[u]) {
      indeg[v] -= 1;
      if (indeg[v] === 0) {
        // insert keeping `ready` sorted ascending (stable min-heap behavior)
        let lo = 0;
        while (lo < ready.length && ready[lo] < v) lo++;
        ready.splice(lo, 0, v);
      }
    }
  }
  if (order.length !== n) {
    const cyc: string[] = [];
    for (let i = 0; i < n; i++) if (indeg[i] > 0) cyc.push(nodes[i].pname);
    throw new CycleError(cyc);
  }

  // 5. Emit in sorted order.
  return { stages: order.map((i) => nodes[i].planned) };
}
```

(Note the sorted-array `ready` reproduces the Rust min-heap-by-index exactly: always pop the smallest available config index, keeping the array ascending on insert.)

- [ ] **Step 2: Update `plannerJsonPure.ts` `resolveJsonPure` catch** (replace the `catch (e)` block; import the new classes at the top)

Change the import on line 7:
```ts
import {
  resolvePure, WiringError, AmbiguousProducerError, CycleError, UnknownNeedError,
  type PlannedStage,
} from "../engine/resolver.js";
```

Replace the catch:
```ts
  } catch (e) {
    if (e instanceof WiringError) {
      return JSON.stringify({ err: { kind: "missing_producer", stage: e.stage, artifact: e.artifact } });
    }
    if (e instanceof AmbiguousProducerError) {
      return JSON.stringify({ err: { kind: "ambiguous_producer", artifact: e.artifact, producers: e.producers } });
    }
    if (e instanceof CycleError) {
      return JSON.stringify({ err: { kind: "cycle", stages: e.stages } });
    }
    if (e instanceof UnknownNeedError) {
      return JSON.stringify({ err: { kind: "unknown_need", stage: e.stage, needs: e.needs } });
    }
    // unknown `use`: registry.get threw a plain Error
    const arg2 = JSON.parse(inputStr) as { config: { stages: unknown[] }; stages: Array<{ key: string }> };
    const known = new Set(arg2.stages.map((s) => s.key));
    for (const raw of arg2.config.stages) {
      const use = typeof raw === "string" ? raw : (raw as { use: string }).use;
      if (!known.has(use)) return JSON.stringify({ err: { kind: "unknown_stage", use } });
    }
    throw e;
  }
```

(Confirm against the existing tail of `resolveJsonPure` — if it already maps unknown-stage via a different probe, keep that logic and only add the four `instanceof` branches. Read the current file before editing.)

- [ ] **Step 3: Update `plannerJson.ts` `throwFromErr`** (add branches after the `missing_producer` branch from Task 2)

```ts
  if (err.kind === "ambiguous_producer") {
    throw new AmbiguousProducerError(String(err.artifact), (err.producers as string[]) ?? []);
  }
  if (err.kind === "cycle") {
    throw new CycleError((err.stages as string[]) ?? []);
  }
  if (err.kind === "unknown_need") {
    throw new UnknownNeedError(String(err.stage), (err.needs as string[]) ?? []);
  }
```

Add to the import at the top of `plannerJson.ts`:
```ts
import { WiringError, AmbiguousProducerError, CycleError, UnknownNeedError } from "../engine/resolver.js";
```

- [ ] **Step 4: Re-export the new error classes** (so host `catch` sites can `instanceof` them) — `packages/typescript/goldenpipe/src/core/index.ts:55`

```ts
export { Resolver, WiringError, AmbiguousProducerError, CycleError, UnknownNeedError } from "./engine/resolver.js";
```

- [ ] **Step 5: Typecheck-in-head + commit** (no local vitest — CI runs Leg A/Leg B)

Verify: every new symbol is imported where used; `resolvePure` signature unchanged (`Resolver.resolve` still calls it); no `node:` imports added.

```bash
git add packages/typescript/goldenpipe/src/core/engine/resolver.ts \
        packages/typescript/goldenpipe/src/core/wasm/plannerJsonPure.ts \
        packages/typescript/goldenpipe/src/core/wasm/plannerJson.ts \
        packages/typescript/goldenpipe/src/core/index.ts
git commit -m "feat(goldenpipe): re-conform TS resolver to the DAG contract"
```

---

## Task 7: README doc sweep

**Files:**
- Modify: `packages/typescript/goldenpipe/README.md` (the "Planner: one Rust source of truth" section, ~line 174) and the Python `packages/python/goldenpipe/README.md` if it documents wiring validation.

- [ ] **Step 1: Update the planner section** to note the DAG behavior — one paragraph: the planner now activates `needs`, reorders minimally to satisfy declared dependencies (a consumer listed before its sole producer resolves instead of erroring), and rejects genuinely ambiguous co-production / cycles / unknown `needs` as typed errors, all locked byte-identical across surfaces by the golden vectors.

- [ ] **Step 2: Grep for stale "wiring" references** — Run:
```bash
cd /d/show_case/gg-local-llm && grep -rn "Wiring\|wiring" packages/*/goldenpipe/README.md docs/ | grep -iv "spec\|plan"
```
Fix any that describe the old "errors on any out-of-order" behavior.

- [ ] **Step 3: Commit**

```bash
git add packages/typescript/goldenpipe/README.md packages/python/goldenpipe/README.md
git commit -m "docs(goldenpipe): document the dependency-DAG planner contract"
```

Run the `rollout-docs-sweep` skill after merge for the full doc-surface inventory (context network ADR, docs site, llms.txt) per `feedback_rollout_docs_sweep`.

---

## Task 8: Rebase onto main (after SP3 #1427 merges) + open the PR

**Files:** none (git + gh).

- [ ] **Step 1: Confirm SP3 merged** — Run: `gh pr view 1427 --json state -q .state` (expect `MERGED`). If still `OPEN`, STOP — the DAG branch is stacked on it; opening the PR now would show SP3's diff too.

- [ ] **Step 2: Rebase `--onto origin/main`** (drop the SP3 base commits) — Run:
```bash
cd /d/show_case/gg-local-llm && git fetch origin
git rebase --onto origin/main <last-SP3-commit-sha> feat/goldenpipe-dag
```
Find `<last-SP3-commit-sha>` = the tip of the SP3 branch this was stacked on (`git log --oneline` — the commit just before the first DAG commit `d0494414`). See `reference_rebase_onto_squashed_base` for the squashed-base gotcha (use `--onto`, not a plain rebase).

- [ ] **Step 3: Re-run Rust + Python parity on-box** (post-rebase sanity) — the two box-safe suites from Tasks 3/5. Expected: green.

- [ ] **Step 4: Push + open PR + arm auto-merge, then STOP** (per `feedback_dont_poll_ci_arm_automerge` — do NOT sit in a CI poll loop)

```bash
unset GH_TOKEN
gh auth switch --user benzsevern
git push -u origin feat/goldenpipe-dag
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "goldenpipe-core: dependency-DAG planner contract" \
  --body "$(cat <<'EOF'
Hardens the goldenpipe-core planner (SP1-SP3) from a linear resolver into a dependency-DAG resolver.

- Activates the dead `needs` field; reorders minimally to satisfy declared dependencies (config-order-authoritative + stable Kahn sort). A consumer listed before its sole producer now resolves instead of erroring.
- Renames the `Wiring` PlanError to `MissingProducer` (drops `available`); adds `AmbiguousProducer` / `Cycle` / `UnknownNeed`.
- Rust `goldenpipe-core` is the reference; Python (`resolver.py`) and TS (`resolvePure`) re-conform, locked byte-identical by the SP2/SP3 golden-vector parity gates.
- Already-valid pipelines resolve byte-identically (regression-pinned).

Spec: docs/superpowers/specs/2026-07-04-goldenpipe-dag-contract-design.md
Plan: docs/superpowers/plans/2026-07-04-goldenpipe-dag-contract.md

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
gh pr merge --auto --squash --delete-branch
```

---

## Graduation checklist (spec §9)

- [ ] Rust `resolve.rs` DAG algorithm; on-box `cargo test` / `fmt --check` / `clippy -D warnings` clean.
- [ ] `resolve.json`: wiring case rewritten to `missing_producer`; 6 new DAG cases added; `golden_vectors::vec_resolve` green.
- [ ] Python `resolver.py` + `_planner_json.py` re-conformed; Leg A parity green on all resolve vectors incl. new ones; engine/pipeline tests green.
- [ ] TS `resolvePure` + `plannerJsonPure` + `plannerJson` re-conformed (CI Leg A pure-TS == core + Leg B wasm == core).
- [ ] Regression vector proves already-valid pipelines byte-identical.
- [ ] README planner section updated.
- [ ] PR opened, auto-merge armed, session stopped (no CI poll).
