"""GOLDENGRAPH_BENCH_HOMOGRAPH injects same-surface / different-coarse-type collisions."""
from erkgbench.qa_e2e.engineered import emit_gold_mentions, generate_engineered


def _surface_to_entities(corpus):
    by_surface: dict[str, set] = {}
    for eid, surface, _doc in emit_gold_mentions(corpus.documents):
        by_surface.setdefault(surface, set()).add(eid)
    return by_surface


def test_homograph_injection_shares_surface_across_distinct_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_BENCH_HOMOGRAPH", "1")
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    shared = {s: ids for s, ids in _surface_to_entities(corpus).items() if len(ids) > 1}
    assert shared, "no homograph collision injected"
    # the collision docs carry the appositive coarse-type cue
    homo_surface = next(iter(shared))
    cued = [d for d in corpus.documents if homo_surface in d.text and ", a " in d.text]
    assert cued, "homograph docs must render the coarse-type appositive cue"


def test_homograph_off_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_BENCH_HOMOGRAPH", raising=False)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    # baseline concept corpus: no surface maps to >1 distinct entity id
    assert not any(len(ids) > 1 for ids in _surface_to_entities(corpus).values())
