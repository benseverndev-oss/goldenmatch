"""Schema-constrained + direction-canonical extraction post-processing.

The 7B (and even a 32B -- measured: scaling the model did NOT help) makes the same
structural errors on open extraction: it paraphrases predicates ("authored negative
evidence"), and it reverses edge direction on passive/inverse phrasings ("X was
authored BY Y", "Y contains X"). Neither is a capacity problem -- both are fixable
deterministically GIVEN a closed schema. `canonicalize_extraction` snaps every edge
predicate to exactly one canonical relation (dropping out-of-schema edges) and flips
the subject/object of edges phrased in the reverse direction.

This is a post-extraction pass over an `Extraction`; it never calls the LLM. It is
the source-side fix the query-time walk repairs (`_bridge_surfaces`, the reversed
fallback) were standing in for -- canonicalize the graph ONCE at ingest instead of
patching every traversal.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from .extract import Extraction, Relationship


def _norm(s: str) -> str:
    return " ".join(str(s).lower().replace("_", " ").split())


#: Per-relation alias table for the engineered RELATION_SCHEMA. `forward` phrasings
#: keep subj->obj; `reverse` phrasings (passive / inverse verbs) mean the edge was
#: extracted backwards, so canonicalization FLIPS it. Kept to UNAMBIGUOUS reverse
#: aliases (each maps to one relation) so a flip is never a guess. Unknown relations
#: fall back to label-only forward matching (no flip) via `default_schema`.
_ENGINEERED_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "works_at": {
        "forward": ("works at", "employed at", "works for", "affiliated with"),
        "reverse": ("employs", "employer of", "has employee"),
    },
    "located_in": {
        "forward": ("located in", "is located in", "based in", "situated in"),
        "reverse": ("location of", "is home to"),
    },
    "acquired": {
        "forward": ("acquired", "bought", "purchased", "took over"),
        "reverse": ("acquired by", "bought by", "purchased by", "was acquired by"),
    },
    "authored": {
        "forward": ("authored", "wrote", "created", "developed"),
        "reverse": ("authored by", "written by", "created by", "was authored by"),
    },
    "part_of": {
        "forward": ("part of", "is part of", "belongs to", "member of"),
        "reverse": ("has part", "comprises", "consists of", "made up of"),
    },
}


@dataclass(frozen=True)
class RelationSchema:
    """Closed relation vocabulary with direction. `forward[r]`/`reverse[r]` are the
    normalized alias phrases for canonical relation `r`; the relation label itself is
    always a forward alias."""
    relations: tuple[str, ...]
    forward: dict[str, frozenset[str]]
    reverse: dict[str, frozenset[str]]

    def match(self, predicate: str) -> tuple[str, bool] | None:
        """Map a raw predicate to (canonical_relation, flip). `flip=True` means the
        edge is phrased in reverse and its subj/obj must be swapped. Returns None when
        the predicate matches no relation (the edge is dropped -- closed schema).

        Match order: exact reverse alias -> exact forward alias -> substring against a
        forward alias (absorbs object-token bleed like 'authored negative evidence' ->
        'authored'). Reverse is checked first so a passive phrase ('acquired by') is
        not shadowed by the forward substring ('acquired')."""
        p = _norm(predicate)
        if not p:
            return None
        for r in self.relations:
            if p in self.reverse[r]:
                return (r, True)
        for r in self.relations:
            if p in self.forward[r]:
                return (r, False)
        # substring fallback (forward only): the canonical label / a forward alias is a
        # token-subset of a noisy predicate. Longest alias first so 'part of' beats 'of'.
        best: tuple[str, int] | None = None
        for r in self.relations:
            for alias in self.forward[r]:
                if alias and alias in p and (best is None or len(alias) > best[1]):
                    best = (r, len(alias))
        return (best[0], False) if best else None


def default_schema(vocab) -> RelationSchema:
    """Build a `RelationSchema` from a relation vocabulary. Known engineered relations
    get their full forward/reverse alias sets; any other relation is forward-only (its
    own normalized label), so the schema generalizes to arbitrary vocabularies without
    a flip it can't justify."""
    relations = tuple(dict.fromkeys(_norm(v).replace(" ", "_") for v in vocab if str(v).strip()))
    fwd: dict[str, frozenset[str]] = {}
    rev: dict[str, frozenset[str]] = {}
    for r in relations:
        aliases = _ENGINEERED_ALIASES.get(r, {})
        f = {_norm(r), *(_norm(a) for a in aliases.get("forward", ()))}
        v = {_norm(a) for a in aliases.get("reverse", ())}
        fwd[r] = frozenset(a for a in f if a)
        rev[r] = frozenset(a for a in v if a)
    return RelationSchema(relations=relations, forward=fwd, reverse=rev)


def canonicalize_extraction(ext: Extraction, schema: RelationSchema) -> Extraction:
    """Return a new `Extraction` whose relationships are snapped to `schema`: each
    predicate becomes a canonical relation label, reverse-phrased edges are flipped,
    and out-of-schema edges are dropped. Mentions and attributes pass through
    unchanged (attributes use free predicates by design)."""
    rels: list[Relationship] = []
    for r in ext.relationships:
        m = schema.match(r.predicate)
        if m is None:
            continue
        canon, flip = m
        s, o = (r.obj, r.subj) if flip else (r.subj, r.obj)
        rels.append(Relationship(subj=s, predicate=canon, obj=o))
    return Extraction(mentions=ext.mentions, relationships=rels, attributes=ext.attributes)


def schema_canon_enabled() -> bool:
    """`GOLDENGRAPH_SCHEMA_CANON` gate. Off by default; the canonicalization needs a
    relation vocab (`GOLDENGRAPH_RELATION_VOCAB`) to have a schema to snap to."""
    return os.environ.get("GOLDENGRAPH_SCHEMA_CANON", "0") not in ("0", "false", "")


#: Coarse entity-type vocab -- the closed set the extractor is constrained to and the cross-doc key
#: coarsens to. Deliberately small so a weak model is CONSISTENT within it (kills type jitter) while
#: still separating homograph classes (person vs organization). Override via GOLDENGRAPH_ENTITY_TYPE_VOCAB.
#: 4 buckets, MEASURED best on the substrate homograph sweep: vs an 8-type vocab, standard-corpus recall
#: +0.05 (0.637->0.686, less within-vocab jitter) AND homograph precision +0.05 (0.886->0.931). Going to
#: 3 buckets did NOT help (recall 0.670 < 0.686) -- 4 is the sweet spot on the concept corpus.
DEFAULT_ENTITY_TYPE_VOCAB = (
    "person", "organization", "concept", "other",
)

#: Substring keyword -> coarse type, for the open prose a 7B emits when it ignores the constraint.
_ENTITY_TYPE_HINTS = {
    "technique": "concept", "method": "concept", "algorithm": "concept", "process": "concept",
    "index": "concept", "measure": "concept", "metric": "concept", "model": "concept",
    "concept": "concept", "theory": "concept", "approach": "concept", "function": "concept",
    "company": "organization", "corp": "organization", "inc": "organization", "ltd": "organization",
    "organization": "organization", "organisation": "organization", "university": "organization",
    "lab": "organization", "institute": "organization", "agency": "organization", "team": "organization",
    "person": "person", "author": "person", "researcher": "person", "scientist": "person",
    "city": "location", "country": "location", "region": "location", "place": "location",
    "location": "location", "site": "location",
    "book": "work", "paper": "work", "article": "work", "publication": "work", "work": "work",
    "event": "event", "conference": "event", "war": "event",
    "product": "product", "tool": "product", "software": "product", "system": "product",
    "device": "product", "technology": "product",
}


def entity_type_vocab() -> tuple:
    """The closed coarse-type vocab (`GOLDENGRAPH_ENTITY_TYPE_VOCAB`, comma-separated) or the default."""
    raw = os.environ.get("GOLDENGRAPH_ENTITY_TYPE_VOCAB", "")
    vocab = tuple(dict.fromkeys(v.strip().lower() for v in raw.split(",") if v.strip()))
    return vocab or DEFAULT_ENTITY_TYPE_VOCAB


def canonicalize_entity_type(raw: str, vocab: tuple | None = None) -> str:
    """Snap an open-vocab type string to the closed coarse vocab: exact match, else a substring hint,
    else `other` (or the vocab's last entry if it has no `other`). Pure + goldenmatch-free, so the
    normalization is unit-testable without the fingerprint."""
    vocab = vocab or entity_type_vocab()
    t = (raw or "").strip().lower()
    if t in vocab:
        return t
    for kw, coarse in _ENTITY_TYPE_HINTS.items():
        if kw in t and coarse in vocab:
            return coarse
    return "other" if "other" in vocab else (vocab[-1] if vocab else "other")


def entity_type_canon_enabled() -> bool:
    """`GOLDENGRAPH_ENTITY_TYPE_CANON` gate: constrain extraction to `entity_type_vocab()`."""
    return os.environ.get("GOLDENGRAPH_ENTITY_TYPE_CANON", "0") not in ("0", "false", "")
