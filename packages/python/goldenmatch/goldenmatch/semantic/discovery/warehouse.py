"""Warehouse-scale derivation off `information_schema` (PR-17).

A warehouse has hundreds of tables; you can't pull them all into memory just to learn
where to start. `information_schema` gives a cheap CANDIDATE structure — columns, declared
PK/FK — for free. But Snowflake/BigQuery/Redshift do NOT enforce PK/FK constraints, so a
declaration is exactly the kind of guess this arc PROVES against data, never a certificate.

So this module reads the three ANSI `information_schema` relations into a
`WarehouseManifest` (a planning artifact, not a model), `plan_certification` ranks which
tables to pull + certify first, and `discover_from_manifest` pulls the data and runs the
normal certified `discover_semantic_model` — the declared PK is used only to RANK, the
grain is still proven from data. No live DB connection: inputs are pyarrow tables or row
dicts, so it stays testable and credential-free.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

_PK = "PRIMARY KEY"
_FK = "FOREIGN KEY"


def _rows(x: Any) -> list[dict[str, Any]]:
    """Normalize a pyarrow Table / list-of-dicts / None into a list of row dicts."""
    if x is None:
        return []
    to_pylist = getattr(x, "to_pylist", None)
    if callable(to_pylist):
        return list(to_pylist())
    return list(x)


def _truthy_no(val: Any) -> bool:
    """`is_nullable` is the SQL string `'YES'`/`'NO'` (or a bool). True = nullable."""
    if isinstance(val, bool):
        return val
    return str(val).strip().upper() not in {"NO", "FALSE", "0"}


@dataclass(frozen=True)
class CandidateColumn:
    """One column as `information_schema.columns` declares it."""

    name: str
    data_type: str
    nullable: bool

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "data_type": self.data_type, "nullable": self.nullable}


@dataclass(frozen=True)
class CandidateFK:
    """A DECLARED foreign key. `certified` is always False — it's a hypothesis until the
    certifier proves the cardinality against data."""

    columns: tuple[str, ...]
    to_table: str
    to_columns: tuple[str, ...]
    certified: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "to_table": self.to_table,
            "to_columns": list(self.to_columns),
            "certified": self.certified,
        }


@dataclass(frozen=True)
class CandidateTable:
    """One table's DECLARED shape. `declared_pk` is a grain hypothesis, not a certified
    grain — the warehouse does not enforce it."""

    name: str
    columns: tuple[CandidateColumn, ...]
    declared_pk: tuple[str, ...] = ()
    declared_fks: tuple[CandidateFK, ...] = ()
    row_count: int | None = None

    @property
    def has_declared_pk(self) -> bool:
        return bool(self.declared_pk)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "columns": [c.to_dict() for c in self.columns],
            "declared_pk": list(self.declared_pk),
            "declared_fks": [fk.to_dict() for fk in self.declared_fks],
            "row_count": self.row_count,
        }


@dataclass(frozen=True)
class WarehouseManifest:
    """The declared structure of a warehouse — a PLANNING artifact, not a semantic model.
    Nothing here is certified; it tells you where to point the certified pipeline."""

    tables: tuple[CandidateTable, ...] = ()

    def table(self, name: str) -> CandidateTable | None:
        return next((t for t in self.tables if t.name == name), None)

    def to_dict(self) -> dict[str, Any]:
        return {"tables": [t.to_dict() for t in self.tables]}


@dataclass(frozen=True)
class CertifyStep:
    """One table in the certify plan: why it ranks here + why its declarations are unproven."""

    table: str
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {"table": self.table, "reason": self.reason, "warnings": list(self.warnings)}


def _grouped(rows: list[dict[str, Any]], key: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(str(r.get(key)), []).append(r)
    return out


def _ordered_cols(rows: list[dict[str, Any]], col_key: str = "column_name") -> tuple[str, ...]:
    """Columns of a constraint in declared order (`ordinal_position`, else input order)."""
    ordered = sorted(rows, key=lambda r: r.get("ordinal_position") or 0)
    return tuple(str(r[col_key]) for r in ordered)


def read_information_schema(
    columns: Any,
    table_constraints: Any,
    key_column_usage: Any,
    tables: Any = None,
) -> WarehouseManifest:
    """Read the ANSI `information_schema` relations into a `WarehouseManifest`.

    Args:
        columns: `information_schema.columns` rows (`table_name`, `column_name`,
            `data_type`, `ordinal_position`, `is_nullable`).
        table_constraints: `information_schema.table_constraints` rows (`table_name`,
            `constraint_name`, `constraint_type`).
        key_column_usage: `information_schema.key_column_usage` rows (`table_name`,
            `constraint_name`, `column_name`, `ordinal_position`, and for FKs
            `referenced_table_name` / `referenced_column_name`).
        tables: optional `information_schema.tables` rows (`table_name` + `row_count` /
            `table_rows`) — used only for certify-plan ranking.

    Returns:
        A `WarehouseManifest`. Every declared PK/FK is left UNCERTIFIED — it's a
        hypothesis the certified pipeline must prove against data.
    """
    col_rows = _rows(columns)
    tc_rows = _rows(table_constraints)
    kcu_rows = _rows(key_column_usage)
    tbl_rows = _rows(tables)

    cols_by_table = _grouped(col_rows, "table_name")
    kcu_by_constraint = _grouped(kcu_rows, "constraint_name")

    # constraint_name -> (table, type)
    pk_constraints: dict[str, str] = {}   # table -> constraint_name
    fk_constraints: list[tuple[str, str]] = []  # (table, constraint_name)
    for r in tc_rows:
        tbl, cname, ctype = str(r.get("table_name")), str(r.get("constraint_name")), \
            str(r.get("constraint_type", "")).upper()
        if ctype == _PK:
            pk_constraints[tbl] = cname
        elif ctype == _FK:
            fk_constraints.append((tbl, cname))

    fks_by_table: dict[str, list[CandidateFK]] = {}
    for tbl, cname in fk_constraints:
        usage = kcu_by_constraint.get(cname, [])
        if not usage:
            continue
        ordered = sorted(usage, key=lambda r: r.get("ordinal_position") or 0)
        local = tuple(str(r["column_name"]) for r in ordered)
        to_table = str(ordered[0].get("referenced_table_name") or "")
        to_cols = tuple(
            str(r.get("referenced_column_name") or "") for r in ordered
        )
        if to_table:
            fks_by_table.setdefault(tbl, []).append(
                CandidateFK(columns=local, to_table=to_table, to_columns=to_cols)
            )

    row_counts: dict[str, int] = {}
    for r in tbl_rows:
        n = r.get("row_count", r.get("table_rows"))
        if n is not None:
            row_counts[str(r.get("table_name"))] = int(n)

    out: list[CandidateTable] = []
    for tbl in sorted(cols_by_table):
        col_defs = sorted(cols_by_table[tbl], key=lambda r: r.get("ordinal_position") or 0)
        cand_cols = tuple(
            CandidateColumn(
                name=str(r["column_name"]),
                data_type=str(r.get("data_type", "")),
                nullable=_truthy_no(r.get("is_nullable", True)),
            )
            for r in col_defs
        )
        pk_cname = pk_constraints.get(tbl)
        declared_pk = _ordered_cols(kcu_by_constraint.get(pk_cname, [])) if pk_cname else ()
        fks = tuple(sorted(fks_by_table.get(tbl, []),
                           key=lambda fk: (fk.to_table, fk.columns)))
        out.append(
            CandidateTable(
                name=tbl,
                columns=cand_cols,
                declared_pk=declared_pk,
                declared_fks=fks,
                row_count=row_counts.get(tbl),
            )
        )
    return WarehouseManifest(tables=tuple(out))


def plan_certification(manifest: WarehouseManifest) -> list[CertifyStep]:
    """Rank the tables worth pulling + certifying first.

    Order: has-declared-PK (a grain hypothesis to prove), then FK-referenced in-degree
    (spine / dimension tables that many facts point at), then smaller row_count (certifies
    faster), then name. Every step names WHY its declarations are unproven — the warehouse
    doesn't enforce them, so `certify_key_integrity` / `certify_cube_joins` must.
    """
    in_degree: dict[str, int] = {}
    for t in manifest.tables:
        for fk in t.declared_fks:
            in_degree[fk.to_table] = in_degree.get(fk.to_table, 0) + 1

    def _sort_key(t: CandidateTable) -> tuple:
        # larger is better for the first two -> negate; row_count asc with None last.
        return (
            0 if t.has_declared_pk else 1,
            -in_degree.get(t.name, 0),
            t.row_count if t.row_count is not None else float("inf"),
            t.name,
        )

    steps: list[CertifyStep] = []
    for t in sorted(manifest.tables, key=_sort_key):
        deg = in_degree.get(t.name, 0)
        reasons = []
        if t.has_declared_pk:
            reasons.append("has a declared primary key (grain hypothesis to prove)")
        if deg:
            reasons.append(f"referenced by {deg} declared FK(s) (spine/dimension)")
        if t.row_count is not None:
            reasons.append(f"~{t.row_count} rows")
        reason = "; ".join(reasons) or "no declared constraints"

        warnings: list[str] = []
        if t.has_declared_pk:
            warnings.append(
                f"declared PRIMARY KEY {list(t.declared_pk)} is not enforced by the "
                "warehouse — certify_key_integrity must prove it against data"
            )
        for fk in t.declared_fks:
            warnings.append(
                f"declared FOREIGN KEY {list(fk.columns)} -> {fk.to_table} is not "
                "enforced — certify_cube_joins must prove the cardinality"
            )
        steps.append(CertifyStep(table=t.name, reason=reason, warnings=tuple(warnings)))
    return steps


def discover_from_manifest(
    manifest: WarehouseManifest,
    loader: Callable[[str], Any],
    **kwargs: Any,
) -> Any:
    """Pull each candidate table's data (in certify-plan order) and run the normal
    certified `discover_semantic_model`.

    The declared PK/FK are used only to decide WHICH tables to pull and in what order —
    the grain, joins, and measures are all re-derived and PROVEN from the loaded data, so
    a wrong declaration in `information_schema` can never leak into the model. `loader`
    maps a table name to any input the discovery pipeline accepts (e.g. a pyarrow Table).
    """
    from goldenmatch.semantic.discovery.model import discover_semantic_model

    order = [step.table for step in plan_certification(manifest)]
    data = {name: loader(name) for name in order}
    return discover_semantic_model(data, **kwargs)
