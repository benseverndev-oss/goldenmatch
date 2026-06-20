"""goldengraph — build an own-your-KG knowledge graph from text.

text -> LLM extraction -> goldenmatch entity resolution -> a durable, bi-temporal
store (the goldengraph-native engine). Entity resolution is the differentiator:
duplicate surface forms across documents collapse into one durable entity.
"""

from .answer import ask, to_cypher
from .embed import Embedder, GoldenmatchEmbedder, seed_by_query
from .extract import Extraction, Mention, Relationship, extract, parse_extraction
from .ingest import build_batch, ingest
from .llm import LLMClient, OpenAIClient
from .resolve import ResolvedEntity, resolve
from .synthesize import synthesize_global, synthesize_local

__all__ = [
    "LLMClient",
    "OpenAIClient",
    "Mention",
    "Relationship",
    "Extraction",
    "extract",
    "parse_extraction",
    "ResolvedEntity",
    "resolve",
    "build_batch",
    "ingest",
    # SP4c — retrieval + synthesis + query
    "Embedder",
    "GoldenmatchEmbedder",
    "seed_by_query",
    "synthesize_local",
    "synthesize_global",
    "ask",
    "to_cypher",
]
