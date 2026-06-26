"""Engineered edge documents expose their rendered surfaces (Task 4a) so the
ablation can assign per-mention record_keys without re-parsing Document.text."""
from __future__ import annotations

from pathlib import Path

from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph


def _surfaces_of(g: GoldGraph) -> dict[str, set[str]]:
    from dataset.concepts_loader import load_concepts  # type: ignore

    bench_root = Path(__file__).resolve().parents[1]
    out: dict[str, set[str]] = {}
    for c in load_concepts(bench_root / "dataset" / "concepts.jsonl"):
        out[c.canonical_id] = {c.concept, *(v.surface for v in c.variants)}
    return out


def test_edge_docs_carry_rendered_surfaces_within_the_entity_universe():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.6, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    surf = _surfaces_of(g)
    saw_variant = False
    for d in corpus.documents:
        src, _rel, dst = d.id.split("::")
        assert d.src_surface and d.dst_surface  # non-empty
        assert d.src_surface in surf[src]
        assert d.dst_surface in surf[dst]
        # at ambiguity 0.6 at least some doc should render a non-canonical surface
        if d.src_surface != g.canonical_name(src):
            saw_variant = True
    assert saw_variant, "ambiguity>0 should produce some variant surface"
