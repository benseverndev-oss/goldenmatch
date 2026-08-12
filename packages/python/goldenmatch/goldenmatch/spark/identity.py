"""S5: identity-on-Sail — distributed create + edge-emit (Stage S5).

Re-expresses Layer 1 of one-box ``identity.resolve.resolve_clusters`` (create +
``same_as`` edges — entity-independent + content-deterministic) as relational
Spark ops + scalar arrow-UDFs. The stateful incremental layer (absorb/merge
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

# --- frozen wire schema (the public contract, #859) ----------------------------
# The column order/names of each produced frame. A downstream store persists these
# verbatim, so they are the stable contract; the contract test pins them, and a
# consumer (e.g. the golden-showcase IdentityGraph seam) maps onto them. The edge
# frame carries full provenance: endpoints (record_a_id, record_b_id), the pair
# ``score``, the ``matchkey_name`` that fired, and the ``run_name`` (run id).
NODE_COLUMNS = (
    "entity_id", "status", "merged_into", "golden_record",
    "confidence", "dataset", "created_at", "updated_at",
)
RECORD_COLUMNS = ("record_id", "entity_id", "dataset", "first_seen_at", "last_seen_at")
EDGE_COLUMNS = (
    "entity_id", "record_a_id", "record_b_id", "kind",
    "score", "matchkey_name", "run_name", "dataset", "recorded_at",
)
EVENT_COLUMNS = ("entity_id", "kind", "run_name", "dataset", "recorded_at")


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
    arrow_udf over the payload columns (parity with one-box by construction).
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

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

    # ONE struct argument rather than a variadic UDF. The pandas version took
    # *cols and rebuilt a frame with `pd.concat`; arrow_udf's variadic form is
    # not part of the documented contract, and a struct is both supported and
    # self-describing -- `to_pylist()` yields one dict per row with the field
    # names already attached, so no column-name reassembly is needed.
    @arrow_udf("string")
    def _rid(rows):
        out = []
        for row in to_pylist(rows):
            payload = {c: row[c] for c in payload_cols}
            source = str(row[source_col]) if has_source else "dataframe"
            out.append(record_id_for_row(payload, source, None))
        return from_pylist(out, "string")

    return source_df.withColumn(
        "record_id", _rid(F.struct(*[F.col(c) for c in udf_cols]))
    )


def mint_entity_ids(assignments_with_recid: Any) -> Any:
    """``(cluster_id, record_id)`` -> ``(cluster_id, entity_id)``: collect each
    cluster's member record_ids and hash them deterministically. ``uuid7``
    scheme mints a per-cluster UUIDv7 instead (non-deterministic; matches the
    one-box scheme).
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

    grouped = assignments_with_recid.groupBy("cluster_id").agg(
        F.collect_list("record_id").alias("__rids__")
    )

    if _id_scheme() == "uuid7":
        from goldenmatch.identity.store import new_entity_id

        @arrow_udf("string")
        def _eid(col):
            return from_pylist([new_entity_id() for _ in to_pylist(col)], "string")
    else:

        @arrow_udf("string")
        def _eid(col):
            return from_pylist(
                [entity_id_for_members(list(v)) for v in to_pylist(col)], "string"
            )

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

    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

    # entity -> golden JSON for multi-member clusters.
    gcols = [c for c in golden_df.columns if c != "cluster_id"]

    # One struct argument, as in `_rid`. `pd.isna` is gone with it: a pa.Array
    # carries a validity bitmap, so `to_pylist()` yields None for a null and
    # there is no NaN to test for.
    @arrow_udf("string")
    def _as_json(rows):
        out = []
        for row in to_pylist(rows):
            rec = {c: row[c] for c in gcols}
            out.append(json.dumps(rec, default=str))
        return from_pylist(out, "string")

    golden_json = (
        golden_df.join(entity_ids, on="cluster_id", how="inner")
        .withColumn("golden_record", _as_json(F.struct(*[F.col(c) for c in gcols])))
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


def build_identity_events(
    entity_ids: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """One ``CREATED`` event per entity (store-parity bookkeeping).

    The optional ``events`` frame mirrors the one-box append-only event log's
    create rows, so a downstream store can replay them. Columns: ``EVENT_COLUMNS``.
    """
    from pyspark.sql import functions as F

    return entity_ids.select(
        "entity_id",
        F.lit("CREATED").alias("kind"),
        F.lit(run_meta["run_name"]).alias("run_name"),
        F.lit(run_meta.get("dataset")).alias("dataset"),
        F.lit(run_meta["recorded_at"]).alias("recorded_at"),
    )


def resolve_cluster_entities(
    assign_rid: Any,
    existing_records: Any,
    existing_nodes: Any,
) -> tuple[Any, Any]:
    """Layer 2 core (#966): decide create / absorb / merge per cluster.

    Returns ``(resolution, losers)``:

    * ``resolution`` -- ``(cluster_id, entity_id, action)`` where ``action`` is
      ``create`` / ``absorb`` / ``merge``. Every cluster in ``assign_rid``
      appears exactly once.
    * ``losers`` -- ``(cluster_id, loser_entity_id, entity_id)`` for merges;
      ``entity_id`` is the winner the loser is retired into. Empty otherwise.

    Winner selection mirrors ``resolve.py:1091`` EXACTLY, including the part
    that is easy to get backwards: the rank key counts how many of THIS
    CLUSTER'S records point at each entity, not how big the entity is overall.
    Tie-break is oldest ``created_at``, then ``entity_id`` ascending -- the last
    of those is ours (see the design spec): one-box breaks that tie on dict
    order and Spark has no stable sort, so leaving it implicit would make the
    surviving entity non-deterministic.
    """
    from pyspark.sql import Window
    from pyspark.sql import functions as F

    all_clusters = assign_rid.select("cluster_id").distinct()

    overlap = assign_rid.join(
        existing_records.select("record_id", "entity_id"),
        on="record_id",
        how="inner",
    )
    # (cluster, entity) -> how many of THIS cluster's records that entity holds.
    counts = overlap.groupBy("cluster_id", "entity_id").agg(
        F.count(F.lit(1)).alias("__n__")
    )
    counts = counts.join(
        existing_nodes.select("entity_id", "created_at"), on="entity_id", how="left"
    )

    part = Window.partitionBy("cluster_id")
    ranked = counts.withColumn(
        "__rank__",
        F.row_number().over(
            part.orderBy(
                F.col("__n__").desc(),
                F.col("created_at").asc(),
                F.col("entity_id").asc(),
            )
        ),
    ).withColumn("__n_entities__", F.count(F.lit(1)).over(part))

    winners = ranked.filter(F.col("__rank__") == 1).select(
        "cluster_id",
        "entity_id",
        F.when(F.col("__n_entities__") == 1, F.lit("absorb"))
        .otherwise(F.lit("merge"))
        .alias("action"),
    )
    losers = (
        ranked.filter(F.col("__rank__") > 1)
        .select("cluster_id", F.col("entity_id").alias("loser_entity_id"))
        .join(winners.select("cluster_id", "entity_id"), on="cluster_id", how="inner")
    )

    # Clusters with NO overlap are creates -- mint from member record_ids, the
    # same content-derived scheme the create path uses.
    create_clusters = all_clusters.join(winners, on="cluster_id", how="left_anti")
    created = mint_entity_ids(
        assign_rid.join(create_clusters, on="cluster_id", how="inner")
    ).withColumn("action", F.lit("create"))

    resolution = winners.select("cluster_id", "entity_id", "action").unionByName(
        created.select("cluster_id", "entity_id", "action")
    )
    return resolution, losers


def build_incremental_records(
    assign_rid: Any,
    resolution: Any,
    losers: Any,
    existing_records: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """record -> entity for this run: the clusters' own records at their resolved
    entity, PLUS every merge loser's existing records reassigned to the winner.

    ``first_seen_at`` is preserved for a record the store already knows and set
    to this run for a new one; ``last_seen_at`` is always this run.
    """
    from pyspark.sql import functions as F

    now = run_meta["recorded_at"]
    prior = existing_records.select(
        "record_id", F.col("first_seen_at").alias("__prior_first__")
    )

    own = assign_rid.join(resolution, on="cluster_id", how="inner").select(
        "record_id", "entity_id"
    )
    # Reassigned: the losers' OTHER records (ones not in this run's clusters).
    reassigned = (
        existing_records.select(
            F.col("record_id"), F.col("entity_id").alias("loser_entity_id")
        )
        .join(
            losers.select("loser_entity_id", "entity_id").distinct(),
            on="loser_entity_id",
            how="inner",
        )
        .select("record_id", "entity_id")
    )

    # A record in both lands on its cluster's resolved entity: `own` wins.
    combined = own.unionByName(
        reassigned.join(own.select("record_id"), on="record_id", how="left_anti")
    )
    return (
        combined.join(prior, on="record_id", how="left")
        .select(
            "record_id",
            "entity_id",
            F.lit(run_meta.get("dataset")).alias("dataset"),
            F.coalesce(F.col("__prior_first__"), F.lit(now)).alias("first_seen_at"),
            F.lit(now).alias("last_seen_at"),
        )
    )


def build_incremental_nodes(
    resolution: Any,
    losers: Any,
    golden_df: Any,
    existing_nodes: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """Nodes for this run: creates, absorb/merge winners, and retired losers.

    A winner keeps its original ``created_at`` (it is the same identity, still
    active); a loser keeps its ``created_at`` too but goes
    ``status="merged_into"`` with ``merged_into`` set -- mirroring
    ``store.retire_identity``.
    """
    from pyspark.sql import functions as F

    now = run_meta["recorded_at"]
    golden_json = _golden_record_json(resolution, golden_df)
    prior = existing_nodes.select(
        "entity_id", F.col("created_at").alias("__prior_created__")
    )

    live = (
        resolution.select("cluster_id", "entity_id")
        .join(golden_json, on="entity_id", how="left")
        .join(prior, on="entity_id", how="left")
        .select(
            "entity_id",
            F.lit("active").alias("status"),
            F.lit(None).cast("string").alias("merged_into"),
            F.col("golden_record"),
            F.lit(None).cast("double").alias("confidence"),
            F.lit(run_meta.get("dataset")).alias("dataset"),
            F.coalesce(F.col("__prior_created__"), F.lit(now)).alias("created_at"),
            F.lit(now).alias("updated_at"),
        )
    )

    retired = (
        losers.select(
            F.col("loser_entity_id").alias("entity_id"),
            F.col("entity_id").alias("merged_into"),
        )
        .distinct()
        .join(prior, on="entity_id", how="left")
        .select(
            "entity_id",
            F.lit("merged_into").alias("status"),
            F.col("merged_into"),
            F.lit(None).cast("string").alias("golden_record"),
            F.lit(None).cast("double").alias("confidence"),
            F.lit(run_meta.get("dataset")).alias("dataset"),
            F.coalesce(F.col("__prior_created__"), F.lit(now)).alias("created_at"),
            F.lit(now).alias("updated_at"),
        )
    )
    return live.unionByName(retired)


def _golden_record_json(entity_ids: Any, golden_df: Any) -> Any:
    """``entity_id -> golden_record`` JSON for multi-member clusters (the same
    projection ``build_identity_nodes`` does inline, factored out so the create
    and incremental node builders cannot drift)."""
    from pyspark.sql import functions as F

    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

    gcols = [c for c in golden_df.columns if c != "cluster_id"]

    # One struct argument, as in `_rid`. `pd.isna` is gone with it: a pa.Array
    # carries a validity bitmap, so `to_pylist()` yields None for a null and
    # there is no NaN to test for.
    @arrow_udf("string")
    def _as_json(rows):
        out = []
        for row in to_pylist(rows):
            rec = {c: row[c] for c in gcols}
            out.append(json.dumps(rec, default=str))
        return from_pylist(out, "string")

    return (
        golden_df.join(entity_ids.select("cluster_id", "entity_id"), on="cluster_id",
                       how="inner")
        .withColumn("golden_record", _as_json(F.struct(*[F.col(c) for c in gcols])))
        .select("entity_id", "golden_record")
    )


def build_incremental_events(
    resolution: Any,
    losers: Any,
    assign_rid: Any,
    existing_records: Any,
    *,
    run_meta: dict[str, Any],
) -> Any:
    """``CREATED`` per created entity, ``ABSORBED_RECORD`` per record an absorb
    newly added, ``MERGED_WITH`` for each merge winner and each loser.

    NOTE the kind casing is UPPERCASE, matching the shipped S5
    ``build_identity_events``; one-box's ``EventKind`` values are lowercase. That
    divergence is pre-existing on a frozen contract -- see the design spec.
    """
    from pyspark.sql import functions as F

    def _rows(df: Any, kind: str) -> Any:
        return df.select(
            "entity_id",
            F.lit(kind).alias("kind"),
            F.lit(run_meta["run_name"]).alias("run_name"),
            F.lit(run_meta.get("dataset")).alias("dataset"),
            F.lit(run_meta["recorded_at"]).alias("recorded_at"),
        )

    created = _rows(
        resolution.filter(F.col("action") == "create").select("entity_id"), "CREATED"
    )
    # Absorb: only records this run actually ADDED (resolve.py's `newly_added`);
    # a pure re-observation emits nothing.
    absorbed = _rows(
        assign_rid.join(
            resolution.filter(F.col("action") == "absorb"), on="cluster_id", how="inner"
        )
        .join(existing_records.select("record_id"), on="record_id", how="left_anti")
        .select("entity_id"),
        "ABSORBED_RECORD",
    )
    merged = _rows(
        losers.select("entity_id")
        .unionByName(losers.select(F.col("loser_entity_id").alias("entity_id")))
        .distinct(),
        "MERGED_WITH",
    )
    return created.unionByName(absorbed).unionByName(merged)


@dataclass
class IdentityGraphFrames:
    """The distributed identity graph as four Spark frames (S5 create path).

    The stable public contract (#859): ``nodes`` (one per entity, ``NODE_COLUMNS``),
    ``records`` (record->entity assignment, ``RECORD_COLUMNS``), ``edges``
    (``same_as`` evidence with full provenance, ``EDGE_COLUMNS``), and an optional
    ``events`` frame (one ``CREATED`` row per entity, ``EVENT_COLUMNS``). Each frame
    is a distributed Spark DataFrame, written as parquet, never collected to the
    driver. Incremental absorb/merge is the deferred Layer 2 (honest-null): on a
    fresh store every cluster is a create, which is the common case.
    """

    nodes: Any
    records: Any
    edges: Any
    events: Any = None


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
    with_events: bool = True,
) -> IdentityGraphFrames:
    """Produce the create-path identity graph as distributed Spark frames.

    Layer 1 only (create + ``same_as`` edges + ``CREATED`` events); incremental
    absorb/merge against an existing store is the deferred Layer 2 (honest-null,
    driver-side as the Ray path left it). ``run_meta`` carries ``run_name`` (the
    run id stamped onto edges/events), ``recorded_at``, and optional ``dataset`` /
    ``matchkey_name``. Set ``with_events=False`` to skip the bookkeeping frame.
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
    events = build_identity_events(entity_ids, run_meta=run_meta) if with_events else None
    return IdentityGraphFrames(nodes=nodes, records=records, edges=edges, events=events)


def build_identity_graph_incremental(
    pairs: Any,
    assignments: Any,
    source_df: Any,
    golden_df: Any,
    *,
    existing_records: Any = None,
    existing_nodes: Any = None,
    run_meta: dict[str, Any],
    source_col: str = "__source__",
    source_pk_col: str | None = None,
    id_col: str = "__row_id__",
    with_events: bool = True,
) -> IdentityGraphFrames:
    """Layer 2 (#966): resolve this run's clusters AGAINST AN EXISTING STORE.

    The incremental sibling of :func:`build_identity_graph`. Same four output
    frames, same frozen columns -- the difference is that a cluster whose
    records already belong to an entity ABSORBS into it (or MERGES several)
    instead of minting a fresh id, so a store converges across runs instead of
    accumulating duplicate entities.

    ``existing_records`` (``RECORD_COLUMNS``) and ``existing_nodes``
    (``NODE_COLUMNS``) are the prior state, normally read straight back from the
    parquet the last run wrote. Passing neither (or empty frames) degrades to
    exactly the create path -- every cluster is a create -- which is what makes
    this safe to call unconditionally.

    Semantics mirror one-box ``resolve_clusters``; the winner rule and its
    tie-breaks are documented on :func:`resolve_cluster_entities`.
    """
    from pyspark.sql import functions as F

    if existing_records is None or existing_nodes is None:
        # No prior state -> the create path, verbatim. Not merely equivalent:
        # the SAME function, so the degenerate case cannot drift.
        return build_identity_graph(
            pairs, assignments, source_df, golden_df,
            run_meta=run_meta, source_col=source_col,
            source_pk_col=source_pk_col, id_col=id_col, with_events=with_events,
        )

    src_rid = derive_record_ids(
        source_df, source_col=source_col, source_pk_col=source_pk_col, id_col=id_col
    )
    recid_map = src_rid.select(F.col(id_col).alias("member_id"), F.col("record_id"))
    assign_rid = assignments.join(recid_map, on="member_id", how="inner").select(
        "cluster_id", "record_id"
    )

    resolution, losers = resolve_cluster_entities(
        assign_rid, existing_records, existing_nodes
    )
    entity_ids = resolution.select("cluster_id", "entity_id")

    edges = build_same_as_edges(
        pairs, assignments, recid_map, entity_ids, run_meta=run_meta
    )
    nodes = build_incremental_nodes(
        resolution, losers, golden_df, existing_nodes, run_meta=run_meta
    )
    records = build_incremental_records(
        assign_rid, resolution, losers, existing_records, run_meta=run_meta
    )
    events = (
        build_incremental_events(
            resolution, losers, assign_rid, existing_records, run_meta=run_meta
        )
        if with_events
        else None
    )
    return IdentityGraphFrames(nodes=nodes, records=records, edges=edges, events=events)
