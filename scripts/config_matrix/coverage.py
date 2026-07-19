"""Natural-language explanation coverage per package.

Reports how many config knobs carry a code-derived explanation -- pydantic
`Field(description=...)`, CLI `help`, MCP tool `.description` -- vs how many are
still bare. Advisory: surfaces the authoring gap so explanations can be filled
in the code (their source of truth) high-value-first. A package hits 100% when
every knob in these introspectable sections is explained.
"""
from __future__ import annotations

import importlib

from .render import _import, _reachable_models


def _schema_cov(spec) -> tuple[int, int]:
    total = explained = 0
    seen = set()
    for root in spec.schema_roots:
        for m in _reachable_models(_import(root)):
            if m in seen:
                continue
            seen.add(m)
            for fi in m.model_fields.values():
                total += 1
                explained += bool(fi.description)
    return total, explained


def _cli_cov(spec) -> tuple[int, int]:
    if not spec.cli_module:
        return 0, 0
    from typer.main import get_command

    group = get_command(importlib.import_module(spec.cli_module).app)
    total = explained = 0

    def walk(cmd):
        nonlocal total, explained
        sub = getattr(cmd, "commands", None)
        if sub:
            for n in sub:
                walk(sub[n])
            return
        for p in cmd.params:
            total += 1
            explained += bool(getattr(p, "help", None))

    for n in group.commands:
        walk(group.commands[n])
    return total, explained


def _mcp_cov(spec) -> tuple[int, int]:
    if not spec.mcp_module:
        return 0, 0
    try:
        tools = importlib.import_module(spec.mcp_module).TOOLS
    except Exception:
        return 0, 0
    from .render import tool_field
    return len(tools), sum(bool(tool_field(t, "description").strip()) for t in tools)


def coverage(spec) -> dict[str, tuple[int, int]]:
    return {"schema": _schema_cov(spec), "cli": _cli_cov(spec), "mcp": _mcp_cov(spec)}


def format_report(name: str, cov: dict[str, tuple[int, int]]) -> str:
    parts, tot, exp = [], 0, 0
    for sec, (t, e) in cov.items():
        if t:
            parts.append(f"{sec} {e}/{t}")
            tot += t
            exp += e
    pct = (100 * exp // tot) if tot else 100
    return f"{name:15} {pct:3}%  ({exp}/{tot})  " + "  ".join(parts)
