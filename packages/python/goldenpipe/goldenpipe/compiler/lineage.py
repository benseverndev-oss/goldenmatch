"""Host lineage: compute field-level provenance from a compiled pipeline (via the
pure kernel) and render it human-readable."""
from __future__ import annotations

from goldenpipe.compiler.provenance import provenance


def field_lineage(compiled: dict) -> dict:
    return provenance(compiled or {"nodes": [], "edges": []})


def format_lineage(lineage: dict) -> str:
    lines = []
    for f in lineage.get("fields", []):
        parts = []
        if f["checks"]:
            parts.append(f"checks[{','.join(f['checks'])}]")
        if f["transforms"]:
            parts.append(f"transforms[{','.join(f['transforms'])}]")
        roles = [r for r, on in (("blocking-key", f["blocking_key"]), ("scorer-input", f["scorer_input"])) if on]
        if roles:
            parts.append(",".join(roles))
        lines.append(f"{f['column']}: " + " -> ".join(parts) if parts else f"{f['column']}: (no ops)")
    return "\n".join(lines)
