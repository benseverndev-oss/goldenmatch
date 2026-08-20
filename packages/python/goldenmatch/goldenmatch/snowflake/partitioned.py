"""Partitioned Snowflake execution: distribute scoring, cluster centrally.

## Why this exists

``docs/snowflake-handlers.md`` Phase 2 describes the stored-procedure shape as
"reads the input relation into a Polars frame via Snowpark, calls
``goldenmatch.dedupe_df(df, config)``, writes the golden output back". That is
correct, and it is also **structurally single-node**: one process materialises
the whole relation, so the ceiling is one warehouse node's memory however large
the warehouse is.

Measured on a STANDARD X-Small, one Snowpark stored procedure, a six-field
probabilistic config over four blocking passes:

    209,000 rows    ->  94 s   OK
    1,010,343 rows  -> 366 s   OK
    2,800,000 rows  ->         "Function available memory exhausted.
                                Consider using Snowpark-optimized Warehouses"

A bigger warehouse moves that number. It does not change the shape. This module
changes the shape.

## The shape

Snowflake's unit of parallelism for user code is a **vectorized UDTF partitioned
by a key**: one ``end_partition`` call per partition, spread across the
warehouse. Blocking already partitions an ER workload -- a candidate pair only
ever forms inside a block -- so the two line up exactly:

    for each blocking pass:
        SELECT <block key> AS __gm_block__, ... FROM relation
        WHERE <key is valid>
        -- one call per block, in parallel, across the warehouse
        TABLE(gm_score_partition(...) OVER (PARTITION BY __gm_block__))
    UNION ALL the pairs from every pass
    -> connected_components(pairs)          # GoldenMatch's own union-find

Only the O(n^2)-within-a-block work distributes. Clustering stays central
because it is global by definition, and because the pair set is far smaller than
the row set -- a run of this shape produces pairs numbering in the tens of
millions where the rows do not fit at all.

**The engine still drives.** The worker calls ``dedupe_df`` on its block with the
caller's own config, so matchkeys, the trained model, thresholds, negative
evidence and the reference data all apply exactly as they do one-box. Clustering
calls ``core.pairs.connected_components`` -- the same native union-find that
``build_clusters`` uses. Nothing here re-implements a matching decision; it only
decides *where* the work runs.

## What this module does not do

- It does not stage wheels or create the UDTF. Deployment belongs to the caller.
- It does not do survivorship or identity resolution. Those consume clusters and
  are unchanged by where scoring ran.
- ``plan_passes``, ``block_key_sql`` and ``cluster_pairs`` are pure and unit
  tested. ``score_partition`` needs the engine but not Snowflake. Only the
  assembly of the two needs a live session.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Mirrors ``spark/config_pipeline.py``. A block key joins its parts with this
#: separator, and ANY null part makes the whole key null -- ``concat_ws`` alone
#: would SKIP nulls and collide ``("a", null)`` with ``("a", "")``.
BLOCK_KEY_SEP = "||"

#: ``frame.filter_valid_key``'s sentinels. An EMPTY STRING is kept: it is a real
#: value (#390). Only stringified missing markers are dropped.
KEY_SENTINELS = ("nan", "null", "none")

#: Column the worker blocks on. The partition IS the block, so the worker hands
#: the engine a constant key and lets it score the block whole.
PARTITION_KEY_COLUMN = "__gm_block__"


class UnsupportedBlockingError(ValueError):
    """A blocking config this tier will not silently approximate."""


def blocking_passes(config: Any) -> list[Any]:
    """Every key set whose blocks must be unioned into the candidate set.

    A ``multi_pass`` config carries its passes in ``blocking.passes`` and leaves
    ``blocking.keys`` holding ONE of them, so reading ``keys`` on a multi_pass
    config silently generates candidates from a single pass out of N -- a recall
    loss that looks like a clean run. ``passes`` wins when present.

    Deliberately duplicated from ``spark/config_pipeline.blocking_passes``
    rather than imported: that module reaches for pyspark, and this tier must
    not require Spark to be installed.
    """
    blocking = config.blocking
    if getattr(blocking, "strategy", "static") == "multi_pass":
        passes = list(getattr(blocking, "passes", None) or [])
        if passes:
            return passes
    return list(blocking.keys or [])


#: Names that are legal bare identifiers; anything else must be quoted.
_PLAIN_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")


def _sql_ident(name: str, quote: bool = False) -> str:
    """Render a column reference.

    Default is UNQUOTED, and that is the load-bearing choice. GoldenMatch field
    names are conventionally lower case (``npi``); Snowflake folds unquoted
    identifiers to upper case at DDL time, so a table created as ``NPI`` is not
    matched by ``"npi"`` -- quoting a lower-case config field name against an
    ordinary Snowflake table fails to resolve. Unquoted, Snowflake's own
    case-insensitive resolution does the right thing.

    ``quote=True`` is for relations that really were created with quoted mixed
    case. A name that is not a legal bare identifier is always quoted, because
    the alternative is emitting SQL that cannot parse.
    """
    text = str(name)
    if quote or not _PLAIN_IDENT.match(text):
        return '"' + text.replace('"', '""') + '"'
    return text


def block_key_sql(key_config: Any, quote: bool = False) -> tuple[str, list[str]]:
    """``(sql_expression, fields)`` for one blocking key.

    Mirrors ``spark/config_pipeline._block_key_column`` and, through it,
    ``arrow_derive.block_key``: parts join with :data:`BLOCK_KEY_SEP`, and any
    null part nulls the whole key.

    Transforms are NOT applied here. A transform chain is GoldenMatch's own
    (``lowercase``, ``strip``, the domain normalizers), and re-expressing it as
    Snowflake SQL would be a second implementation that can disagree with the
    engine's. A config whose blocking keys carry transforms is refused by
    :func:`plan_passes` with the reason named, rather than approximated.
    """
    fields = list(key_config.fields)
    parts = ["NULLIF(TO_VARCHAR(" + _sql_ident(f, quote) + "), '')" for f in fields]
    any_null = " OR ".join(p + " IS NULL" for p in parts)
    sep = " || '" + BLOCK_KEY_SEP + "' || "
    joined = parts[0] if len(parts) == 1 else sep.join(parts)
    return ("CASE WHEN " + any_null + " THEN NULL ELSE " + joined + " END", fields)


def valid_key_sql(expr: str) -> str:
    """``filter_valid_key``'s predicate: non-null and not a missing sentinel."""
    sentinels = ", ".join("'" + s + "'" for s in KEY_SENTINELS)
    return (
        "(" + expr + ") IS NOT NULL AND LOWER(TRIM(" + expr + ")) NOT IN ("
        + sentinels + ")"
    )


@dataclass(frozen=True)
class BlockingPassPlan:
    """One blocking pass, expressed as the SQL that partitions it."""

    index: int
    fields: list[str]
    key_sql: str
    predicate_sql: str

    def select_sql(
        self, relation: str, id_column: str, columns: list[str], quote: bool = False
    ) -> str:
        """Rows for this pass, carrying the partition key the UDTF blocks on."""
        cols = ", ".join(_sql_ident(c, quote) for c in columns)
        return (
            "SELECT " + self.key_sql + " AS " + _sql_ident(PARTITION_KEY_COLUMN, True)
            + ", " + _sql_ident(id_column, quote) + (", " + cols if cols else "")
            + " FROM " + relation + " WHERE " + self.predicate_sql
        )


@dataclass
class PartitionedPlan:
    """The full partitioned plan for one config."""

    passes: list[BlockingPassPlan] = field(default_factory=list)
    id_column: str = "unique_id"
    columns: list[str] = field(default_factory=list)
    quote_identifiers: bool = False

    def pair_sql(self, relation: str, udtf: str) -> str:
        """UNION ALL of every pass's partitioned UDTF call.

        A pair found by two passes appears twice. De-duplication is the
        clustering step's job -- union-find is idempotent over repeated edges --
        so this stays a cheap ``UNION ALL`` rather than a sort.

        The UDTF arguments are listed EXPLICITLY. ``TABLE(f(s.*) OVER (...))``
        does not parse in Snowflake, and the column order here is the order the
        handler's ``end_partition`` frame arrives in, so it is also the contract
        the handler names its columns by.
        """
        if not self.passes:
            raise ValueError("no blocking passes -- nothing to partition by")
        q = self.quote_identifiers
        args = ", ".join(
            ["s." + _sql_ident(PARTITION_KEY_COLUMN, True),
             "s." + _sql_ident(self.id_column, q)]
            + ["s." + _sql_ident(c, q) for c in self.columns]
        )
        legs = []
        for p in self.passes:
            src = p.select_sql(relation, self.id_column, self.columns, q)
            legs.append(
                "SELECT t.ID_A, t.ID_B, t.SCORE FROM (" + src + ") s, TABLE("
                + udtf + "(" + args + ") OVER (PARTITION BY s."
                + _sql_ident(PARTITION_KEY_COLUMN, True) + ")) t"
            )
        return "\nUNION ALL\n".join(legs)


def plan_passes(
    config: Any,
    *,
    id_column: str = "unique_id",
    columns: list[str] | None = None,
    quote_identifiers: bool = False,
) -> PartitionedPlan:
    """Build the partitioned plan, or refuse with the reason named.

    Refuses rather than approximates when a blocking key carries a transform
    chain (see :func:`block_key_sql`). An approximated block key changes which
    candidates exist, which changes recall, and would surface as a tuning
    difference rather than as the bug it is.
    """
    passes: list[BlockingPassPlan] = []
    for i, key in enumerate(blocking_passes(config)):
        shared = list(getattr(key, "transforms", None) or [])
        per_field = {
            k: v for k, v in (getattr(key, "field_transforms", None) or {}).items() if v
        }
        if shared or per_field:
            raise UnsupportedBlockingError(
                "blocking pass " + str(i) + " on " + str(list(key.fields))
                + " carries transforms " + str(shared or per_field)
                + "; the partitioned Snowflake tier does not re-express "
                "GoldenMatch transform chains as SQL, because a second "
                "implementation of a block key can disagree with the engine's "
                "and the disagreement reads as a tuning difference. Precompute "
                "the transformed column in the relation and block on it, or use "
                "the single-node stored-procedure path."
            )
        key_sql, fields = block_key_sql(key, quote_identifiers)
        passes.append(
            BlockingPassPlan(
                index=i,
                fields=fields,
                key_sql=key_sql,
                predicate_sql=valid_key_sql(key_sql),
            )
        )
    logger.info(
        "Snowflake partitioned tier: %d blocking pass(es) -> %s",
        len(passes),
        [p.fields for p in passes],
    )
    return PartitionedPlan(
        passes=passes,
        id_column=id_column,
        columns=list(columns or []),
        quote_identifiers=quote_identifiers,
    )


def _constant_blocking(cfg: Any) -> Any:
    """Blocking that puts the whole partition in one block."""
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

    key = BlockingKeyConfig(fields=[PARTITION_KEY_COLUMN])
    valid = set(BlockingConfig.model_fields)
    kw: dict[str, Any] = {"keys": [key]}
    if "passes" in valid:
        kw["passes"] = [key]
    if "strategy" in valid:
        kw["strategy"] = "static"
    # The partition IS the block by construction, so an oversized-block guard
    # here would DROP candidates the caller asked for. Bounding partition size is
    # the planner's problem -- a hub key is a hub key on any engine.
    if "skip_oversized" in valid:
        kw["skip_oversized"] = False
    existing = getattr(cfg.blocking, "max_block_size", None)
    if "max_block_size" in valid and existing:
        kw["max_block_size"] = existing
    return BlockingConfig(**kw)


def score_partition(
    rows: Any, config: Any, *, id_column: str = "unique_id"
) -> list[tuple[Any, Any, float]]:
    """Score ONE block with the caller's config; return ``(id_a, id_b, score)``.

    ``rows`` is the partition -- a pandas DataFrame or a pyarrow Table -- and it
    IS the block, so the engine gets a constant blocking key and scores it whole.
    Everything that decides a match (matchkeys, the trained model, thresholds,
    negative evidence, reference data) comes from ``config`` and behaves exactly
    as it does one-box.

    Row positions are mapped back to ``id_column`` before returning: the engine's
    pair endpoints are 0-based offsets into the frame it was handed, and a
    partition is a slice of the relation, so returning them unmapped would name
    rows that belong to someone else.
    """
    import pyarrow as pa

    from goldenmatch import dedupe_df

    table = (
        rows
        if isinstance(rows, pa.Table)
        else pa.Table.from_pandas(rows, preserve_index=False)
    )
    if table.num_rows < 2:
        return []
    ids = table.column(id_column).to_pylist()

    cfg = config.model_copy(deep=True)
    cfg.identity = None  # clusters are assembled globally, not per block
    cfg.blocking = _constant_blocking(cfg)
    if PARTITION_KEY_COLUMN not in table.column_names:
        table = table.append_column(
            PARTITION_KEY_COLUMN, pa.array(["1"] * table.num_rows, pa.string())
        )

    result = dedupe_df(table, config=cfg)
    out: list[tuple[Any, Any, float]] = []
    for a, b, score in result.scored_pairs or []:
        if 0 <= a < len(ids) and 0 <= b < len(ids):
            out.append((ids[a], ids[b], float(score)))
    return out


def cluster_pairs(
    pairs: list[tuple[Any, Any, float]], all_ids: list[Any] | None = None
) -> dict[Any, int]:
    """``id -> cluster_id`` over the union of every pass's pairs.

    Delegates to ``core.pairs.connected_components`` -- the same native
    union-find ``build_clusters`` uses -- so a partitioned run and a one-box run
    agree on what a cluster IS. Union-find is idempotent over repeated edges, so
    a pair emitted by two passes needs no de-duplication first.

    Ids are interned to ints because the kernel is integer union-find; the
    mapping is restored on the way out.
    """
    from goldenmatch.core.pairs import connected_components

    index: dict[Any, int] = {}

    def idx(value: Any) -> int:
        if value not in index:
            index[value] = len(index)
        return index[value]

    edges = [(idx(a), idx(b), float(s)) for a, b, s in pairs]
    for value in all_ids or []:
        idx(value)
    components = connected_components(edges, list(range(len(index))))
    back = {v: k for k, v in index.items()}
    out: dict[Any, int] = {}
    for cid, members in enumerate(components):
        for m in members:
            out[back[m]] = cid
    return out
