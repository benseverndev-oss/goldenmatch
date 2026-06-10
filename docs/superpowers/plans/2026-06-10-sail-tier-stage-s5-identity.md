# Sail Tier — Stage S5: identity-on-Sail Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Sail-native distributed **create + edge-emit** identity stage that, on a fresh store, produces the durable identity graph (`nodes`, `source_records`, `same_as` edges) as Spark DataFrames, parity-proven against the one-box `resolve_clusters` create path.

**Architecture:** A new `goldenmatch/sail/identity.py` re-expresses Layer 1 of identity resolution (create + edges — entity-independent + content-deterministic) as relational Spark ops + scalar pandas-UDFs, mirroring S3's `build_golden`. Pure-python helpers carry the parity-critical logic (record-id derivation, deterministic entity-id) reused identically by the reference and the UDFs (parity by construction). The stateful incremental layer (absorb/merge against an existing store) is deferred, honest-null — exactly as the Ray path left it.

**Tech Stack:** Python, PySpark (Spark Connect client, pure gRPC — no JVM), pysail (in-process Sail server), pandas-UDFs, pytest. Spec: `docs/superpowers/specs/2026-06-10-sail-tier-stage-s5-identity-design.md`.

---

## File Structure

| File | Responsibility | Action |
|------|----------------|--------|
| `packages/python/goldenmatch/goldenmatch/sail/identity.py` | The S5 stage: pure helpers + Spark frame builders + `build_identity_graph` orchestrator | Create |
| `packages/python/goldenmatch/goldenmatch/sail/pipeline.py` | Add opt-in `emit_identity` to `run_sail_pipeline` | Modify (`run_sail_pipeline`, lines 10-55) |
| `packages/python/goldenmatch/tests/test_sail_identity_parity.py` | Unit tests (pure helpers) + the 3-part parity gate + determinism, against the in-process Sail server | Create |
| `.github/workflows/ci.yml` | New blocking gate step in the `sail` lane (path filter already covers `tests/test_sail_*.py` + `sail/**`) | Modify (`sail` job, after the golden gate ~line 1314) |
| `docs/superpowers/specs/2026-06-03-sail-tier-design.md` | Record the S5 honest-null (deferred incremental) | Modify (append S5 note) |

**Conventions to follow (from S1–S4, non-negotiable):**
- Lazy `pyspark` imports **inside** functions (never at module top) — keeps `sail/identity.py` importable without the extra (mirrors `sail/golden.py`).
- Joins on **shared column names** only; rename before join. Never `df["col"]` cross-handle refs across self-similar joins (the S2 `AMBIGUOUS_REFERENCE` lesson).
- Test files `pytest.importorskip("pysail")` / `("pyspark")` at top; module-scoped `spark` fixture spins up `SparkConnectServer` (copy from `test_sail_golden_parity.py:15-26`).
- These tests run in the **`sail` CI lane** (`.venv/bin/python -m pytest ... --timeout=300`), not the default python lane. Local runs need `pip install -e 'packages/python/goldenmatch[sail]'`; the sail lane is the authoritative gate.

---

### Task 0: Branch setup

- [ ] **Step 1: Create the feature branch**

This plan was NOT created in a dedicated worktree. Per the project branch/merge SOP, work on a feature branch off `main` (not the release branch).

```bash
git fetch origin
git switch -c feat/sail-s5-identity origin/main
```

Expected: on a fresh `feat/sail-s5-identity` branch.

---

### Task 1: Pure helpers — record-id + deterministic entity-id

The parity-critical logic, with zero pyspark dependency. These are reused verbatim by both the parity reference and the Spark UDFs → parity by construction.

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/sail/identity.py`
- Test: `packages/python/goldenmatch/tests/test_sail_identity_parity.py`

- [ ] **Step 1: Write the failing unit tests**

In a NEW `tests/test_sail_identity_parity.py` (top of file: `import pytest`; the pure-helper tests do NOT need the server, but keep the file under `test_sail_*` so the path filter routes it to the sail lane):

```python
from goldenmatch.sail.identity import record_id_for_row, entity_id_for_members


def test_record_id_pk_path():
    # PK present -> "{source}:{pk}" (mirrors one-box _record_id_candidates).
    rid = record_id_for_row({"id": 42, "name": "x"}, "people", "id")
    assert rid == "people:42"


def test_record_id_h1_path_matches_one_box():
    # No PK -> "{source}:h1:{fingerprint[:12]}", byte-identical to the one-box
    # primary id (parity by construction: same record_fingerprint call).
    from goldenmatch.core._hashing import record_fingerprint
    from goldenmatch.identity.fingerprint_batch import _canonical_payload

    payload = {"first_name": "Jon", "email": "jon@x.com"}
    expected = f"dataframe:h1:{record_fingerprint(_canonical_payload(payload))[:12]}"
    assert record_id_for_row(payload, "dataframe", None) == expected


def test_entity_id_order_independent():
    # Shuffling members yields the SAME entity_id (sorted before hashing).
    a = entity_id_for_members(["people:1", "people:2", "people:3"])
    b = entity_id_for_members(["people:3", "people:1", "people:2"])
    assert a == b
    assert a.startswith("ent:h1:")


def test_entity_id_distinct_for_distinct_members():
    a = entity_id_for_members(["people:1", "people:2"])
    b = entity_id_for_members(["people:1", "people:3"])
    assert a != b
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_sail_identity_parity.py -k "record_id or entity_id" -v`
Expected: FAIL — `cannot import name 'record_id_for_row'`.

- [ ] **Step 3: Implement the pure helpers**

Create `sail/identity.py` (lazy pyspark imports come in later tasks; module top stays pyspark-free):

```python
"""S5: identity-on-Sail — distributed create + edge-emit (Stage S5).

Re-expresses Layer 1 of one-box ``identity.resolve.resolve_clusters`` (create +
``same_as`` edges — entity-independent + content-deterministic) as relational
Spark ops + scalar pandas-UDFs. The stateful incremental layer (absorb/merge
against an existing store) is DEFERRED, honest-null: it stays driver-side, as
the Ray path left it. Spec: docs/superpowers/specs/2026-06-10-sail-tier-stage
-s5-identity-design.md.

pyspark is imported lazily INSIDE the builder functions so this module imports
without the [sail] extra (mirrors sail/golden.py)."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

_ENT_PREFIX = "ent:h1:"
_ENT_HASH_LEN = 16  # 64 bits of hex; collision-safe for entity populations.


def record_id_for_row(
    payload: dict[str, Any], source: str, source_pk_col: str | None
) -> str:
    """Primary record_id for a row, mirroring one-box ``_record_id_candidates``
    PRIMARY path. PK -> ``{source}:{pk}``. No PK -> canonical fingerprint
    ``{source}:h1:{fingerprint[:12]}``; un-fingerprintable rows fall to the
    legacy ``{source}:hash:{12}`` (same as the one-box ``except`` branch). The
    legacy id is NOT emitted as a separate lookup candidate here — candidate
    resolution is the deferred Layer-2 (overlap) concern."""
    if source_pk_col and source_pk_col in payload and payload[source_pk_col] is not None:
        return f"{source}:{payload[source_pk_col]}"
    clean = {k: v for k, v in payload.items() if not str(k).startswith("__")}
    from goldenmatch.core._hashing import record_fingerprint
    from goldenmatch.identity.fingerprint_batch import _canonical_payload

    try:
        full_h1 = record_fingerprint(_canonical_payload(clean))
    except (TypeError, ValueError):
        blob = json.dumps(clean, sort_keys=True, default=str)
        return f"{source}:hash:{hashlib.sha256(blob.encode('utf-8')).hexdigest()[:12]}"
    return f"{source}:h1:{full_h1[:12]}"


def entity_id_for_members(record_ids: list[str]) -> str:
    """Deterministic content-derived entity_id: SHA-256 of the cluster's
    canonical (sorted) member record_ids. Order-independent, reproducible, no
    worker coordination. Sail-create-only scheme (``ent:h1:``)."""
    canonical = "\n".join(sorted(record_ids))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_ENT_PREFIX}{digest[:_ENT_HASH_LEN]}"


def _id_scheme() -> str:
    """``h1`` (deterministic content hash, default) or ``uuid7`` (per-worker
    UUIDv7, matches the one-box scheme but non-deterministic output)."""
    return os.environ.get("GOLDENMATCH_SAIL_IDENTITY_ID_SCHEME", "h1").strip().lower()
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_sail_identity_parity.py -k "record_id or entity_id" -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/goldenmatch/sail/identity.py
git add packages/python/goldenmatch/goldenmatch/sail/identity.py packages/python/goldenmatch/tests/test_sail_identity_parity.py
git commit -m "feat(sail): S5 pure helpers — record_id + deterministic entity_id"
```

---

### Task 2: Spark frame builders — record_ids + entity_ids

**Files:**
- Modify: `sail/identity.py`
- Test: `tests/test_sail_identity_parity.py`

- [ ] **Step 1: Add the module-scoped `spark` fixture + builder tests**

Add to the test file (the fixture is copied verbatim from `test_sail_golden_parity.py:15-26`; add `pytest.importorskip` guards just above it so server-dependent tests skip without the extra but the pure-helper tests above still run):

```python
pysail = pytest.importorskip("pysail")
pyspark = pytest.importorskip("pyspark")


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


def test_derive_record_ids_pk(spark):
    from goldenmatch.sail.identity import derive_record_ids

    df = spark.createDataFrame(
        [(0, "people", 10, "Jon"), (1, "people", 11, "Marg")],
        ["__row_id__", "__source__", "pk", "first_name"],
    )
    out = {r["__row_id__"]: r["record_id"]
           for r in derive_record_ids(df, source_pk_col="pk").collect()}
    assert out == {0: "people:10", 1: "people:11"}


def test_mint_entity_ids(spark):
    from goldenmatch.sail.identity import entity_id_for_members, mint_entity_ids

    # assignments with a record_id per member.
    rows = [(0, "people:10"), (0, "people:11"), (5, "people:15")]
    df = spark.createDataFrame(rows, ["cluster_id", "record_id"])
    got = {r["cluster_id"]: r["entity_id"] for r in mint_entity_ids(df).collect()}
    assert got[0] == entity_id_for_members(["people:10", "people:11"])
    assert got[5] == entity_id_for_members(["people:15"])
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_sail_identity_parity.py -k "derive_record_ids or mint_entity_ids" -v`
Expected: FAIL — `cannot import name 'derive_record_ids'`.

- [ ] **Step 3: Implement the builders**

Append to `sail/identity.py`:

```python
def derive_record_ids(
    source_df: Any,
    *,
    source_col: str = "__source__",
    source_pk_col: str | None = None,
    id_col: str = "__row_id__",
) -> Any:
    """Add a ``record_id`` column to ``source_df``. PK path is a pure column
    expression; the no-PK h1 path runs ``record_id_for_row`` in a struct
    pandas_udf over the payload columns (parity with one-box by construction)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    has_source = source_col in source_df.columns
    src_expr = F.col(source_col) if has_source else F.lit("dataframe")

    if source_pk_col is not None:
        return source_df.withColumn(
            "record_id", F.concat(src_expr, F.lit(":"), F.col(source_pk_col).cast("string"))
        )

    payload_cols = [c for c in source_df.columns if not c.startswith("__")]
    # Thread the row's REAL __source__ through to the helper (one-box uses the
    # row's source, not a constant) -- pass it as an extra struct column so the
    # no-PK h1 id matches one-box per-row. Falls back to "dataframe" per row when
    # __source__ is absent (matches one-box row.get default).
    udf_cols = payload_cols + ([source_col] if has_source else [])

    @F.pandas_udf(StringType())
    def _rid(*cols):
        import pandas as pd

        frame = pd.concat(cols, axis=1)
        frame.columns = udf_cols
        out = []
        for _, row in frame.iterrows():
            payload = {c: row[c] for c in payload_cols}
            source = str(row[source_col]) if has_source else "dataframe"
            out.append(record_id_for_row(payload, source, None))
        return pd.Series(out)

    return source_df.withColumn("record_id", _rid(*[F.col(c) for c in udf_cols]))


def mint_entity_ids(assignments_with_recid: Any) -> Any:
    """``(cluster_id, record_id)`` -> ``(cluster_id, entity_id)``: collect each
    cluster's member record_ids and hash them deterministically. ``uuid7``
    scheme mints a per-cluster UUIDv7 instead (non-deterministic; matches the
    one-box scheme)."""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    grouped = assignments_with_recid.groupBy("cluster_id").agg(
        F.collect_list("record_id").alias("__rids__")
    )

    if _id_scheme() == "uuid7":
        from goldenmatch.identity.store import new_entity_id

        @F.pandas_udf(StringType())
        def _eid(col):
            import pandas as pd

            return pd.Series([new_entity_id() for _ in col])
    else:
        @F.pandas_udf(StringType())
        def _eid(col):
            import pandas as pd

            return pd.Series([entity_id_for_members(list(v)) for v in col])

    return grouped.withColumn("entity_id", _eid(F.col("__rids__"))).select(
        "cluster_id", "entity_id"
    )
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_sail_identity_parity.py -k "derive_record_ids or mint_entity_ids" -v`
Expected: PASS (skips locally if `[sail]` absent — then verify via the sail lane after push).

- [ ] **Step 5: Lint + commit**

```bash
.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/goldenmatch/sail/identity.py
git add -A && git commit -m "feat(sail): S5 derive_record_ids + mint_entity_ids Spark builders"
```

---

### Task 3: `build_same_as_edges` — distributed edge emit

**Files:** Modify `sail/identity.py`; Test `tests/test_sail_identity_parity.py`.

- [ ] **Step 1: Write the failing test** (edge-set is entity-independent; assert the canonical pair set + within-cluster invariant):

```python
def test_same_as_edges_set(spark):
    from goldenmatch.sail.identity import build_same_as_edges

    # pairs (a,b,score) post-dedup; assignments map members to clusters;
    # recid_map maps member_id -> record_id.
    pairs = spark.createDataFrame([(0, 1, 0.97), (2, 3, 0.91)], ["a", "b", "score"])
    assignments = spark.createDataFrame(
        [(0, 0), (0, 1), (2, 2), (2, 3)], ["cluster_id", "member_id"]
    )
    recid = spark.createDataFrame(
        [(0, "p:0"), (1, "p:1"), (2, "p:2"), (3, "p:3")], ["member_id", "record_id"]
    )
    entity_ids = spark.createDataFrame(
        [(0, "ent:A"), (2, "ent:B")], ["cluster_id", "entity_id"]
    )
    run_meta = {"run_name": "r1", "dataset": None, "recorded_at": "2026-06-10T00:00:00",
                "matchkey_name": "mk"}
    edges = build_same_as_edges(pairs, assignments, recid, entity_ids, run_meta=run_meta)
    got = {(r["record_a_id"], r["record_b_id"], r["entity_id"]) for r in edges.collect()}
    assert got == {("p:0", "p:1", "ent:A"), ("p:2", "p:3", "ent:B")}
    assert all(r["kind"] == "same_as" for r in edges.collect())
```

- [ ] **Step 2: Run → FAIL** (`cannot import name 'build_same_as_edges'`).

- [ ] **Step 3: Implement**:

```python
def build_same_as_edges(
    pairs: Any,
    assignments: Any,
    recid_map: Any,
    entity_ids: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """``same_as`` evidence edges, one per scored within-cluster pair. Join each
    pair's endpoints to their cluster (via assignments) and entity, map member
    ids to record_ids. Entity-independent content; every post-dedup pair is
    within-cluster by WCC construction."""
    from pyspark.sql import functions as F

    # member_id -> cluster_id (a's cluster == b's cluster by construction).
    a_cl = assignments.select(
        F.col("member_id").alias("a"), F.col("cluster_id")
    )
    ra = recid_map.select(F.col("member_id").alias("a"), F.col("record_id").alias("record_a_id"))
    rb = recid_map.select(F.col("member_id").alias("b"), F.col("record_id").alias("record_b_id"))

    e = (
        pairs.join(a_cl, on="a", how="inner")
        .join(entity_ids, on="cluster_id", how="inner")
        .join(ra, on="a", how="inner")
        .join(rb, on="b", how="inner")
    )
    return e.select(
        "entity_id", "record_a_id", "record_b_id",
        F.lit("same_as").alias("kind"),
        F.col("score"),
        F.lit(run_meta.get("matchkey_name")).alias("matchkey_name"),
        F.lit(run_meta["run_name"]).alias("run_name"),
        F.lit(run_meta.get("dataset")).alias("dataset"),
        F.lit(run_meta["recorded_at"]).alias("recorded_at"),
    )
```

- [ ] **Step 4: Run → PASS** (or verify in the sail lane).

- [ ] **Step 5: Commit** `feat(sail): S5 build_same_as_edges`.

---

### Task 4: `build_identity_nodes` + `build_source_records` (incl. singletons)

The reviewer-flagged gap: `build_golden` emits multi-member clusters only; nodes need one row per entity **including singletons**.

**Files:** Modify `sail/identity.py`; Test `tests/test_sail_identity_parity.py`.

- [ ] **Step 1: Write the failing test** (assert one node per cluster incl. the singleton, and the record→entity assignment):

```python
def test_nodes_include_singletons_and_records(spark):
    from pyspark.sql import functions as F

    from goldenmatch.sail.identity import build_identity_nodes, build_source_records

    assignments = spark.createDataFrame(
        [(0, 0), (0, 1), (5, 5)], ["cluster_id", "member_id"]
    )
    recid = spark.createDataFrame(
        [(0, "p:0"), (1, "p:1"), (5, "p:5")], ["member_id", "record_id"]
    )
    entity_ids = spark.createDataFrame(
        [(0, "ent:A"), (5, "ent:S")], ["cluster_id", "entity_id"]
    )
    # build_golden emits cluster 0 only (multi-member); cluster 5 is a singleton.
    golden = spark.createDataFrame([(0, "Jonathan")], ["cluster_id", "first_name"])
    source = spark.createDataFrame(
        [(0, "Jon"), (1, "Jonathan"), (5, "Solo")], ["__row_id__", "first_name"]
    )
    run_meta = {"run_name": "r1", "dataset": None, "recorded_at": "2026-06-10T00:00:00"}

    nodes = build_identity_nodes(entity_ids, golden, run_meta=run_meta)
    node_ids = {r["entity_id"] for r in nodes.collect()}
    assert node_ids == {"ent:A", "ent:S"}  # singleton node MUST exist

    records = build_source_records(assignments, recid, entity_ids, run_meta=run_meta)
    rec_to_ent = {r["record_id"]: r["entity_id"] for r in records.collect()}
    assert rec_to_ent == {"p:0": "ent:A", "p:1": "ent:A", "p:5": "ent:S"}
```

- [ ] **Step 2: Run → FAIL**.

- [ ] **Step 3: Implement**:

```python
def build_identity_nodes(
    entity_ids: Any,
    golden_df: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """One node per entity (incl. singletons). ``golden_record`` LEFT-joins
    ``build_golden`` (multi-member only); SINGLETON ``golden_record`` is NULL by
    design -- node *count* (one per cluster) is the gate invariant, content is
    not. (One-box populates singleton golden from the single row; S5 leaves it
    NULL, a documented gate-neutral simplification -- populating it is a deferred
    polish, not needed for the create-path graph.)"""
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    # entity -> golden JSON for multi-member clusters.
    gcols = [c for c in golden_df.columns if c != "cluster_id"]

    @F.pandas_udf(StringType())
    def _as_json(*cols):
        import pandas as pd

        frame = pd.concat(cols, axis=1)
        frame.columns = gcols
        out = []
        for _, row in frame.iterrows():
            rec = {c: (None if pd.isna(row[c]) else row[c]) for c in gcols}
            out.append(json.dumps(rec, default=str))
        return pd.Series(out)

    golden_json = (
        golden_df.join(entity_ids, on="cluster_id", how="inner")
        .withColumn("golden_record", _as_json(*[F.col(c) for c in gcols]))
        .select("entity_id", "golden_record")
    )

    # LEFT join keeps EVERY entity (singletons get NULL golden_record).
    nodes = entity_ids.select("cluster_id", "entity_id").join(
        golden_json, on="entity_id", how="left"
    )
    return nodes.select(
        "entity_id",
        F.lit("active").alias("status"),
        F.lit(None).cast("string").alias("merged_into"),
        F.col("golden_record"),
        F.lit(None).cast("double").alias("confidence"),
        F.lit(run_meta.get("dataset")).alias("dataset"),
        F.lit(run_meta["recorded_at"]).alias("created_at"),
        F.lit(run_meta["recorded_at"]).alias("updated_at"),
    )


def build_source_records(
    assignments: Any,
    recid_map: Any,
    entity_ids: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """record_id -> entity assignment (the record->entity partition)."""
    from pyspark.sql import functions as F

    return (
        assignments.join(recid_map, on="member_id", how="inner")
        .join(entity_ids, on="cluster_id", how="inner")
        .select(
            "record_id",
            "entity_id",
            F.lit(run_meta.get("dataset")).alias("dataset"),
            F.lit(run_meta["recorded_at"]).alias("first_seen_at"),
            F.lit(run_meta["recorded_at"]).alias("last_seen_at"),
        )
    )
```

> Note: drop the dead `emit_singletons=False` branch above when implementing — S5's default is `True`; keep the param for API symmetry but implement only the gated-on path. (Cleaned up in the simplify pass.)

- [ ] **Step 4: Run → PASS**.

- [ ] **Step 5: Commit** `feat(sail): S5 identity nodes (incl singletons) + source_records`.

---

### Task 5: `build_identity_graph` orchestrator + the 3-part parity gate

This is the make-or-break gate.

**Files:** Modify `sail/identity.py`; Test `tests/test_sail_identity_parity.py`.

- [ ] **Step 1: Write the failing parity test**

Reference = one-box `resolve_clusters` on a **fresh SQLite store**, on a chain + junction-multimerge + singleton fixture. Feed one-box the **same post-dedup pairs** S5 builds edges from (fixture constraint from the spec). Compare (1) `same_as` edge set, (2) record→entity partition equivalence, (3) counts.

> Note: this fixture sets a `pk` column + `source_pk_col="pk"`, so both sides take the **PK record-id path** (`p:100` …). The end-to-end parity gate therefore exercises the PK path only; the no-PK `h1` path's parity rests on the shared-helper-by-construction argument + the Task 1 unit tests (`test_record_id_h1_path_matches_one_box`), not this gate. Deliberate — keeps the gate about graph structure, not fingerprint reproduction (already gated elsewhere).

```python
def _fixture():
    # rows: (__row_id__, __source__, pk, name). Clusters by design:
    #   chain 0-1-2 (a-b 0.96, b-c 0.95), junction-multimerge 5-6,6-7 (two pairs
    #   share member 6), singleton 9.
    rows = [
        (0, "p", 100, "Ann"), (1, "p", 101, "Anne"), (2, "p", 102, "Annie"),
        (5, "p", 105, "Bob"), (6, "p", 106, "Bobby"), (7, "p", 107, "Robert"),
        (9, "p", 109, "Zed"),
    ]
    pairs = [(0, 1, 0.96), (1, 2, 0.95), (5, 6, 0.93), (6, 7, 0.92)]
    # assignments via union-find over pairs (cluster_id = min member id).
    assignments = [(0, 0), (0, 1), (0, 2), (5, 5), (5, 6), (5, 7), (9, 9)]
    return rows, pairs, assignments


def _one_box_graph(rows, pairs, assignments):
    """Run the one-box resolver on a fresh SQLite store; return
    (edge_set, record->entity partition signature)."""
    import polars as pl

    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    df = pl.DataFrame(
        {"__row_id__": [r[0] for r in rows], "__source__": [r[1] for r in rows],
         "pk": [r[2] for r in rows], "name": [r[3] for r in rows]}
    )
    members = {}
    for cid, mid in assignments:
        members.setdefault(cid, []).append(mid)
    pair_scores = {}
    for a, b, s in pairs:
        # bucket each pair under its cluster.
        cid = next(c for c, ms in members.items() if a in ms)
        pair_scores.setdefault(cid, {})[(min(a, b), max(a, b))] = s
    clusters = {
        cid: {"members": ms, "confidence": 1.0, "bottleneck_pair": None,
              "pair_scores": pair_scores.get(cid, {})}
        for cid, ms in members.items()
    }
    store = IdentityStore(backend="sqlite", path=":memory:")
    resolve_clusters(clusters, df, [(a, b, s) for a, b, s in pairs], "mk", store,
                     "r1", source_pk_col="pk")
    edges, partition = set(), {}
    for node in store.list_identities():
        eid = node.entity_id
        for rec in store.get_records_for_entity(eid):
            partition[rec.record_id] = eid
        for edge in store.edges_for_entity(eid):
            if edge.kind == "same_as":
                edges.add((edge.record_a_id, edge.record_b_id))
    return edges, _partition_sig(partition)


def _partition_sig(rec_to_ent):
    # Entity-id-independent signature: frozenset of frozensets of record_ids.
    from collections import defaultdict
    groups = defaultdict(set)
    for rid, eid in rec_to_ent.items():
        groups[eid].add(rid)
    return frozenset(frozenset(g) for g in groups.values())


def test_identity_graph_parity(spark):
    from goldenmatch.sail.identity import build_identity_graph

    rows, pairs, assignments = _fixture()
    edges_ref, part_ref = _one_box_graph(rows, pairs, assignments)

    source = spark.createDataFrame(rows, ["__row_id__", "__source__", "pk", "name"])
    pairs_df = spark.createDataFrame(pairs, ["a", "b", "score"])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])
    golden_df = spark.createDataFrame(
        [(0, "Annie"), (5, "Robert")], ["cluster_id", "name"]  # multi-member only
    )
    run_meta = {"run_name": "r1", "dataset": None, "recorded_at": "2026-06-10T00:00:00",
                "matchkey_name": "mk"}

    g = build_identity_graph(pairs_df, assign_df, source, golden_df,
                             run_meta=run_meta, source_pk_col="pk")

    edges_got = {(r["record_a_id"], r["record_b_id"]) for r in g.edges.collect()}
    part_got = _partition_sig(
        {r["record_id"]: r["entity_id"] for r in g.records.collect()}
    )
    node_count = g.nodes.count()

    # canonicalize edge direction for comparison (one-box may store either order)
    canon = lambda S: {tuple(sorted(p)) for p in S}
    assert canon(edges_got) == canon(edges_ref)        # (1) edge-set parity
    assert part_got == part_ref                         # (2) partition equivalence
    assert node_count == len(assignments_to_clusters(assignments))  # (3) count


def assignments_to_clusters(assignments):
    return {cid for cid, _ in assignments}
```

- [ ] **Step 2: Run → FAIL** (`cannot import name 'build_identity_graph'`).

- [ ] **Step 3: Implement the orchestrator**:

```python
@dataclass
class IdentityGraphFrames:
    nodes: Any
    records: Any
    edges: Any


def build_identity_graph(
    pairs: Any,
    assignments: Any,
    source_df: Any,
    golden_df: Any,
    *,
    run_meta: dict[str, Any],
    source_col: str = "__source__",
    source_pk_col: str | None = None,
    id_col: str = "__row_id__",
) -> IdentityGraphFrames:
    """Produce the create-path identity graph as distributed Spark frames.
    Layer 1 only (create + same_as edges); incremental absorb/merge is the
    deferred Layer 2 (honest-null)."""
    from pyspark.sql import functions as F

    src_rid = derive_record_ids(
        source_df, source_col=source_col, source_pk_col=source_pk_col, id_col=id_col
    )
    # member_id -> record_id
    recid_map = src_rid.select(
        F.col(id_col).alias("member_id"), F.col("record_id")
    )
    assign_rid = assignments.join(recid_map, on="member_id", how="inner").select(
        "cluster_id", "record_id"
    )
    entity_ids = mint_entity_ids(assign_rid)

    edges = build_same_as_edges(pairs, assignments, recid_map, entity_ids, run_meta=run_meta)
    nodes = build_identity_nodes(entity_ids, golden_df, run_meta=run_meta)
    records = build_source_records(assignments, recid_map, entity_ids, run_meta=run_meta)
    return IdentityGraphFrames(nodes=nodes, records=records, edges=edges)
```

- [ ] **Step 4: Run → PASS** (sail lane). If edge direction mismatches, the `canon` lambda already normalizes; if the partition differs, debug the WCC→assignment fixture, not the resolver.

- [ ] **Step 5: Commit** `feat(sail): S5 build_identity_graph orchestrator + 3-part parity gate`.

---

### Task 6: Determinism test

- [ ] **Step 1: Write the failing test** (re-run → byte-identical frames):

```python
def test_identity_graph_deterministic(spark):
    from goldenmatch.sail.identity import build_identity_graph

    rows, pairs, assignments = _fixture()
    source = spark.createDataFrame(rows, ["__row_id__", "__source__", "pk", "name"])
    pairs_df = spark.createDataFrame(pairs, ["a", "b", "score"])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])
    golden_df = spark.createDataFrame([(0, "Annie"), (5, "Robert")], ["cluster_id", "name"])
    run_meta = {"run_name": "r1", "dataset": None, "recorded_at": "2026-06-10T00:00:00",
                "matchkey_name": "mk"}

    def run():
        g = build_identity_graph(pairs_df, assign_df, source, golden_df,
                                 run_meta=run_meta, source_pk_col="pk")
        return sorted((r["record_id"], r["entity_id"]) for r in g.records.collect())

    assert run() == run()  # deterministic entity_ids across runs
```

- [ ] **Step 2: Run → PASS** (the content-hash scheme makes this hold; if it fails, a non-deterministic id leaked in).
- [ ] **Step 3: Commit** `test(sail): S5 identity determinism gate`.

---

### Task 7: Pipeline wiring — `run_sail_pipeline(emit_identity=...)`

**Files:** Modify `sail/pipeline.py` (`run_sail_pipeline`, lines 10-55); Test `tests/test_sail_pipeline.py` (add a case).

- [ ] **Step 1: Write the failing test** in `test_sail_pipeline.py` (mirror its existing fixture; assert default path unchanged + opt-in returns identity frames):

```python
def test_run_sail_pipeline_emit_identity(spark):
    from goldenmatch.sail.identity import IdentityGraphFrames
    from goldenmatch.sail.pipeline import run_sail_pipeline

    # ... build the existing pipeline fixture df with __row_id__/block/value/golden cols,
    # plus __source__ + pk for identity ...
    out = run_sail_pipeline(
        source_df, id_col="__row_id__", block_col="blk", value_col="name",
        golden_cols=["name"], emit_identity=True, source_pk_col="pk",
        run_meta={"run_name": "r1", "dataset": None,
                  "recorded_at": "2026-06-10T00:00:00", "matchkey_name": "jaro_winkler"},
    )
    assert isinstance(out.identity, IdentityGraphFrames)
    assert out.golden is not None
```

- [ ] **Step 2: Run → FAIL**.

- [ ] **Step 3: Implement** — add `emit_identity=False`, `source_col`, `source_pk_col`, `run_meta` params **after the existing `*` (keyword-only, all defaulted)** so the existing positional `run_sail_pipeline(source_df, ...)` callers in `test_sail_pipeline.py` and the S4 bench entrypoint don't break. When set, call `build_identity_graph` after `build_golden` and return a small result object `SailPipelineResult(golden, identity)`. Default (`emit_identity=False`) returns the golden frame exactly as today (back-compat — existing `test_sail_pipeline.py` asserts unchanged).

```python
from dataclasses import dataclass

@dataclass
class SailPipelineResult:
    golden: Any
    identity: Any  # IdentityGraphFrames | None

# in run_sail_pipeline, after building `golden = build_golden(...)`:
#   if not emit_identity:
#       return golden            # unchanged back-compat path
#   from goldenmatch.sail.identity import build_identity_graph
#   identity = build_identity_graph(pairs, assignments, source_df, golden,
#       run_meta=run_meta, source_col=source_col, source_pk_col=source_pk_col, id_col=id_col)
#   return SailPipelineResult(golden=golden, identity=identity)
```

- [ ] **Step 4: Run → PASS**; also run the full `test_sail_pipeline.py` to confirm the default path is unchanged.
- [ ] **Step 5: Commit** `feat(sail): S5 run_sail_pipeline emit_identity opt-in`.

---

### Task 8: CI lane — 7th blocking gate

**Files:** Modify `.github/workflows/ci.yml` (`sail` job).

- [ ] **Step 1: Add the gate step** after the golden gate (~line 1314), before the e2e pipeline gate:

```yaml
      # GATE (blocking): identity create + edge-emit parity vs the one-box
      # resolve_clusters on a fresh store (S5) — edge-set + record->entity
      # partition equivalence + count + determinism.
      - name: Sail identity parity gate (blocking)
        run: |
          .venv/bin/python -m pytest packages/python/goldenmatch/tests/test_sail_identity_parity.py -v --timeout=300
```

- [ ] **Step 2: Confirm path filter** — `tests/test_sail_*.py` + `sail/**` already in the `sail:` filter (ci.yml lines 234-235), so the new file is covered. No filter edit needed.

- [ ] **Step 3: Commit** `ci(sail): S5 identity parity gate (7th sail gate)`.

- [ ] **Step 4: Push + watch the sail lane** (the authoritative verification — local Windows runs are best-effort):

```bash
git push -u origin feat/sail-s5-identity
# open PR, then:
gh run watch <run-id> --exit-status
```
Expected: the `sail` job green with 7 gates (connectivity, score, WCC, golden, **identity**, pipeline).

---

### Task 9: Record the honest-null + finish

**Files:** Modify `docs/superpowers/specs/2026-06-03-sail-tier-design.md`; update memory.

- [ ] **Step 1: Append an S5 note** to the sail design doc: S5 shipped distributed create + edge-emit, parity-gated; incremental absorb/merge remains deferred/driver-side (honest-null, like the Ray path). Ray NOT retired (still gates on the real 100M bench).

- [ ] **Step 2: Update the `project_sail_tier` memory** — add the S5 line (7th gate, deferred incremental, kill-switch `GOLDENMATCH_SAIL_IDENTITY_ID_SCHEME`).

- [ ] **Step 3: Open the PR** (per the branch/merge SOP; `benzsevern` auth dance for the `benseverndev-oss` remote). PR body: scope, parity gate, the honest-null on incremental.

- [ ] **Step 4: Simplify pass** — invoke `code-simplifier` on `sail/identity.py` (remove the dead `emit_singletons=False` branch noted in Task 4, tighten the UDFs) before merge.

---

## Done = all of:
- `sail/identity.py` with 6 functions + 2 pure helpers; pyspark imported lazily.
- 7th `sail` CI gate green: edge-set + partition + count + determinism parity vs one-box fresh-store create.
- `run_sail_pipeline(emit_identity=...)` opt-in; default path byte-for-byte unchanged.
- Deferred incremental recorded as an explicit honest-null in the sail design doc + memory.
- Ray untouched (retirement still gates on the real 100M bench).
