"""S5: identity-on-Sail — distributed create + edge-emit (Stage S5).

Re-expresses Layer 1 of one-box ``identity.resolve.resolve_clusters`` (create +
``same_as`` edges — entity-independent + content-deterministic) as relational
Spark ops + scalar pandas-UDFs. The stateful incremental layer (absorb/merge
against an existing store) is DEFERRED, honest-null: it stays driver-side, as
the Ray path left it. Spec: docs/superpowers/specs/2026-06-10-sail-tier-stage
-s5-identity-design.md.

pyspark is imported lazily INSIDE the builder functions so this module imports
without the [sail] extra (mirrors sail/golden.py).
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any

_ENT_PREFIX = "ent:h1:"
_ENT_HASH_LEN = 16  # 64 bits of hex; collision-safe for entity populations.


# --- pure helpers (no pyspark; locally testable, parity-by-construction) ---


def record_id_for_row(
    payload: dict[str, Any], source: str, source_pk_col: str | None
) -> str:
    """Primary record_id for a row, mirroring one-box ``_record_id_candidates``
    PRIMARY path. PK -> ``{source}:{pk}``. No PK -> canonical fingerprint
    ``{source}:h1:{fingerprint[:12]}``; un-fingerprintable rows fall to the
    legacy ``{source}:hash:{12}`` (same as the one-box ``except`` branch). The
    legacy id is NOT emitted as a separate lookup candidate here — candidate
    resolution is the deferred Layer-2 (overlap) concern.
    """
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
    worker coordination. Sail-create-only scheme (``ent:h1:``).
    """
    canonical = "\n".join(sorted(record_ids))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_ENT_PREFIX}{digest[:_ENT_HASH_LEN]}"


def _id_scheme() -> str:
    """``h1`` (deterministic content hash, default) or ``uuid7`` (per-worker
    UUIDv7, matches the one-box scheme but non-deterministic output).
    """
    return os.environ.get("GOLDENMATCH_SAIL_IDENTITY_ID_SCHEME", "h1").strip().lower()


# --- Spark frame builders (lazy pyspark imports) ---


def derive_record_ids(
    source_df: Any,
    *,
    source_col: str = "__source__",
    source_pk_col: str | None = None,
    id_col: str = "__row_id__",
) -> Any:
    """Add a ``record_id`` column to ``source_df``. PK path is a pure column
    expression; the no-PK h1 path runs ``record_id_for_row`` in a struct
    pandas_udf over the payload columns (parity with one-box by construction).
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import StringType

    has_source = source_col in source_df.columns
    src_expr = F.col(source_col) if has_source else F.lit("dataframe")

    if source_pk_col is not None:
        return source_df.withColumn(
            "record_id",
            F.concat(src_expr, F.lit(":"), F.col(source_pk_col).cast("string")),
        )

    payload_cols = [c for c in source_df.columns if not c.startswith("__")]
    # Thread the row's REAL __source__ through to the helper (one-box uses the
    # row's source, not a constant) -- pass it as an extra struct column so the
    # no-PK h1 id matches one-box per-row. Falls back to "dataframe" per row
    # when __source__ is absent (matches one-box row.get default).
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
    one-box scheme).
    """
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
    within-cluster by WCC construction.
    """
    from pyspark.sql import functions as F

    # member_id -> cluster_id (a's cluster == b's cluster by construction).
    a_cl = assignments.select(
        F.col("member_id").alias("a"), F.col("cluster_id")
    )
    ra = recid_map.select(
        F.col("member_id").alias("a"), F.col("record_id").alias("record_a_id")
    )
    rb = recid_map.select(
        F.col("member_id").alias("b"), F.col("record_id").alias("record_b_id")
    )

    e = (
        pairs.join(a_cl, on="a", how="inner")
        .join(entity_ids, on="cluster_id", how="inner")
        .join(ra, on="a", how="inner")
        .join(rb, on="b", how="inner")
    )
    return e.select(
        "entity_id",
        "record_a_id",
        "record_b_id",
        F.lit("same_as").alias("kind"),
        F.col("score"),
        F.lit(run_meta.get("matchkey_name")).alias("matchkey_name"),
        F.lit(run_meta["run_name"]).alias("run_name"),
        F.lit(run_meta.get("dataset")).alias("dataset"),
        F.lit(run_meta["recorded_at"]).alias("recorded_at"),
    )


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
    polish, not needed for the create-path graph.)
    """
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
    deferred Layer 2 (honest-null).
    """
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

    edges = build_same_as_edges(
        pairs, assignments, recid_map, entity_ids, run_meta=run_meta
    )
    nodes = build_identity_nodes(entity_ids, golden_df, run_meta=run_meta)
    records = build_source_records(
        assignments, recid_map, entity_ids, run_meta=run_meta
    )
    return IdentityGraphFrames(nodes=nodes, records=records, edges=edges)
