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

import re

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
