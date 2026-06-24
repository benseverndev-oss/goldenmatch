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
    by_id = {e["entity_id"]: e for e in view["entities"]}

    def _name(i):
        e = by_id.get(i)
        if e is None:
            return str(i)
        nm = e["canonical_name"]
        # Quote literal-value leaves so a chain reads `X -[born on]-> "1929"` and the
        # model can tell a value answer from an entity answer.
        return f'"{nm}"' if str(e.get("typ", "")).startswith("literal:") else nm

    def _label(e):
        typ = str(e.get("typ", ""))
        if typ.startswith("literal:"):
            return f'"{e["canonical_name"]}" ({typ.split(":", 1)[1]} value)'
        return f"{e['canonical_name']} ({typ})"

    ents = "; ".join(_label(e) for e in view["entities"])
    edges = "\n".join(
        f"  {_name(e['subj'])} -[{e['predicate']}]-> {_name(e['obj'])}"
        for e in view["edges"]
    )
    return f"Entities: {ents}\nRelationships (subject -[relation]-> object):\n{edges}"


# The prompt is shared except for the final-answer clause, which is gated on the
# literal-attributes flag so the entity-only path (flag off) stays byte-identical
# to the #1227 prompt -- a relaxed "entity OR literal" instruction must not perturb
# the measured entity-only baseline.
_LOCAL_HEAD = (
    "Answer the question using ONLY the knowledge subgraph below (entities joined by "
    "directed, labelled relationships).\n"
    "These questions are usually MULTI-HOP: the answer is reached by chaining several "
    "relationships, and the bridge entities in the middle are often NOT named in the "
    "question. Work it out like this:\n"
    "1. Decompose the question into an ordered chain of sub-questions, where each "
    "sub-question's answer is the subject of the next.\n"
    "2. START from the anchor entities below (they were retrieved as the most relevant "
    "to the question), then resolve each sub-question against the subgraph in turn. "
    "Treat the relationship labels as hints, not exact keys -- if no edge matches a "
    "sub-question's wording, pick the edge whose meaning is closest (the extraction may "
    "have phrased the relation differently). Follow edges in EITHER direction.\n"
    "3. Carry the resolved bridge entity forward to the next sub-question until you "
    "reach the final entity.\n"
    "4. MIND THE ARROW DIRECTION. An edge 'A -[relation]-> B' means the relation runs "
    "FROM A TO B (e.g. 'Mao -[reported to]-> Politburo' means Mao reported to the "
    "Politburo, so 'who did Mao report to?' is the Politburo, NOT Mao). When the final "
    "hop resolves, the answer is the NEW entity that hop reaches -- the one on the far "
    "end of the answering edge from the entity you carried in. Never answer with the "
    "entity you already held going into the final hop.\n"
    "Anchor entities: {seeds}\n"
)
_ANSWER_ENTITY = (
    "Your final answer is ALWAYS a single entity that appears in the Entities list -- "
    "output its EXACT name, nothing else, on the last line prefixed 'Answer: '. Commit "
    "to the single most plausible entity even if an intermediate hop is uncertain; do "
    "NOT answer with a description, a phrase, or 'cannot answer' unless NOTHING in the "
    "Entities list is even loosely related. Show each hop briefly first.\n"
)
_ANSWER_LITERAL = (
    "Your final answer is a single item from the Entities list -- usually a named "
    "entity, but it MAY be a literal VALUE leaf (shown in quotes: a date, quantity, "
    "rank/ordinal, range, region, or event) when the question asks 'when', 'how much', "
    "'how many', 'where', or 'which rank/position'. Output its EXACT "
    "text without the quotes, nothing else, on the last line prefixed 'Answer: '. "
    "Commit to the single most plausible item even if an intermediate hop is uncertain; "
    "do NOT answer with a free-form description or 'cannot answer' unless NOTHING in the "
    "Entities list is even loosely related. Show each hop briefly first.\n"
)
_LOCAL_TAIL = "Question: {q}\n{sub}"

_LOCAL_PROMPT = _LOCAL_HEAD + _ANSWER_ENTITY + _LOCAL_TAIL
_LOCAL_PROMPT_LITERALS = _LOCAL_HEAD + _ANSWER_LITERAL + _LOCAL_TAIL


def _literals_enabled() -> bool:
    """Mirror of ingest._literal_attrs_enabled (kept local to avoid importing the
    build module into synthesis)."""
    import os

    return os.environ.get("GOLDENGRAPH_LITERAL_ATTRS", "0") not in ("0", "false", "")
_MAP_PROMPT = "Summarize this community as it bears on the question.\nQuestion: {q}\n{sub}"
_REDUCE_PROMPT = (
    "Answer the question by combining these community summaries.\n"
    "Question: {q}\nSummaries:\n{summaries}"
)


def _extract_answer(text: str) -> str:
    """Pull the final answer out of a chain-of-thought completion.

    `_LOCAL_PROMPT` tells the model to show each hop first, then put the final
    answer on the last line prefixed ``Answer: ``. Returning the raw completion
    therefore led with the decomposition scaffold, so the bench scored the gold
    answer against the reasoning text instead of the answer (2026-06-23 MuSiQue
    trace: ``pred='1. What system did Knight Rider come out on? 2. ...'``).

    Take the text after the last ``Answer:`` marker (first line of it); fall back
    to the last non-empty line, then to the stripped completion."""
    if not text or not text.strip():
        return text
    idx = text.lower().rfind("answer:")
    if idx != -1:
        tail = text[idx + len("answer:"):].lstrip()
        first = tail.splitlines()[0].strip() if tail else ""
        if first:
            return first
        # Marker present but empty (model emitted a bare "Answer:") -> a non-answer,
        # not the literal string "Answer:". Return empty rather than the marker.
        return ""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines[-1] if lines else text.strip()


def synthesize_local(
    query: str, subgraph: dict, llm: LLMClient, *, seed_names: list[str] | None = None
) -> str:
    """Synthesize an answer over the retrieved subgraph. `seed_names` are the
    embedding-retrieved anchor entities most relevant to the query -- handed to the
    model so it starts the multi-hop walk at the right place instead of guessing
    among every entity in the ball (the measured SYNTHESIS miss: the answer edge was
    present but the chain went unwalked).

    Returns the parsed final answer (the ``Answer:`` line), NOT the model's full
    chain-of-thought -- see `_extract_answer`."""
    seeds = ", ".join(dict.fromkeys(s for s in (seed_names or []) if s)) or (
        "(none identified -- choose the most relevant entities yourself)"
    )
    prompt = _LOCAL_PROMPT_LITERALS if _literals_enabled() else _LOCAL_PROMPT
    return _extract_answer(
        llm.complete(
            prompt.format(q=query, seeds=seeds, sub=_format_subgraph(subgraph))
        )
    )


def synthesize_global(query: str, community_views: list[dict], llm: LLMClient) -> str:
    summaries = [
        llm.complete(_MAP_PROMPT.format(q=query, sub=_format_subgraph(v)))
        for v in community_views
    ]
    return llm.complete(
        _REDUCE_PROMPT.format(q=query, summaries="\n".join(summaries))
    )
