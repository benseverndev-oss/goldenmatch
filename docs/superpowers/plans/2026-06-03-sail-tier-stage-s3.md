# Sail Tier — Stage S3: golden survivorship on Sail (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build golden records (survivorship) distributed on Sail (Spark Connect), parity-gated to identical per-cluster golden field values vs the one-box `merge_field` primitive — proving the distributed group-and-merge assembles the right per-cluster inputs.

**Architecture:** `goldenmatch/sail/golden.py::build_golden(assignments_df, source_df, *, value_cols, strategy)` joins the S2 `assignments` (`cluster_id`, `member_id`) to the source records, filters to multi-member clusters, `groupBy(cluster_id).agg(collect_list(field))` per field, then a scalar `pandas_udf` per field calls the **one-box `core.golden.merge_field`** over each collected value list → the survivor value. Reusing the exact one-box primitive guarantees semantic parity; Sail only distributes the grouping. Pure-relational (collect_list + scalar UDF — builds on S1's proven `pandas_udf`, NOT the riskier grouped-map `applyInPandas`).

**Tech Stack:** Python 3.12, `pysail` + `pyspark[connect]` (the `[sail]` extra), PySpark DataFrame API, `goldenmatch.core.golden.merge_field`, pytest. Runs in the existing `sail` CI lane.

---

## Scope decision (read first): S3 = golden ONLY; identity is split out

The spec's S3 bullet says "golden + identity." This plan scopes S3 to **golden survivorship**
and defers **identity** to its own later stage. Reason (writing-plans scope-check): identity is a
**stateful graph subsystem** (durable entity store, `resolve_clusters` overlap detection, evidence
edges) — even the existing Ray distributed path resolves it DRIVER-SIDE against a pooled Postgres
connection (`distributed/identity.py`, Phase 6), not as a relational op. It is a different beast
from golden's clean grouped-aggregation and does not belong in the same plan. Golden ships a real,
cleanly parity-gateable distributed win on its own.

**S3 golden scope (YAGNI):** the uniform-strategy, order-INDEPENDENT case — the default
`most_complete` strategy over multi-member clusters. Explicitly DEFERRED (consistent with the Ray
distributed golden's "custom field-rules + quality_scores always fall back to in-memory"):
order-DEPENDENT strategies (`most_recent`/`source_priority` need collected dates/sources),
custom plugin strategies, oversized-cluster exclusion, and provenance. These are follow-ons, not S3.

## Critical context for the executor
- **This box HANGS on imports; Sail isn't installed locally.** Validate with `ruff check` +
  `python -m py_compile` ONLY. The `sail` CI lane is the only verifier.
- **Branch off `origin/main`** (S1+S2 merged: `goldenmatch.sail.{session,scorers,scoring,clustering}`
  + the `sail` lane exist). Branch `feat/sail-tier-s3`.
- `ruff check packages/python/goldenmatch` exit 0 before EVERY commit (I001; `ruff check --fix`;
  ruff sorts `pysail` before `pyspark`). Never pipe through `tail`.
- GitHub auth: `GH_TOKEN=$(gh auth token --user benzsevern)`. Push may hit a cosmetic
  `.git/config` permission error — re-run `git push` and verify `git ls-remote` HEAD == local.
- **Spark Connect discipline (the S2 lesson):** join on a SHARED COLUMN NAME + rename-before-join;
  NEVER reference a column via the `df["col"]` handle across a self-similar join (AMBIGUOUS_REFERENCE).
- pyright slice does NOT cover `goldenmatch/sail/` or `tests/`.

## Grounding references
- `core/golden.py::merge_field(values, rule, ...) -> (value, confidence, source_index)` — the per-
  FIELD survivorship primitive. `rule` is a `config.schemas.GoldenFieldRule` (`GoldenFieldRule(strategy="most_complete")`).
  When all non-null values are identical it returns the value at confidence 1.0; `most_complete`
  picks the most-complete/longest value (order-independent given a unique winner).
- `core/golden.py::build_golden_records_from_frames(source_df, frames, rules, ...)` — the one-box
  whole-frame golden (the conceptual reference; S3's testable reference is `merge_field` directly,
  since both the Sail path and the reference call it → parity by construction).
- S2: `sail/clustering.py::connected_components` → `assignments` (`cluster_id`, `member_id`).
- S1: `tests/test_sail_score_parity.py` — the in-process Sail server fixture pattern to mirror.

## File Structure
- **Create** `packages/python/goldenmatch/goldenmatch/sail/golden.py` — `build_golden` + `make_merge_udf`.
- **Create** `packages/python/goldenmatch/tests/test_sail_golden_parity.py` — the golden gate.
- **Modify** `.github/workflows/ci.yml` — add the golden parity test to the `sail` lane.

---

## Task 1: `build_golden` (collect_list + merge_field UDF)

**Files:**
- Create: `goldenmatch/sail/golden.py`

- [ ] **Step 1: Write `sail/golden.py`.**

```python
"""Golden-record survivorship on Sail (Spark Connect), distributed.

Joins the S2 ``assignments`` (cluster_id, member_id) to the source records,
filters to multi-member clusters, then for each field collects the cluster's
values (``collect_list``) and merges them with the ONE-BOX
``core.golden.merge_field`` primitive via a scalar pandas UDF -- reusing the
exact survivorship logic guarantees semantic parity; Sail distributes the
group-and-merge. Pure-relational (collect_list + scalar UDF), building on S1's
proven pandas_udf mechanism (not grouped-map applyInPandas).

S3 scope: the uniform, order-INDEPENDENT case (default ``most_complete`` over
multi-member clusters). Order-dependent strategies (most_recent/source_priority),
custom plugin strategies, oversized exclusion, and provenance are deferred
(mirrors the Ray distributed golden's in-memory fallback for those)."""
from __future__ import annotations

from typing import Any


def make_merge_udf(strategy: str) -> Any:
    """A scalar pandas UDF mapping an array-of-values column (one cluster's
    collected field values) to the survivor value via ``merge_field``."""
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("string")
    def _udf(col):  # col: pandas Series where each element is a list of values
        import pandas as pd

        from goldenmatch.config.schemas import GoldenFieldRule
        from goldenmatch.core.golden import merge_field

        rule = GoldenFieldRule(strategy=strategy)
        out = []
        for vals in col:
            values = list(vals) if vals is not None else []
            merged, _conf, _src = merge_field(values, rule)
            out.append(None if merged is None else str(merged))
        return pd.Series(out)

    return _udf


def build_golden(
    assignments_df: Any,
    source_df: Any,
    *,
    value_cols: list[str],
    source_id_col: str = "__row_id__",
    strategy: str = "most_complete",
) -> Any:
    """Build one golden record per multi-member cluster, distributed.

    Args:
        assignments_df: Spark DataFrame ``(cluster_id, member_id)`` (from S2).
        source_df: Spark DataFrame with ``source_id_col`` + the ``value_cols``.
        value_cols: the fields to survivor-merge.
        source_id_col: the id column in ``source_df`` (joined to ``member_id``).
        strategy: survivorship strategy (S3: order-independent, default
            ``most_complete``).

    Returns:
        Spark DataFrame ``(cluster_id, *value_cols)`` -- one golden row per
        multi-member cluster, each field survivor-merged.
    """
    from pyspark.sql import functions as F

    # Join on a SHARED name (rename source's id col -> member_id); no df["col"]
    # cross-handle refs (the S2 AMBIGUOUS_REFERENCE lesson).
    src = source_df.withColumnRenamed(source_id_col, "member_id")
    joined = assignments_df.join(src, on="member_id", how="inner")

    # Multi-member clusters only (golden is the multi-member rollup; singletons
    # are "unique", not golden).
    multi = (
        assignments_df.groupBy("cluster_id")
        .count()
        .where(F.col("count") > 1)
        .select("cluster_id")
    )
    joined = joined.join(multi, on="cluster_id", how="inner")

    # Collect each field's values per cluster, then merge via the UDF.
    agg = joined.groupBy("cluster_id").agg(
        *[F.collect_list(c).alias(c) for c in value_cols]
    )
    merge_udf = make_merge_udf(strategy)
    for c in value_cols:
        agg = agg.withColumn(c, merge_udf(F.col(c)))
    return agg
```

- [ ] **Step 2: Static-validate + commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/sail/golden.py
git commit -m "feat(sail): golden survivorship via collect_list + merge_field UDF (S3)"
```

---

## Task 2: the S3 golden parity gate

**Files:**
- Create: `tests/test_sail_golden_parity.py`

- [ ] **Step 1: Write the gate.** Fixture: source records with a clear `most_complete` winner per
  field (one full value + nulls/shorter values, so the result is order-independent), assigned to a
  2-member and a 3-member cluster plus a singleton (excluded). Reference = `merge_field` over the
  same per-cluster member values (multi-member only). Compare `{cluster_id -> {field -> value}}`.

```python
"""S3 gate: Sail build_golden produces per-cluster golden field values
identical to the one-box merge_field over the same members (multi-member
clusters only). Both call merge_field -> parity by construction; the gate
proves the Sail join/group/collect_list plumbing assembles the right per-
cluster inputs. Skips where the sail extra is absent; runs in the `sail` lane."""
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


_VALUE_COLS = ["first_name", "email"]


def _source_rows():
    """(row_id, first_name, email). Each multi-member cluster has a clear
    most_complete winner per field (one full value, others null/shorter), so
    the survivor is order-independent."""
    return [
        # cluster A (members 0,1): first_name winner "Jonathan" (vs null),
        #   email winner "jon@x.com" (vs null).
        (0, "Jonathan", None),
        (1, None, "jon@x.com"),
        # cluster B (members 2,3,4): first_name "Margaret" (vs "Marg", None),
        #   email "marg@y.com" (vs None, None).
        (2, "Marg", None),
        (3, "Margaret", "marg@y.com"),
        (4, None, None),
        # singleton (member 5): excluded from golden.
        (5, "Solo", "solo@z.com"),
    ]


def _assignments():
    # (cluster_id, member_id). cluster_id = min member id (S2 convention).
    return [(0, 0), (0, 1), (2, 2), (2, 3), (2, 4), (5, 5)]


def _reference_golden(rows, assignments, value_cols, strategy):
    from collections import defaultdict

    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    by_id = {r[0]: dict(zip(["__row_id__", *value_cols], r)) for r in rows}
    members = defaultdict(list)
    for cid, mid in assignments:
        members[cid].append(mid)
    rule = GoldenFieldRule(strategy=strategy)
    out = {}
    for cid, mids in members.items():
        if len(mids) < 2:
            continue
        rec = {}
        for c in value_cols:
            merged, _c, _s = merge_field([by_id[m][c] for m in mids], rule)
            rec[c] = None if merged is None else str(merged)
        out[cid] = rec
    return out


def _sail_golden(out_df, value_cols):
    return {
        int(r["cluster_id"]): {c: r[c] for c in value_cols}
        for r in out_df.collect()
    }


def test_sail_golden_content_parity(spark):
    from goldenmatch.sail.golden import build_golden

    rows = _source_rows()
    assignments = _assignments()
    source_df = spark.createDataFrame(rows, ["__row_id__", *_VALUE_COLS])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])

    out = build_golden(assign_df, source_df, value_cols=_VALUE_COLS)
    got = _sail_golden(out, _VALUE_COLS)
    expected = _reference_golden(rows, assignments, _VALUE_COLS, "most_complete")
    assert got == expected
    # Singleton cluster 5 excluded.
    assert 5 not in got
```

- [ ] **Step 2: Add the gate to the `sail` CI lane.** In `.github/workflows/ci.yml`, after the
  S2 WCC step, add:

```yaml
      - name: Sail golden parity gate (blocking)
        run: |
          .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_golden_parity.py -v --timeout=300
```

- [ ] **Step 3: Static-validate** (`ruff check`, `py_compile`, `yaml.safe_load` ci.yml) **+ commit.**

```bash
git add packages/python/goldenmatch/tests/test_sail_golden_parity.py .github/workflows/ci.yml
git commit -m "test(sail): S3 golden content-parity gate vs merge_field reference"
```

---

## Task 3: push, green the `sail` lane, merge

- [ ] **Step 1: Push + open the PR.** Body: "Sail tier Stage S3 — golden survivorship on Sail (collect_list + merge_field UDF), content-parity-gated per multi-member cluster vs the one-box merge_field. Reuses the exact one-box survivorship primitive -> parity by construction. Scope: uniform most_complete; identity split to its own stage. Spec: docs/superpowers/specs/2026-06-03-sail-tier-design.md (S3, golden half)."

- [ ] **Step 2: Watch the `sail` lane** (connectivity + S1 + S2 + S3 golden gates). Poll `while gh pr checks <N> | grep -qE "\bpending\b|in_progress"; do sleep 30; done`. If the golden gate fails: likely `collect_list` array-typed `pandas_udf` input handling, or the multi-member filter join. Debug from the CI pytest output (grep the raw log). **Contingency:** if a scalar `pandas_udf` over a `collect_list` (array) column isn't supported on Sail v0.6.x, fall back to grouped-map `applyInPandas` (one StructType-schema'd row per cluster). If behind main, `gh pr update-branch <N>`. A `ci.yml` change forces the full matrix — wait before the policy allows merge.

- [ ] **Step 3: Merge** once the `sail` lane + `ci-required` are green: `gh pr merge <N> --squash --delete-branch`.

---

## Definition of done
- `build_golden` produces one golden record per multi-member cluster on Sail; per-cluster golden
  field values are identical to the one-box `merge_field` reference (singletons excluded). The
  `sail` lane's golden gate is green. PR merged.

## Out of scope (later stages — explicitly recorded)
- **Identity on Sail** — a separate stage (stateful entity store + `resolve_clusters` overlap,
  resolved driver-side; not a relational op).
- Order-DEPENDENT golden strategies (most_recent/source_priority — need collected dates/sources),
  custom plugin strategies, oversized-cluster exclusion, provenance (in-memory fallback, like Ray).
- S4: the binding 100M+ multi-node bench + the large-star/small-star WCC scale swap + Ray retirement.
