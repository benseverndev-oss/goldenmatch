# SQL-native graph + embedding UDFs (native-direct) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the #503 JSON/CPython-bridge placeholder UDFs with native-direct graph + embedding SQL primitives: a pyo3-free `graph-core` kernel shared across DuckDB (Arrow), Postgres (native arrays), and DataFusion (Arrow), plus a `goldenmatch-embed` pyo3 wheel over `goldenembed-rs`.

**Architecture:** Extract the graph kernels (`connected_components`, `dedup_pairs_max_score`) into a new pyo3-free `graph-core` crate (mirrors `score-core`); `native` keeps thin `#[pyfunction]` shims delegating to it. DuckDB/DataFusion feed Arrow columns straight into `graph-core`; Postgres feeds native PG arrays. String record IDs are dictionary-mapped (first-seen order) to i64 around the kernel; int64 IDs pass through. `goldenmatch_embed_local` runs the same `goldenembed-rs` ONNX kernel everywhere — a new pyo3 wheel for DuckDB, direct crate calls for Postgres/DataFusion.

**Tech Stack:** Rust (pyo3/abi3, pgrx 0.12.9, arrow 55, datafusion FFI, maturin), Python (DuckDB UDFs), `goldenembed`/`ort` ONNX runtime.

**Spec:** `docs/superpowers/specs/2026-06-04-sql-native-graph-embed-udfs-design.md`

---

## Environment preamble (every cargo command)

```bash
export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"
```

## Local vs CI test matrix (read before executing)

| Component | Local (Windows) | CI |
|---|---|---|
| `graph-core` unit tests | ✅ `cargo test -p goldenmatch-graph-core` | ✅ |
| `native` shim parity | ⚠️ needs goldenmatch Python installed | ✅ |
| DuckDB graph UDFs | ✅ pytest (no ONNX) | ✅ |
| DuckDB embed UDF | ❌ `ort` won't link locally — `cargo check` + CI only | ✅ |
| Postgres (all) | ❌ pgrx needs libclang/Linux — CI only | ✅ PG 15/16/17 |
| DataFusion graph | ✅ `cargo test` | ✅ |
| DataFusion embed | ❌ `ort` — `cargo check` only | ✅ |

When a step is CI-only locally, the "run" action is `cargo check`/`cargo fmt`/`cargo clippy` locally; the actual test assertion runs in CI. Note this in the commit and push to let CI verify. Never claim a CI-only test passed without CI evidence (per `feedback_verify_perf_not_just_ship`).

## File structure

**New:**
- `packages/rust/extensions/graph-core/Cargo.toml` — pyo3-free crate manifest
- `packages/rust/extensions/graph-core/src/lib.rs` — graph kernels + Arrow columnar helpers
- `packages/rust/extensions/graph-core/src/dict.rs` — str↔i64 dictionary (first-seen)
- `packages/rust/extensions/embed-py/Cargo.toml` — pyo3 wrapper crate over `goldenembed`
- `packages/rust/extensions/embed-py/src/lib.rs` — `_embed` pymodule (`load`/`embed`)
- `packages/rust/extensions/embed-py/pyproject.toml` — maturin packaging (`goldenmatch-embed`)
- `packages/rust/extensions/embed-py/python/goldenmatch_embed/__init__.py`
- `.github/workflows/publish-goldenmatch-embed.yml` — wheel publish (tag `goldenmatch-embed-v*`)
- `packages/rust/extensions/postgres/sql/goldenmatch_pg--0.6.0.sql` — new SQL surface
- `packages/rust/extensions/postgres/sql/goldenmatch_pg--0.5.0--0.6.0.sql` — upgrade script
- `packages/rust/extensions/duckdb/tests/test_graph_arrow.py` — DuckDB Arrow graph tests
- `packages/rust/extensions/duckdb/tests/test_parity_graph.py` — cross-backend parity

**Modify:**
- `native/src/cluster.rs`, `native/src/pairs.rs` — delegate to `graph-core`
- `native/Cargo.toml` — add `goldenmatch-graph-core` path dep
- `duckdb/goldenmatch_duckdb/core_kernels.py` — Arrow graph UDFs + embed via wheel
- `postgres/src/kernels.rs` — native-direct graph + embed (drop bridge)
- `postgres/Cargo.toml` — add `goldenmatch-graph-core`, `goldenembed` deps
- `postgres/goldenmatch_pg.control` — `default_version = '0.6.0'`
- `postgres/Cargo.toml` version → `0.6.0`; `duckdb/pyproject.toml` → `0.6.0`
- `datafusion-udf/src/` — graph UDFs + 2-arg `goldenmatch_embed_local`
- `bridge/src/api.rs` — remove the now-unused `connected_components`/`pair_dedup`/`embed_local` (or leave; decide in Task 13)

---

# PHASE A — Graph UDFs (graph-first)

## Task 1: Create pyo3-free `graph-core` crate with `dedup_pairs_max_score`

**Files:**
- Create: `packages/rust/extensions/graph-core/Cargo.toml`
- Create: `packages/rust/extensions/graph-core/src/lib.rs`
- Test: inline `#[cfg(test)]` in `lib.rs`

- [ ] **Step 1: Write `Cargo.toml`** (mirrors `score-core` — empty `[workspace]`, no rust-toolchain)

```toml
# Standalone workspace so this pyo3-free graph core can be a path dependency of
# the `native` (extension-module), `postgres` (pgrx), and `datafusion-udf` (FFI)
# crates without any of their workspaces claiming it. Same isolation rationale as
# the sibling `score-core` / `fingerprint-core` crates.
[workspace]

[package]
name = "goldenmatch-graph-core"
version = "0.1.0"
edition = "2021"
license = "MIT"
authors = ["Ben Severn <benzsevern@gmail.com>"]
description = "Connected-components + pair-dedup graph kernels (no pyo3) shared across the native ext, the pgrx Postgres ext, and the DataFusion FFI UDFs"

[lib]
name = "goldenmatch_graph_core"

[dependencies]
# Arrow columnar helpers for the DuckDB/DataFusion zero-copy path. No pyarrow/pyo3
# here — callers pass `arrow::array::ArrayData`; the pyo3 bridge lives in `native`.
arrow = { version = "55", default-features = false }
```

- [ ] **Step 2: Write the failing test** in `src/lib.rs`

```rust
//! Pyo3-free graph kernels. Behavior-exact extraction of the loops that lived in
//! `native/src/{cluster,pairs}.rs`; the `native` crate keeps thin `#[pyfunction]`
//! shims delegating here (one source of truth, like `score-core`).
use std::collections::BTreeMap;

/// Canonicalize each pair as `(min,max)` and keep the max score per pair.
/// Behavior-exact port of `native::pairs::dedup_pairs_max_score`.
pub fn dedup_pairs_max_score(pairs: &[(i64, i64, f64)]) -> Vec<(i64, i64, f64)> {
    let mut best: BTreeMap<(i64, i64), f64> = BTreeMap::new();
    for &(a, b, s) in pairs {
        let key = if a <= b { (a, b) } else { (b, a) };
        match best.get(&key) {
            Some(&cur) if s <= cur => {}
            _ => {
                best.insert(key, s);
            }
        }
    }
    best.into_iter().map(|((a, b), s)| (a, b, s)).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn dedup_keeps_max_and_canonicalizes() {
        let got = dedup_pairs_max_score(&[(2, 1, 0.5), (1, 2, 0.9), (3, 3, 0.1)]);
        assert_eq!(got, vec![(1, 2, 0.9), (3, 3, 0.1)]);
    }
}
```

- [ ] **Step 3: Run test, verify it passes**

Run: `cargo test -p goldenmatch-graph-core` (with preamble; run from `packages/rust/extensions/graph-core`)
Expected: `test tests::dedup_keeps_max_and_canonicalizes ... ok`

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/graph-core
git commit -m "feat(509): graph-core crate with dedup_pairs_max_score"
```

## Task 2: Add `connected_components` to `graph-core`

**Files:**
- Modify: `packages/rust/extensions/graph-core/src/lib.rs`

- [ ] **Step 1: Write the failing test** (append to `tests` mod)

```rust
#[test]
fn cc_groups_transitive_and_includes_singletons() {
    let comps = connected_components(&[(1, 2, 0.9), (2, 3, 0.8)], &[1, 2, 3, 4]);
    let mut sorted: Vec<Vec<i64>> = comps.iter().map(|c| { let mut v = c.clone(); v.sort(); v }).collect();
    sorted.sort();
    assert_eq!(sorted, vec![vec![1, 2, 3], vec![4]]);
}
```

- [ ] **Step 2: Run, verify FAIL** — `cargo test -p goldenmatch-graph-core` → "cannot find function `connected_components`"

- [ ] **Step 3: Implement** — copy the union-find verbatim from `native/src/cluster.rs:14-75` (the `find` helper + `connected_components` body), changing the signature to borrow:

```rust
use std::collections::HashMap;

fn find(parent: &mut HashMap<i64, i64>, x: i64) -> i64 { /* verbatim from cluster.rs */ }

/// Connected components over `all_ids` ∪ edge endpoints. Behavior-exact port of
/// `native::cluster::connected_components`. Component membership is independent
/// of union strategy.
pub fn connected_components(edges: &[(i64, i64, f64)], all_ids: &[i64]) -> Vec<Vec<i64>> {
    /* verbatim body, iterating &edges / &all_ids */
}
```

- [ ] **Step 4: Run, verify PASS** — `cargo test -p goldenmatch-graph-core` (both tests ok)

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/graph-core/src/lib.rs
git commit -m "feat(509): graph-core connected_components"
```

## Task 3: Add first-seen str↔i64 dictionary to `graph-core`

**Files:**
- Create: `packages/rust/extensions/graph-core/src/dict.rs`
- Modify: `packages/rust/extensions/graph-core/src/lib.rs` (`mod dict; pub use dict::*;`)

- [ ] **Step 1: Write the failing test** in `dict.rs`

```rust
//! Deterministic first-seen string→i64 dictionary, identical across the DuckDB,
//! Postgres, and DataFusion wrappers so string-id grouping + round-trip agree.

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn first_seen_order_is_deterministic() {
        let mut d = Dict::new();
        assert_eq!(d.intern("b"), 0);
        assert_eq!(d.intern("a"), 1);
        assert_eq!(d.intern("b"), 0); // stable
        assert_eq!(d.resolve(1), Some("a"));
    }
}
```

- [ ] **Step 2: Run, verify FAIL** — `cargo test -p goldenmatch-graph-core dict`

- [ ] **Step 3: Implement**

```rust
use std::collections::HashMap;

#[derive(Default)]
pub struct Dict {
    to_id: HashMap<String, i64>,
    to_str: Vec<String>,
}

impl Dict {
    pub fn new() -> Self { Self::default() }
    /// Return the i64 for `s`, assigning the next id on first sight.
    pub fn intern(&mut self, s: &str) -> i64 {
        if let Some(&id) = self.to_id.get(s) { return id; }
        let id = self.to_str.len() as i64;
        self.to_id.insert(s.to_string(), id);
        self.to_str.push(s.to_string());
        id
    }
    pub fn resolve(&self, id: i64) -> Option<&str> {
        self.to_str.get(id as usize).map(|s| s.as_str())
    }
}
```

- [ ] **Step 4: Run, verify PASS**; **Step 5: Commit** `feat(509): graph-core first-seen str↔i64 dict`

## Task 4: Add Arrow columnar entry points to `graph-core`

**Files:**
- Modify: `packages/rust/extensions/graph-core/src/lib.rs`

These take `arrow::array::ArrayData` (NOT `PyArrowType` — that stays in `native`) so DataFusion (pure arrow) and `native` (via pyarrow→ArrayData) both reuse them.

- [ ] **Step 1: Write failing test** — build `Int64Array` id_a/id_b, `Float64Array` score, call `dedup_pairs_arrow_data` + `connected_components_arrow_data`, assert results match the `&[(i64,i64,f64)]` path. (Reference type-validation from `native/src/pairs.rs:80-100`.)

- [ ] **Step 2: Run, verify FAIL**

- [ ] **Step 3: Implement** `pub fn dedup_pairs_arrow_data(id_a, id_b, score: ArrayData) -> Result<(ArrayData,ArrayData,ArrayData), String>` and `connected_components_arrow_data(...)`. Validate `DataType::Int64`/`Float64`; build `&[(i64,i64,f64)]`; call the slice kernels; rebuild output `ArrayData`. Add a Utf8 overload pair (`*_arrow_data_utf8`) that interns via `Dict` then maps back to a `StringArray`.

- [ ] **Step 4: Run, verify PASS**; **Step 5: Commit** `feat(509): graph-core arrow columnar entry points`

## Task 5: Rewire `native` shims to delegate to `graph-core`

**Files:**
- Modify: `packages/rust/extensions/native/Cargo.toml` (add dep)
- Modify: `packages/rust/extensions/native/src/pairs.rs`, `native/src/cluster.rs`

- [ ] **Step 1:** Add to `native/Cargo.toml` `[dependencies]`:
```toml
goldenmatch-graph-core = { path = "../graph-core" }
```

- [ ] **Step 2:** In `pairs.rs`, replace the body of `dedup_pairs_max_score` with a delegation: keep the `#[pyfunction]` signature, call `goldenmatch_graph_core::dedup_pairs_max_score(&pairs)`. Same for `cluster.rs::connected_components`. For the `*_arrow` pyfunctions, unwrap `PyArrowType` to `ArrayData`, call the `graph-core` `*_arrow_data` fn, rewrap. Leave `build_clusters_arrow`/`build_clusters_native` as-is (out of #509 scope) unless they call the moved helpers — if so, point them at `graph-core`.

- [ ] **Step 3: Verify behavior-exact** — Run `cargo check -p goldenmatch-native` locally (preamble). Full `cargo test -p goldenmatch-native` is CI (needs goldenmatch Python). Expected local: compiles clean.

- [ ] **Step 4: Commit** `refactor(509): native graph kernels delegate to graph-core`. Note in body: native parity tests verified in CI.

## Task 6: DuckDB graph UDFs → Arrow columnar (replace JSON)

**Files:**
- Modify: `packages/rust/extensions/duckdb/goldenmatch_duckdb/core_kernels.py`
- Test: `packages/rust/extensions/duckdb/tests/test_graph_arrow.py`

DuckDB Python UDFs registered with `type='arrow'` receive pyarrow arrays. The kernel lives in `goldenmatch.native` (Python) — but for the Arrow path the UDF calls the native Arrow entry (`goldenmatch.native.dedup_pairs_arrow` / `connected_components_arrow`) with pyarrow arrays, falling back to the pure-Python kernel when the ext isn't built.

- [ ] **Step 1: Write the failing test** `test_graph_arrow.py`

```python
import duckdb
import pytest
from goldenmatch_duckdb.functions import register_functions


@pytest.fixture
def con():
    c = duckdb.connect()
    register_functions(c)
    return c


def test_pair_dedup_int64_arrow(con):
    con.execute("CREATE TABLE p(a BIGINT, b BIGINT, s DOUBLE)")
    con.execute("INSERT INTO p VALUES (2,1,0.5),(1,2,0.9)")
    rows = con.execute(
        "SELECT * FROM goldenmatch_pair_dedup((SELECT list(a) FROM p),"
        " (SELECT list(b) FROM p), (SELECT list(s) FROM p))"
    ).fetchall()
    # canonical (1,2) kept at max score 0.9
    assert (1, 2, 0.9) in [tuple(r) for r in rows]


def test_connected_components_string_ids(con):
    # string ids dictionary-mapped, components returned with original ids
    res = con.execute(
        "SELECT goldenmatch_connected_components(['x','y'], ['y','z'], [0.9,0.8], ['x','y','z','w'])"
    ).fetchone()[0]
    comps = [sorted(c) for c in res]
    assert sorted(comps) == [["w"], ["x", "y", "z"]]
```

- [ ] **Step 2: Run, verify FAIL** — `cd packages/rust/extensions/duckdb && python -m pytest tests/test_graph_arrow.py -v` (set `POLARS_SKIP_CPU_CHECK=1`). Expected: fails (signatures are still JSON `VARCHAR`).

- [ ] **Step 3: Rewrite `core_kernels.py` graph UDFs.** Replace `_connected_components(pairs_json)` / `_pair_dedup(pairs_json)` and their `con.create_function` registrations. Register the new signatures with list/arrow types; accept int64 lists (fast path) and Utf8 lists (build a Python dict, call kernel, map back). Decide the exact final SQL shape (table-returning vs list-return) to match the test above; keep `connected_components` returning `list<list<id>>` and `pair_dedup` table-returning `(a,b,s)`. Delegate to `goldenmatch.native.*_arrow` when available, else the pure-Python `connected_components`/`dedup_pairs_max_score`.

- [ ] **Step 4: Run, verify PASS** — same pytest command, both tests pass.

- [ ] **Step 5: Commit** `feat(509): DuckDB graph UDFs native-direct over Arrow columns`

## Task 7: Postgres graph UDFs → native-direct (replace bridge)

**Files:**
- Modify: `packages/rust/extensions/postgres/Cargo.toml` (add `goldenmatch-graph-core`)
- Modify: `packages/rust/extensions/postgres/src/kernels.rs`

- [ ] **Step 1:** Add dep to `postgres/Cargo.toml`:
```toml
goldenmatch-graph-core = { path = "../graph-core" }
```

- [ ] **Step 2: Write the failing pg_test** in `kernels.rs` `mod tests`:

```rust
#[pg_test]
fn pair_dedup_int_arrays_native() {
    // canonical (1,2) max score 0.9, no CPython
    let got = crate::kernels::goldenmatch_pair_dedup(vec![2, 1], vec![1, 2], vec![0.5, 0.9]);
    // returns Vec<(i64,i64,f64)> composite; assert via SPI or direct call
    assert_eq!(got, vec![(1, 2, 0.9)]);
}
```

- [ ] **Step 3: Rewrite the three graph `#[pg_extern]`s** to native-direct. New signatures take PG arrays and call `graph-core`:

```rust
use pgrx::prelude::*;
use goldenmatch_graph_core as gc;

/// Native-direct (no CPython). Canonical max-score pairs over int64 id arrays.
#[pg_extern]
fn goldenmatch_pair_dedup(
    id_a: Vec<i64>, id_b: Vec<i64>, score: Vec<f64>,
) -> TableIterator<'static, (name!(a, i64), name!(b, i64), name!(s, f64))> {
    let pairs: Vec<(i64,i64,f64)> = id_a.into_iter().zip(id_b).zip(score)
        .map(|((a,b),s)| (a,b,s)).collect();
    TableIterator::new(gc::dedup_pairs_max_score(&pairs).into_iter())
}
```

Add a `text[]` overload (`goldenmatch_pair_dedup_text`) that interns via `gc::Dict`. Same pattern for `goldenmatch_connected_components` (returns `TableIterator<(name!(component, i64), name!(member, i64))>` or `Vec<Vec<i64>>` → SETOF). Drop the old `String`-JSON bodies + `goldenmatch_bridge::api` calls for these three.

- [ ] **Step 4: Verify** — `cargo check -p goldenmatch_pg --no-default-features --features pg16` locally (preamble; may fail on libclang — if so, `cargo fmt --check` + push to CI). Real `pg_test` runs in CI.

- [ ] **Step 5: Commit** `feat(509): Postgres graph UDFs native-direct (drop CPython bridge)`. Body: pg_test verified in CI.

## Task 8: DataFusion graph UDFs (Arrow) over `graph-core`

**Files:**
- Create: `packages/rust/extensions/datafusion-udf/src/graph_udf.rs`
- Modify: `packages/rust/extensions/datafusion-udf/src/lib.rs`, `datafusion-udf/Cargo.toml`

- [ ] **Step 1:** Add `goldenmatch-graph-core` path dep to `datafusion-udf/Cargo.toml`.
- [ ] **Step 2: Write failing test** — a `#[tokio::test]` (or unit) registering the UDFs on a `SessionContext`, running `SELECT goldenmatch_pair_dedup(...)` over an Arrow batch, asserting output. Follow the existing `embed_udf.rs`/`scalar_udf.rs` registration + test pattern.
- [ ] **Step 3: Run, verify FAIL** — `cargo test -p <datafusion-udf-crate>`.
- [ ] **Step 4: Implement** graph UDFs calling `graph-core::*_arrow_data`. Wire registration in `lib.rs`.
- [ ] **Step 5: Run, verify PASS**; **Step 6: Commit** `feat(509): DataFusion graph UDFs over graph-core`

## Task 9: Cross-backend graph parity test

**Files:**
- Test: `packages/rust/extensions/duckdb/tests/test_parity_graph.py`

- [ ] **Step 1: Write the test** — same edge set fed to (a) the DuckDB UDF, (b) the pure-Python `goldenmatch.native` kernel, assert identical grouping. (PG/DataFusion parity asserted in their own CI lanes against the same pinned vectors; document the shared fixture.)
- [ ] **Step 2: Run, verify PASS** locally — `python -m pytest tests/test_parity_graph.py -v`.
- [ ] **Step 3: Commit** `test(509): cross-backend graph parity`

---

# PHASE B — Embedding UDF

## Task 10: `goldenmatch-embed` pyo3 wheel over `goldenembed`

**Files:**
- Create: `packages/rust/extensions/embed-py/{Cargo.toml,pyproject.toml,src/lib.rs}`
- Create: `packages/rust/extensions/embed-py/python/goldenmatch_embed/__init__.py`

Mirrors the `goldenmatch-native` maturin/abi3 layout. Keeps `goldenembed` pyo3-free; this wheel is the only place `ort` meets Python.

- [ ] **Step 1: Cargo.toml** — `[lib] name="_embed" crate-type=["cdylib"]`; deps `pyo3 = {features=["extension-module","abi3-py311"]}`, `goldenembed = { path = "../goldenembed" }`.
- [ ] **Step 2: src/lib.rs** — `#[pymodule] _embed` exposing `PyGoldenEmbed { load(dir) -> Self; embed(texts: Vec<String>) -> Vec<Vec<f32>> }` over `goldenembed::GoldenEmbed`. Hold the model behind a `Mutex` (embed is `&mut self`, like `embed_udf.rs`).
- [ ] **Step 3: pyproject.toml** — maturin backend, `[project] name="goldenmatch-embed" version="0.1.0"`, module path `python/goldenmatch_embed`. Mirror `native/pyproject.toml` (keep Cargo + pyproject versions in lockstep per CLAUDE.md gotcha).
- [ ] **Step 4: `__init__.py`** — loader discovering `goldenmatch_embed._embed`.
- [ ] **Step 5: Verify** — `cargo check -p goldenmatch-embed` (preamble). `ort` won't link locally on Windows → expect this is CI-only; if check fails on link, confirm it's the known `ort` LNK and proceed (push to CI). `cargo fmt --check`.
- [ ] **Step 6: Commit** `feat(509): goldenmatch-embed pyo3 wheel over goldenembed-rs`

## Task 11: Publish workflow for `goldenmatch-embed`

**Files:**
- Create: `.github/workflows/publish-goldenmatch-embed.yml`

- [ ] **Step 1:** Copy `publish-goldenmatch-native.yml`, retarget tag `goldenmatch-embed-v*`, crate path `packages/rust/extensions/embed-py`, build both macOS arches on `macos-14` (per CLAUDE.md), `workflow_dispatch` `publish` toggle for dry-run. Ensure the ONNX runtime is available to the build (reuse goldenembed CI's `ort` setup).
- [ ] **Step 2: Verify** — `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/publish-goldenmatch-embed.yml'))"`. (Workflow itself only fires on tag — verify via `workflow_dispatch` dry-run after merge, per CLAUDE.md trigger-ordering note.)
- [ ] **Step 3: Commit** `ci(509): publish workflow for goldenmatch-embed wheel`

## Task 12: DuckDB + Postgres + DataFusion embed UDFs → goldenembed-rs

**Files:**
- Modify: `duckdb/goldenmatch_duckdb/core_kernels.py` (`_embed_local`)
- Modify: `postgres/src/kernels.rs` (`goldenmatch_embed_local`), `postgres/Cargo.toml` (add `goldenembed`)
- Modify: `datafusion-udf/src/embed_udf.rs` (+ 2-arg `goldenmatch_embed_local`)

- [ ] **Step 1 (DuckDB):** repoint `_embed_local(text, model_path)` to `from goldenmatch_embed import GoldenEmbed; GoldenEmbed.load(model_path).embed([text])[0]`. Fail-soft only if `goldenmatch_embed` unimportable → clear error. Add `goldenmatch-embed` to the duckdb package's optional deps/extras.
- [ ] **Step 2 (Postgres):** add `goldenembed = { path = "../goldenembed" }` to `postgres/Cargo.toml`; rewrite `goldenmatch_embed_local` to call `goldenembed::GoldenEmbed::load(&model_path)?.embed(&[&text])?` directly (no `goldenmatch_bridge`). Return `Vec<f64>`/`float8[]`.
- [ ] **Step 3 (DataFusion):** per the spec divergence note — add a 2-arg `goldenmatch_embed_local(text, model_path)` UDF alongside the existing env-var `goldenmatch_embed`; register in `lib.rs`.
- [ ] **Step 4: Verify** — DuckDB embed test is CI-only (ONNX). `cargo check` the PG + DataFusion crates locally (expect `ort` link → CI). `cargo fmt --check`.
- [ ] **Step 5: Commit** `feat(509): embed UDFs backed by goldenembed-rs on all surfaces`

## Task 13: Embed parity test (vs Python in-house within tolerance)

**Files:**
- Test: `packages/rust/extensions/duckdb/tests/test_parity_embed.py` (CI-gated marker)
- Modify (decision): `bridge/src/api.rs` — remove now-dead `connected_components`/`pair_dedup`/`embed_local`

- [ ] **Step 1:** Write a CI-only parity test (skip locally via marker when `goldenmatch_embed` import fails): embed a fixed string via the wheel and via `goldenmatch.embeddings.inhouse`, assert cosine ≥ 0.999 (or L2 within tolerance). Pin the model fixture.
- [ ] **Step 2:** Remove the three dead bridge API fns + their bridge tests (graph + embed now native-direct everywhere). Confirm nothing else imports them: `grep -rn "api::connected_components\|api::pair_dedup\|api::embed_local" packages/rust/extensions`.
- [ ] **Step 3: Verify** — `cargo check -p goldenmatch-bridge` (preamble); parity test runs in CI.
- [ ] **Step 4: Commit** `test(509): embed parity vs in-house + drop dead bridge fns`

---

# PHASE C — Versioning, SQL surface, CI

## Task 14: Bump versions + Postgres SQL surface 0.6.0

**Files:**
- Modify: `postgres/Cargo.toml` → `0.6.0`, `postgres/goldenmatch_pg.control` → `default_version = '0.6.0'`
- Create: `postgres/sql/goldenmatch_pg--0.6.0.sql`, `postgres/sql/goldenmatch_pg--0.5.0--0.6.0.sql`
- Modify: `duckdb/pyproject.toml` → `0.6.0`

- [ ] **Step 1:** Bump the three versions.
- [ ] **Step 2:** Write the handwritten `--0.6.0.sql` (full surface — copy 0.5.0, replace the three graph/embed function signatures with the native-direct ones, drop the JSON variants). Write `--0.5.0--0.6.0.sql` upgrade: `DROP FUNCTION` old signatures + `CREATE FUNCTION` new (pgrx doesn't auto-generate; see CLAUDE.md).
- [ ] **Step 3: Verify** — SQL is handwritten; sanity-check `CREATE FUNCTION` names match `#[pg_extern]` symbols (the wrapper names). CI postgres-build is the real gate.
- [ ] **Step 4: Commit** `feat(509): goldenmatch_pg 0.6.0 SQL surface + upgrade script; bump duckdb 0.6.0`

## Task 15: Update CLAUDE.md + docs; open PR

**Files:**
- Modify: `packages/rust/extensions/CLAUDE.md` (document `graph-core`, `goldenmatch-embed`, native-direct graph/embed UDFs, the dropped JSON variants)
- Modify: root `CLAUDE.md` if a cross-cutting gotcha emerged

- [ ] **Step 1:** Update the extensions CLAUDE.md SQL-surface section + add a `graph-core`/`goldenmatch-embed` subsection.
- [ ] **Step 2:** Run the full local-testable suite: `cargo test -p goldenmatch-graph-core`; `cd duckdb && python -m pytest tests/test_graph_arrow.py tests/test_parity_graph.py -v` (POLARS_SKIP_CPU_CHECK=1).
- [ ] **Step 3:** Push branch, open PR `feat: SQL-native graph + embedding UDFs (native-direct) — closes #509`. Use the `gh auth switch --user benzsevern` dance (per CLAUDE.md + memory `feedback_github_auth_switch`), switch back after. PR body: summary bullets + the local-vs-CI test matrix + "Closes #509".
- [ ] **Step 4:** Poll CI per the CLAUDE.md poll-loop pattern; fix red lanes (especially postgres-build PG 15/16/17 and the embed ONNX lanes) until green.
- [ ] **Step 5:** Use superpowers:finishing-a-development-branch for merge.

---

## Verification checklist (before claiming done)

- [ ] `cargo test -p goldenmatch-graph-core` green locally
- [ ] DuckDB graph + parity pytest green locally
- [ ] CI: native parity, postgres-build (15/16/17), datafusion, duckdb, embed lanes all green
- [ ] Embed parity (cosine ≥ tolerance) green in CI
- [ ] No remaining `goldenmatch_bridge::api::{connected_components,pair_dedup,embed_local}` references
- [ ] `goldenmatch-embed` wheel `workflow_dispatch` dry-run succeeds post-merge
- [ ] Versions bumped: graph-core 0.1.0, embed-py 0.1.0, goldenmatch_pg 0.6.0, duckdb 0.6.0
- [ ] #509 acceptance criteria all satisfied
