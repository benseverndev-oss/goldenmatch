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
    "These questions are usually MULTI-HOP: the answer is reached by chaining several "
    "relationships, and the bridge entities in the middle are often NOT named in the "
    "question. Work it out like this:\n"
    "1. Decompose the question into an ordered chain of sub-questions, where each "
    "sub-question's answer is the subject of the next.\n"
    "2. Resolve each sub-question against the subgraph in turn. Treat the relationship "
    "labels as hints, not exact keys -- if no edge matches a sub-question's wording, "
    "pick the edge whose meaning is closest (the extraction may have phrased the "
    "relation differently). Follow edges in EITHER direction.\n"
    "3. Carry the resolved bridge entity forward to the next sub-question until you "
    "reach the final entity.\n"
    "Show each hop briefly, then end with the final answer entity's name on the last "
    "line, prefixed 'Answer: '. Give your single best answer even if the chain is "
    "partly inferred; only say you cannot answer if the relevant entities are entirely "
    "absent.\n"
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
