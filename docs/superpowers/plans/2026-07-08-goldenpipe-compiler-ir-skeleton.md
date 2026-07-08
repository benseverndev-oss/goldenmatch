# GoldenPipe Compiler SP1 — IR Walking Skeleton — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the GoldenPipe compiler foundation — a typed IR, a pure `lower`, and a delegating reference backend that records the IR while executing, with an equivalence gate proving compile==classic byte-for-byte. No passes, no fusion, no emit, no TS. Additive + opt-in.

**Architecture:** The IR + `lower` live in the pyo3-free `goldenpipe-core` kernel (Rust; Python pure mirror; golden-vector'd), same pattern as the planner. The compiler host reuses the classic `Runner`'s loop via a new post-stage **hook** (so execution is byte-identical by construction) and, per stage, *captures* the concrete config (host, data-dependent) and calls pure `lower` (kernel) to accumulate a `CompiledPipeline`. An equivalence gate runs classic vs compiled on tiny fixtures.

**Tech Stack:** Python (polars/goldencheck/goldenflow/goldenmatch), Rust (serde tagged-union), the existing goldenpipe-core golden-vector infra.

---

## Box / environment constraints

- **Python is box-runnable** (real red→green TDD). Rust **cannot** `cargo build` on the box (CI only) → write-against-spec + `rustfmt` + grep/eye + CI.
- Python env (native Windows, `;` PYTHONPATH; note **goldenflow + goldenmatch added**):
  ```bash
  INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="packages/python/goldenpipe;packages/python/goldencheck;packages/python/infermap;packages/python/goldencheck-types;packages/python/goldenflow;packages/python/goldenmatch"
  export POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 GOLDENMATCH_AUTOCONFIG_MEMORY=0
  ```
  `cd "D:/show_case/gg-local-llm"` each Bash call. `ruff check` touched Python.
- Equivalence-gate fixtures: **tiny** (<100 rows) so match runs fast + stays under `confidence_required`. Synthetic person rows with **surnames spread across soundex codes** (else blocking hangs — see repo lore).
- Rust: `rustfmt --edition 2021 <file>` works on box. Watch `-D warnings` clippy; use `#[serde(tag = "kind")]` for the `IrNode` union.
- Spec: `docs/superpowers/specs/2026-07-08-goldenpipe-compiler-ir-skeleton-design.md`. @superpowers:test-driven-development per Python task.

## Canonical IR (JSON shape, both surfaces)

`lower` is pure over a **captured concrete config** and returns a list of nodes. Nodes are JSON objects with a `kind` tag:
```
CompiledPipeline = { "nodes": [IrNode], "edges": [[from_id, to_id, artifact]] }
IrNode base fields: { "kind", "id", "origin_stage", "resolved" } plus per-kind:
  Source    { produces: ["df"] }
  Scan      { column, ops: [str] }
  Map       { column, op: str }
  Partition { keys: [str] }
  PairScore { scorer: <json> }
  Connected { method: <json> }
  Barrier   { raw_config: <json> }
```
`lower(origin_stage, kind_hint, concrete_config, next_id) -> ([IrNode], next_id)` is a pure function of its inputs (deterministic ids from `next_id`). The **capture** step (host) turns each executed stage into `(kind_hint, concrete_config, resolved)`; `lower` turns that into nodes. Golden vectors test `lower` directly.

## File structure

- Create `packages/python/goldenpipe/goldenpipe/compiler/ir.py` — pure `lower` + node builders (the Python kernel mirror).
- Create `packages/python/goldenpipe/goldenpipe/compiler/capture.py` — host: `capture_stage(planned, ctx, result) -> (kind, concrete, resolved)`.
- Create `packages/python/goldenpipe/goldenpipe/compiler/compiled_runner.py` — the opt-in compiler entry point (reuses `Runner` + hook, accumulates IR).
- Modify `packages/python/goldenpipe/goldenpipe/engine/runner.py` — add an optional post-stage `hook`.
- Modify `packages/python/goldenpipe/goldenpipe/core/_planner_json.py` — add `lower_json`.
- Modify `packages/python/goldenpipe/tests/core/test_planner_parity.py` — register `lower`.
- Tests: `tests/compiler/test_ir_lower.py`, `test_capture.py`, `test_compiled_runner.py`, `test_equivalence.py`.
- Kernel (Rust): `goldenpipe-core/src/ir.rs`, `src/lib.rs` (`pub mod ir`), `src/json.rs` (`lower_json`), `tests/golden_vectors.rs` (+ `vec_lower`), `tests/vectors/lower.json`; shim exports in `goldenpipe-wasm/src/lib.rs` + `goldenpipe-native/src/lib.rs`; `core/_native_loader.py` passthrough.

---

## Task 1: Python IR + pure `lower` (box TDD)

**Files:** Create `goldenpipe/compiler/__init__.py` (empty), `goldenpipe/compiler/ir.py`; Test `tests/compiler/test_ir_lower.py`.

- [ ] **Step 1: Write the failing test** `tests/compiler/test_ir_lower.py`:

```python
from goldenpipe.compiler.ir import lower


def test_load_lowers_to_source():
    nodes, nid = lower("load", "source", {}, 0)
    assert nodes == [{"kind": "Source", "id": 0, "origin_stage": "load", "resolved": False, "produces": ["df"]}]
    assert nid == 1


def test_flow_transforms_lower_to_ordered_map_nodes():
    concrete = {"transforms": [{"column": "email", "ops": ["email_normalize", "email_canonical"]}]}
    nodes, nid = lower("goldenflow.transform", "map", concrete, 5, resolved=True)
    assert nodes == [
        {"kind": "Map", "id": 5, "origin_stage": "goldenflow.transform", "resolved": True, "column": "email", "op": "email_normalize"},
        {"kind": "Map", "id": 6, "origin_stage": "goldenflow.transform", "resolved": True, "column": "email", "op": "email_canonical"},
    ]
    assert nid == 7


def test_check_lowers_to_scan_per_column():
    concrete = {"columns": [{"column": "name", "ops": ["nullability", "pattern_consistency"]}]}
    nodes, _ = lower("goldencheck.scan", "scan", concrete, 0, resolved=True)
    assert nodes == [{"kind": "Scan", "id": 0, "origin_stage": "goldencheck.scan", "resolved": True, "column": "name", "ops": ["nullability", "pattern_consistency"]}]


def test_match_lowers_to_partition_pairscore_connected():
    concrete = {"keys": ["email"], "scorer": {"name": "jaro"}, "method": {"name": "connected_components"}}
    nodes, _ = lower("goldenmatch.dedupe", "match", concrete, 0, resolved=True)
    kinds = [n["kind"] for n in nodes]
    assert kinds == ["Partition", "PairScore", "Connected"]
    assert nodes[0]["keys"] == ["email"]
    assert nodes[1]["scorer"] == {"name": "jaro"}
    assert nodes[2]["method"] == {"name": "connected_components"}


def test_unknown_stage_lowers_to_barrier():
    nodes, _ = lower("infer_schema", "barrier", {"foo": 1}, 3)
    assert nodes == [{"kind": "Barrier", "id": 3, "origin_stage": "infer_schema", "resolved": False, "raw_config": {"foo": 1}}]
```

- [ ] **Step 2: Run to verify FAIL** — `"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/test_ir_lower.py -v` → ModuleNotFoundError.

- [ ] **Step 3: Implement `goldenpipe/compiler/ir.py`:**

```python
"""Pure IR + lower — the SP2 mirror of goldenpipe-core/src/ir.rs. Deterministic,
no I/O. `lower` turns a captured concrete config into IR nodes with sequential ids."""
from __future__ import annotations


def _node(kind, nid, origin, resolved, **rest):
    return {"kind": kind, "id": nid, "origin_stage": origin, "resolved": resolved, **rest}


def lower(origin_stage: str, kind_hint: str, concrete: dict, next_id: int, resolved: bool = False):
    """(origin_stage, kind_hint, concrete_config, next_id) -> (nodes, next_id).
    kind_hint routes to the node builder; unknown -> Barrier. Pure + total."""
    nid = next_id
    nodes = []
    if kind_hint == "source":
        nodes.append(_node("Source", nid, origin_stage, resolved, produces=["df"]))
        nid += 1
    elif kind_hint == "scan":
        for col in concrete.get("columns", []):
            nodes.append(_node("Scan", nid, origin_stage, resolved, column=col["column"], ops=list(col.get("ops", []))))
            nid += 1
    elif kind_hint == "map":
        for spec in concrete.get("transforms", []):
            for op in spec.get("ops", []):
                nodes.append(_node("Map", nid, origin_stage, resolved, column=spec["column"], op=op))
                nid += 1
    elif kind_hint == "match":
        nodes.append(_node("Partition", nid, origin_stage, resolved, keys=list(concrete.get("keys", []))))
        nid += 1
        nodes.append(_node("PairScore", nid, origin_stage, resolved, scorer=concrete.get("scorer")))
        nid += 1
        nodes.append(_node("Connected", nid, origin_stage, resolved, method=concrete.get("method")))
        nid += 1
    else:  # barrier / unknown
        nodes.append(_node("Barrier", nid, origin_stage, resolved, raw_config=concrete))
        nid += 1
    return nodes, nid
```

- [ ] **Step 4: Run to verify PASS** — expect 5 passed.
- [ ] **Step 5: ruff + commit**
```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/compiler/ir.py packages/python/goldenpipe/tests/compiler/test_ir_lower.py
git add packages/python/goldenpipe/goldenpipe/compiler/ packages/python/goldenpipe/tests/compiler/test_ir_lower.py && git commit -m "feat(goldenpipe): pure IR + lower (compiler SP1)"
```

---

## Task 2: Golden vectors + Python parity leg (box)

**Files:** Create `packages/rust/extensions/goldenpipe-core/tests/vectors/lower.json`; Modify `core/_planner_json.py`, `tests/core/test_planner_parity.py`.

- [ ] **Step 1: Author `lower.json`** — cases `{input:{origin_stage,kind_hint,concrete,next_id,resolved}, expected:{nodes,next_id}}` mirroring the 5 unit cases (load→Source; flow multi-op→ordered Map; check→Scan; match→triple; unknown→Barrier). Compute `expected` by hand from the ir.py logic (deterministic).

Example first case:
```json
[
  {"input": {"origin_stage": "load", "kind_hint": "source", "concrete": {}, "next_id": 0, "resolved": false},
   "expected": {"nodes": [{"kind": "Source", "id": 0, "origin_stage": "load", "resolved": false, "produces": ["df"]}], "next_id": 1}}
]
```
(Add the other 4 cases.)

- [ ] **Step 2: Add `lower_json` to `_planner_json.py`** (calls the real `goldenpipe.compiler.ir.lower`):
```python
from goldenpipe.compiler import ir as _ir  # add to imports

def lower_json(s: str) -> str:
    a = json.loads(s)
    nodes, nid = _ir.lower(a["origin_stage"], a["kind_hint"], a.get("concrete", {}), a.get("next_id", 0), a.get("resolved", False))
    return json.dumps({"nodes": nodes, "next_id": nid})
```

- [ ] **Step 3: Register in `test_planner_parity.py`** — add `("lower", PJ.lower_json)` to `_CASES` (Leg A) and `("lower", "lower_json")` to the Leg B list.

- [ ] **Step 4: Run Leg A** — `"$INTERP" -m pytest packages/python/goldenpipe/tests/core/test_planner_parity.py -k lower -v` → Leg A PASS; Leg B skips or errors on the stale wheel (the documented shadow-venv gotcha — same as repair-plan; report which). Do NOT weaken assertions.

- [ ] **Step 5: ruff + commit**
```bash
"$INTERP" -m ruff check packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/tests/core/test_planner_parity.py
git add packages/rust/extensions/goldenpipe-core/tests/vectors/lower.json packages/python/goldenpipe/goldenpipe/core/_planner_json.py packages/python/goldenpipe/tests/core/test_planner_parity.py && git commit -m "test(goldenpipe): lower golden vectors + python parity leg (compiler SP1)"
```

---

## Task 3: Host capture — concrete config from an executed stage (box TDD)

**Files:** Create `goldenpipe/compiler/capture.py`; Test `tests/compiler/test_capture.py`.

`capture_stage(planned, ctx, result) -> (kind_hint, concrete, resolved)` reads what the stage produced. Flow → reconstruct `{transforms:[{column,ops}]}` from `ctx.artifacts["manifest"].records` (grouped by column, order preserved) — works for explicit AND auto (records reflect what ran). Check → `{columns:[{column, ops}]}` from the profile columns + findings' check names per column. Match → explicit `planned.config` if given, else deterministic `_build_config_from_contexts(column_contexts, df)`, normalized to `{keys, scorer, method}`. load → `("source", {}, False)`. Unknown → `("barrier", planned.config or {}, False)`. `resolved = not planned.config` (auto) for flow/check/match.

- [ ] **Step 1: Write failing tests** `tests/compiler/test_capture.py` (use lightweight stand-ins for `planned`/`ctx`/`result`; assert the `(kind, concrete, resolved)` tuple). Cover: load→source; flow from a fake manifest with 2 records on one column→`{transforms:[{column, ops:[t1,t2]}]}` resolved=True when planned.config empty; unknown stage→barrier. (Match/Check capture are covered end-to-end in the equivalence gate Task 5; here just unit the flow-from-manifest grouping + load + barrier + the resolved flag.)

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3: Implement `capture.py`.** For Flow, group `ctx.artifacts["manifest"].records` by `.column` into ordered `ops` lists (`.transform` per record) → `{"transforms":[{column, ops}]}`. For Match, import `_build_config_from_contexts` from `adapters/match.py`; it returns a **pydantic `GoldenMatchConfig` object (or None)**, NOT a dict — **you MUST dict-ify it** (`.model_dump()` / `.dict()`) before it goes to `lower`, or `lower`'s `.get(...)` calls will `AttributeError` on the object. Its real fields are `matchkeys`/`blocking`, not `keys/scorer/method`, so the lowered `Partition/PairScore/Connected` node is a **placeholder record** for SP1 (empty/None sub-fields) — that is fine: Task 5 asserts *flow* fidelity, not match fidelity, and the match branch degrades gracefully on missing keys. For Check, build `{"columns":[{column, ops}]}` from the profile columns + the check names in `ctx.artifacts["findings"]` per column. `load` → `("source", {}, False)`. Unknown → `("barrier", planned.config or {}, False)`. `resolved = not planned.config` for flow/check/match.

**Implementer note:** the Match/Check node payloads are a **faithful JSON RECORD of what ran**, not a re-runnable explicit config — SP1 only needs the record. If precise sub-field extraction is awkward, store the `.model_dump()` dict as-is; `lower`'s match branch reads `.get("keys"/"scorer"/"method")` so absent keys become empty/None (no crash). The equivalence gate validates *execution* fidelity (byte-identical artifacts) + *flow* IR fidelity, not a match sub-schema.

- [ ] **Step 4: Run to verify PASS.**
- [ ] **Step 5: ruff + commit** (`feat(goldenpipe): compiler stage-capture (SP1)`).

---

## Task 4: Runner hook + CompiledRunner (box TDD)

**Files:** Modify `engine/runner.py`; Create `goldenpipe/compiler/compiled_runner.py`; Test `tests/compiler/test_compiled_runner.py`.

- [ ] **Step 1: Write a failing test** asserting (a) `Runner.run(plan, ctx)` with no hook behaves exactly as today (a small monkeypatched/stub plan → same results), and (b) `compile_and_run(plan, ctx, registry)` returns `(results, compiled)` where `compiled["nodes"]` has a `Source` for load and `Map` nodes for an explicit flow stage, and `results` equals what `Runner` alone produces.

- [ ] **Step 2: Run to verify FAIL.**

- [ ] **Step 3a: Add the hook to `Runner.run`.** Change the signature to `def run(self, plan, ctx, hook=None)`. Place the hook call at the **bottom of the while-loop body, AFTER the whole `try/except`**, guarded on success:
```python
            if hook is not None and result.status == StageStatus.SUCCESS:
                hook(planned, ctx, result)
```
Rationale (from review): putting it *inside* the `try` would let a `capture_stage`/`lower` bug be swallowed by the stage's broad `except Exception` and misattributed as a *stage* failure. Outside the try, a compiler-hook bug propagates as itself. `result` is in scope after the try/except (assigned in both branches); the SKIPPED path `continue`s before the try so the hook never fires for skipped stages. **Byte-identical when `hook is None`** — existing callers unaffected.

- [ ] **Step 3b: Implement `compiled_runner.py`:**
```python
"""Opt-in compiler entry point: reuses the classic Runner (execution byte-identical)
and records the IR via a post-stage hook. Returns (results, CompiledPipeline)."""
from __future__ import annotations

from goldenpipe.compiler.capture import capture_stage
from goldenpipe.compiler.ir import lower
from goldenpipe.engine.runner import Runner


def compile_and_run(plan, ctx, registry):
    compiled = {"nodes": [], "edges": []}
    state = {"nid": 0}
    producer = {}  # artifact -> id of the last node that produced it (for edges)

    def hook(planned, ctx_, result):
        kind, concrete, resolved = capture_stage(planned, ctx_, result)
        nodes, state["nid"] = lower(planned.name, kind, concrete, state["nid"], resolved)
        if not nodes:
            return
        info = getattr(planned.stage, "info", None)
        # edge from each consumed artifact's producer -> this stage's first node
        for art in list(getattr(info, "consumes", []) or []):
            if art in producer:
                compiled["edges"].append([producer[art], nodes[0]["id"], art])
        # this stage's last node becomes the producer of its output artifacts
        for art in list(getattr(info, "produces", []) or []):
            producer[art] = nodes[-1]["id"]
        compiled["nodes"].extend(nodes)

    results = Runner(registry).run(plan, ctx, hook=hook)
    return results, compiled
```

- [ ] **Step 4: Run to verify PASS + regression:** run `tests/test_adapters.py` and any existing `runner` test to prove the `hook=None` default didn't change classic behavior.

- [ ] **Step 5: ruff + commit** (`feat(goldenpipe): Runner post-stage hook + CompiledRunner (SP1)`).

---

## Task 5: Equivalence gate (box, the heart)

**Files:** Test `tests/compiler/test_equivalence.py`; a small fixtures helper.

- [ ] **Step 1: Write the fixtures + normalizer.** A `_tiny_people_df()` returning a polars DataFrame of ~30 rows with `first_name`, `last_name` (surnames spread across distinct soundex codes — e.g. Smith/Jones/Brown/Garcia/Lee/Nguyen/Patel/Khan/Diaz/Okafor…), `email`, plus a couple of dupes. **Include deliberately dirty values** (leading/trailing whitespace, mixed-case emails, inconsistent casing) so Flow auto-detect actually emits transforms — else `manifest.records` is empty → zero `Map` nodes → the structural assertion fails through no fault of the code.

  **CRITICAL — `goldencheck.scan` reads a FILE, not `ctx.df`.** `ScanStage.run` calls `goldencheck.scan_file(ctx.metadata["source"])` which `read_file(path)` from disk — it does NOT read `ctx.df`. So for pipelines #2/#3, the fixture MUST write the tiny df to a temp CSV (`tmp_path` fixture) and set `ctx.metadata["source"] = str(csv_path)` (mirror `Pipeline.run(source=...)` which does `pl.read_csv`). Without this, check lands on the `except` path → the hook never fires (no `Scan` node), check never produces `column_contexts`, and match falls through to the bare-auto nondeterministic path the design engineered around. Pipeline #1 (flow-only, no check) can use `PipeContext(df=df)` directly.

  A `_normalize(artifacts)` that: replaces `manifest.created_at` with a constant (the only wall-clock field in the manifest — confirmed), drops match-stats timing keys, and converts polars frames to a canonical form (`.to_dicts()` or `.write_csv()` string) for comparison.

- [ ] **Step 2: Write the equivalence tests** — for each of 3 pipelines, build the `ExecutionPlan` via `Resolver.resolve(PipelineConfig(stages=[StageSpec(use=...)]), StageRegistry())` (the real entry — see `_api.py:79` / `core/pipeline.py`). **Keep the auto-prepended `load` stage** (do NOT strip it the way `run_stages` in `_api.py` does) — Pipeline #1 asserts a `Source` node, which only exists if `load` runs; `LoadStage` is a no-op that marks the df available, so keeping it with a pre-populated `ctx.df` is correct. Then:
```python
def _run_classic(plan, df, registry):
    ctx = PipeContext(df=df)
    Runner(registry).run(plan, ctx)
    return ctx

def _run_compiled(plan, df, registry):
    ctx = PipeContext(df=df)
    _, compiled = compile_and_run(plan, ctx, registry)
    return ctx, compiled
```
Assert `_normalize(classic.artifacts)` == `_normalize(compiled_ctx.artifacts)` across `df, findings, profile, manifest, clusters, golden` (whichever the pipeline produces). Pipelines:
1. `load → goldenflow.transform` with **explicit** transforms → compare `df, manifest`; assert `compiled` has `Source` + `Map` nodes.
2. `load → goldencheck.scan → goldenflow.transform` **auto** → compare `df, findings, profile, manifest`; assert `Scan` + `Map` nodes with `resolved=True`.
3. `load → goldencheck.scan → goldenflow.transform → goldenmatch.dedupe` → compare `clusters, golden` (+ the rest); assert `Partition/PairScore/Connected` nodes.

- [ ] **Step 3: Fidelity assertion** — for the flow stage in #2, assert the recorded `Map` nodes' `(column, op)` sequence matches `manifest.records` (what actually ran).

- [ ] **Step 4: Run** with the full env (`GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_AUTOCONFIG_MEMORY=0`). Expect all pass. If match hangs → check surname soundex spread; if `confidence_required` raises → shrink/adjust fixtures. Report the exact outcome; do NOT weaken the byte-identity assertion to force a pass — if artifacts differ, investigate (likely a missed normalization field) and fix the normalizer, or surface a real compile≠classic divergence as BLOCKED.

- [ ] **Step 5: ruff + commit** (`test(goldenpipe): compiler equivalence gate (SP1)`).

---

## Task 6: Rust kernel mirror + shims (write-against-spec, CI-verified)

**Files:** Create `goldenpipe-core/src/ir.rs`; Modify `src/lib.rs`, `src/json.rs`, `tests/golden_vectors.rs`; Modify `goldenpipe-wasm/src/lib.rs`, `goldenpipe-native/src/lib.rs`, `packages/python/goldenpipe/goldenpipe/core/_native_loader.py`.

Mirror `ir.py` exactly. No `cargo` on box — `rustfmt` + grep + CI.

- [ ] **Step 1: `src/ir.rs`** — `IrNode` as a `#[serde(tag = "kind")]` enum (variants Source/Scan/Map/Partition/PairScore/Connected/Barrier) OR, simpler for byte-parity with the Python dicts, emit nodes as `serde_json::Value` objects built by hand (guarantees identical key set/order to Python). Given the Python emits plain dicts with a `kind` field, **build `serde_json::Map` objects directly** in `lower` (not a typed enum) so the JSON matches byte-for-byte incl. key order (`preserve_order` is on). Signature: `pub fn lower(origin_stage: &str, kind_hint: &str, concrete: &Value, next_id: u64, resolved: bool) -> (Vec<Value>, u64)`. Reproduce the per-`kind_hint` branches (source/scan/map/match/barrier) exactly, same field insertion order as Python (`kind, id, origin_stage, resolved, <rest>`).

- [ ] **Step 2: `lib.rs`** add `pub mod ir;`; **`json.rs`** add `lower_json` (parse `{origin_stage, kind_hint, concrete, next_id, resolved}` → call `ir::lower` → `{nodes, next_id}`), matching the file's wrapper style + `parse_err`.

- [ ] **Step 3: `tests/golden_vectors.rs`** add `#[test] fn vec_lower() { run("lower", lower_json); }`.

- [ ] **Step 4: Shim exports** — add `lower_json` to `goldenpipe-wasm/src/lib.rs` and `goldenpipe-native/src/lib.rs` (mirror `apply_decision_json`), and a `lower_json` passthrough to `core/_native_loader.py` (mirror the repair-plan `build_repair_plan_json` wiring — the wheel needs the symbol for Leg B).

- [ ] **Step 5: rustfmt + grep-verify** the four `.rs` files; confirm `pub mod ir`, `lower_json` reachable via `use goldenpipe_core::json::*`, key-order matches Python. **Do not** `cargo build`.

- [ ] **Step 6: commit** (`feat(goldenpipe-core): lower kernel + json + shims (compiler SP1)`).

---

## Task 7: Ship

- [ ] **Step 1:** `git fetch origin && git rebase origin/main` (resolve any conflicts in `json.rs`/`lib.rs`/`golden_vectors.rs`/`_planner_json.py`/`test_planner_parity.py`/`_native_loader.py` — keep both sides' entries).
- [ ] **Step 2:** Re-run the box suite:
```bash
"$INTERP" -m pytest packages/python/goldenpipe/tests/compiler/ packages/python/goldenpipe/tests/core/test_planner_parity.py -k "compiler or lower or ir or equivalence or capture or Source" -q
```
plus `tests/test_adapters.py` (no-regression). Expect green.
- [ ] **Step 3:** Push + PR + arm auto-merge, then **STOP** (no CI polling):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/goldenpipe-compiler-ir
gh pr create --base main --title "feat(goldenpipe): compiler SP1 — IR walking skeleton" --body "<summary: IR + pure lower (kernel) + delegating CompiledRunner (reuses Runner via a post-stage hook) + equivalence gate (classic==compiled byte-identical on tiny fixtures); additive/opt-in, classic runner default; no passes/fusion/emit/TS. Links spec + plan. Note: sub-project 1 of the fuse-and-emit compiler program.>"
gh pr merge <N> --auto --squash   # merge-queue: NO --delete-branch
```
Watch CI: Rust golden vectors (`vec_lower`), Python `test_planner_parity` Leg A+B, the compiler tests.

---

## Verification summary
- Box-green: `test_ir_lower`, `test_capture`, `test_compiled_runner`, `test_equivalence` (classic==compiled byte-identical + fidelity), `test_planner_parity -k lower` Leg A, `test_adapters` (no regression).
- CI-green: Rust `vec_lower`, Python Leg B (native wheel), + all above.
- Byte-identical-when-inactive: `Runner.run` with `hook=None` is unchanged; the classic path and all existing callers are untouched.
