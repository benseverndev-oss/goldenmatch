"""Schema-constrained + direction-canonical extraction post-processing (wheel-free)."""
from __future__ import annotations

from goldengraph.extract import Extraction, Mention, Relationship
from goldengraph.schema import canonicalize_extraction, default_schema

_VOCAB = ["works_at", "located_in", "acquired", "authored", "part_of"]


def _ext(rels):
    ms = [Mention(name=n, typ="concept") for n in ("A", "B", "C")]
    return Extraction(mentions=ms, relationships=[Relationship(*r) for r in rels])


def test_forward_predicate_snaps_to_canonical_label():
    # "works at" (spaces) -> canonical "works_at", direction kept.
    out = canonicalize_extraction(_ext([(0, "works at", 1)]), default_schema(_VOCAB))
    assert [(r.subj, r.predicate, r.obj) for r in out.relationships] == [(0, "works_at", 1)]


def test_noisy_predicate_substring_snaps():
    # object-token bleed: 'authored negative evidence' -> 'authored' (substring), direction kept.
    out = canonicalize_extraction(_ext([(0, "authored negative evidence", 1)]), default_schema(_VOCAB))
    assert [(r.subj, r.predicate, r.obj) for r in out.relationships] == [(0, "authored", 1)]


def test_passive_reverse_alias_flips_direction():
    # 'B acquired by A' extracted as subj=B obj=A -> canonical 'acquired' with subj/obj FLIPPED.
    out = canonicalize_extraction(_ext([(1, "acquired by", 0)]), default_schema(_VOCAB))
    assert [(r.subj, r.predicate, r.obj) for r in out.relationships] == [(0, "acquired", 1)]


def test_authored_by_flips():
    out = canonicalize_extraction(_ext([(2, "was authored by", 0)]), default_schema(_VOCAB))
    assert [(r.subj, r.predicate, r.obj) for r in out.relationships] == [(0, "authored", 2)]


def test_out_of_schema_predicate_dropped():
    out = canonicalize_extraction(_ext([(0, "is related to", 1)]), default_schema(_VOCAB))
    assert out.relationships == []


def test_reverse_checked_before_forward_substring():
    # 'acquired by' must map to acquired+flip, NOT be shadowed by the 'acquired' forward substring.
    r, flip = default_schema(_VOCAB).match("acquired by")
    assert (r, flip) == ("acquired", True)


def test_unknown_relation_forward_only():
    # a vocab relation with no alias table is forward-only (label match, never flips).
    sch = default_schema(["cites"])
    assert sch.match("cites") == ("cites", False)
    assert sch.match("cited by") is None  # no reverse alias -> unmatched -> dropped
