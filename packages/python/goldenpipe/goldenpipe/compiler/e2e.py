"""End-to-end field lineage (SP3): stitch SP2 field-lineage (pre-match Flow-clean +
matching role, from the IR) with goldenmatch's golden-provenance (post-match
survivorship) into one per-golden-field journey. Host-only — needs the Match output."""
from __future__ import annotations

from goldenpipe.compiler.lineage import field_lineage


def end_to_end_lineage(compiled: dict, golden_provenance: list | None) -> dict:
    """Join SP2 field-lineage with goldenmatch ClusterProvenance on column name.
    Returns {entries, notes}. None/empty provenance -> [] + a note (plan-only view)."""
    if not golden_provenance:
        return {"entries": [], "notes": ["survivorship inactive — use field_lineage(compiled) for the plan-only view"]}
    by_col = {f["column"]: f for f in field_lineage(compiled).get("fields", [])}
    entries = []
    for cp in golden_provenance:
        cid = getattr(cp, "cluster_id", None)
        for col, fp in (getattr(cp, "fields", None) or {}).items():
            plan = by_col.get(col, {})
            entries.append({
                "cluster_id": cid,
                "column": col,
                "value": getattr(fp, "value", None),
                "source_row_id": getattr(fp, "source_row_id", None),
                "strategy": getattr(fp, "strategy", None),
                "survivor_confidence": getattr(fp, "confidence", None),
                "checks": list(plan.get("checks", [])),
                "transforms": list(plan.get("transforms", [])),
                "blocking_key": bool(plan.get("blocking_key", False)),
                "scorer_input": bool(plan.get("scorer_input", False)),
            })
    return {"entries": entries, "notes": []}


def format_end_to_end(result: dict) -> str:
    lines = []
    for e in result.get("entries", []):
        pre = []
        if e["transforms"]:
            pre.append(f"transforms[{','.join(e['transforms'])}]")
        pre.extend(r for r, on in (("blocking-key", e["blocking_key"]), ("scorer-input", e["scorer_input"])) if on)
        pre_s = ("; pre-match " + ", ".join(pre)) if pre else ""
        lines.append(f"cluster {e['cluster_id']} {e['column']} = {e['value']!r} (row {e['source_row_id']} via {e['strategy']}){pre_s}")
    for n in result.get("notes", []):
        lines.append(f"# {n}")
    return "\n".join(lines)
