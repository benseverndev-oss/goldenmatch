"""Hand-authored paraphrased-NL questions the heuristic lead-in regexes MISS, each carrying gold
slots. Anchors are real concept surfaces (dataset/concepts.jsonl) and relations are real predicate
ids (engineered.RELATION_SCHEMA). Used by slice 3 to prove heuristic-misses -> LLM(stub)-recovers.
Deterministic; no LLM, no randomness.

The QUESTION text is natural phrasing (avoids "list all entities that"/"how many entities does"/
"as of <D>, what does" so the heuristic lead-ins don't fire); the `relation` field is the underscored
predicate id the tier-2 classifier should recover; `as_of` is the integer date for temporal ones.
"""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.route import QueryIntent


@dataclass(frozen=True)
class Paraphrase:
    question: str
    intent: QueryIntent
    anchor_surface: str
    relation: str
    as_of: str | None = None


PARAPHRASES = [
    # --- aggregation paraphrases ---
    Paraphrase("who all does Soundex works at, name them", QueryIntent.AGGREGATE, "Soundex", "works_at"),
    Paraphrase("what things is Metaphone connected to via works at", QueryIntent.AGGREGATE, "Metaphone", "works_at"),
    Paraphrase("tell me everything Levenshtein distance located in", QueryIntent.AGGREGATE, "Levenshtein distance", "located_in"),
    Paraphrase("everything that cosine similarity authored please", QueryIntent.AGGREGATE, "cosine similarity", "authored"),
    Paraphrase("enumerate what MinHash part of", QueryIntent.AGGREGATE, "MinHash", "part_of"),
    Paraphrase("show me the entities Jaccard index acquired", QueryIntent.AGGREGATE, "Jaccard index", "acquired"),
    Paraphrase("give me the set Hamming distance works at", QueryIntent.AGGREGATE, "Hamming distance", "works_at"),
    # --- temporal as-of paraphrases (no "as of <D>, what does" lead-in) ---
    Paraphrase("back in year 3, what did Soundex works at", QueryIntent.TEMPORAL_ASOF, "Soundex", "works_at", "3"),
    Paraphrase("at time 7 what was Metaphone works at", QueryIntent.TEMPORAL_ASOF, "Metaphone", "works_at", "7"),
    Paraphrase("in period 5 who did Jaccard index acquired", QueryIntent.TEMPORAL_ASOF, "Jaccard index", "acquired", "5"),
    Paraphrase("rewind to 2 and tell me what Levenshtein distance located in", QueryIntent.TEMPORAL_ASOF, "Levenshtein distance", "located_in", "2"),
    Paraphrase("when the clock read 9 what did MinHash part of", QueryIntent.TEMPORAL_ASOF, "MinHash", "part_of", "9"),
]
