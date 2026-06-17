"""Real-framework adapters, each behind an optional dependency.

available_real_adapters() returns only the adapters whose optional deps are
installed; a missing dep degrades to "skipped" (the adapter is absent), never a
hard failure -- mirrors the keyed-row pattern.
"""
from __future__ import annotations


def available_real_adapters() -> list:
    out = []
    try:
        from erkgbench.adapters.real.neo4j_graphrag import RealNeo4jGraphRAGFuzzy
        out.append(RealNeo4jGraphRAGFuzzy())
    except ImportError:
        pass  # neo4j-graphrag not installed -> skip this real row
    return out
