"""Catalog reconciliation (PR-18).

Diff a discovered (certified) `ProposedModel` against an EXISTING catalog — a list of
parsed MetricFlow `DeclaredKeySpec` or Cube `Cube` objects (reuse `parse_semantic_models`
/ `parse_cube_models`; no new format code).

The differentiator over a plain text diff: the discovered side is PROVEN. So when the
discovered grain is certified trustworthy and disagrees with the catalog's declared key,
that `grain_drift` is a **provable defect** in the catalog — every SUM at the declared
grain double-counts — not a stylistic difference. `proven=True` marks exactly those.

Scope (logged, not silently omitted): v1 covers tables, grain, and measures.
Cross-dialect *join*-edge reconciliation and a LookML reader (no parser exists yet) are
explicit follow-ons.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class _CatalogTable:
    """A dialect-neutral view of one existing-catalog table."""

    name: str
    key: tuple[str, ...]
    measures: frozenset[str]


@dataclass(frozen=True)
class TableDiff:
    """One reconciliation finding. `kind` is one of `only_in_model` / `only_in_catalog` /
    `grain_drift` / `measure_only_in_model` / `measure_only_in_catalog`. `proven` is True
    only when the discovered side's certification makes the finding authoritative (a
    certified grain that the catalog's declared key contradicts)."""

    table: str
    kind: str
    detail: str
    proven: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "kind": self.kind, "detail": self.detail,
                "proven": self.proven}


@dataclass(frozen=True)
class Reconciliation:
    """The diff between a discovered model and an existing catalog."""

    matched_tables: tuple[str, ...]
    diffs: tuple[TableDiff, ...]
    n_tables_model: int
    n_tables_catalog: int

    @property
    def in_sync(self) -> bool:
        return not self.diffs

    def to_dict(self) -> dict[str, Any]:
        return {
            "in_sync": self.in_sync,
            "matched_tables": list(self.matched_tables),
            "diffs": [d.to_dict() for d in self.diffs],
            "n_tables_model": self.n_tables_model,
            "n_tables_catalog": self.n_tables_catalog,
        }


def _normalize_one(obj: Any) -> _CatalogTable:
    """Normalize a single parsed catalog entry (MetricFlow `DeclaredKeySpec` or Cube
    `Cube`) into the neutral shape. Duck-typed so we don't import the dialect classes."""
    # MetricFlow DeclaredKeySpec: .model / .key / .measures (list[str]).
    if hasattr(obj, "model") and hasattr(obj, "key"):
        return _CatalogTable(
            name=str(obj.model),
            key=tuple(obj.key or ()),
            measures=frozenset(str(m) for m in getattr(obj, "measures", []) or []),
        )
    # Cube: .name / .primary_key (property) / .measures (list[CubeMeasure] with .name).
    if hasattr(obj, "name") and hasattr(obj, "primary_key"):
        measures = frozenset(
            str(getattr(m, "name", m)) for m in getattr(obj, "measures", []) or []
        )
        return _CatalogTable(name=str(obj.name), key=tuple(obj.primary_key or ()),
                             measures=measures)
    raise TypeError(
        f"unsupported catalog entry {type(obj).__name__!r}; expected a parsed MetricFlow "
        "DeclaredKeySpec or Cube (use parse_semantic_models / parse_cube_models)"
    )


def _normalize_catalog(existing: Any) -> dict[str, _CatalogTable]:
    entries = list(existing) if not isinstance(existing, (str, bytes)) else []
    return {ct.name: ct for ct in (_normalize_one(o) for o in entries)}


def reconcile_model(proposed: Any, existing: Any) -> Reconciliation:
    """Reconcile a discovered `ProposedModel` against a parsed existing catalog.

    Args:
        proposed: a `ProposedModel` from `discover_semantic_model`.
        existing: the parsed catalog — a list of MetricFlow `DeclaredKeySpec` (from
            `parse_semantic_models`) or Cube `Cube` (from `parse_cube_models`).

    Returns:
        A `Reconciliation`. A `grain_drift` where the discovered grain is certified
        trustworthy and disagrees with the catalog's declared key is marked
        `proven=True` — a provable double-counting defect in the catalog.
    """
    catalog = _normalize_catalog(existing)
    model_tables = {pt.table: pt for pt in proposed.tables}

    diffs: list[TableDiff] = []
    matched: list[str] = []

    for name in sorted(model_tables.keys() - catalog.keys()):
        diffs.append(TableDiff(name, "only_in_model",
                               "discovered but absent from the catalog"))
    for name in sorted(catalog.keys() - model_tables.keys()):
        diffs.append(TableDiff(name, "only_in_catalog",
                               "declared in the catalog but not discovered"))

    for name in sorted(model_tables.keys() & catalog.keys()):
        pt = model_tables[name]
        ct = catalog[name]
        matched.append(name)

        model_grain = tuple(pt.grain)
        if set(model_grain) != set(ct.key):
            # certified grain vs declared key: PROVEN when our grain is trustworthy.
            proven = bool(pt.grain_trustworthy)
            diffs.append(TableDiff(
                name, "grain_drift",
                f"discovered grain {list(model_grain)} != catalog key {list(ct.key)}"
                + (" (certified — the catalog key double-counts)" if proven else ""),
                proven=proven,
            ))

        model_measures = {m.column for m in pt.measures}
        for col in sorted(model_measures - ct.measures):
            diffs.append(TableDiff(name, "measure_only_in_model",
                                   f"measure {col!r} discovered but not in the catalog"))
        for col in sorted(ct.measures - model_measures):
            diffs.append(TableDiff(name, "measure_only_in_catalog",
                                   f"measure {col!r} in the catalog but not discovered"))

    return Reconciliation(
        matched_tables=tuple(matched),
        diffs=tuple(diffs),
        n_tables_model=len(model_tables),
        n_tables_catalog=len(catalog),
    )
