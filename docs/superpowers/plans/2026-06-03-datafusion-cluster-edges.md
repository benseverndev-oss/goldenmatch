# DataFusion Cluster-Edge Stream (scale-substrate SP1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development.
> Steps use checkbox (`- [ ]`) syntax. **CI-validated posture:** the dev box HANGS
> on `import goldenmatch`/`import polars`/`import datafusion`. Subagents validate
> Python via `ruff check` + `python -m py_compile` ONLY — NEVER `import`, `pytest`,
> `uv`, or `pyright`. Real tests + the bench run in CI. The controller runs
> bounded `pyright` itself. Branch off `main`; benzsevern auth
> (`GH_TOKEN=$(gh auth token --user benzsevern)`), NEVER benzsevern-mjh.
> `docs/superpowers/` is gitignored — do NOT `git add` the spec/plan.

**Goal:** Replace the per-pair `dict[int,dict]` edge view (the 566s `id_prep`)
with an embedded-DataFusion cid-sorted edge stream that spills to disk, and
measure it 3-way (legacy/datafusion/polars) at 25M/100M/OOM-seeking — the first
clean test of whether the columnar substrate kills the limiter and survives where
the dict OOMs.

**Architecture:** A new `cluster_edges_datafusion(pairs, assignments, *,
memory_limit)` returns `(edges_reader, rollup_table)`: MAX-dedup → join+filter →
external-sort edge stream (PRIMARY, spills) + an assignments-driven rollup
(SECONDARY, parity only). Gated behind scale mode; `datafusion` is an optional
extra. Parity is SEMANTIC (edge sets + rollup), not bit-identical.

**Tech Stack:** Apache DataFusion (Python pkg), PyArrow, Polars, pytest (CI only).

**Spec:** `docs/superpowers/specs/2026-06-03-datafusion-scale-substrate-design.md`

---

## File structure

- Create `packages/python/goldenmatch/goldenmatch/core/cluster_edges_df.py` —
  the `cluster_edges_datafusion` function + helpers. One responsibility: the
  DataFusion edge-stream + rollup. No identity/pipeline wiring (out of scope).
- Create `packages/python/goldenmatch/tests/fixtures/cluster_edges_shapes.py` —
  a realistic-shaped fixture builder (sparse, singleton, oversized/split, dup
  pairs) returning `(pairs, assignments, expected_per_cluster)`.
- Create `packages/python/goldenmatch/tests/test_cluster_edges_df.py` — edge-set
  parity, rollup parity (incl. singleton/edgeless), dedup, determinism.
- Create `packages/python/goldenmatch/scripts/bench_df_cluster_edges.py` — 3-way
  bench + realistic input (heavy-tailed generator).
- Create `.github/workflows/bench-df-cluster-edges.yml` — `workflow_dispatch`.
- Modify `packages/python/goldenmatch/pyproject.toml` — `[project.optional-
  dependencies] datafusion = ["datafusion>=43"]` (pin a current major at impl).

## Reference semantics (the parity target — from the spec + code)

- `_bucket_pairs` (`core/cluster_pairscores.py:12-26`): keep pair iff
  `member_to_cid[a]==member_to_cid[b]`; key `(a,b)` as-given; INPUT-order
  LAST-WINS. Scale mode replaces last-wins with **MAX** (signed-off R1).
- `compute_cluster_confidence` (`core/cluster.py:1244-1261`): `size<=1 →
  confidence 1.0`; `min_edge=min(scores)`; `avg_edge=sum/len`;
  `connectivity=len(pair_scores)/(size*(size-1)/2)`;
  `confidence=0.4*min+0.3*avg+0.3*conn`; bottleneck=argmin (scale mode: lexicographic
  `(a,b)` tie-break); weak iff `avg-min>0.3`.
- The legacy view the bench compares against: `ClusterPairScores.from_frames(
  assignments, pairs).for_cluster(cid)` (`core/cluster_pairscores.py`).

---

### Task 1: Optional `datafusion` extra + zero-copy ingest smoke

**Files:**
- Modify: `packages/python/goldenmatch/pyproject.toml`
- Create: `packages/python/goldenmatch/tests/test_cluster_edges_df.py`

- [ ] **Step 1: Pin the version FIRST, then add the extra.** Check the latest
  released `datafusion` Python major on PyPI (it tracks the Rust crate but is NOT
  identical — recent Python releases are in the ~40s). Write ALL subsequent API
  calls against THAT version's docs. Add to `pyproject.toml`
  `[project.optional-dependencies]`: `datafusion = ["datafusion>=N,<N+1"]` (the
  confirmed major). Do NOT add to core deps. `datafusion` is on PyPI, so no
  `uv.sources` workspace trap (unlike `goldenmatch-native`).

- [ ] **Step 2: Wire CI to actually RUN the tests (the #1 hazard).** The tests
  are `importorskip("datafusion")`-gated, so they SILENTLY SKIP unless the lane
  installs the extra. Add to the `python (goldenmatch)` lane in
  `.github/workflows/ci.yml`: `uv pip install datafusion` (or add the extra to
  that lane's sync). AND add a guard so a silent-skip is a loud failure — one
  test (below) that imports datafusion WITHOUT importorskip and is expected to
  run; plus verify via the raw pytest summary (CLAUDE.md: per-step JSON lies on
  `continue-on-error`; grep the log for the expected `passed` count).

- [ ] **Step 3: Write the failing zero-copy smoke test** (prefer `from_arrow`,
  the stable ingest across recent releases; `register_record_batches` partition
  nesting drifts by version):

```python
import pytest
pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")

def test_datafusion_from_arrow_ingest():
    from datafusion import SessionContext
    ctx = SessionContext()
    tbl = pa.table({"x": pa.array([1, 2, 3], pa.int64())})
    df = ctx.from_arrow(tbl, name="t")          # confirm signature vs pinned ver
    out = ctx.sql("SELECT sum(x) AS s FROM t").to_arrow_table()
    assert out.column("s")[0].as_py() == 6
```

- [ ] **Step 4: Validate** `ruff check` + `py_compile` the test file.

- [ ] **Step 5: Commit.** `feat(datafusion): optional extra (version-pinned) + CI install + from_arrow smoke`

### Task 2: Realistic-shaped parity fixture

**Files:**
- Create: `packages/python/goldenmatch/tests/fixtures/cluster_edges_shapes.py`

- [ ] **Step 1: Write the fixture builder.** `build_cluster_edges_fixture() ->
  (pairs, assignments, expected)` where `pairs: list[tuple[int,int,float]]`,
  `assignments: pl.DataFrame{member_id,cluster_id}`, `expected: dict[cid ->
  {members:set, edges:dict[(a,b)->score_MAX], size:int, min_edge, avg_edge,
  connectivity, confidence, quality, bottleneck:(a,b)}]`. Construct BY HAND so
  it's verifiable, INCLUDING:
  - a fully-connected triangle (cid A: 3 members, 3 edges);
  - a **sparse** cluster (cid B: 4 members, only 3 edges → connectivity<1);
  - a **singleton** (cid C: 1 member, 0 edges, confidence 1.0);
  - an **edgeless** multi-member cluster is N/A under WCC (members imply ≥1 edge),
    so instead include a cluster whose only dup pair makes `edge_count` differ
    from naive count — i.e. a **duplicate canonical pair with a LOWER later score**
    (cid A: `(1,2,0.9)` then `(1,2,0.4)` → MAX keeps 0.9, last-wins would keep 0.4;
    pins MAX≠LAST);
  - a **reversed** pair `(7,3)` and a separate non-canonical-only pair to exercise
    as-given keys and the lexicographic bottleneck tie-break;
  - a cross-cut edge (one endpoint in another cid → dropped).
  Compute `expected` per the reference semantics above (MAX dedup; lexicographic
  bottleneck). Assert `assignments["member_id"].is_unique().all()` inside.

- [ ] **Step 2: Validate** `ruff check` + `py_compile`.

- [ ] **Step 3: Commit.** `test(datafusion): realistic cluster-edges parity fixture (sparse/singleton/dup/reversed/cross-cut)`

### Task 3: `cluster_edges_datafusion` — dedup + join + edge stream + rollup

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/cluster_edges_df.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_edges_df.py`

- [ ] **Step 1: Write the failing parity tests** (use the Task-2 fixture):

```python
def test_edge_sets_match_legacy():
    pairs, assignments, expected = build_cluster_edges_fixture()
    edges_reader, rollup = cluster_edges_datafusion(
        _pairs_to_arrow(pairs), _assign_to_arrow(assignments), memory_limit=None)
    # consume the cid-sorted stream into per-cid edge SETS
    by_cid = _collect_runs(edges_reader)        # {cid: {(a,b): score}}
    for cid, exp in expected.items():
        assert by_cid.get(cid, {}) == exp["edges"], f"cid {cid} edges"

def test_rollup_matches_legacy_incl_singleton_and_sparse():
    pairs, assignments, expected = build_cluster_edges_fixture()
    _, rollup = cluster_edges_datafusion(...)
    got = {r["cluster_id"]: r for r in rollup.to_pylist()}
    for cid, exp in expected.items():
        assert got[cid]["size"] == exp["size"]
        assert got[cid]["edge_count"] == len(exp["edges"])
        assert got[cid]["min_edge"] == pytest.approx(exp["min_edge"], abs=1e-12)
        assert got[cid]["avg_edge"] == pytest.approx(exp["avg_edge"], abs=1e-12)
        conf = _confidence(got[cid])            # size<=1 -> 1.0 else 0.4min+0.3avg+0.3conn
        assert conf == pytest.approx(exp["confidence"], abs=1e-9)
        assert (got[cid]["bottleneck_a"], got[cid]["bottleneck_b"]) == exp["bottleneck"]

def test_max_dedup_not_last_wins():
    # cid A has (1,2,0.9) then (1,2,0.4): MAX keeps 0.9
    ...
    assert by_cid[A][(1, 2)] == 0.9
```

- [ ] **Step 2: Implement `cluster_edges_datafusion`** per the spec's Steps 0-4.
  **The four API calls below are Context7-verified corrections — use them, not
  the spec's looser sketch:**
  - **Context build + spilling (memory lives on the RUNTIME ENV, not config):**
    ```python
    from datafusion import SessionContext, SessionConfig, RuntimeEnvBuilder
    cfg = SessionConfig()
    if memory_limit is not None:
        runtime = RuntimeEnvBuilder().with_memory_limit(memory_limit, 0.8).build()  # NOTE .build()
        ctx = SessionContext(config=cfg, runtime=runtime)
    else:
        ctx = SessionContext(config=cfg)
    ```
    (Confirm `RuntimeEnvBuilder` vs the older `RuntimeConfig` name on the pinned
    version — renamed ~40.x. There is NO `ctx.set_memory_limit()`.)
  - Step 0: `SELECT a, b, max(score) AS score FROM pairs GROUP BY a, b` (MAX dedup,
    as-given keys — do NOT canonicalize).
  - Step 1: `JOIN` deduped pairs to `assignments` on `a=member_id` (→ `cid_a`) and
    on `b=member_id` (→ `cid_b`); `WHERE cid_a IS NOT NULL AND cid_b IS NOT NULL
    AND cid_a = cid_b`; project `cid=cid_a, a, b, score`.
  - **Step 2 (PRIMARY) — streaming sorted edges:** `ctx.sql("... ORDER BY cid")`
    then `.execute_stream()` (NOT `to_arrow_table_reader` — that does not exist).
    Iterate the stream incrementally; if the consumer needs a `pa.RecordBatchReader`
    object, wrap via `pa.RecordBatchReader.from_batches(schema, batch_iter)`.
  - **Step 3 (SECONDARY) — rollup; bottleneck = TWO scalar `first_value`s, NOT a
    struct:**
    ```sql
    size_t: SELECT cluster_id, count(*) AS size FROM assignments GROUP BY cluster_id
    agg:    SELECT cid,
                   min(score) AS min_edge, avg(score) AS avg_edge, count(*) AS edge_count,
                   first_value(a ORDER BY score ASC, a ASC, b ASC) AS bottleneck_a,
                   first_value(b ORDER BY score ASC, a ASC, b ASC) AS bottleneck_b
            FROM edges GROUP BY cid
    rollup: size_t LEFT JOIN agg ON cluster_id = cid     -- singletons/edgeless survive
            -> coalesce(edge_count,0), coalesce(min_edge,0.0), coalesce(avg_edge,0.0)
    ```
    If the pinned build does NOT support `first_value(... ORDER BY ...)` as an
    aggregate, use the PRE-COMMITTED fallback: two-pass — compute `min_edge` per
    cid, then `SELECT cid, min(a) , min(b)` over rows where `score = min_edge`
    (lexicographic). Do NOT discover this at impl; wire the fallback behind a
    capability check.
  - Step 4: the spilling is already wired via the runtime env above (covers both
    the external sort in Step 2 and `GroupByHashExec` in Step 3).
  Add `_pairs_to_arrow`, `_assign_to_arrow`, `_collect_runs` (consumes the
  `execute_stream` into `{cid: {(a,b): score}}` by same-cid runs), `_confidence`
  helpers (shared with tests — put them in the module).

- [ ] **Step 3: Validate** `ruff check` + `py_compile`. Controller runs bounded
  `pyright` on the module.

- [ ] **Step 4: Commit.** `feat(datafusion): cluster_edges_datafusion — dedup+join+sorted edge stream+rollup`

### Task 4: Determinism gate

**Files:**
- Test: `packages/python/goldenmatch/tests/test_cluster_edges_df.py`

- [ ] **Step 1: Write the determinism test.** Run `cluster_edges_datafusion` at
  `target_partitions ∈ {1, 4, 17}` (17 > typical core count) — the impl must
  accept a `target_partitions` arg and attach it explicitly via
  `SessionContext(config=SessionConfig().with_target_partitions(n))` (not a
  post-hoc setter); assert identical `cluster_id`,
  `size`, `edge_count`, `bottleneck`, and edge SETS across all three, and
  `avg_edge` equal to `abs=1e-12`. If `avg_edge` drifts, the impl must pin the
  reduction (e.g. `avg` over a `sort`ed input, or sum-then-divide on a sorted
  partition) — note this in the test's failure message.

- [ ] **Step 2: Validate** `ruff check` + `py_compile`.

- [ ] **Step 3: Commit.** `test(datafusion): cross-target_partitions determinism (>=3, eps 1e-12)`

### Task 5: Heavy-tailed bench input + 3-way bench script

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_df_cluster_edges.py`

- [ ] **Step 1: Write the input generator.** `make_heavytailed(n_pairs, seed) ->
  (pairs_arrow, assignments_arrow)`: a heavy-tailed cluster-size distribution
  (most size 2-5, a long tail incl. a few oversized >100), partial connectivity
  (NOT fully connected), varied scores, and a small fraction of duplicate
  canonical pairs. Vectorized (numpy) so it scales to 200M pairs. (Mirror the
  vectorization style of `bench_pipeline_complete_path.py:_make_pairs_df`.)

- [ ] **Step 2: Write the 3-way bench.** `--np "25000000,100000000"`,
  `--memory-limit` (bytes, optional, for the OOM-seeking leg), `--variants
  legacy,datafusion,polars`. Per variant per scale, measure wall + peak RSS
  (RSS via the same harness as the complete-path bench) and catch the legacy
  OOM (record "legacy OOM" rather than crashing):
  - `legacy`: `ClusterPairScores.from_frames(assignments, pairs)` + per-cid iter.
    NOTE: `from_frames` is LAST-WINS on dup canonical pairs; DataFusion is MAX. To
    keep the bottleneck-divergence number clean (tie-break only, not muddied by
    MAX-vs-last-wins on dups), **pre-dedup the bench input to one row per `(a,b)`
    by MAX before feeding the legacy leg too** — OR report the two divergence
    sources separately. Pick one; state it in the bench output.
  - `datafusion`: `cluster_edges_datafusion(..., memory_limit=...)` + consume the
    stream in cid-runs.
  - `polars`: `pairs.join(assignments...).filter(...).sort("cid")` collected
    streaming + group-by rollup.
  Peak RSS via the complete-path bench's `_peak_rss_mb` (`resource.getrusage`,
  **Linux/CI-only** — returns 0 on Windows; don't add a Windows path). Also print
  the **bottleneck-divergence rate** (datafusion vs legacy) + a one-time check
  that DataFusion ingest didn't double input RSS (the spec's zero-copy risk).
  Emit a markdown table.

- [ ] **Step 3: Validate** `ruff check` + `py_compile`.

- [ ] **Step 4: Commit.** `bench(datafusion): 3-way cluster-edges bench + heavy-tailed generator`

### Task 6: Bench workflow

**Files:**
- Create: `.github/workflows/bench-df-cluster-edges.yml`

- [ ] **Step 1: Write the workflow.** `workflow_dispatch` inputs `np`
  (default `25000000,100000000`), `memory_limit` (default empty), `variants`
  (default `legacy,datafusion,polars`). `runs-on: large-new-64GB`,
  `timeout-minutes: 85`. Mirror `bench-pipeline-complete-path.yml`: checkout,
  rust-toolchain, setup-uv (cache), `uv sync --all-packages`, **`uv pip install
  datafusion`** (the extra), `uv run python scripts/build_native.py`, then run
  `bench_df_cluster_edges.py` piping to `$GITHUB_STEP_SUMMARY`. Upload the JSON
  artifact.

- [ ] **Step 2: Validate** the YAML (yamllint if available, else inspect).

- [ ] **Step 3: Commit.** `ci(datafusion): bench-df-cluster-edges workflow`

---

## Execution order & gates

1. Tasks 1→4 (lib + tests): land on a branch off main, push (benzsevern), open PR.
   CI `python (goldenmatch)` must run the new tests GREEN (parity + determinism).
   **HARD REQUIREMENT (Task 1 Step 2): the lane MUST install `datafusion` AND the
   run must prove the new tests did not silently skip** — grep the raw pytest log
   for the expected `passed` count (per CLAUDE.md, the per-step JSON reports
   `success` on `continue-on-error` regardless). A green-by-skip is a false pass
   and the whole parity gate is decorative without this.
2. Task 5→6 (bench): land, then dispatch `bench-df-cluster-edges.yml` at
   `np=25000000,100000000` AND an OOM-seeking run (`np=200000000` or a low
   `memory_limit`). Commit the 3-way table into the roadmap doc
   (`2026-06-01-arrow-native-finish-line-design.md`).
3. The verdict is DATA: it answers (a) does DataFusion kill the 566s, (b) is the
   win DataFusion-specific vs polars, (c) does it spill+survive where the dict
   OOMs (binding-vs-non-binding). No default flip; no further sub-project until
   reviewed.

## Final review

After Tasks 1-6: dispatch a final code-reviewer over the whole diff (DataFusion
API correctness, parity-semantics, the spilling/determinism claims), then finish
the branch (PR off main, benzsevern, CI green).
