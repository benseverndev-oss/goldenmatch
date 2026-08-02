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

import json
import os
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


def _key_integrity_native_enabled() -> bool:
    """Opt-in gate for the `key-integrity-core` kernel path.

    OFF by default: the pyarrow group_by is the right Arrow-at-bulk boundary and
    the reference the cross-surface golden is generated from. The kernel path is
    JSON-marshaled (slower than pyarrow group_by) and exists for the
    single-Rust-owner guarantee, not for speed — mirrors the TS opt-in
    `enableKeyIntegrityWasm()`.
    """
    return os.environ.get("GOLDENMATCH_KEY_INTEGRITY_NATIVE", "").strip().lower() in (
        "1",
        "true",
        "on",
        "yes",
    )


def _structural_pyarrow(
    table: pa.Table,
    group_columns: list[str],
    numeric_measures: list[str],
    n_rows: int,
) -> dict[str, Any]:
    """The reference structural reduction: pyarrow group_by uniqueness + fan-out."""
    aggs: list = [([], "count_all")]
    for m in numeric_measures:
        aggs.append((m, "max"))
    grouped = table.group_by(group_columns).aggregate(aggs)
    n_key_groups = grouped.num_rows
    counts = grouped.column("count_all")
    max_fan_out = float(pc.max(counts).as_py()) if n_key_groups else 0.0
    duplicate_key_groups = (
        int(pc.sum(pc.cast(pc.greater(counts, 1), pa.int64())).as_py() or 0)
        if n_key_groups
        else 0
    )
    is_unique_at_grain = n_key_groups == n_rows
    measure_fan_out: dict[str, float] = {}
    for m in numeric_measures:
        total_sum = pc.sum(table.column(m)).as_py()
        dedup_sum = pc.sum(grouped.column(f"{m}_max")).as_py()
        measure_fan_out[m] = float(total_sum) / float(dedup_sum) if dedup_sum else 1.0
    return {
        "n_key_groups": n_key_groups,
        "max_fan_out": max_fan_out,
        "duplicate_key_groups": duplicate_key_groups,
        "is_unique_at_grain": is_unique_at_grain,
        "measure_fan_out": measure_fan_out,
    }


def _structural_native(
    table: pa.Table,
    group_columns: list[str],
    numeric_measures: list[str],
) -> dict[str, Any] | None:
    """Structural reduction via the shared `key-integrity-core` kernel.

    Fail-open: returns None (→ pyarrow fallback) if the native wheel lacks the
    symbol or the input isn't JSON-safe (e.g. a date/decimal group column that
    `certify_structural_json` can't accept), so opting in never breaks the
    certificate.
    """
    try:
        from goldenmatch.core._native_loader import native_module

        nm = native_module()
        if nm is None or not hasattr(nm, "certify_structural_json"):
            return None
        payload = json.dumps(
            {
                "n_rows": table.num_rows,
                "group_columns": [table.column(c).to_pylist() for c in group_columns],
                "measures": [
                    {"name": m, "values": table.column(m).to_pylist()}
                    for m in numeric_measures
                ],
            }
        )
        result = json.loads(nm.certify_structural_json(payload))
        return {
            "n_key_groups": result["n_key_groups"],
            "max_fan_out": result["max_fan_out"],
            "duplicate_key_groups": result["duplicate_key_groups"],
            "is_unique_at_grain": result["is_unique_at_grain"],
            "measure_fan_out": result["measure_fan_out"],
        }
    except Exception:  # fail-open: any marshaling / symbol issue → pyarrow reference
        return None


def certify_structural_json(input_json: str) -> str:
    """The JSON-in/JSON-out structural certifier — the Python analogue of the
    `key-integrity-core` kernel's `certify_structural_json`.

    Input: ``{"n_rows": N, "group_columns": [[..], ..],
    "measures": [{"name": .., "values": [..]}, ..]}`` (the group columns are the
    declared key, or key+grain — the caller picks). Output:
    ``{"n_rows", "n_key_groups", "duplicate_key_groups", "max_fan_out",
    "is_unique_at_grain", "measure_fan_out": {name: ratio}}`` — byte-identical to
    the Rust core (locked by `key-integrity-core`'s golden + the native parity
    test), so the SQL surfaces (`goldenmatch_certify_structural` on Postgres via
    the Rust core, and DuckDB via this function) return the same certificate.

    Runs through the shared native kernel when opted in
    (``GOLDENMATCH_KEY_INTEGRITY_NATIVE`` + the wheel), else the pyarrow group_by
    reference. Raises ``ValueError`` on invalid JSON / a wrong-shaped input,
    matching the core's contract.
    """
    try:
        root = json.loads(input_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"certify_structural_json: invalid JSON: {exc}") from exc
    if not isinstance(root, dict):
        raise ValueError("certify_structural_json: input must be a JSON object")

    n_rows = root.get("n_rows")
    if not isinstance(n_rows, int) or isinstance(n_rows, bool) or n_rows < 0:
        raise ValueError("certify_structural_json: n_rows must be a non-negative integer")
    group_cols_raw = root.get("group_columns")
    if not isinstance(group_cols_raw, list) or not all(
        isinstance(c, list) for c in group_cols_raw
    ):
        raise ValueError("certify_structural_json: group_columns must be an array of arrays")
    measures_raw = root.get("measures") or []
    if not isinstance(measures_raw, list):
        raise ValueError("certify_structural_json: measures must be an array")

    # Native pass-through when opted in: the SAME core the Postgres surface calls,
    # so the two are byte-identical by construction.
    if _key_integrity_native_enabled():
        try:
            from goldenmatch.core._native_loader import native_module

            nm = native_module()
            if nm is not None and hasattr(nm, "certify_structural_json"):
                return str(nm.certify_structural_json(input_json))
        except Exception:  # fail-open → pyarrow reference below
            pass

    # pyarrow reference. Reconstruct a table from the columnar JSON and run the
    # same reduction certify_key_integrity uses; serialize to the core's shape.
    columns: dict[str, list] = {}
    group_names = [f"__g{i}__" for i in range(len(group_cols_raw))]
    for name, vals in zip(group_names, group_cols_raw):
        columns[name] = vals
    measure_names: list[str] = []
    for m in measures_raw:
        if not isinstance(m, dict) or "name" not in m or "values" not in m:
            raise ValueError("certify_structural_json: each measure needs name + values")
        columns[str(m["name"])] = list(m["values"])
        measure_names.append(str(m["name"]))
    if columns:
        table = pa.table(columns)
    else:  # zero group columns — an n_rows-length empty frame
        table = pa.table({"__empty__": [None] * n_rows})
        group_names = []
    structural = _structural_pyarrow(table, group_names, measure_names, n_rows)
    out = {
        "n_rows": n_rows,
        "n_key_groups": structural["n_key_groups"],
        "duplicate_key_groups": structural["duplicate_key_groups"],
        "max_fan_out": structural["max_fan_out"],
        "is_unique_at_grain": structural["is_unique_at_grain"],
        "measure_fan_out": structural["measure_fan_out"],
    }
    return json.dumps(out)


def certify_key_integrity(
    df: Any,
    *,
    key: str | Sequence[str],
    measures: Sequence[str] | None = None,
    grain: Sequence[str] | None = None,
    attributes: Sequence[str] | None = None,
    resolve: bool = False,
    grain_strict: bool = False,
) -> KeyIntegrityCertificate:
    """Certify a declared entity key for metric use.

    Args:
        df: the table backing a semantic model (pyarrow / polars / pandas / dict).
        key: the declared entity key column(s) (MetricFlow `entity`, Cube
            `primary_key`, OSI dataset key).
        measures: numeric columns aggregated against this entity — fan-out is
            quantified per measure.
        grain: the model's aggregation grain. Advisory context by default
            (uniqueness is evaluated on the entity `key` alone); when
            `grain_strict=True` it is folded into the grouping so uniqueness /
            fan-out is evaluated on `key + grain` (true "unique at grain").
        attributes: columns to run entity resolution on when `resolve=True`
            (default: every column that isn't a key or a measure).
        resolve: also measure entity fragmentation / undercount via GoldenMatch ER.
        grain_strict: opt-in. When True *and* `grain` is supplied, evaluate
            uniqueness and fan-out on `key + grain` rather than the key alone —
            i.e. certify the key is unique *within each grain bucket* (a fact at
            `(customer_id, date)` grain legitimately repeats a customer across
            days, so the default key-only pass over-reports fan-out for it). The
            default (False) is byte-identical to prior behavior: grain stays
            advisory context and uniqueness is evaluated on the key.

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
    # grain is validated only on the strict path (the default path treats grain
    # as free-text advisory context, so a bogus grain there stays back-compat).
    if grain_strict and grain_cols:
        missing_grain = [c for c in grain_cols if c not in present]
        if missing_grain:
            raise ValueError(
                f"certify_key_integrity: grain column(s) not in table: {missing_grain}"
            )

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

    # Uniqueness/fan-out grouping. Default: the declared key alone. Strict:
    # key + grain, so a fact legitimately repeating a key across grain buckets
    # (e.g. daily rows per customer) is certified unique *at grain* rather than
    # reported as fan-out. Grain cols already in the key aren't repeated.
    if grain_strict and grain_cols:
        group_columns = key_columns + [g for g in grain_cols if g not in key_columns]
    else:
        group_columns = key_columns

    # The group-by uniqueness + fan-out reduction runs through the shared
    # `key-integrity-core` kernel when opted in (GOLDENMATCH_KEY_INTEGRITY_NATIVE
    # + the native wheel), else the pyarrow group-by (the default + reference —
    # Arrow-native, and the source the cross-surface golden is generated from).
    # Both produce identical output (parity-locked in
    # tests/test_key_integrity_native_parity.py). Default stays pyarrow because it
    # is the right Arrow-at-bulk boundary; the kernel path is JSON-marshaled and
    # exists for the single-Rust-owner guarantee, not for speed.
    structural = None
    if _key_integrity_native_enabled():
        structural = _structural_native(table, group_columns, numeric_measures)
    if structural is None:
        structural = _structural_pyarrow(table, group_columns, numeric_measures, n_rows)
    n_key_groups = structural["n_key_groups"]
    max_fan_out = structural["max_fan_out"]
    duplicate_key_groups = structural["duplicate_key_groups"]
    is_unique_at_grain = structural["is_unique_at_grain"]
    measure_fan_out = structural["measure_fan_out"]

    if grain_cols:
        if grain_strict:
            notes.append(f"uniqueness evaluated at grain: key + {grain_cols}")
        else:
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
        cfg = auto_configure_df(sub, confidence_required=False, allow_red_config=True)
        for mk in cfg.get_matchkeys():
            if getattr(mk, "rerank", False):
                mk.rerank = False
        # allow_red_config so a large table (>= the controller's REFUSE_AT_N) still
        # MEASURES fragmentation rather than raising ControllerNotConfidentError and
        # falling through to the "resolution unavailable" note (the #715 contract:
        # confidence_required=False no longer bypasses the RED-refuse).
        res = dedupe_df(sub, config=cfg, confidence_required=False, allow_red_config=True)
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
