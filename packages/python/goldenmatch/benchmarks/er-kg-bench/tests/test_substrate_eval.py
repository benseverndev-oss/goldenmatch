"""Substrate-quality eval: pure scoring over a built graph (alignment / coherence / provenance / A-B)."""
from __future__ import annotations


def test_emit_gold_mentions_from_documents():
    from erkgbench.qa_e2e.engineered import emit_gold_mentions

    class _Doc:  # mimic corpora.Document (id + src_surface + dst_surface)
        def __init__(self, id, ss, ds):
            self.id, self.src_surface, self.dst_surface = id, ss, ds

    docs = [_Doc("gm:a::works_at::gm:b", "Ay", "Bee"),
            _Doc("gm:a::located_in::gm:c", "Ay", "Cee"),
            _Doc("gm:a::works_at::gm:b::1", "X", "Y")]   # a co-occurrence extra (::1) -> SKIPPED
    mentions = emit_gold_mentions(docs)
    assert mentions == [
        ("gm:a", "Ay", "gm:a::works_at::gm:b"), ("gm:b", "Bee", "gm:a::works_at::gm:b"),
        ("gm:a", "Ay", "gm:a::located_in::gm:c"), ("gm:c", "Cee", "gm:a::located_in::gm:c"),
    ]
