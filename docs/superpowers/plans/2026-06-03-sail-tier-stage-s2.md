# Sail Tier — Stage S2: connected components on Sail (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compute weakly-connected components (the Union-Find holdout) distributed on Sail (Spark Connect), parity-gated to an identical cluster PARTITION (Rand 1.0) vs a reference on fixtures including a chain and a multi-merge archetype — closing the make-or-break "can WCC be done correctly + distributed on Sail" risk.

**Architecture:** `goldenmatch/sail/clustering.py::connected_components(pairs_df, ids_df, *, id_col)` runs **min-label propagation** over symmetric edges as pure Spark Connect DataFrame joins/aggregations to a fixpoint: each node starts labeled with its own id (seeded from a DISTRIBUTED `ids_df`, NOT a driver `list[int]`), then repeatedly adopts the minimum label among itself and its neighbors until no label changes. Each component converges to a stable label = its min member id. Returns an `assignments` DataFrame (`cluster_id`, `member_id`) matching `core.cluster.build_cluster_frames`'s shape. Singletons (ids with no edge) surface as their own component.

**Tech Stack:** Python 3.12, `pysail` + `pyspark[connect]` (the `[sail]` extra from S1), PySpark DataFrame API, pytest. Runs in the existing `sail` CI lane.

---

## CRITICAL: algorithm choice + the deliberate deviation from the spec (read first)

The spec (`2026-06-03-sail-tier-design.md` §Decision 1) ordered **two-phase WCC** first
(chain-robust; `mapInArrow` partition-UF + driver boundary merge) with large-star/small-star
as the fallback. **This plan deliberately leads with min-label propagation instead, for S2.**
Rationale (the receiving-code-review discipline — explain, don't blindly follow):

- **S2's gate is CORRECTNESS at small fixture scale**, not scale. The spec's reason for
  preferring two-phase over label-prop is purely about SCALE — "chains are label-prop's worst
  case" means label-prop takes O(chain-length) iterations on long chains. At S2's fixture scale
  (a 3-chain, a few components) that's 2-3 iterations. The chain concern does not bite here.
- **Min-label propagation's correctness is trivially sound** (monotone min-propagation bounded
  below by the component min → reaches the fixpoint where every node = its component's min id).
  Under the no-local-testing constraint (this box can't run anything; CI is the only verifier),
  the simplest provably-correct algorithm minimizes CI churn. Two-phase's `mapInArrow`
  partition-UF + driver merge is far more code and far easier to get subtly wrong with no local
  REPL.
- **It is genuinely Sail-native** (pure Spark Connect DataFrame joins — no `mapInArrow` UDTF, the
  very risk the spec flagged as the trigger for falling back from two-phase). So it satisfies the
  "Sail-native everything" decision.
- **It closes the EXISTENTIAL make-or-break risk** — "can connected-components (a non-relational
  op) be computed correctly + distributed on Sail at all?" — decisively. That is S2's job.

**What this DEFERS (and where):** the chain-SCALE-robust algorithm (large-star/small-star,
O(log n) iterations — the spec's pure-relational option) is a **REQUIRED prerequisite for S4's
100M chain-heavy binding bench**, because label-prop's O(diameter) iterations won't bind there.
S2 proves WCC-on-Sail works correctly; **S4's plan MUST swap in / add large-star/small-star (or
two-phase) before the binding bench** — this is recorded as an S4 entry, not silently dropped.
If a reviewer wants the scale algorithm in S2, that's a defensible alternative — but it trades
higher first-cut bug risk (under no local testing) for scale-readiness S2's gate doesn't measure.

## Critical context for the executor
- **This box HANGS on imports; Sail isn't installed locally.** Validate with `ruff check` +
  `python -m py_compile` ONLY. The `sail` CI lane is the only verifier. Every "run test" = push,
  read the `sail` lane.
- **Branch off `origin/main`** (S1 is merged: `goldenmatch.sail` + the `sail` lane exist). Branch `feat/sail-tier-s2`.
- `ruff check packages/python/goldenmatch` exit 0 before EVERY commit (I001; `ruff check --fix` if it fires — note ruff sorts `pysail` before `pyspark`). Never pipe through `tail`.
- GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`. Push may hit a cosmetic `.git/config` permission error — re-run `git push` and verify `git ls-remote` HEAD matches local.
- pyright slice does NOT cover `goldenmatch/sail/` or `tests/`.
- **The isolated-node TRAP (spec Decision 1):** seed labels from the DISTRIBUTED `ids_df`, NEVER a
  driver-side `list[int]` of every record id (the WCC-rehydration OOM). The driver must never hold
  a per-record Python list. S2 enforces this by construction (`ids_df` is a Spark DataFrame).

## Grounding references
- Spec: `docs/superpowers/specs/2026-06-03-sail-tier-design.md` (S2 = "WCC on Sail — the gate").
- `distributed/clustering.py::two_phase_wcc(pairs_ds, all_ids) -> {id, label}` — the Ray algorithm
  being conceptually replaced; output label = min-id member of each component (S2 matches that
  labeling so `cluster_id` is the component's min id).
- `core/cluster.py::build_cluster_frames` → `ClusterFrames.assignments` is a polars DataFrame with
  `cluster_id` + `member_id` columns. S2's `connected_components` returns the Spark analog.
- S1: `goldenmatch/sail/scoring.py::score_and_dedup` returns `(a, b, score)` — the edge source.
  `tests/test_sail_score_parity.py` — the server fixture + fixture-row pattern to mirror.

## File Structure
- **Create** `packages/python/goldenmatch/goldenmatch/sail/clustering.py` — `connected_components`.
- **Create** `packages/python/goldenmatch/tests/test_sail_clustering_parity.py` — the WCC gate (reuses the in-process Sail server fixture).
- **Modify** `.github/workflows/ci.yml` — add the clustering parity test to the `sail` lane.

---

## Task 1: `connected_components` (min-label propagation)

**Files:**
- Create: `goldenmatch/sail/clustering.py`

- [ ] **Step 1: Write `sail/clustering.py`.**

```python
"""Weakly-connected components on Sail (Spark Connect) -- the Union-Find
holdout, computed distributed via min-label propagation.

S2 uses min-label propagation (pure Spark DataFrame joins to a fixpoint):
each node starts labeled with its own id (seeded from a DISTRIBUTED ids
frame -- NEVER a driver list[int], the WCC-rehydration OOM trap), then
adopts the min label among itself + its neighbors until nothing changes.
Each component converges to label = its min member id. Correct + genuinely
Sail-native; the chain-SCALE-robust large-star/small-star is an S4
prerequisite (label-prop is O(diameter) iterations on long chains -- fine
at S2's correctness-gate scale, not at 100M)."""
from __future__ import annotations

from typing import Any


def connected_components(
    pairs_df: Any,
    ids_df: Any,
    *,
    id_col: str = "__row_id__",
) -> Any:
    """Distributed weakly-connected components.

    Args:
        pairs_df: Spark DataFrame of edges with ``a`` and ``b`` (int) columns
            (canonical ``a < b`` from ``score_and_dedup``; not required).
        ids_df: Spark DataFrame of the FULL node universe with ``id_col``
            (every record id, singletons included). DISTRIBUTED -- never a
            driver list. Singletons surface as their own component.
        id_col: the id column name in ``ids_df``.

    Returns:
        Spark DataFrame ``(cluster_id, member_id)`` -- one row per node;
        ``cluster_id`` is the component's min member id (a stable, label-
        independent partition). Matches ``build_cluster_frames.assignments``.
    """
    from pyspark.sql import functions as F

    # Symmetric edges so labels flow both ways: (src, dst) for both
    # orientations. Self-loops are harmless (a < b avoids them anyway).
    fwd = pairs_df.select(F.col("a").alias("src"), F.col("b").alias("dst"))
    rev = pairs_df.select(F.col("b").alias("src"), F.col("a").alias("dst"))
    edges = fwd.unionByName(rev)

    # Seed: every node labeled with itself (from the DISTRIBUTED universe).
    labels = ids_df.select(F.col(id_col).cast("long").alias("node")).withColumn(
        "label", F.col("node")
    )

    # Iterate to fixpoint. Bounded by component diameter; at S2 fixture scale
    # this is 2-3 rounds. The convergence count is a driver scalar (cheap).
    # NOTE for S4: cache/checkpoint `labels` each round + swap in large-star/
    # small-star -- label-prop's O(diameter) won't bind at 100M chains.
    #
    # Spark Connect discipline: every join is on a SHARED COLUMN NAME (auto-
    # coalesced, no duplicate-column ambiguity), and the other side is RENAMED
    # before the join so no two inputs share a non-key name. We NEVER reference
    # a column via the `df["col"]` handle across a self-similar join (the
    # AMBIGUOUS_REFERENCE / CANNOT_RESOLVE footgun across iterations).
    max_rounds = 100
    for _ in range(max_rounds):
        # Each node's neighbor-min label. Join edges.dst == labels.node by
        # renaming labels -> (dst, dst_label) and joining on the shared "dst".
        lab_for_nbr = labels.select(
            F.col("node").alias("dst"), F.col("label").alias("dst_label")
        )
        nbr_min = (
            edges.join(lab_for_nbr, on="dst", how="inner")
            .groupBy("src")
            .agg(F.min("dst_label").alias("nbr_min"))
            .select(F.col("src").alias("node"), F.col("nbr_min"))
        )
        # Update: node adopts min(own label, neighbor-min). Left join on the
        # shared "node"; nodes with no neighbor keep their label (coalesce).
        new_labels = labels.join(nbr_min, on="node", how="left").select(
            F.col("node"),
            F.least(
                F.col("label"), F.coalesce(F.col("nbr_min"), F.col("label"))
            ).alias("label"),
        )
        # Convergence: compare new vs old on the shared "node"; old renamed.
        old_for_cmp = labels.select(
            F.col("node"), F.col("label").alias("old_label")
        )
        changed = (
            new_labels.join(old_for_cmp, on="node", how="inner")
            .where(F.col("label") != F.col("old_label"))
            .limit(1)
            .count()
        )
        labels = new_labels
        if changed == 0:
            break

    return labels.select(
        F.col("label").alias("cluster_id"), F.col("node").alias("member_id")
    )
```

- [ ] **Step 2: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/clustering.py
git commit -m "feat(sail): connected_components via min-label propagation (S2 WCC)"
```

---

## Task 2: the S2 WCC parity gate

**Files:**
- Create: `tests/test_sail_clustering_parity.py`

- [ ] **Step 1: Write the gate.** Fixtures stress what WCC must get right: a 3-chain (transitive
  merge across hops — the label-prop concern's correctness analog), a separate 2-member component,
  and a singleton (isolated-node seeding). Reference = inline Python Union-Find over the same edges
  + id universe (the canonical connected-components partition). Compare partitions (sets of
  frozensets of member ids) — label-independent.

```python
"""S2 gate: Sail connected_components produces a cluster PARTITION identical
to a reference Union-Find on fixtures including a chain + a singleton. Self-
contained; skips where the sail extra is absent; runs in the `sail` lane."""
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


def _reference_partition(ids, edges):
    """Canonical connected components via plain Union-Find -> set of
    frozensets of member ids (singletons included)."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comp = {}
    for i in ids:
        comp.setdefault(find(i), set()).add(i)
    return {frozenset(v) for v in comp.values()}


def _sail_partition(out_df):
    """assignments DataFrame -> set of frozensets of member ids per cluster_id."""
    from collections import defaultdict

    by_cid = defaultdict(set)
    for r in out_df.collect():
        by_cid[r["cluster_id"]].add(int(r["member_id"]))
    return {frozenset(v) for v in by_cid.values()}


def test_sail_wcc_partition_parity(spark):
    from goldenmatch.sail.clustering import connected_components

    # ids 0..6: chain {0-1-2}, pair {3-4}, singletons {5},{6}.
    ids = list(range(7))
    edges = [(0, 1), (1, 2), (3, 4)]  # canonical a<b
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_deep_chain_converges(spark):
    """A longer chain 0-1-2-...-9 must collapse to ONE component (label-prop
    across many hops -- the correctness analog of the chain concern)."""
    from goldenmatch.sail.clustering import connected_components

    ids = list(range(10))
    edges = [(i, i + 1) for i in range(9)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    part = _sail_partition(out)
    assert part == {frozenset(range(10))}


def test_sail_wcc_junction_multimerge(spark):
    """The spec-named multi-merge archetype: branches 0,1,2 all merge at a
    junction node 3 (min-propagation arrives from multiple neighbors in one
    round), a separate pair {4,5}, and a singleton {6}. Stresses the case
    most likely to surface a subtle min-propagation bug."""
    from goldenmatch.sail.clustering import connected_components

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # canonical a<b
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)
```

- [ ] **Step 2: Add the gate to the `sail` CI lane.** In `.github/workflows/ci.yml`, after the
  S1 parity step, add:

```yaml
      - name: Sail WCC parity gate (blocking)
        run: |
          .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_clustering_parity.py -v --timeout=300
```

- [ ] **Step 3: Static-validate** (`ruff check`, `py_compile`, `yaml.safe_load` ci.yml) **+ commit.**

```bash
git add packages/python/goldenmatch/tests/test_sail_clustering_parity.py .github/workflows/ci.yml
git commit -m "test(sail): S2 WCC partition-parity gate (chain + singleton fixtures)"
```

---

## Task 3: push, green the `sail` lane, merge

- [ ] **Step 1: Push + open the PR.** Body: "Sail tier Stage S2 — connected components on Sail via min-label propagation (the Union-Find holdout, computed distributed), partition-parity-gated vs a reference UF on chain + singleton fixtures. Closes the make-or-break 'WCC-on-Sail' existential risk. Leads with label-prop (correctness gate); the chain-scale-robust algorithm is an S4 prerequisite. Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md (S2)."

- [ ] **Step 2: Watch the `sail` lane** (connectivity + S1 score/dedup + S2 WCC gates). Poll `while gh pr checks <N> | grep -qE "\bpending\b|in_progress"; do sleep 30; done`. **This is the make-or-break gate** — if the WCC parity fails, the min-label-prop logic is wrong (likely the join-aliasing or the convergence check); debug from the CI pytest output (grep the raw log) and iterate. If behind main, `gh pr update-branch <N>`. Note: a `ci.yml` change forces the full matrix — wait for it before the policy allows merge.

- [ ] **Step 3: Merge** once the `sail` lane + `ci-required` are green: `gh pr merge <N> --squash --delete-branch`.

---

## Definition of done
- `connected_components` computes WCC distributed on Sail; the cluster PARTITION is identical to a
  reference Union-Find on chain + multi-component + singleton fixtures (Rand 1.0). The `sail` lane's
  WCC gate is green. PR merged.
- The existential "WCC-on-Sail correctly + distributed" risk is CLOSED.

## Out of scope (later stages — explicitly recorded)
- **S4 prerequisite (REQUIRED before the 100M bench):** the chain-SCALE-robust algorithm
  (large-star/small-star, O(log n) iterations, or two-phase WCC) + `labels` checkpointing — min-
  label-prop's O(diameter) iterations + growing Spark Connect lineage won't bind at 100M chains.
- Oversized-cluster auto-split / cluster-quality (the spine's post-WCC `build_cluster_frames`
  step) — separate from pure connected-components; an S3/golden concern if needed on Sail.
- golden + identity on Sail (S3); the binding multi-node bench + Ray retirement (S4).
