"""Malloy (malloydata.dev) semantic-model reader + emitter — the BI dialect.

Malloy is Google's open semantic modeling language. Its unit is a **`source`**
(`source: users is table('users') { primary_key: id }`), whose identity is the
declared **`primary_key`**; joins ride on it via `join_one:` / `join_many:` /
`join_cross:` with an `on` condition. So — exactly as with Cube's `primary_key` +
joins and MetricFlow entities — the join key is the identity the metrics silently
assume, and it is what `certify_key_integrity` must check.

- **consume** a Malloy model to learn the declared primary keys + the keys each
  join rides on (`parse_malloy_models`, `malloy_join_keys`) — "what to resolve".
  Reads a structured `{sources: [...]}` projection (dict / YAML) OR raw `.malloy`
  DSL text (a focused parser for the declaration constructs);
- **certify** (bridge to wedge A) the one-side key of each join
  (`certify_malloy_joins`) — certifying exactly the identity the metrics depend on;
- **emit** (bridge to wedge B) a Malloy source declaring the GoldenMatch-resolved
  key as a `primary_key` + a `join_one` to it (`emit_malloy_from_crosswalk`).
  `parse_malloy_models(emit_malloy_source(...))` round-trips.

Declaration only, advisory, parity-free (it plugs into the existing
`certify_semantic_model` dialect dispatch on a top-level `sources:` key). The DSL
reader handles the common well-formed shape; the structured `{sources: ...}` form
is the exact, dependency-free path the front door detects.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# join_one -> to-one (joined source is the one-side), join_many -> to-many
# (declaring source is the one-side), join_cross -> cross (no key to certify).
_JOIN_KINDS = {"join_one": "one", "join_many": "many", "join_cross": "cross"}
# `source.column` dotted refs inside an `on` condition.
_REF = re.compile(r"\b(\w+)\.(\w+)\b")


# --- model -------------------------------------------------------------------


@dataclass
class MalloyJoin:
    name: str                            # the joined source's name
    relationship: str                    # "one" / "many" / "cross"
    on: str = ""                         # the ON condition, e.g. "a.id = b.a_id"


@dataclass
class MalloySource:
    name: str
    table: str | None = None             # table('...') target
    base: str | None = None              # an extended/refined source name
    primary_key: list[str] = field(default_factory=list)  # declared primary_key
    joins: list[MalloyJoin] = field(default_factory=list)
    measures: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)


@dataclass
class MalloyModel:
    sources: list[MalloySource] = field(default_factory=list)

    def source_by_name(self, name: str) -> MalloySource | None:
        for s in self.sources:
            if s.name == name:
                return s
        return None


# --- parse (consume) ---------------------------------------------------------


def _normalize_pk(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return []


def _parse_join_dict(j: dict[str, Any]) -> MalloyJoin:
    rel = str(j.get("relationship", "one")).strip().lower()
    # accept "join_one" / "one_to_many" style too
    rel = _JOIN_KINDS.get(rel, rel)
    if rel in ("one_to_many", "many"):
        rel = "many"
    elif rel in ("many_to_one", "one_to_one", "one"):
        rel = "one"
    elif "cross" in rel:
        rel = "cross"
    return MalloyJoin(
        name=str(j.get("name", "")).strip(),
        relationship=rel,
        on=str(j.get("on", j.get("sql", ""))).strip(),
    )


def _parse_source_dict(s: dict[str, Any]) -> MalloySource:
    return MalloySource(
        name=str(s.get("name", "")).strip(),
        table=(str(s["table"]).strip() if s.get("table") is not None else None),
        base=(str(s["base"]).strip() if s.get("base") is not None else None),
        primary_key=_normalize_pk(s.get("primary_key")),
        joins=[_parse_join_dict(j) for j in (s.get("joins") or []) if isinstance(j, dict)],
        measures=[str(m).strip() for m in (s.get("measures") or []) if str(m).strip()],
        dimensions=[str(d).strip() for d in (s.get("dimensions") or []) if str(d).strip()],
    )


def _looks_like_dsl(text: str) -> bool:
    return bool(re.search(r"(^|\n)\s*source:\s*\w+\s+is\b", text))


def parse_malloy_models(source: str | Any) -> MalloyModel:
    """Parse a Malloy model into a `MalloyModel`.

    Accepts a structured `{sources: [...]}` projection (dict / YAML string / path)
    OR raw Malloy `.malloy` DSL text (or a path to it). The structured form is the
    exact, dependency-free path; the DSL reader handles the common declaration
    shape (`source: N is table('t') { primary_key: k; join_one: J on ...; measure:
    m is ...; dimension: d is ... }`).
    """
    if isinstance(source, dict):
        data = source
    elif isinstance(source, (str, Path)) and os.path.exists(source):
        text = Path(source).read_text(encoding="utf-8")
        return _parse_dsl(text) if (str(source).endswith(".malloy") or _looks_like_dsl(text)) \
            else _from_structured(yaml.safe_load(text) or {})
    elif isinstance(source, str) and _looks_like_dsl(source):
        return _parse_dsl(source)
    else:
        data = yaml.safe_load(str(source)) or {}
    return _from_structured(data)


def _from_structured(data: dict[str, Any]) -> MalloyModel:
    return MalloyModel(
        sources=[_parse_source_dict(s) for s in (data.get("sources") or []) if isinstance(s, dict)]
    )


def _match_braces(text: str, open_idx: int) -> int:
    """Index of the `}` matching the `{` at `open_idx` (or len(text) if unbalanced)."""
    depth = 0
    for i in range(open_idx, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return len(text)


_SOURCE_HEAD = re.compile(r"source:\s*(\w+)\s+is\s+([^\{\n]+?)\s*(\{|$|\n)", re.MULTILINE)


def _parse_dsl(text: str) -> MalloyModel:
    """A focused parser for well-formed Malloy source declarations. Extracts each
    `source:`'s name, `table('...')`/base, `primary_key`, joins and measures/
    dimensions. Not a full Malloy grammar — the declaration constructs only."""
    sources: list[MalloySource] = []
    for m in _SOURCE_HEAD.finditer(text):
        name = m.group(1)
        head = m.group(2).strip()
        table = None
        base = None
        tbl = re.search(r"table\(\s*['\"]([^'\"]+)['\"]\s*\)", head)
        if tbl:
            table = tbl.group(1)
        else:  # `is other_source` / `is other_source extend`
            base = head.split()[0] if head else None
        body = ""
        if m.group(3) == "{":
            close = _match_braces(text, m.end() - 1)
            body = text[m.end():close]
        src = MalloySource(name=name, table=table, base=base)
        pk = re.search(r"primary_key:\s*(\w+)", body)
        if pk:
            src.primary_key = [pk.group(1)]
        for jm in re.finditer(
            r"(join_one|join_many|join_cross):\s*(\w+)(?:\s+is\s+[^\n]*?)?"
            r"(?:\s+(?:on|with)\s+([^\n]+))?(?=\n|$)",
            body,
        ):
            src.joins.append(MalloyJoin(
                name=jm.group(2),
                relationship=_JOIN_KINDS[jm.group(1)],
                on=(jm.group(3) or "").strip(),
            ))
        src.measures = [mm.group(1) for mm in re.finditer(r"measure:\s*(\w+)\s+is\b", body)]
        src.dimensions = [dm.group(1) for dm in re.finditer(r"dimension:\s*(\w+)\s+is\b", body)]
        sources.append(src)
    return MalloyModel(sources=sources)


def _on_refs(on: str) -> list[tuple[str, str]]:
    """`(source, column)` pairs from an `on` condition's dotted refs."""
    return [(src, col) for src, col in _REF.findall(on or "")]


def malloy_join_keys(model: MalloyModel | str | Any) -> list[dict[str, Any]]:
    """The keys each join rides on — the identity its metrics depend on. One entry
    per join: `{from_source, to_source, relationship, from_columns, to_columns,
    on}`, columns best-effort parsed from the `on` condition. `join_cross` has no
    key and is emitted with empty columns."""
    model = model if isinstance(model, MalloyModel) else parse_malloy_models(model)
    out: list[dict[str, Any]] = []
    for src in model.sources:
        for j in src.joins:
            from_cols: list[str] = []
            to_cols: list[str] = []
            for ref_src, col in _on_refs(j.on):
                if ref_src == src.name:
                    from_cols.append(col)
                elif ref_src == j.name:
                    to_cols.append(col)
            out.append({
                "from_source": src.name,
                "to_source": j.name,
                "relationship": j.relationship,
                "from_columns": from_cols,
                "to_columns": to_cols,
                "on": j.on,
            })
    return out


# --- emit (produce) ----------------------------------------------------------


def emit_malloy_source(model: MalloyModel | MalloySource | list[MalloySource]) -> str:
    """Render `MalloySource`(s) as Malloy DSL text. Round-trips through
    `parse_malloy_models`."""
    if isinstance(model, MalloyModel):
        sources = model.sources
    elif isinstance(model, MalloySource):
        sources = [model]
    else:
        sources = list(model)
    blocks: list[str] = []
    for s in sources:
        base = f"table('{s.table}')" if s.table else (s.base or "table('')")
        lines = [f"source: {s.name} is {base} {{"]
        if s.primary_key:
            lines.append(f"  primary_key: {s.primary_key[0]}")
        for j in s.joins:
            kind = {"one": "join_one", "many": "join_many", "cross": "join_cross"}.get(
                j.relationship, "join_one")
            on = f" on {j.on}" if (j.on and kind != "join_cross") else ""
            lines.append(f"  {kind}: {j.name}{on}")
        lines.append("}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n"


def emit_malloy_from_crosswalk(
    crosswalk: Any,
    *,
    source_name: str,
    source_join_column: str | None = None,
    crosswalk_source: str = "crosswalk",
    crosswalk_table: str | None = None,
    resolved_field: str | None = None,
    certificate: Any = None,
) -> str:
    """Emit Malloy DSL for a `ResolvedCrosswalk` (wedge B): a crosswalk source keyed
    on `source_pk` (a `primary_key`), plus a `join_one` from the source to it — so
    metrics group by the conformed `resolved_entity_id`. Returned as a
    `(malloy_text, provenance)` tuple: Malloy has no metadata slot in this subset,
    so GoldenMatch provenance (+ optional certificate verdict) is returned
    alongside rather than embedded.
    """
    src_pk = getattr(crosswalk, "source_pk_column", "source_pk")
    resolved_field = resolved_field or getattr(crosswalk, "resolved_key", "resolved_entity_id")
    join_col = source_join_column or src_pk

    xw = MalloySource(name=crosswalk_source, table=crosswalk_table, primary_key=[src_pk])
    src = MalloySource(
        name=source_name,
        joins=[MalloyJoin(
            name=crosswalk_source, relationship="one",
            on=f"{source_name}.{join_col} = {crosswalk_source}.{src_pk}",
        )],
    )
    provenance: dict[str, Any] = {
        "generated_by": "goldenmatch.semantic",
        "resolved_key": resolved_field,
        "n_records": getattr(crosswalk, "n_records", None),
        "n_entities": getattr(crosswalk, "n_entities", None),
        "reduction_ratio": round(getattr(crosswalk, "reduction_ratio", 0.0), 6),
    }
    if certificate is not None:
        from goldenmatch.core.key_integrity_certificate import certificate_verdict

        provenance["key_integrity"] = certificate_verdict(certificate)
    return emit_malloy_source([src, xw]), provenance


# --- bridge: certify the keys a Malloy model joins on (metric-aware, wedge A) --


def certify_malloy_joins(
    model: MalloyModel | str | Any, frames: dict[str, Any], *, resolve: bool = False,
    roles: Any = None,
) -> list[dict[str, Any]]:
    """For each join in a Malloy model, certify the ONE-side key it joins on via
    wedge A. The one-side depends on direction: for `join_one` the joined (`to`)
    source is the one-side, but for `join_many` the declaring (`from`) source is,
    so its key is what must be unique. `join_cross` has no key and is skipped.
    `frames` maps source name -> table; a join whose one-side frame is absent or
    whose one-side columns can't be parsed is skipped. `resolve=True` also measures
    fragmentation / undercount via ER (fail-open); pass `roles` (a
    `SemanticFieldRoles`) to make that ER metric-aware.

    Returns `[{from_source, to_source, key, certificate}]`.
    """
    from goldenmatch.semantic.blocking import _frame_columns, metric_aware_attributes
    from goldenmatch.semantic.key_integrity import certify_key_integrity

    model = model if isinstance(model, MalloyModel) else parse_malloy_models(model)
    out: list[dict[str, Any]] = []
    for jk in malloy_join_keys(model):
        if jk["relationship"] == "cross":
            continue
        if jk["relationship"] == "many":
            one_source, one_columns = jk["from_source"], jk["from_columns"]
        else:  # one -> the joined (to) source is the one-side
            one_source, one_columns = jk["to_source"], jk["to_columns"]
        # fall back to the declared primary_key of the one-side source
        if not one_columns:
            s = model.source_by_name(one_source)
            one_columns = list(s.primary_key) if s is not None else []
        df = frames.get(one_source)
        if df is None or not one_columns:
            continue
        attributes = (
            metric_aware_attributes(roles, _frame_columns(df))
            if (resolve and roles is not None) else None
        )
        cert = certify_key_integrity(
            df, key=one_columns, resolve=resolve, attributes=attributes
        )
        out.append({
            "from_source": jk["from_source"],
            "to_source": jk["to_source"],
            "key": list(one_columns),
            "certificate": cert,
        })
    return out
