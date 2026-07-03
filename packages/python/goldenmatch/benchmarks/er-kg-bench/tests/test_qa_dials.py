"""ER-quality dial key-policies: the record_key each (entity, surface) emits, which
controls cross-document merge in the store. Wheel-free (dedupe_df is fuzzy string,
no embeddings/network with rerank off)."""
from __future__ import annotations

from erkgbench.qa_e2e import dials
from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph


def _setup():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.6, max_hops=4)
    return corpus, GoldGraph.from_corpus(corpus)


def test_oracle_merges_all_surfaces_of_an_entity():
    corpus, g = _setup()
    km = dials.oracle_keys(corpus, g)
    keys_by_entity: dict[str, set] = {}
    for (eid, _surface), key in km.items():
        keys_by_entity.setdefault(eid, set()).add(key)
    assert all(len(s) == 1 for s in keys_by_entity.values())


def test_none_gives_every_mention_a_unique_key():
    corpus, g = _setup()
    km = dials.none_keys(corpus, g)
    assert len(set(km.values())) == len(km)  # all distinct


def test_name_only_keys_by_exact_surface():
    corpus, g = _setup()
    km = dials.name_only_keys(corpus, g)
    by_entity: dict[str, set] = {}
    for (eid, _surface), key in km.items():
        by_entity.setdefault(eid, set()).add(key)
    multi = [s for s in by_entity.values() if len(s) > 1]
    assert multi, "ambiguity>0 should give some entity >1 surface-key"


def test_goldengraph_merges_at_least_exact_and_no_more_than_oracle():
    corpus, g = _setup()
    o = dials.oracle_keys(corpus, g)
    gg = dials.goldengraph_keys(corpus, g)
    nm = dials.name_only_keys(corpus, g)
    # distinct-key count: more merging = fewer keys. oracle <= goldengraph <= name_only
    assert len(set(o.values())) <= len(set(gg.values())) <= len(set(nm.values()))
