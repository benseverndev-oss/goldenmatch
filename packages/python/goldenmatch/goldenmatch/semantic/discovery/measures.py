"""Measure & dimension proposal (semantic-model discovery, Phase 4).

Given a table and its grain (a Phase-1 key), propose the MEASURES and DIMENSIONS a
semantic model would declare over it — and, the differentiator, gate each measure's
`SUM`-safety on the grain key's CERTIFICATE. A metric summed over a grain that fans out
double-counts; the Phase-1 fan-out verdict is exactly what tells us whether a `SUM` is
sound, so the certifier that graded the key directly grades the measures.

  * **Measures.** Numeric columns → propose aggregations. `COUNT`/`AVG`/`MIN`/`MAX` are
    always safe (order-independent of grain fan-out). `SUM` is proposed ONLY when the
    grain key certified clean (`max_fan_out == 1.0`); on a fanned-out grain the measure
    is returned with `safe_to_sum == False` and `SUM` withheld — the loud "this would
    double-count" signal.
  * **Dimensions.** Low-cardinality categorical columns, dates, and geo columns — the
    attributes you group/slice by. High-cardinality free text and the grain/id columns
    themselves are not dimensions.
  * **Grain.** The certified key from Phase 1.

Advisory only — it proposes; a human approves. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic.discovery.keys import KeyCandidate

# A categorical column is a dimension when it is low-cardinality enough to group by.
_DEFAULT_MAX_DIM_CARDINALITY = 0.5
# Aggregations that are always sound regardless of grain fan-out (they don't accumulate
# across duplicated rows the way SUM does).
_FANOUT_SAFE_AGGS = ("count", "avg", "min", "max")
# col_types that are dimensions by nature (grouped/sliced, never summed).
_DIMENSION_COL_TYPES = frozenset({"date", "geo"})
# col_types that are neither measures nor dimensions on their own (keys / references /
# free text handled elsewhere).
_CATEGORICAL_EXCLUDED = frozenset({"numeric", "identifier", "description"})


@dataclass
class Measure:
    """A proposed numeric measure over a table, with its grain-gated SUM-safety."""

    column: str
    aggregations: list[str]  # the proposed aggregations (SUM present only when safe)
    safe_to_sum: bool        # True only when the grain key certified clean
    grain: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Dimension:
    """A proposed dimension (group-by / slice attribute)."""

    column: str
    kind: str  # "categorical" | "date" | "geo"
    cardinality_ratio: float = 0.0


@dataclass
class TableMeasures:
    """The measures + dimensions proposed for one table at a certified grain."""

    table: str
    grain: list[str]
    grain_trustworthy: bool
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)


def _grain_spec(key: Any) -> tuple[list[str], KeyIntegrityCertificate | None]:
    """Normalize a grain key into `(columns, certificate_or_None)`."""
    if key is None:
        return [], None
    if isinstance(key, KeyCandidate):
        return list(key.columns), key.certificate
    if isinstance(key, str):
        return [key], None
    if isinstance(key, (list, tuple)):
        return [str(c) for c in key], None
    return [], None


def discover_measures(
    table: Any,
    *,
    key: Any = None,
    grain_certificate: KeyIntegrityCertificate | None = None,
    table_name: str = "table",
    max_dim_cardinality: float = _DEFAULT_MAX_DIM_CARDINALITY,
) -> TableMeasures:
    """Propose measures + dimensions for one table at a certified grain.

    Args:
        table: the source table (any input `profile_columns` accepts).
        key: the grain key — a `KeyCandidate` (carries its own certificate), a column
            name, or a list of columns. When a `KeyCandidate` is passed its certificate
            gates `SUM`-safety; otherwise pass `grain_certificate` explicitly.
        grain_certificate: the grain key's certificate, when `key` is not a
            `KeyCandidate`. If neither is available the grain is treated as untrusted
            and no measure is proposed `SUM`-safe (conservative).
        table_name: label recorded on the result.
        max_dim_cardinality: a categorical column is a dimension only at or below this
            cardinality ratio.

    Returns:
        A `TableMeasures` with grain-gated measures and dimensions. A measure over a
        fanned-out grain is returned with `safe_to_sum == False` and `SUM` withheld.
    """
    from goldenmatch.core.autoconfig import profile_columns

    grain_cols, cert = _grain_spec(key)
    if grain_certificate is not None:
        cert = grain_certificate
    grain_trustworthy = bool(cert is not None and cert.is_trustworthy())
    grain_set = set(grain_cols)

    try:
        profiles = profile_columns(table)
    except Exception:  # noqa: BLE001 - proposal is advisory; empty on profiling failure
        return TableMeasures(table_name, grain_cols, grain_trustworthy)

    measures: list[Measure] = []
    dimensions: list[Dimension] = []
    for p in profiles:
        if p.name in grain_set:
            continue  # the grain is neither a measure nor a dimension
        if p.col_type == "numeric":
            aggs = list(_FANOUT_SAFE_AGGS)
            if grain_trustworthy:
                aggs = ["sum", *aggs]
                reason = "grain certified unique at grain — SUM is sound"
            else:
                reason = (
                    "grain fans out (or grain unknown) — SUM withheld, would double-count"
                )
            measures.append(
                Measure(
                    column=p.name,
                    aggregations=aggs,
                    safe_to_sum=grain_trustworthy,
                    grain=list(grain_cols),
                    reason=reason,
                )
            )
        elif p.col_type in _DIMENSION_COL_TYPES:
            dimensions.append(Dimension(p.name, p.col_type, p.cardinality_ratio))
        elif (
            p.col_type not in _CATEGORICAL_EXCLUDED
            and p.cardinality_ratio <= max_dim_cardinality
        ):
            dimensions.append(Dimension(p.name, "categorical", p.cardinality_ratio))

    measures.sort(key=lambda m: (not m.safe_to_sum, m.column))
    dimensions.sort(key=lambda d: (d.kind, d.column))
    return TableMeasures(
        table=table_name,
        grain=grain_cols,
        grain_trustworthy=grain_trustworthy,
        measures=measures,
        dimensions=dimensions,
    )
