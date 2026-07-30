"""Cube (cube.dev) semantic-layer reader + emitter — the third dialect.

Cube's YAML data model is a `cubes:` list (+ an optional `views:` list). A cube's
identity is a **dimension marked `primary_key: true`** (composite = several so
marked); joins live in the cube's `joins:` block, each `{name, relationship,
sql}` where `relationship` is `one_to_one` / `one_to_many` / `many_to_one` and
the direction is written from the declaring cube's point of view. So — exactly as
with MetricFlow entities and OSI relationships — the join key is the identity the
metrics silently assume.

- **consume** a Cube model to learn the declared keys + joins (`parse_cube_models`,
  `cube_join_keys`) — "what to resolve";
- **emit** valid Cube YAML declaring the GoldenMatch-resolved key as a conformed
  join: a crosswalk cube keyed on the source PK + a join from the source cube to
  it (`emit_cube_from_crosswalk`, bridging wedge B), with provenance in `meta`.

Schema-faithful to the current snake_case YAML data model (member refs use single
braces `{CUBE}.fk = {other.pk}`; the `${...}` template form is JS-models only).
Legacy relationship aliases (`has_one`/`has_many`/`belongs_to` and their camelCase
spellings) are read and normalized to the modern enum on emit. `parse_cube_models(
emit_cube_yaml(...))` round-trips. Library-only, advisory, parity-free.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from goldenmatch.semantic.metricflow import _load

# relationship enum + legacy aliases -> modern form (direction: declaring cube first)
_RELATIONSHIPS = frozenset({"one_to_one", "one_to_many", "many_to_one"})
_RELATIONSHIP_ALIASES = {
    "has_one": "one_to_one", "hasone": "one_to_one",
    "has_many": "one_to_many", "hasmany": "one_to_many",
    "belongs_to": "many_to_one", "belongsto": "many_to_one",
}
# Member refs are asymmetric: `{CUBE}.col` (self, column OUTSIDE the braces) and
# `{other_cube.member}` (column INSIDE). Capture the braced token + an optional
# trailing `.column`.
_MEMBER_REF = re.compile(r"\{([^}]+)\}(?:\.(\w+))?")


# --- model -------------------------------------------------------------------


@dataclass
class CubeDimension:
    name: str
    sql: str                             # the SQL expression — usually the column
    type: str = "string"                 # string / number / time / boolean / geo
    primary_key: bool = False


@dataclass
class CubeMeasure:
    name: str
    type: str = "count"                  # count / sum / avg / count_distinct / ...
    sql: str | None = None               # not required for `count`


@dataclass
class CubeJoin:
    name: str                            # the joined cube's name
    relationship: str                    # one_to_one / one_to_many / many_to_one
    sql: str                             # the ON condition, e.g. "{CUBE}.fk = {other.id}"


@dataclass
class Cube:
    name: str
    sql_table: str | None = None
    sql: str | None = None               # a SELECT used instead of a table
    dimensions: list[CubeDimension] = field(default_factory=list)
    measures: list[CubeMeasure] = field(default_factory=list)
    joins: list[CubeJoin] = field(default_factory=list)
    meta: dict[str, Any] | None = None

    @property
    def primary_key(self) -> list[str]:
        """The cube's (possibly composite) primary key — the dimensions marked
        `primary_key: true`, in declaration order."""
        return [d.name for d in self.dimensions if d.primary_key]


# --- parse (consume) ---------------------------------------------------------


def _normalize_relationship(rel: Any) -> str:
    r = str(rel or "").strip()
    key = r.lower().replace("-", "_")
    if key in _RELATIONSHIPS:
        return key
    return _RELATIONSHIP_ALIASES.get(key, r)


def _parse_dimension(d: dict[str, Any]) -> CubeDimension:
    return CubeDimension(
        name=str(d.get("name", "")),
        sql=str(d.get("sql", d.get("name", ""))),
        type=str(d.get("type", "string")),
        primary_key=bool(d.get("primary_key", False)),
    )


def _parse_measure(m: dict[str, Any]) -> CubeMeasure:
    return CubeMeasure(
        name=str(m.get("name", "")),
        type=str(m.get("type", "count")),
        sql=m.get("sql"),
    )


def _parse_join(j: dict[str, Any]) -> CubeJoin:
    return CubeJoin(
        name=str(j.get("name", "")),
        relationship=_normalize_relationship(j.get("relationship")),
        sql=str(j.get("sql", "")),
    )


def parse_cube_models(source: str | Any) -> list[Cube]:
    """Parse a Cube data model (path, YAML string, or dict) into `Cube`s.

    Reads the top-level `cubes:` list; `views:` are a consumption re-projection
    (no keys/joins of their own) and are intentionally not modeled here.
    """
    data = _load(source)
    out: list[Cube] = []
    for c in data.get("cubes") or []:
        if not isinstance(c, dict):
            continue
        out.append(Cube(
            name=str(c.get("name", "")),
            sql_table=c.get("sql_table"),
            sql=c.get("sql"),
            dimensions=[_parse_dimension(d) for d in (c.get("dimensions") or []) if isinstance(d, dict)],
            measures=[_parse_measure(m) for m in (c.get("measures") or []) if isinstance(m, dict)],
            joins=[_parse_join(j) for j in (c.get("joins") or []) if isinstance(j, dict)],
            meta=c.get("meta"),
        ))
    return out


def _member_refs(sql: str) -> list[tuple[str | None, str]]:
    """Pull `{CUBE}.col` / `{other.col}` member refs out of a join `sql`, as
    `(cube_or_None, column)` — `{CUBE}` (self) yields `(None, col)`."""
    refs: list[tuple[str | None, str]] = []
    for inside, after in _MEMBER_REF.findall(sql):
        token = inside.strip()
        if "." in token:                      # `{other_cube.member}` form
            head, _, col = token.rpartition(".")
            head = head.strip()
            refs.append((None if head == "CUBE" else head, col.strip()))
        elif after:                           # `{CUBE}.col` / `{cube}.col` form
            refs.append((None if token == "CUBE" else token, after.strip()))
    return refs


def cube_join_keys(cube: Cube) -> list[dict[str, Any]]:
    """The keys a cube's joins ride on — the identity its metrics depend on. One
    entry per join: `{from_cube, to_cube, relationship, from_columns, to_columns,
    sql}`. Columns are best-effort parsed from the join `sql` member refs — this
    is what GoldenMatch should resolve/certify (metric-aware)."""
    out: list[dict[str, Any]] = []
    for j in cube.joins:
        from_cols: list[str] = []
        to_cols: list[str] = []
        for ref_cube, col in _member_refs(j.sql):
            if ref_cube is None or ref_cube == cube.name:
                from_cols.append(col)
            elif ref_cube == j.name:
                to_cols.append(col)
        out.append({
            "from_cube": cube.name,
            "to_cube": j.name,
            "relationship": j.relationship,
            "from_columns": from_cols,
            "to_columns": to_cols,
            "sql": j.sql,
        })
    return out


# --- emit (produce) ----------------------------------------------------------


def emit_cube_dimension(d: CubeDimension) -> dict[str, Any]:
    out: dict[str, Any] = {"name": d.name, "sql": d.sql, "type": d.type}
    if d.primary_key:
        out["primary_key"] = True
    return out


def emit_cube_measure(m: CubeMeasure) -> dict[str, Any]:
    out: dict[str, Any] = {"name": m.name, "type": m.type}
    if m.sql is not None:
        out["sql"] = m.sql
    return out


def emit_cube_join(j: CubeJoin) -> dict[str, Any]:
    return {"name": j.name, "relationship": _normalize_relationship(j.relationship), "sql": j.sql}


def emit_cube(cube: Cube) -> dict[str, Any]:
    out: dict[str, Any] = {"name": cube.name}
    if cube.sql_table:
        out["sql_table"] = cube.sql_table
    elif cube.sql:
        out["sql"] = cube.sql
    if cube.joins:
        out["joins"] = [emit_cube_join(j) for j in cube.joins]
    if cube.dimensions:
        out["dimensions"] = [emit_cube_dimension(d) for d in cube.dimensions]
    if cube.measures:
        out["measures"] = [emit_cube_measure(m) for m in cube.measures]
    if cube.meta:
        out["meta"] = cube.meta
    return out


def emit_cube_yaml(cubes: Cube | list[Cube]) -> str:
    """Render `Cube`(s) as a valid Cube data model — a top-level `cubes:` list.
    Round-trips through `parse_cube_models`."""
    if isinstance(cubes, Cube):
        cubes = [cubes]
    doc = {"cubes": [emit_cube(c) for c in cubes]}
    return yaml.safe_dump(doc, sort_keys=False, default_flow_style=False)


def emit_cube_from_crosswalk(
    crosswalk: Any,
    *,
    source_cube: str,
    source_join_column: str | None = None,
    crosswalk_cube: str = "crosswalk",
    crosswalk_sql_table: str | None = None,
    resolved_field: str | None = None,
    certificate: Any = None,
) -> str:
    """Emit valid Cube YAML for a `ResolvedCrosswalk` (wedge B): a crosswalk cube
    keyed on `source_pk` (a `primary_key` dimension), plus a `many_to_one` join
    from the source cube to it — so metrics group by the conformed
    `resolved_entity_id`. GoldenMatch provenance (+ an optional key-integrity
    certificate) rides in the crosswalk cube's `meta.goldenmatch`.
    """
    src_pk = getattr(crosswalk, "source_pk_column", "source_pk")
    resolved_field = resolved_field or getattr(crosswalk, "resolved_key", "resolved_entity_id")
    join_col = source_join_column or src_pk

    xw = Cube(
        name=crosswalk_cube,
        sql_table=crosswalk_sql_table,
        dimensions=[
            CubeDimension(src_pk, src_pk, type="string", primary_key=True),
            CubeDimension("source", "source", type="string"),
            CubeDimension(resolved_field, resolved_field, type="string"),
        ],
    )

    gm: dict[str, Any] = {
        "generated_by": "goldenmatch.semantic",
        "resolved_key": resolved_field,
        "n_records": getattr(crosswalk, "n_records", None),
        "n_entities": getattr(crosswalk, "n_entities", None),
        "reduction_ratio": round(getattr(crosswalk, "reduction_ratio", 0.0), 6),
    }
    if certificate is not None:
        gm["key_integrity"] = {
            "uniqueness_estimate": getattr(certificate, "estimate", None),
            "max_fan_out": getattr(certificate, "max_fan_out", None),
            "undercount_estimate": getattr(certificate, "undercount_estimate", None),
        }
    xw.meta = {"goldenmatch": gm}

    # The source cube declares the conformed join to the crosswalk (many source
    # rows -> one crosswalk row on the source PK).
    src = Cube(
        name=source_cube,
        joins=[CubeJoin(
            name=crosswalk_cube,
            relationship="many_to_one",
            sql=f"{{CUBE}}.{join_col} = {{{crosswalk_cube}.{src_pk}}}",
        )],
    )
    return emit_cube_yaml([src, xw])


# --- bridge: certify the keys a Cube model joins on (metric-aware, wedge A) ----


def certify_cube_joins(model: list[Cube] | str | Any, frames: dict[str, Any]) -> list[dict[str, Any]]:
    """For each join in a Cube model, certify the ONE-side key it joins on (the
    referenced primary key) using wedge A — certifying exactly the identity the
    metrics depend on. The one-side depends on the join's direction: for
    `many_to_one` / `one_to_one` it is the joined (`to`) cube, but for
    `one_to_many` the declaring (`from`) cube is the one-side, so its key is what
    must be unique (certifying the many-side FK would spuriously flag a fan-out).
    `frames` maps cube name -> table; a join whose one-side frame is absent or
    whose one-side columns can't be parsed is skipped.

    Returns `[{from_cube, to_cube, key, certificate}]`.
    """
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    cubes = model if isinstance(model, list) else parse_cube_models(model)
    out: list[dict[str, Any]] = []
    for cube in cubes:
        for jk in cube_join_keys(cube):
            if jk["relationship"] == "one_to_many":
                one_cube, one_columns = jk["from_cube"], jk["from_columns"]
            else:  # many_to_one / one_to_one → the joined (to) cube is the one-side
                one_cube, one_columns = jk["to_cube"], jk["to_columns"]
            df = frames.get(one_cube)
            if df is None or not one_columns:
                continue
            cert = certify_key_integrity(df, key=one_columns)
            out.append({
                "from_cube": jk["from_cube"],
                "to_cube": jk["to_cube"],
                "key": list(one_columns),
                "certificate": cert,
            })
    return out
