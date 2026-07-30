"""Metric-aware key-integrity certification (the semantic-layer wedge).

`certify_key_integrity(df, key=..., measures=..., grain=..., resolve=...)` returns
a `KeyIntegrityCertificate` — an advisory answer to "is the entity key this
semantic model declares actually trustworthy?":

  * **structural** (always): is the key unique at grain, and how much would a
    duplicated key inflate a `SUM` / `COUNT(DISTINCT)` (fan-out)? Cheap,
    SQL-expressible — this is what the `goldenmatch_key_integrity` dbt test mirrors.
  * **resolution** (opt-in `resolve=True`): run entity resolution on the record's
    *attributes* and check whether distinct declared keys collapse onto one real
    entity (fragmentation → undercount). This is the part a semantic layer can't do.

Arrow-native by design (no polars import), consistent with the rest of modern
goldenmatch. The resolution tier is fail-open: any failure leaves the resolution
fields None with an explanatory note and never breaks the structural certificate.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate


def _to_arrow(df: Any) -> pa.Table:
    """Coerce a supported table type to a pyarrow.Table without importing polars."""
    if isinstance(df, pa.Table):
        return df
    to_arrow = getattr(df, "to_arrow", None)  # polars.DataFrame
    if callable(to_arrow):
        out = df.to_arrow()
        if isinstance(out, pa.Table):
            return out
    if isinstance(df, dict):
        return pa.table(df)
    try:  # pandas.DataFrame
        return pa.Table.from_pandas(df)
    except Exception as exc:  # pragma: no cover - defensive
        raise TypeError(
            f"certify_key_integrity: unsupported table type {type(df)!r}; "
            "pass a pyarrow.Table, polars.DataFrame, pandas.DataFrame, or dict"
        ) from exc


def _as_list(x: str | Sequence[str] | None) -> list[str]:
    if x is None:
        return []
    if isinstance(x, str):
        return [x]
    return list(x)


def _row_key_values(table: pa.Table, key_columns: list[str]) -> list[tuple]:
    """Per-row declared-key tuple (positional; row i == __row_id__ i)."""
    cols = [table.column(c).to_pylist() for c in key_columns]
    return list(zip(*cols))


def certify_key_integrity(
    df: Any,
    *,
    key: str | Sequence[str],
    measures: Sequence[str] | None = None,
    grain: Sequence[str] | None = None,
    attributes: Sequence[str] | None = None,
    resolve: bool = False,
) -> KeyIntegrityCertificate:
    """Certify a declared entity key for metric use.

    Args:
        df: the table backing a semantic model (pyarrow / polars / pandas / dict).
        key: the declared entity key column(s) (MetricFlow `entity`, Cube
            `primary_key`, OSI dataset key).
        measures: numeric columns aggregated against this entity — fan-out is
            quantified per measure.
        grain: the model's aggregation grain (recorded as advisory context in v1;
            uniqueness is evaluated on the entity `key`).
        attributes: columns to run entity resolution on when `resolve=True`
            (default: every column that isn't a key or a measure).
        resolve: also measure entity fragmentation / undercount via GoldenMatch ER.

    Returns:
        A `KeyIntegrityCertificate`. Advisory only — it never mutates the data.
    """
    table = _to_arrow(df)
    key_columns = _as_list(key)
    measure_cols = _as_list(measures)
    grain_cols = _as_list(grain)

    present = set(table.column_names)
    missing_keys = [c for c in key_columns if c not in present]
    if not key_columns:
        raise ValueError("certify_key_integrity: `key` must name at least one column")
    if missing_keys:
        raise ValueError(f"certify_key_integrity: key column(s) not in table: {missing_keys}")
    missing_measures = [c for c in measure_cols if c not in present]
    if missing_measures:
        raise ValueError(f"certify_key_integrity: measure column(s) not in table: {missing_measures}")

    n_rows = table.num_rows
    notes: list[str] = []

    # --- structural: uniqueness at grain + fan-out (pyarrow group-by) ---
    numeric_measures = [
        m for m in measure_cols
        if pa.types.is_integer(table.schema.field(m).type)
        or pa.types.is_floating(table.schema.field(m).type)
    ]
    skipped = [m for m in measure_cols if m not in numeric_measures]
    if skipped:
        notes.append(f"non-numeric measures skipped for fan-out: {skipped}")

    aggs: list = [([], "count_all")]
    for m in numeric_measures:
        aggs.append((m, "max"))
    grouped = table.group_by(key_columns).aggregate(aggs)

    n_key_groups = grouped.num_rows
    counts = grouped.column("count_all")
    max_fan_out = float(pc.max(counts).as_py()) if n_key_groups else 0.0
    duplicate_key_groups = (
        int(pc.sum(pc.cast(pc.greater(counts, 1), pa.int64())).as_py() or 0)
        if n_key_groups else 0
    )
    is_unique_at_grain = n_key_groups == n_rows

    measure_fan_out: dict[str, float] = {}
    for m in numeric_measures:
        total_sum = pc.sum(table.column(m)).as_py()
        dedup_sum = pc.sum(grouped.column(f"{m}_max")).as_py()
        if dedup_sum:  # non-zero, non-None
            measure_fan_out[m] = float(total_sum) / float(dedup_sum)
        else:
            measure_fan_out[m] = 1.0

    if grain_cols:
        notes.append(f"grain {grain_cols} recorded as context; uniqueness evaluated on key")

    cert = KeyIntegrityCertificate(
        key_columns=key_columns,
        grain=grain_cols or None,
        n_rows=n_rows,
        n_key_groups=n_key_groups,
        is_unique_at_grain=is_unique_at_grain,
        duplicate_key_groups=duplicate_key_groups,
        max_fan_out=max_fan_out,
        measure_fan_out=measure_fan_out,
    )

    # --- resolution (opt-in, fail-open): does ER collapse distinct declared keys? ---
    if resolve:
        _add_resolution(cert, table, key_columns, measure_cols, attributes, notes)

    cert.note = "; ".join(notes)
    return cert


def _add_resolution(
    cert: KeyIntegrityCertificate,
    table: pa.Table,
    key_columns: list[str],
    measure_cols: list[str],
    attributes: Sequence[str] | None,
    notes: list[str],
) -> None:
    """Populate the fragmentation/undercount fields via GoldenMatch ER.

    Fail-open: any error leaves the resolution fields None and appends a note.
    """
    exclude = set(key_columns) | set(measure_cols)
    attrs = list(attributes) if attributes is not None else [
        c for c in table.column_names if c not in exclude
    ]
    if not attrs:
        notes.append("resolution skipped: no attribute columns to resolve on")
        return

    try:
        from goldenmatch._api import dedupe_df
        from goldenmatch.core.autoconfig import auto_configure_df

        sub = table.select(attrs)
        # Build config zero-config, then disable rerank/cross-encoder so the
        # certification path never blocks on a model download (offline-safe).
        cfg = auto_configure_df(sub, confidence_required=False)
        for mk in cfg.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
        res = dedupe_df(sub, config=cfg, confidence_required=False)
        clusters = getattr(res, "clusters", None) or {}

        keyvals = _row_key_values(table, key_columns)
        resolved = 0
        fragmented = 0
        for cl in clusters.values():
            members = cl.get("members", []) if isinstance(cl, dict) else getattr(cl, "members", [])
            if len(members) < 2:
                continue
            resolved += 1
            distinct_keys = {keyvals[int(m)] for m in members}
            if len(distinct_keys) > 1:
                fragmented += 1

        cert.resolved_entities = resolved
        cert.fragmented_entities = fragmented
        cert.undercount_estimate = (fragmented / resolved) if resolved else 0.0
        if fragmented:
            notes.append(
                f"{fragmented}/{resolved} resolved entities span >1 declared key "
                "(fragmentation → the join undercounts distinct entities)"
            )
    except Exception as exc:  # fail-open: never break the structural certificate
        cert.estimable = cert.is_unique_at_grain and not cert.duplicate_key_groups
        notes.append(f"resolution unavailable ({type(exc).__name__}: {exc})")
