"""REBEL fusion (GOLDENGRAPH_REBEL_FUSE): a distinct relation-recall lever. Runs REBEL
(Babelscape/rebel-large, discriminative end-to-end relation extraction) per sentence-window,
and maps each (head, rel, tail) triple's endpoints onto the ALREADY-extracted entities --
adding an edge only when BOTH endpoints map, never a new node. Composes with (and is measured
on top of) the relation re-prompt. Pure w.r.t. the store; REBEL injectable for tests; gate off
by default.

SCHEMA_CANON note: REBEL emits Wikidata-style predicates that a closed relation vocab won't
contain, so under GOLDENGRAPH_SCHEMA_CANON=1 canonicalization drops them. This lever targets
canon-off configs (see the spec)."""

from __future__ import annotations

import os
import threading

from .chunk_extract import _env_int, sentence_windows, split_sentences
from .extract import Mention, Relationship

_REBEL_LOCK = threading.Lock()
_REBEL = None  # cached `text -> list[(head, rel, tail)]` callable


def rebel_fuse_enabled() -> bool:
    """`GOLDENGRAPH_REBEL_FUSE` gate. Off by default; case-insensitive, stripped:
    ""/"0"/"false"/"no"/"off" -> off."""
    return os.environ.get("GOLDENGRAPH_REBEL_FUSE", "0").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def _rebel_params() -> tuple[int, int]:
    """(window size, overlap) for REBEL's 256-token input; defaults (4, 1). Defensive parse."""
    return _env_int("GOLDENGRAPH_REBEL_SENTENCES", 4), _env_int("GOLDENGRAPH_REBEL_OVERLAP", 1)


def _load_rebel():
    """Lazily load Babelscape/rebel-large ONCE (lock-guarded, double-checked, for the concurrent
    prepare phase). Returns a `text -> list[(head, rel, tail)]` callable reusing the existing
    unit-tested `extract_local.parse_rebel_triplets` decoder."""
    global _REBEL
    if _REBEL is not None:
        return _REBEL
    with _REBEL_LOCK:
        if _REBEL is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            from .extract_local import parse_rebel_triplets

            tok = AutoTokenizer.from_pretrained("Babelscape/rebel-large")
            mdl = AutoModelForSeq2SeqLM.from_pretrained("Babelscape/rebel-large")

            def _triples(text: str):
                inp = tok(text, return_tensors="pt", truncation=True, max_length=256)
                out = mdl.generate(**inp, max_length=256)
                return parse_rebel_triplets(tok.decode(out[0], skip_special_tokens=False))

            _REBEL = _triples
    return _REBEL


def _match_mention(surface_lc: str, mentions: list[Mention]) -> int | None:
    """Index of the mention whose (case-folded) name matches `surface_lc` -- exact preferred over
    substring-either-way, lowest index breaking ties; None if none match."""
    if not surface_lc:
        return None
    for i, m in enumerate(mentions):  # exact first
        if m.name.strip().lower() == surface_lc:
            return i
    for i, m in enumerate(mentions):  # substring either way
        n = m.name.strip().lower()
        if n and (surface_lc in n or n in surface_lc):
            return i
    return None


def rebel_fuse(text: str, mentions: list[Mention], *, rebel=None) -> list[Relationship]:
    """Run REBEL per sentence-window over `text`, map triple endpoints onto `mentions`, and return
    Relationships for triples where BOTH endpoints map and are distinct. Empty mentions -> [] (no
    model call). Any error -> [] (fail-soft). `rebel` (injectable) is a `text -> list[(head,rel,tail)]`
    callable; default None -> the cached real REBEL."""
    if not mentions:
        return []
    try:
        triples_fn = rebel or _load_rebel()
        size, overlap = _rebel_params()
        out: list[Relationship] = []
        for window in sentence_windows(split_sentences(text), size, overlap):
            try:
                triples = triples_fn(window)
            except Exception:
                continue  # a bad window skips, never sinks the doc
            for head, rel, tail in triples:
                s = _match_mention(str(head).strip().lower(), mentions)
                o = _match_mention(str(tail).strip().lower(), mentions)
                if s is not None and o is not None and s != o:
                    out.append(Relationship(subj=s, predicate=str(rel), obj=o))
        return out
    except Exception:
        return []
