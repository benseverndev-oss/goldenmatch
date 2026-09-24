"""User-facing dedupe output tables: what a person gets back, not pipeline state.

The pipeline's internal frames carry working columns -- derived matchkeys, cached
transforms, block keys, raw snapshots -- that exist so blocking and scoring can
run. They leaked into the user-facing ``dupes``/``unique`` tables and files, and
a few into golden (#2991). This module is the one place that decides what a user
sees, applied at the boundaries only (``DedupeResult`` and the output writer), so
the pipeline's own result dict -- which internal consumers and the frames/dict
parity tests read -- is unchanged.

``__row_id__`` and ``__source__`` are kept: they identify the input record and
the file it came from, and callers join on them. ``__cluster_id__`` and
``__golden_confidence__`` are results, not state.
"""

from __future__ import annotations

from typing import Any

# Working columns the pipeline adds and a user never asked for. Prefix match.
PIPELINE_INTERNAL_PREFIXES = ("__mk_", "__xform_", "__block_key__", "__bucket__", "__raw__")


def _as_arrow(table: Any) -> Any:
    if table is None or hasattr(table, "num_rows"):
        return table
    return table.to_arrow()  # polars DataFrame (zero-copy)


def strip_pipeline_internals(table: Any) -> Any:
    """Drop pipeline working columns from a result table (pa.Table or polars).

    Returns a ``pa.Table`` (or ``None``). Columns a user supplied are never
    dropped, whatever they are called, unless they carry one of the working
    prefixes above -- which no real input column does in practice.
    """
    table = _as_arrow(table)
    if table is None:
        return None
    keep = [c for c in table.column_names if not c.startswith(PIPELINE_INTERNAL_PREFIXES)]
    if len(keep) == len(table.column_names):
        return table
    return table.select(keep)


def deduplicated_table(golden: Any, unique: Any, clusters: dict | None) -> Any:
    """One row per real-world entity: the golden records, then the unique records.

    This is the answer to "give me my records minus the duplicates": the 4 golden
    rows of a 12-record file with 4 duplicate groups are only 4 of its 7 people.
    Columns are the user's columns plus ``__cluster_id__`` (every row, so a row
    can be traced back through ``clusters``); golden-only columns such as
    ``__golden_confidence__`` are null on unique rows. ``__row_id__`` and
    ``__source__`` are dropped: a golden row merges several of each.

    Values are what the pipeline holds, i.e. after standardization, on both
    kinds of row alike.
    """
    import pyarrow as pa

    golden = strip_pipeline_internals(golden)
    unique = strip_pipeline_internals(unique)
    parts = []
    if golden is not None and golden.num_rows:
        parts.append(_without(golden, ("__row_id__", "__source__")))
    if unique is not None and unique.num_rows:
        row_to_cluster: dict[int, int] = {}
        for cid, info in (clusters or {}).items():
            for member in info.get("members", ()):
                row_to_cluster[member] = cid
        u = unique
        if "__row_id__" in u.column_names:
            cids = [row_to_cluster.get(r) for r in u.column("__row_id__").to_pylist()]
            u = u.append_column("__cluster_id__", pa.array(cids, type=pa.int64()))
        u = _without(u, ("__row_id__", "__source__"))
        parts.append(u)
    if not parts:
        return golden if golden is not None else unique
    if len(parts) == 1:
        return parts[0]
    return _concat_aligned(parts[0], parts[1])


def _without(table: Any, names: tuple[str, ...]) -> Any:
    # `select`, not `drop_columns`: the latter is pyarrow>=14; goldenmatch supports >=10.
    return table.select([c for c in table.column_names if c not in names])


def _concat_aligned(first: Any, second: Any) -> Any:
    """Concatenate two tables whose columns overlap, golden's column order first.

    Missing columns are null-filled; a column whose type differs between the two
    is cast to string on both sides rather than failing the whole output.
    """
    import pyarrow as pa

    names = list(first.column_names) + [c for c in second.column_names if c not in first.column_names]
    cols_a, cols_b = [], []
    for name in names:
        a = first.column(name) if name in first.column_names else None
        b = second.column(name) if name in second.column_names else None
        if a is not None and b is not None and a.type != b.type:
            a, b = a.cast(pa.string()), b.cast(pa.string())
        present = a if a is not None else b
        assert present is not None  # every name comes from one of the two tables
        typ = present.type
        cols_a.append(a if a is not None else pa.nulls(first.num_rows, typ))
        cols_b.append(b if b is not None else pa.nulls(second.num_rows, typ))
    return pa.concat_tables([pa.table(cols_a, names=names), pa.table(cols_b, names=names)])
