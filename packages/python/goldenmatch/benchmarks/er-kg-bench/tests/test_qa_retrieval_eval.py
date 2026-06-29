"""Retrieval-coverage eval -- wheel-free parts (the full evaluate needs the PyStore wheel -> lane)."""
from __future__ import annotations

from erkgbench.qa_e2e.retrieval_eval import RetrievalCoverage, _fraction_covered, render_md


def test_fraction_covered():
    assert _fraction_covered({"a", "b", "c", "d"}, {"a", "b"}) == 0.5
    assert _fraction_covered(set(), {"a"}) == 0.0  # no targets -> 0
    assert _fraction_covered({"a"}, {"a", "x", "y"}) == 1.0


def test_render_md():
    md = render_md(
        RetrievalCoverage(anchor_seed_recall=0.6, chain_coverage=0.35,
                          by_hop={1: 0.9, 2: 0.5, 3: 0.1, 4: 0.0}, n=20),
        embed_model="nomic-embed-text",
    )
    assert "anchor-seed recall" in md and "0.600" in md and "0.350" in md and "1-hop 0.90" in md
