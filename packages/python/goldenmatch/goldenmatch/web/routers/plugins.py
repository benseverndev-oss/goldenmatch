"""GET /api/v1/plugins (#predefined-merge-plugins surface sync, v1.19.0).

Discovery endpoint exposing the 22 v1.18.2 predefined golden-strategy
plugins plus any user-registered plugins. Each entry includes name,
category, source (builtin / user), and the first line of the merge
docstring.

Used by UI dropdowns to populate available golden-strategy options.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/v1/plugins")


def _build_response(category: str = "all") -> dict[str, list[dict[str, Any]]]:
    """Helper -- module-level so the endpoint can call it AND tests can too."""
    from goldenmatch.plugins.builtin import BUILTIN_PLUGINS
    from goldenmatch.plugins.registry import PluginRegistry

    registry = PluginRegistry.instance()
    registry.discover()
    builtin_names = {cls().name for cls in BUILTIN_PLUGINS}

    def _serialize(plugin_dict: dict, kind: str) -> list[dict]:
        out: list[dict] = []
        for plugin_name, plugin in plugin_dict.items():
            merge_doc = ""
            if hasattr(plugin, "merge") and plugin.merge.__doc__:
                merge_doc = plugin.merge.__doc__.strip().split("\n")[0][:200]
            out.append({
                "name": plugin_name,
                "category": kind,
                "source": (
                    "builtin"
                    if (kind == "golden_strategy" and plugin_name in builtin_names)
                    else "user"
                ),
                "doc": merge_doc,
            })
        # Builtins first, then user; alphabetical within each.
        return sorted(out, key=lambda d: (d["source"] != "builtin", d["name"]))

    result: dict[str, list[dict[str, Any]]] = {}
    kinds = (
        ("golden_strategy", "_golden_strategies"),
        ("scorer", "_scorers"),
        ("transform", "_transforms"),
        ("connector", "_connectors"),
    )
    for kind, attr in kinds:
        if category not in ("all", kind):
            continue
        store_dict = getattr(registry, attr, {})
        result[kind] = _serialize(store_dict, kind)
    return result


@router.get("")
def list_plugins(
    category: str = Query(
        "all",
        description="Filter to one category, or 'all'",
        pattern="^(all|golden_strategy|scorer|transform|connector)$",
    ),
) -> dict[str, list[dict[str, Any]]]:
    """List all registered goldenmatch plugins by category.

    Includes the 22 v1.18.2 predefined golden-strategy plugins
    (numeric / format / business / aggregation) plus any
    user-registered plugins.
    """
    return _build_response(category=category)
