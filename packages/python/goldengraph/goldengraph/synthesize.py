"""LLM answer synthesis over a retrieved subgraph — local + global (map-reduce).

Local: one subgraph → one answer. Global: per-community summaries (map) combined
into one answer (reduce) — the GraphRAG global mode over SP3 communities. Budget
enforcement lives in the `LLMClient` impl (the protocol returns text, not usage);
the caller (`ask`) pre-emptively caps community count.
"""

from __future__ import annotations

from .llm import LLMClient


def _format_subgraph(view: dict) -> str:
    ents = "; ".join(
        f"{e['entity_id']}:{e['canonical_name']}({e['typ']})" for e in view["entities"]
    )
    edges = "; ".join(
        f"{e['subj']} -{e['predicate']}-> {e['obj']}" for e in view["edges"]
    )
    return f"Entities: {ents}\nRelationships: {edges}"


_LOCAL_PROMPT = (
    "Answer the question using ONLY this knowledge subgraph; if it is "
    "insufficient, say so.\nQuestion: {q}\n{sub}"
)
_MAP_PROMPT = "Summarize this community as it bears on the question.\nQuestion: {q}\n{sub}"
_REDUCE_PROMPT = (
    "Answer the question by combining these community summaries.\n"
    "Question: {q}\nSummaries:\n{summaries}"
)


def synthesize_local(query: str, subgraph: dict, llm: LLMClient) -> str:
    return llm.complete(_LOCAL_PROMPT.format(q=query, sub=_format_subgraph(subgraph)))


def synthesize_global(query: str, community_views: list[dict], llm: LLMClient) -> str:
    summaries = [
        llm.complete(_MAP_PROMPT.format(q=query, sub=_format_subgraph(v)))
        for v in community_views
    ]
    return llm.complete(
        _REDUCE_PROMPT.format(q=query, summaries="\n".join(summaries))
    )
