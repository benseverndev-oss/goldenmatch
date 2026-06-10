# Sail Tier — Stage S4 harness: scale-WCC + end-to-end pipeline + bench scaffold (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build everything S4 needs EXCEPT the actual cluster run: the chain-scale-robust large-star/small-star WCC (so 100M chains bind), the end-to-end `run_sail_pipeline` (load → block → score → dedup → WCC → golden), and a `workflow_dispatch` 100M bench scaffold wired to a `SAIL_REMOTE` secret. Ray is NOT retired (that waits for the binding run).

**Architecture:** `goldenmatch/sail/clustering.py` gains `connected_components_large_star` — the Kiveris alternating-star connected-components (O(log n) iterations vs label-prop's O(diameter)), pure Spark Connect DataFrame ops, parity-gated to the same reference Union-Find as S2. `goldenmatch/sail/pipeline.py::run_sail_pipeline` threads the S1-S3 pieces + the chosen WCC into one end-to-end run. `.github/workflows/bench-sail-100m.yml` is a `workflow_dispatch` scaffold that connects to `SAIL_REMOTE`, runs the pipeline on a parquet, and times it — fail-fast if `SAIL_REMOTE` is unset (no real run in this plan).

**Tech Stack:** Python 3.12, `pysail` + `pyspark[connect]`, PySpark DataFrame API, pytest. Algorithm + pipeline are CI-gated on the in-process `sail` lane; the bench workflow is scaffold-only.

---

## Critical context for the executor
- **This box HANGS on imports; Sail isn't installed locally.** Validate with `ruff check` +
  `python -m py_compile` ONLY. The `sail` CI lane is the only verifier. The **large-star algorithm
  is the highest-risk piece** — if its parity gate fails, the per-row star logic or the convergence
  read-off is wrong; debug from the CI pytest output and iterate. The gate is the safety net.
- **Branch off `origin/main`** (S1-S3 merged). Branch `feat/sail-tier-s4-harness`.
- `ruff check packages/python/goldenmatch` exit 0 before EVERY commit (I001; `ruff check --fix`).
  Never pipe through `tail`.
- GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`. Push may hit a cosmetic
  `.git/config` permission error — re-run `git push`, verify `git ls-remote` HEAD == local.
- **Spark Connect discipline:** join on a SHARED COLUMN NAME + rename-before-join; NEVER a
  `df["col"]` handle ref across a self-similar join (the S2 AMBIGUOUS_REFERENCE lesson).
- pyright slice does NOT cover `goldenmatch/sail/` or `tests/`.
- **Ray is NOT retired here.** Retirement is gated on the real binding run (out of scope).

## Grounding references
- Spec: `docs/superpowers/specs/2026-06-03-sail-tier-design.md` (S4). S2's plan recorded
  large-star/small-star as a REQUIRED S4 prerequisite.
- `sail/clustering.py::connected_components` (S2, min-label-prop) — the proven baseline + the
  `(cluster_id, member_id)` output shape the large-star version must match.
- `tests/test_sail_clustering_parity.py` (S2) — the reference Union-Find `_reference_partition` +
  `_sail_partition` helpers + `spark` fixture to reuse for the large-star gate.
- `sail/scoring.py::score_and_dedup` (S1), `sail/golden.py::build_golden` (S3) — pipeline stages.
- Algorithm: Kiveris et al., "Connected Components in MapReduce and Beyond" — alternating
  large-star / small-star, each re-points neighbors to the local min; star per component in O(log n).

## File Structure
- **Modify** `goldenmatch/sail/clustering.py` — add `connected_components_large_star`.
- **Create** `goldenmatch/sail/pipeline.py` — `run_sail_pipeline`.
- **Modify** `tests/test_sail_clustering_parity.py` — large-star parity (reuse helpers; add a long chain).
- **Create** `tests/test_sail_pipeline.py` — end-to-end pipeline test.
- **Create** `.github/workflows/bench-sail-100m.yml` + `scripts/bench_sail_100m.py` — the scaffold.
- **Modify** `.github/workflows/ci.yml` — add the pipeline test to the `sail` lane.

---

## Task 1: chain-robust O(log n) WCC (pointer-jumping) — the scale algorithm

**Files:** Modify `goldenmatch/sail/clustering.py`

**ALGORITHM NOTE (honest naming):** the spec/S2-plan said "large-star/small-star." A first
blind attempt at literal Kiveris large-star/small-star was WRONG (it read labels off a
collapsing edge set → returned singletons; caught by hand-trace in plan review). This task
ships the **chain-robust O(log n) WCC via min-label propagation with pointer-jumping
(Shiloach-Vishkin-style shortcutting)** instead — same goal (O(log n), beats label-prop's
O(diameter) on chains), but **provably correct and hand-verifiable** under the no-local-
testing constraint. Both are standard O(log n) connected-components; this one is the one we
can verify. **Hand-traced** below on the 2-node and 3-node chains (converge in 1 round).

- [ ] **Step 1: Add `connected_components_scale`** to `clustering.py`.

```python
def connected_components_scale(
    pairs_df: Any,
    ids_df: Any,
    *,
    id_col: str = "__row_id__",
    max_rounds: int = 40,
) -> Any:
    """Chain-robust O(log n) weakly-connected components via min-label
    propagation with POINTER-JUMPING (Shiloach-Vishkin shortcutting). Pure
    Spark Connect. The scale algorithm for the 100M bench (label-prop is
    O(diameter) on long chains; the pointer-jump halves the distance to the
    root each round -> O(log n)).

    Same output as ``connected_components``: ``(cluster_id, member_id)`` where
    cluster_id is the component's min member id. Isolated nodes (singletons)
    seeded from the DISTRIBUTED ``ids_df`` (the rehydration-OOM trap).

    Each round: (1) PROPAGATE -- each node adopts min(own label, min neighbor
    label); (2) SHORTCUT -- ``label[v] = label[label[v]]`` (jump to the label's
    label). Early-exit when labels stop changing. NOTE for the real run:
    cache/checkpoint ``labels`` each round (Spark Connect lineage grows) + a
    cheaper change-counter; the gate runs tiny fixtures.

    HAND TRACE (2-node, edges=[(0,1)], seed {0:0,1:1}):
      r1 propagate: node0 min(0,lbl[1]=1)=0; node1 min(1,lbl[0]=0)=0 -> {0:0,1:0}
         shortcut: lbl[0]=lbl[0]=0; lbl[1]=lbl[lbl[1]=0]=0 -> {0:0,1:0}
      r2: no change -> CONVERGED {0:0,1:0}.  ONE component. CORRECT.
    HAND TRACE (3-chain, edges=[(0,1),(1,2)], seed {0:0,1:1,2:2}):
      r1 propagate: 0->min(0,1)=0; 1->min(1,min(0,2)=0)=0; 2->min(2,1)=1 -> {0:0,1:0,2:1}
         shortcut: 0->lbl[0]=0; 1->lbl[0]=0; 2->lbl[1]=0 -> {0:0,1:0,2:0}
      r2: no change -> CONVERGED all 0.  ONE component. CORRECT.
    """
    from pyspark.sql import functions as F

    # Symmetric edges (node, nbr) so labels flow both ways.
    fwd = pairs_df.select(F.col("a").alias("node"), F.col("b").alias("nbr"))
    rev = pairs_df.select(F.col("b").alias("node"), F.col("a").alias("nbr"))
    edges = fwd.unionByName(rev)

    # Labels seeded from the DISTRIBUTED universe (singletons -> own label).
    labels = ids_df.select(
        F.col(id_col).cast("long").alias("node")
    ).withColumn("label", F.col("node"))

    # Spark Connect discipline: join on a SHARED NAME, other side renamed; no
    # df["col"] cross-handle refs (the S2 AMBIGUOUS_REFERENCE lesson).
    for _ in range(max_rounds):
        # (1) PROPAGATE: each node adopts min(own, min neighbor label).
        lab_for_nbr = labels.select(
            F.col("node").alias("nbr"), F.col("label").alias("nbr_label")
        )
        nbr_min = (
            edges.join(lab_for_nbr, on="nbr", how="inner")
            .groupBy("node")
            .agg(F.min("nbr_label").alias("nbr_min"))
        )
        propagated = labels.join(nbr_min, on="node", how="left").select(
            F.col("node"),
            F.least(
                F.col("label"), F.coalesce(F.col("nbr_min"), F.col("label"))
            ).alias("label"),
        )
        # (2) SHORTCUT (pointer-jump): label[v] = label[label[v]].
        # Join propagated(label) to a copy keyed by node (renamed to the shared
        # "label" join key); grandlabel = label of node `label[v]`.
        lab_target = propagated.select(
            F.col("node").alias("label"), F.col("label").alias("grandlabel")
        )
        jumped = propagated.join(lab_target, on="label", how="left").select(
            F.col("node"),
            F.coalesce(F.col("grandlabel"), F.col("label")).alias("label"),
        )
        # Convergence: any label changed vs the previous round?
        prev_r = labels.select(
            F.col("node"), F.col("label").alias("prev_label")
        )
        changed = (
            jumped.join(prev_r, on="node", how="inner")
            .where(F.col("label") != F.col("prev_label"))
            .limit(1)
            .count()
        )
        labels = jumped
        if changed == 0:
            break

    return labels.select(
        F.col("label").alias("cluster_id"), F.col("node").alias("member_id")
    )
```

  **Spark Connect self-join risk (the shortcut):** `propagated.join(lab_target, on="label")`
  is a self-join (both derive from `propagated`). It uses the shared-name `on="label"` form
  (auto-coalesced) with the other side's non-key column renamed to `grandlabel` (no overlap),
  which is the robust pattern. **If CI raises a self-join AMBIGUOUS_REFERENCE here,** the fix
  is to break lineage before the join (e.g. round-trip `propagated` through
  `spark.createDataFrame(propagated.collect())` is NOT acceptable at scale — instead use
  `.checkpoint()` / `.localCheckpoint()` if Sail supports it, else `.cache()` + an action).
  Note this in the debug step.

- [ ] **Step 2: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/clustering.py
git commit -m "feat(sail): chain-robust O(log n) WCC via pointer-jumping (S4 scale)"
```

---

## Task 2: scale-WCC parity gate (2-node minimal + long chain)

**Files:** Modify `tests/test_sail_clustering_parity.py` (reuse `_reference_partition`/`_sail_partition`/`spark`)

- [ ] **Step 1: Add `connected_components_scale` parity tests** — the 2-node minimal case (the
  fastest-failing case per the review), chain+pair+singleton, a 30-node chain (stresses O(log n)
  where label-prop needs ~30 rounds), and the junction.

```python
def test_sail_wcc_scale_two_node(spark):
    """Minimal case: edges=[(0,1)] -> one component {0,1}. The fastest-failing
    case for a wrong WCC (it returned two singletons in the blind attempt)."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = [0, 1]
    edges = [(0, 1)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset({0, 1})}


def test_sail_wcc_scale_partition_parity(spark):
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 1), (1, 2), (3, 4)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_long_chain(spark):
    """A 30-node chain: pointer-jumping converges in O(log 30) rounds where
    label-prop would need ~30. Must collapse to ONE component."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(30))
    edges = [(i, i + 1) for i in range(29)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset(range(30))}


def test_sail_wcc_scale_junction(spark):
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # singleton 6
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)
```

- [ ] **Step 2: Static-validate + commit.** (The `sail` lane's WCC step already runs this file.)

```bash
git add packages/python/goldenmatch/tests/test_sail_clustering_parity.py
git commit -m "test(sail): scale-WCC (pointer-jump) partition parity (2-node + 30-node chain)"
```

---

## Task 3: `run_sail_pipeline` (end-to-end) + a small gate

**Files:** Create `goldenmatch/sail/pipeline.py` + `tests/test_sail_pipeline.py`; Modify `ci.yml`

- [ ] **Step 1: Write `sail/pipeline.py`.**

```python
"""End-to-end Sail pipeline: load -> block -> score -> dedup -> WCC -> golden,
all distributed on Sail (Spark Connect). The bench entrypoint (S4). Blocking is
a single pre-existing column (S1 scope); the scorer is the rapidfuzz pandas UDF;
WCC defaults to large-star/small-star (chain-robust at scale)."""
from __future__ import annotations

from typing import Any


def run_sail_pipeline(
    source_df: Any,
    *,
    id_col: str,
    block_col: str,
    value_col: str,
    golden_cols: list[str],
    scorer_name: str = "jaro_winkler",
    threshold: float = 0.85,
    strategy: str = "most_complete",
    wcc: str = "scale",
) -> Any:
    """Run the full Sail pipeline. Returns the golden DataFrame
    ``(cluster_id, *golden_cols)`` (one per multi-member cluster). ``wcc``:
    ``"scale"`` (pointer-jumping, chain-robust O(log n)) or ``"label_prop"``."""
    from goldenmatch.sail.clustering import (
        connected_components,
        connected_components_scale,
    )
    from goldenmatch.sail.golden import build_golden
    from goldenmatch.sail.scoring import score_and_dedup

    pairs = score_and_dedup(
        source_df, block_col=block_col, value_col=value_col, id_col=id_col,
        scorer_name=scorer_name, threshold=threshold,
    )
    ids_df = source_df.select(id_col)
    wcc_fn = (
        connected_components_scale if wcc == "scale"
        else connected_components
    )
    assignments = wcc_fn(pairs, ids_df, id_col=id_col)
    return build_golden(
        assignments, source_df, value_cols=golden_cols,
        source_id_col=id_col, strategy=strategy,
    )
```

- [ ] **Step 2: Write `tests/test_sail_pipeline.py`** (reuse the server fixture).

```python
"""S4 end-to-end gate: run_sail_pipeline runs on Sail and produces golden per
multi-member cluster. Skips where the sail extra is absent; runs in the `sail`
lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


@pytest.fixture(scope="module")
def spark():
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def test_run_sail_pipeline_end_to_end(spark):
    from goldenmatch.sail.pipeline import run_sail_pipeline

    rows = [
        (0, "10001", "Smith", "Jon"),
        (1, "10001", "Smith", None),     # cluster {0,1}: first_name "Jon"
        (2, "20002", "Brown", "Ann"),
        (3, "20002", "Brown", None),     # cluster {2,3}: first_name "Ann"
        (4, "30003", "Solo", "Zed"),     # singleton (excluded)
    ]
    df = spark.createDataFrame(
        rows, ["__row_id__", "zip", "last_name", "first_name"]
    )
    golden = run_sail_pipeline(
        df, id_col="__row_id__", block_col="zip", value_col="last_name",
        golden_cols=["first_name"], threshold=0.85, wcc="scale",
    )
    got = {int(r["cluster_id"]): r["first_name"] for r in golden.collect()}
    assert got == {0: "Jon", 2: "Ann"}
```

- [ ] **Step 3: Add the pipeline test to the `sail` lane.** In `ci.yml`, after the golden gate:

```yaml
      - name: Sail end-to-end pipeline gate (blocking)
        run: |
          .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_pipeline.py -v --timeout=300
```

- [ ] **Step 4: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/pipeline.py \
        packages/python/goldenmatch/tests/test_sail_pipeline.py .github/workflows/ci.yml
git commit -m "feat(sail): run_sail_pipeline end-to-end (load->...->golden) + e2e gate"
```

---

## Task 4: the 100M bench scaffold (workflow_dispatch, no real run)

**Files:** Create `scripts/bench_sail_100m.py` + `.github/workflows/bench-sail-100m.yml`

- [ ] **Step 1: Write the bench driver** `packages/python/goldenmatch/scripts/bench_sail_100m.py`.

```python
"""S4 binding bench DRIVER (scaffold). Connects to a REAL Sail cluster via
SAIL_REMOTE, runs run_sail_pipeline over a parquet at scale, times it, writes
JSON. No in-process server -- needs a real BYO cluster (not run in this plan).
Usage: SAIL_REMOTE=sc://host:port python bench_sail_100m.py --input <parquet>."""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="parquet path/URI on the cluster")
    ap.add_argument("--id-col", default="__row_id__")
    ap.add_argument("--block-col", default="last_name_soundex")
    ap.add_argument("--value-col", default="last_name")
    ap.add_argument("--golden-cols", default="first_name,email")
    ap.add_argument("--out", default=".profile_tmp/sail_100m.json")
    args = ap.parse_args()

    remote = os.environ.get("SAIL_REMOTE")
    if not remote:
        print(
            "::error::SAIL_REMOTE unset -- this bench needs a real BYO Sail cluster.",
            file=sys.stderr,
        )
        return 2

    from goldenmatch.sail.pipeline import run_sail_pipeline
    from goldenmatch.sail.session import connect

    spark = connect(remote)
    src = spark.read.parquet(args.input)
    t0 = time.perf_counter()
    golden = run_sail_pipeline(
        src, id_col=args.id_col, block_col=args.block_col,
        value_col=args.value_col, golden_cols=args.golden_cols.split(","),
        wcc="scale",
    )
    n_golden = golden.count()  # forces the full pipeline
    wall = time.perf_counter() - t0

    payload = {
        "wall_s": wall, "golden_count": n_golden,
        "remote": remote, "input": args.input,
    }
    print(json.dumps(payload, indent=2))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Write `.github/workflows/bench-sail-100m.yml`** (workflow_dispatch only).

```yaml
# Sail tier S4 binding bench (workflow_dispatch only). Runs the full Sail
# pipeline over a parquet on a REAL BYO Sail cluster (SAIL_REMOTE secret), the
# binding "beats one-box / scales out" proof. SCAFFOLD: without SAIL_REMOTE the
# driver exits 2 (fail-fast). Mirrors the Ray phase5 BYO-cluster posture (docs
# not bootstrap). Ray is NOT retired until this binds.
# Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md (S4).
name: bench-sail-100m
on:
  workflow_dispatch:
    inputs:
      input:
        description: "parquet path/URI reachable from the Sail cluster"
        required: true
permissions:
  contents: read
jobs:
  bench:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@34e114876b0b11c390a56381ad16ebd13914f8d5  # v4
      - uses: astral-sh/setup-uv@caf0cab7a618c569241d31dcd442f54681755d39  # v3
      - run: uv sync --all-packages
      - name: Install sail extra
        run: uv pip install -e 'packages/python/goldenmatch[sail]'
      - name: Run Sail bench (real cluster via SAIL_REMOTE)
        env:
          SAIL_REMOTE: ${{ secrets.SAIL_REMOTE }}
        run: |
          .venv/bin/python packages/python/goldenmatch/scripts/bench_sail_100m.py \
            --input "${{ inputs.input }}" --out .profile_tmp/sail_100m.json
          { echo '## bench-sail-100m'; echo '```'; cat .profile_tmp/sail_100m.json; echo '```'; } >> "$GITHUB_STEP_SUMMARY"
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: sail-100m-bench
          path: .profile_tmp/sail_100m.json
          if-no-files-found: warn
```

- [ ] **Step 3: Static-validate** (`ruff` the driver, `yaml.safe_load` the workflow) **+ commit.**

```bash
git add packages/python/goldenmatch/scripts/bench_sail_100m.py .github/workflows/bench-sail-100m.yml
git commit -m "ci(sail): S4 100M bench scaffold (workflow_dispatch, BYO cluster via SAIL_REMOTE)"
```

---

## Task 5: push, green the `sail` lane, merge

- [ ] **Step 1: Push + open the PR.** Body: large-star WCC parity (incl. 30-node chain);
  run_sail_pipeline e2e; 100M bench scaffold (no real run); Ray NOT retired.
- [ ] **Step 2: Watch the `sail` lane** (6 gates now). **The large-star gate is the risk** — if it
  fails, the star per-row logic or convergence read-off is wrong; debug from CI pytest output and
  iterate. If behind main, `gh pr update-branch <N>`. A `ci.yml` change forces the full matrix.
- [ ] **Step 3: Merge** once `sail` + `ci-required` green: `gh pr merge <N> --squash --delete-branch`.

---

## Definition of done
- `connected_components_large_star` partition-parity-green incl. a 30-node chain.
- `run_sail_pipeline` runs end-to-end on Sail and produces correct golden.
- `bench-sail-100m.yml` + driver exist (workflow_dispatch, SAIL_REMOTE, fail-fast) — NO real run.
- PR merged. Ray NOT retired.

## Out of scope
- The actual 100M multi-node run (real BYO Sail cluster + `SAIL_REMOTE` + 100M parquet) → the
  binding verdict + Ray retirement.
- Identity on Sail (its own stage). Per-node RSS instrumentation (cluster-side metrics). Distributed
  soundex/other blocking transforms (the driver assumes a pre-computed block column on the parquet).
