"""LightRAG real entity-merge decision as a benchmark adapter (in-process)."""
from __future__ import annotations

from erkgbench.adapters.base import Record
from erkgbench.real_resolvers import lightrag_clusters


class RealLightRAG:
    name = "LightRAG*"
    defaults = (
        "REAL normalize_extracted_info key (HTML-strip, CJK fold, outer-quote strip, "
        "CASE-SENSITIVE -- no lower/upper) + exact name dict group-by, GLOBAL "
        "(operate.py merge_nodes_and_edges + utils.py; LLM only summarizes "
        "descriptions, never moves a record; graph-store upsert stubbed)"
    )
    deterministic = True
    # real-inproc: runs LightRAG's real key-derivation fn (normalize_extracted_info);
    # the merge decision IS exact equality on that key. Only the graph-store upsert is
    # elided (it cannot change which names are equal). Case-SENSITIVE, unlike the old
    # _norm model -- see FIDELITY.md.
    fidelity = "real-inproc"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return lightrag_clusters(items)
