"""Semantic-model discovery orchestrator (Phase 5) — the front door.

`discover_semantic_model(tables)` assembles the four discovery phases into a single
DRAFT semantic model where **every key is already graded by the certifier**:

  1. `discover_keys` per table  → the grain of each table (Phase 1).
  2. `discover_entity_types`     → which tables are surfaces of the same real thing (Phase 2).
  3. `discover_joins`            → the certified foreign-key graph (Phase 3).
  4. `discover_measures`         → grain-gated measures + dimensions per table (Phase 4).

The draft is emitted through the EXISTING dialect emitters (MetricFlow for this slice)
and then re-certified end-to-end with `certify_semantic_model`, so the deliverable is a
normal MetricFlow file plus a verdict-rich certification report — the same shared
`certification_report_dict` shape the certify surface emits. Nothing auto-ships: the
human reviews the graded draft in their catalog.

The reason to trust this over a pure-LLM proposal is not that the guesses are cleverer —
it's that each guess is PROVEN against the data before you see it. Design:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from goldenmatch.semantic.discovery.entities import EntityType, discover_entity_types
from goldenmatch.semantic.discovery.joins import JoinCandidate, discover_joins
from goldenmatch.semantic.discovery.keys import KeyCandidate, discover_keys
from goldenmatch.semantic.discovery.measures import (
    Dimension,
    Measure,
    TableMeasures,
    discover_measures,
)

_SUPPORTED_DIALECTS = frozenset({"metricflow"})


@dataclass
class ProposedTable:
    """One table's discovered semantic shape: its certified grain, entity type, and
    grain-gated measures + dimensions."""

    table: str
    entity_type: str | None
    key: KeyCandidate | None
    measures: list[Measure] = field(default_factory=list)
    dimensions: list[Dimension] = field(default_factory=list)

    @property
    def grain(self) -> list[str]:
        return list(self.key.columns) if self.key is not None else []

    @property
    def grain_trustworthy(self) -> bool:
        return bool(self.key is not None and self.key.is_trustworthy)


@dataclass
class ProposedModel:
    """A draft semantic model discovered from source tables, pre-graded by the certifier.

    `yaml` is the emitted (MetricFlow) model; `certification` is the verdict-rich
    `certification_report_dict` over that model — `all_trustworthy` is the headline
    build-gate signal.
    """

    dialect: str
    tables: list[ProposedTable]
    entity_types: list[EntityType]
    joins: list[JoinCandidate]
    yaml: str = ""
    certification: dict[str, Any] = field(default_factory=dict)

    @property
    def all_trustworthy(self) -> bool:
        """True when every certified key in the emitted model is unique at grain."""
        return bool(self.certification.get("all_trustworthy", False))

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable view — the shape the MCP/REST discover surfaces emit."""
        return {
            "dialect": self.dialect,
            "all_trustworthy": self.all_trustworthy,
            "tables": [
                {
                    "table": t.table,
                    "entity_type": t.entity_type,
                    "grain": t.grain,
                    "grain_trustworthy": t.grain_trustworthy,
                    "measures": [
                        {
                            "column": m.column,
                            "aggregations": m.aggregations,
                            "safe_to_sum": m.safe_to_sum,
                        }
                        for m in t.measures
                    ],
                    "dimensions": [
                        {"column": d.column, "kind": d.kind} for d in t.dimensions
                    ],
                }
                for t in self.tables
            ],
            "entity_types": [
                {
                    "name": e.name,
                    "tables": e.tables,
                    "signature": e.signature,
                    "key_by_table": e.key_by_table,
                    "confidence": e.confidence,
                }
                for e in self.entity_types
            ],
            "joins": [
                {
                    "from_table": j.from_table,
                    "from_column": j.from_column,
                    "to_table": j.to_table,
                    "to_column": j.to_column,
                    "relationship": j.relationship,
                    "is_trustworthy": j.is_trustworthy,
                }
                for j in self.joins
            ],
            "certification": self.certification,
        }


def _best_key(candidates: list[KeyCandidate]) -> KeyCandidate | None:
    """The grain for a table: the top-ranked trustworthy single-column key, else the
    top-ranked candidate (which `discover_keys` already sorts best-first), else None."""
    if not candidates:
        return None
    for c in candidates:
        if c.is_trustworthy:
            return c
    return candidates[0]


def discover_semantic_model(
    tables: dict[str, Any],
    *,
    dialect: str = "metricflow",
    resolve: bool = False,
) -> ProposedModel:
    """Discover a draft semantic model from a set of source tables.

    Args:
        tables: `{table_name: table}` — the source tables (any input the certifier and
            profiler accept).
        dialect: the emit dialect. `"metricflow"` for this slice (Cube/OSI emit is a
            follow-on; certification already spans all three).
        resolve: forwarded to join discovery + re-certification — also measures entity
            fragmentation / undercount via ER (fail-open).

    Returns:
        A `ProposedModel` with per-table shape, entity types, the certified join graph,
        the emitted MetricFlow YAML, and the end-to-end certification report. Every key
        is pre-graded; nothing is auto-applied.

    Raises:
        ValueError: on an unsupported emit dialect.
    """
    if dialect not in _SUPPORTED_DIALECTS:
        raise ValueError(
            f"unsupported emit dialect {dialect!r}; supported: {sorted(_SUPPORTED_DIALECTS)}"
        )

    from goldenmatch.semantic import (
        certification_report_dict,
        certify_semantic_model,
        emit_metricflow_yaml,
        emit_semantic_model,
    )

    # Phase 1 — grain per table.
    keys: dict[str, list[KeyCandidate]] = {
        name: discover_keys(t) for name, t in tables.items()
    }
    grains: dict[str, KeyCandidate | None] = {n: _best_key(keys[n]) for n in tables}

    # Phase 2 — entity types (name each table's entity).
    entity_types = discover_entity_types(tables, keys=keys)
    entity_of: dict[str, str] = {}
    for et in entity_types:
        for tbl in et.tables:
            entity_of[tbl] = et.name

    # Phase 3 — certified join graph.
    joins = discover_joins(tables, keys, resolve=resolve)

    # Phase 4 — measures + dimensions per table.
    proposed_tables: list[ProposedTable] = []
    per_table_measures: dict[str, TableMeasures] = {}
    for name, table in tables.items():
        grain = grains[name]
        tm = discover_measures(table, key=grain, table_name=name)
        per_table_measures[name] = tm
        proposed_tables.append(
            ProposedTable(
                table=name,
                entity_type=entity_of.get(name),
                key=grain,
                measures=tm.measures,
                dimensions=tm.dimensions,
            )
        )

    # Phase 5 — emit a draft MetricFlow model (only SUM-safe measures are declared, so
    # the emitted model never scaffolds a double-counting SUM) + re-certify end-to-end.
    models = []
    for pt in proposed_tables:
        if pt.key is None:
            continue
        key_col = pt.key.columns[0]
        safe_measures = [m.column for m in pt.measures if m.safe_to_sum]
        models.append(
            emit_semantic_model(
                pt.table,
                resolved_key=key_col,
                entity_name=pt.entity_type or pt.table,
                measures=safe_measures,
                certificate=pt.key.certificate,
            )
        )

    model_yaml = emit_metricflow_yaml(models) if models else ""
    certification: dict[str, Any] = {}
    if model_yaml:
        report = certify_semantic_model(model_yaml, tables, resolve=resolve)
        certification = certification_report_dict(report)

    return ProposedModel(
        dialect=dialect,
        tables=proposed_tables,
        entity_types=entity_types,
        joins=joins,
        yaml=model_yaml,
        certification=certification,
    )
