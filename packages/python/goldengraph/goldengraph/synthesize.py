"""LLM answer synthesis over a retrieved subgraph — local + global (map-reduce).

Local: one subgraph → one answer. Global: per-community summaries (map) combined
into one answer (reduce) — the GraphRAG global mode over SP3 communities. Budget
enforcement lives in the `LLMClient` impl (the protocol returns text, not usage);
the caller (`ask`) pre-emptively caps community count.
"""

from __future__ import annotations

from .llm import LLMClient


def _format_subgraph(view: dict) -> str:
    """Render the subgraph for the LLM with edges keyed by entity NAME, not id.

    The old ``subj_id -pred-> obj_id`` form forced the model to cross-reference a
    separate id->name list to trace a chain -- the measured multi-hop bottleneck (the
    answer was IN the subgraph but went unread). Name-keyed edges spell the chain out
    directly (``Acme -[made]-> Rocket``), so following a path is a lexical walk, not a
    join."""
    by_id = {e["entity_id"]: e["canonical_name"] for e in view["entities"]}

    def _name(i):
        return by_id.get(i, str(i))

    ents = "; ".join(f"{e['canonical_name']} ({e['typ']})" for e in view["entities"])
    edges = "\n".join(
        f"  {_name(e['subj'])} -[{e['predicate']}]-> {_name(e['obj'])}"
        for e in view["edges"]
    )
    return f"Entities: {ents}\nRelationships (subject -[relation]-> object):\n{edges}"


_LOCAL_PROMPT = (
    "Answer the question using ONLY the knowledge subgraph below (entities joined by "
    "directed, labelled relationships).\n"
    "Reason step by step: find the entity the question starts from, then follow the "
    "named relationship(s) one hop at a time to the entity they lead to. End with the "
    "answer as that target entity's canonical name on the final line, prefixed "
    "'Answer: '. If the subgraph cannot answer the question, say so.\n"
    "Question: {q}\n{sub}"
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
