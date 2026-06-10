# DataFusion Spine (SP2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development.
> Steps use checkbox (`- [ ]`). **CI-validated posture:** the dev box HANGS on
> `import goldenmatch`/`import polars`/`import datafusion`. Subagents validate
> Python via `ruff check` + `python -m py_compile` ONLY, and Rust via `cargo
> check`/`cargo build` (NOT a long `cargo test` if it hangs — push to CI). NEVER
> `import`, `pytest`, `uv`, `pyright`. Real tests run in CI. Branch off `main`;
> benzsevern auth (`GH_TOKEN=$(gh auth token --user benzsevern)`), NEVER
> benzsevern-mjh. `docs/superpowers/` is gitignored — don't `git add` spec/plan.

**Goal:** Stand up the DataFusion spine over score→dedup→[UF]→id_prep→golden with
out-of-core spill and a Rust scorer `ScalarUDF` (B2). **This plan fully details
Stage A (the FFI hard gate); Stages B-E are outlined and re-planned after A.**

**Architecture:** A separate maturin crate exports a native `ScalarUDF` via
`datafusion-ffi` as a PyCapsule; the Python `datafusion` 53 `SessionContext`
registers it and threads the relational stages with a fair-spill pool; UF routes
to the existing scipy/label-prop path. Stage A proves ONLY the cross-crate FFI
boundary on v53 with a trivial UDF.

**Tech Stack:** Rust (datafusion 53.x, datafusion-ffi 53.x, pyo3/arrow-ffi),
maturin, Python datafusion 53, PyArrow.

**Spec:** `docs/superpowers/specs/2026-06-03-datafusion-spine-design.md`

---

## Stage A is a HARD GATE

Stage A proves a Rust-crate `ScalarUDF` can be registered into the Python
`datafusion` 53 `SessionContext` via `datafusion-ffi` (PyCapsule). **If it cannot
be made to work on v53 after a bounded effort, STOP and escalate to the human**
(fall back: B1 Python UDF in `datafusion_backend.py` for the spine). Do NOT start
Stages B-E until A is GREEN in CI.

### CANONICAL REFERENCE (mirror this verbatim — it solves the FFI shape)

Clone `apache/datafusion-python` at tag **`53.0.0`** and mirror
`examples/datafusion-ffi-example/`:
- `src/scalar_udf.rs` → the `#[pyclass]` + `__datafusion_scalar_udf__` + FFI export
  pattern (Tasks A1/A2).
- `python/tests/_test_scalar_udf.py` → the `udf(MyUDF())` + `ctx.register_udf(...)`
  registration (Task A3).
Swap their `IsNullUDF` (Any→Bool) for our `AddOneUDF` (Int64→Int64). This reference
resolves every FFI-API question below; do NOT hand-roll from memory.

### Verified FFI facts (from datafusion-python 53 source + the example)

- The protocol object is a `#[pyclass]` with a `__datafusion_scalar_udf__(&self,
  py) -> PyResult<Bound<PyCapsule>>` method (NOT a bare pyfunction returning a
  capsule). Capsule name: `cr"datafusion_scalar_udf"`.
- FFI build: `FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(impl)))` — needs `Arc`
  (`From<Arc<ScalarUDF>>` only).
- `ScalarUDFImpl` v53 method is `invoke_with_args(&self, args: ScalarFunctionArgs)
  -> Result<ColumnarValue>` (NOT `invoke_batch`/`invoke`).
- Python registration: `from datafusion import udf; ctx.register_udf(udf(
  AddOneUDF()))` — `udf()` detects the dunder + calls `from_pycapsule` internally.
  Do NOT pass a raw capsule to `from_pycapsule`.
- Arrow major: datafusion 53 pins **arrow 58** (NOT 53). New crate uses `arrow =
  "58"` with `features=["ffi"]`; this differs from `_native`'s arrow 55 — the
  reason the crate MUST be separate (separate cdylibs tolerate it).

## File structure (Stage A)

- Create `packages/rust/extensions/datafusion-udf/` — a NEW, standalone maturin
  crate (own `Cargo.toml` with `[workspace]` to isolate it, own
  `pyproject.toml`). One responsibility: export native ScalarUDFs over the FFI
  boundary. Do NOT touch the existing `native` crate or `scripts/build_native.py`.
  - `Cargo.toml` (mirror the example's dep set — sub-crates, not the umbrella):
    `datafusion-ffi = "=53.x"`, `datafusion-expr = "=53.x"` (for `ScalarUDF`/
    `ScalarUDFImpl`), `datafusion-common = "=53.x"`, `arrow = { version="58",
    features=["ffi"] }`, `arrow-array`, `arrow-schema`, `pyo3 = { features=[
    "extension-module","abi3-py311"] }`. `[lib] crate-type=["cdylib"]`. (datafusion-ffi's
    own Cargo.toml says do NOT add the umbrella `datafusion` crate.)
  - `src/lib.rs`: an `AddOneUDF` `#[pyclass]` implementing `ScalarUDFImpl`
    (`invoke_with_args`) with a `__datafusion_scalar_udf__` method exporting
    `FFI_ScalarUDF::from(Arc::new(ScalarUDF::from(...)))` as a `PyCapsule` named
    `cr"datafusion_scalar_udf"`. Registered in the `#[pymodule]`.
- Create `packages/rust/extensions/datafusion-udf/pyproject.toml` — maturin build
  (mirror `packages/rust/extensions/native/`'s maturin pyproject).
- Create `packages/python/goldenmatch/tests/test_datafusion_ffi_udf.py` — the
  Python registration + `SELECT add_one(x)` test.
- Modify `.github/workflows/ci.yml` — a step (goldenmatch lane) that builds the
  new crate (`maturin develop` / `pip install`) + installs `datafusion>=53,<54`,
  so the FFI test actually runs (loud guard, no silent skip).

---

### Task A1: The maturin crate skeleton + version pins

**Files:**
- Create: `packages/rust/extensions/datafusion-udf/Cargo.toml`
- Create: `packages/rust/extensions/datafusion-udf/pyproject.toml`
- Create: `packages/rust/extensions/datafusion-udf/src/lib.rs` (stub)

- [ ] **Step 1: Confirm the exact 53 minor.** Check crates.io for the latest
  `datafusion-ffi` 53.x and the matching `datafusion` 53.x + the `arrow` major
  datafusion 53 pins. Pin all three EXACTLY (`=53.x`) in `Cargo.toml`. Read
  `packages/rust/extensions/native/Cargo.toml` for the pyo3/abi3 + `[workspace]`
  isolation pattern to mirror.

- [ ] **Step 2: Write `Cargo.toml`** — `[package]`, `[lib] crate-type=["cdylib"]`,
  `[workspace]` (empty, to isolate from the bridge workspace like `native`), the
  sub-crate dep set above (`datafusion-ffi`/`datafusion-expr`/`datafusion-common`
  `=53.x`, `arrow=58` `features=["ffi"]`, `pyo3` extension-module+abi3-py311).
  `pyproject.toml` — maturin backend mirroring `native/pyproject.toml` (module
  name `goldenmatch_datafusion_udf`).

- [ ] **Step 3: Write a stub `src/lib.rs`** — empty `#[pymodule]` so the crate
  compiles. Validate: `cargo check` in the crate dir (if it doesn't hang; else
  push to CI). Do NOT `cargo build --release` locally if it's slow — CI builds it.

- [ ] **Step 4: Commit.** `feat(df-udf): maturin crate skeleton (datafusion 53.x, ffi) — isolated`

### Task A2: The `add_one` ScalarUDF + FFI PyCapsule export

**Files:**
- Modify: `packages/rust/extensions/datafusion-udf/src/lib.rs`

- [ ] **Step 1: Mirror the canonical example** `examples/datafusion-ffi-example/
  src/scalar_udf.rs` @ tag 53.0.0, swapping `IsNullUDF` → `AddOneUDF`. Implement
  `AddOneUDF` as a `#[pyclass]` AND `impl ScalarUDFImpl for AddOneUDF`:
  `name()->"add_one"`, `signature()=Signature::exact(vec![Int64], Immutable)`,
  `return_type()->Int64`, `invoke_with_args(&self, args: ScalarFunctionArgs) ->
  Result<ColumnarValue>` adding 1 to the Int64 array.

- [ ] **Step 2: The FFI export METHOD on the pyclass** (NOT a bare pyfunction):
  ```rust
  #[pymethods]
  impl AddOneUDF {
      #[new]
      fn new() -> Self { Self { signature: Signature::exact(vec![DataType::Int64], Volatility::Immutable) } }
      fn __datafusion_scalar_udf__<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyCapsule>> {
          let name = cr"datafusion_scalar_udf";
          let func = Arc::new(ScalarUDF::from(self.clone()));   // Arc REQUIRED
          let provider = FFI_ScalarUDF::from(func);
          PyCapsule::new(py, provider, Some(name.into()))
      }
  }
  ```
  Register `AddOneUDF` in the `#[pymodule]` (`m.add_class::<AddOneUDF>()?`).

- [ ] **Step 3: Validate** `cargo check` (the crate compiles + FFI types line up).
  Do NOT run Python. Commit. `feat(df-udf): AddOneUDF pyclass + __datafusion_scalar_udf__ FFI export`

### Task A3: Python registration test + CI wiring (the GATE)

**Files:**
- Create: `packages/python/goldenmatch/tests/test_datafusion_ffi_udf.py`
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1: Write the failing test.**

```python
import pytest
pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")
# HARD import (NOT importorskip) — the lane BUILDS this crate; a build failure
# must FAIL, not skip (the loud guard).
import goldenmatch_datafusion_udf  # noqa: E402

def test_ffi_scalar_udf_registers_and_evaluates():
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import AddOneUDF
    ctx = SessionContext()
    ctx.register_udf(udf(AddOneUDF()))     # udf() detects __datafusion_scalar_udf__ + from_pycapsule
    ctx.from_arrow(pa.table({"x": pa.array([1, 2, 3], pa.int64())}), name="t")
    batches = ctx.sql("SELECT add_one(x) AS y FROM t ORDER BY x").collect()
    got = [v for b in batches for v in b.column(0).to_pylist()]
    assert got == [2, 3, 4]
```

- [ ] **Step 2: Wire CI to build the crate + run the test (no silent skip).** In
  the `python (goldenmatch)` lane: build/install the new crate (`maturin develop
  -m packages/rust/extensions/datafusion-udf/pyproject.toml` or `pip install` the
  built wheel) AND `uv pip install 'datafusion>=53,<54'`. Add a non-importorskip
  guard test asserting `goldenmatch_datafusion_udf` imports, so a build failure
  fails loudly (per CLAUDE.md the per-step JSON lies on continue-on-error — the
  guard test is the real signal).

- [ ] **Step 3: Validate** `ruff` + `py_compile` the test; inspect the ci.yml diff.
  Commit. `test(df-udf): FFI ScalarUDF registration gate + CI build/install`

- [ ] **Step 4: THE GATE — push, open PR, confirm CI GREEN.** The test must
  actually RUN and PASS in CI (not skip). If it fails on an API-name mismatch
  (`from_pycapsule` / capsule name / `invoke_*`), fix against the v53 source and
  re-push. If the FFI boundary fundamentally cannot be made to work on v53 after a
  bounded effort (a few iterations), STOP and escalate with the exact error —
  recommend the B1 fallback. **Do NOT proceed to Stage B until this is GREEN.**

---

## Stages B-E (OUTLINE — detailed in a follow-on plan AFTER Stage A is GREEN)

Each becomes its own fully-detailed plan once the prior gate passes. Listed so the
arc is visible; do NOT execute from these outlines.

- **Stage B — scorer as FFI ScalarUDF.** Replace `add_one` with the native string
  scorers (jaro_winkler/token_sort/...) as `FFI_ScalarUDF`(s) over `(Utf8, Utf8)
  -> Float64`. Gate: UDF score == in-process native scorer (ε 1e-6, f32-origin)
  on a string-pair fixture. Reuses the native scorer Rust already in `_native`
  (may need to vendor/share the scorer code into the new crate).
- **Stage C — spine orchestration.** `run_spine(blocked_candidates, config, *,
  memory_limit) -> (golden_df, assignments_df)` threading score (block-self-join
  + UDF) → dedup (`max(score) GROUP BY a,b`) → UF (scipy via
  `build_clusters_distributed`, frame-native `all_ids`, no `materialize_cluster_
  dict`) → id_prep (group_by edges, #695/#696 shape) + golden (group_by
  representative) on ONE ctx with `with_fair_spill_pool`. Written against the UDF
  INTERFACE (B1↔B2 swap). Gate: Rand-1.0 partition + golden content + id_prep
  edge-set parity vs the in-memory pipeline.
- **Stage D — scale-mode contract.** Determinism across `target_partitions`
  {1,3,N} on the emitted PAIR-SET/PARTITION (not f32 float; fixture with no pair
  within 1e-6 of threshold); MAX dedup; feature-gating (LLM/rerank/boost/NE/exotic
  → explicit error). D doesn't block on C.
- **Stage E — out-of-core spill bench.** Full spine at an OOM-seeking scale →
  spill-survival for the RELATIONAL stages (bound the UF-collection pair-frame
  below scipy's ~50M envelope so the UF island doesn't OOM and misread as failure).
  3-way (in-memory / DataFusion / bucket). Commit numbers to the roadmap doc.

---

## Execution order & gates

1. Tasks A1→A3 on a branch off main; push (benzsevern); PR. **CI must run the FFI
   test GREEN (not skip).** This is the hard gate.
2. If GREEN: re-invoke writing-plans for Stage B (then C, D, E) — each its own
   plan + review, executed via subagent-driven-development, gated on the prior.
3. If the FFI boundary fails on v53: STOP, escalate, recommend B1.

No defaults flip in this plan (Stage A is a feasibility spike; nothing wires into
the pipeline until Stage C, which is itself behind `mode="scale"`).

## Final review

After Stage A: the gate IS the review (CI green = FFI works). Then a code-reviewer
over the crate (FFI types, capsule name, version pins) before declaring A done and
planning B.
