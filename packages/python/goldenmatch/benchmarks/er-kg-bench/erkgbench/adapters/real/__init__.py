"""Real-framework adapters, each behind an optional dependency.

available_real_adapters() returns only the adapters whose optional deps are
installed; a missing dep degrades to "skipped" (the adapter is absent), never a
hard failure -- mirrors the keyed-row pattern.
"""
from __future__ import annotations


def available_real_adapters() -> list:
    out = []
    # GraphRAG + Cognee are `validated` reproductions of pure exact-key rules (no
    # library call, no optional dep), grouped here with the other real rows. Own
    # try-block each so one import error can't suppress the rest.
    try:
        from erkgbench.adapters.real.exact_family import RealGraphRAG
        out.append(RealGraphRAG())
    except ImportError:
        pass
    try:
        from erkgbench.adapters.real.exact_family import RealCognee
        out.append(RealCognee())
    except ImportError:
        pass
    try:
        from erkgbench.adapters.real.neo4j_graphrag import RealNeo4jGraphRAGFuzzy
        out.append(RealNeo4jGraphRAGFuzzy())
    except ImportError:
        pass  # neo4j-graphrag not installed -> skip this real row
    # The exact resolver is a `validated` model of the library's Cypher (no library
    # call), so it has no optional dep -- still grouped here with neo4j-graphrag's
    # resolvers for presentation. Own try-block so one failure can't suppress the other.
    try:
        from erkgbench.adapters.real.neo4j_graphrag import RealNeo4jGraphRAGExact
        out.append(RealNeo4jGraphRAGExact())
    except ImportError:
        pass
    # The spaCy resolver needs both `spacy` AND the vector model (en_core_web_lg).
    # The adapter module imports fine without them (the helper lazy-imports), so we
    # PROBE with resolve([]) -- it constructs SpaCySemanticMatchResolver, which loads
    # the model (auto_download_spacy_model=False -> raises if absent). Catch broadly so
    # a missing spacy/model degrades to "skipped", never a hard fail or implicit download.
    try:
        from erkgbench.adapters.real.neo4j_graphrag import RealNeo4jGraphRAGSpaCy
        adapter = RealNeo4jGraphRAGSpaCy()
        adapter.resolve([])  # probe the model loads now, not mid-board
        out.append(adapter)
    except Exception:  # noqa: BLE001 - missing spacy/model -> skip this real row
        pass
    return out
