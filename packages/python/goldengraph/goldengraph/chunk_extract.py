"""Chunked extraction (GOLDENGRAPH_CHUNK_EXTRACT): split a dense document into
overlapping sentence windows, extract each window with the SAME extractor, and
union the results before resolution. The default single-pass extraction attends
over a whole ~20-sentence Wikipedia lead in one call and drops entities (the ~0.44
real-prose extraction-recall ceiling); a short window lets a weak model extract
both entities AND relations well, and the union aggregates. resolve() collapses
duplicate mentions across windows downstream, so no new dedup is needed here.

Pure w.r.t. the store: stdlib only, depends on the extract dataclasses. Gate is
off by default -- the single-pass path is unchanged when GOLDENGRAPH_CHUNK_EXTRACT
is unset.
"""

from __future__ import annotations

import os
import re

from .extract import Attribute, Extraction, Mention, Relationship
from .llm import LLMClient

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split `text` into sentences on `.!?` boundaries (stdlib regex, network-free).
    Empty / whitespace-only input -> [] (no windows, no wasted extractor call).
    Abbreviations may over-split; harmless for extraction (a fragment yields fewer
    entities, never wrong ones)."""
    if not text or not text.strip():
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def sentence_windows(sents: list[str], size: int, overlap: int) -> list[str]:
    """Overlapping sentence windows joined back into text. Advances by `size -
    overlap`. Guards (in order): size<=0 -> 1; overlap<0 -> 0; overlap>=size ->
    size-1 (stride>=1, terminates); [] -> []; len<=size -> one whole-doc window."""
    size = max(1, size)
    overlap = max(0, overlap)
    if overlap >= size:
        overlap = size - 1
    stride = size - overlap  # >= 1
    if not sents:
        return []
    if len(sents) <= size:
        return [" ".join(sents)]
    out: list[str] = []
    i = 0
    n = len(sents)
    while i < n:
        out.append(" ".join(sents[i : i + size]))
        if i + size >= n:  # last window reached the end
            break
        i += stride
    return out


def _env_int(name: str, default: int) -> int:
    """Parse an int env var defensively: unset OR set-but-empty OR non-numeric ->
    `default` (the empty-string-env footgun -- `NAME=` must not raise ValueError)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def chunk_extract_enabled() -> bool:
    """`GOLDENGRAPH_CHUNK_EXTRACT` gate. Off by default; "0"/"false"/"" -> off."""
    return os.environ.get("GOLDENGRAPH_CHUNK_EXTRACT", "0") not in ("0", "false", "")


def _chunk_params() -> tuple[int, int]:
    """(window size, overlap) from env; defaults (4, 1). Defensive parse."""
    return _env_int("GOLDENGRAPH_CHUNK_SENTENCES", 4), _env_int("GOLDENGRAPH_CHUNK_OVERLAP", 1)


def chunk_extract(text: str, llm: LLMClient | None, extractor) -> Extraction:
    """Split `text` into overlapping sentence windows, run `extractor(window, llm)`
    on each, and union: concatenate mentions and OFFSET each window's relationship /
    attribute indices by the running mention count. A window whose extractor raises
    is skipped (its mentions just don't contribute), never fatal to the doc.

    `extractor` is the same callable the single-pass path uses (`extract.extract`,
    or a rebel/gliner closure) -- it still honors LITERAL_ATTRS / vocab / recall
    gates internally per window."""
    size, overlap = _chunk_params()
    windows = sentence_windows(split_sentences(text), size, overlap)
    merged_mentions: list[Mention] = []
    merged_rels: list[Relationship] = []
    merged_attrs: list[Attribute] = []
    for window in windows:
        try:
            ex = extractor(window, llm)
        except Exception:
            continue  # a bad window degrades recall, never sinks the doc
        base = len(merged_mentions)  # captured BEFORE the append -> correct per-window offset
        merged_mentions += ex.mentions
        merged_rels += [
            Relationship(r.subj + base, r.predicate, r.obj + base) for r in ex.relationships
        ]
        merged_attrs += [
            Attribute(a.subj + base, a.predicate, a.value, a.typ)
            for a in getattr(ex, "attributes", ())
        ]
    return Extraction(mentions=merged_mentions, relationships=merged_rels, attributes=merged_attrs)
