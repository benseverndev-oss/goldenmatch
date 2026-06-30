"""LLM answer synthesis over a retrieved subgraph — local + global (map-reduce).

Local: one subgraph → one answer. Global: per-community summaries (map) combined
into one answer (reduce) — the GraphRAG global mode over SP3 communities. Budget
enforcement lives in the `LLMClient` impl (the protocol returns text, not usage);
the caller (`ask`) pre-emptively caps community count.
"""

from __future__ import annotations

import os
import string
from collections import Counter

from .llm import LLMClient


def _synth_samples() -> int:
    """`GOLDENGRAPH_SYNTH_SAMPLES` (default 1 = single call). Non-int / <=1 -> 1 (fail-safe)."""
    try:
        n = int(os.environ.get("GOLDENGRAPH_SYNTH_SAMPLES", "1"))
    except ValueError:
        return 1
    return n if n > 1 else 1


def _synth_temperature() -> float:
    """`GOLDENGRAPH_SYNTH_TEMPERATURE` (default 0.7). Non-float -> 0.7."""
    try:
        return float(os.environ.get("GOLDENGRAPH_SYNTH_TEMPERATURE", "0.7"))
    except ValueError:
        return 0.7


def _vote_key(s: str) -> str:
    """Group-key for voting: lowercase, collapse whitespace, strip surrounding punctuation.
    goldengraph-LOCAL + minimal (cannot import the bench's metrics._normalize); its only job is
    to make 'Firefox' and 'firefox.' vote together."""
    return " ".join(s.lower().split()).strip(string.punctuation + " ")


def _vote_answer(answers: list[str]) -> str:
    """Majority vote over parsed answers. Group by `_vote_key`, pick the key with the most votes
    (tie -> first-seen key), return the FIRST raw answer carrying that key (preserves real casing).
    Empty/blank answers are skipped; no candidates -> ''."""
    cand = [a for a in answers if a and a.strip()]
    if not cand:
        return ""
    keys = [_vote_key(a) for a in cand]
    counts = Counter(keys)
    # max() is stable -> first-seen order breaks ties; iterate keys in first-seen order
    best_key = max(dict.fromkeys(keys), key=lambda k: counts[k])
    return next(a for a, k in zip(cand, keys) if k == best_key)


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
    filled = prompt.format(q=query, seeds=seeds, sub=_format_subgraph(subgraph))
    n = _synth_samples()
    if n > 1 and hasattr(llm, "complete_many"):
        try:
            samples = llm.complete_many(filled, n=n, temperature=_synth_temperature())
        except Exception:
            samples = []
        voted = _vote_answer([_extract_answer(s) for s in samples])
        if voted:
            return voted
        # all samples empty/failed -> single-call fallback below
    return _extract_answer(llm.complete(filled))


_HYBRID_HEAD = (
    "Answer the question using the PASSAGES below as the primary source of truth.\n"
    "These questions are usually MULTI-HOP: the answer is reached by chaining facts "
    "that may be split across several passages, and the bridge entity in the middle is "
    "often NOT named in the question. Use the RELATIONSHIP GRAPH (entities joined by "
    "directed, labelled relationships) as a MAP to connect facts across passages -- it "
    "shows how entities mentioned in different passages link up, so you can carry a "
    "bridge entity from one passage to the next. The PASSAGES are AUTHORITATIVE on the "
    "actual facts and values; the graph is only a navigation aid -- never answer with a "
    "graph entity that the passages contradict.\n"
    "Work it out step by step: decompose the question into an ordered chain of "
    "sub-questions, resolve each against the passages (using the graph to find the next "
    "bridge entity when a passage doesn't state it directly), and carry the result "
    "forward until you reach the answer.\n"
    "Anchor entities (most query-relevant): {seeds}\n"
)
# Free-form answer instruction (UNLIKE synthesize_local's entity-only clause): the
# passages can carry the non-entity answers (dates/numbers/phrases) the extracted
# triples drop, so the hybrid path must not force the answer to be a graph node.
_HYBRID_ANSWER = (
    "Give the SHORTEST exact answer (an entity, name, date, number, or short phrase) "
    "and nothing else on the last line, prefixed 'Answer: '. Show brief reasoning "
    "first if helpful.\n"
)
_HYBRID_TAIL = "Question: {q}\n\nPassages:\n{passages}\n\nRelationship graph:\n{sub}"
_HYBRID_PROMPT = _HYBRID_HEAD + _HYBRID_ANSWER + _HYBRID_TAIL


def synthesize_hybrid(
    query: str,
    subgraph: dict,
    passages: list[str],
    llm: LLMClient,
    *,
    seed_names: list[str] | None = None,
) -> str:
    """Hybrid synthesis: raw retrieved PASSAGES (ground truth) + the graph subgraph
    (a multi-hop navigation scaffold).

    The bench's structural finding was that the KG is a LOSSY intermediate -- the
    extracted triples drop the source-text fidelity that plain text-RAG keeps, which
    is why goldengraph lost to a naive paragraph retriever. This path layers the
    passages back in (recovering fidelity) while keeping the graph as a cross-passage
    multi-hop map. It also FREES the answer from `synthesize_local`'s entity-only
    constraint -- the passages can carry the date/number/phrase answers the triples
    can't. Returns the parsed ``Answer:`` line (see `_extract_answer`)."""
    seeds = ", ".join(dict.fromkeys(s for s in (seed_names or []) if s)) or (
        "(none identified -- choose the most relevant entities yourself)"
    )
    ctx = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages)) or (
        "(no passages retrieved)"
    )
    return _extract_answer(
        llm.complete(
            _HYBRID_PROMPT.format(
                q=query, seeds=seeds, passages=ctx, sub=_format_subgraph(subgraph)
            )
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
