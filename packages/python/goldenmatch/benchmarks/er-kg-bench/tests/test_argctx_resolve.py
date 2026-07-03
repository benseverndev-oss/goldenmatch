"""Argument-context relation-resolution experiment (Phase-2 de-risk)."""
from __future__ import annotations

from erkgbench.qa_e2e.argctx_resolve import (
    RELATION_TYPES,
    argctx_features,
    bcubed,
    build_argctx_gold,
    must_pass_cases,
    resolve_distributional,
    resolve_gm,
)


def test_relation_types_have_a_deliberate_collision():
    sigs = list(RELATION_TYPES.values())
    assert len(set(sigs)) < len(sigs), "need a type-signature collision (acquired & part_of org->org)"


def test_gold_edges_respect_types_and_disjoint_pairs():
    obs = build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0)
    for e in obs:
        assert (e["subj_type"], e["obj_type"]) == RELATION_TYPES[e["rel"]]
    pairs = [(e["subj"], e["obj"]) for e in obs]
    assert len(pairs) == len(set(pairs))


def test_argctx_features_pair_sets_and_type_sig():
    feats = argctx_features(build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0))
    works = [p for p in feats if p in ("works at", "is employed at", "is on staff at")]
    assert len(works) >= 2
    assert feats[works[0]]["pairs"] == feats[works[1]]["pairs"]  # co-occurrence -> identical pair sets
    assert feats["works at"]["types"].most_common(1)[0][0] == ("person", "org")


def test_distributional_merges_synonyms_separates_distinct():
    feats = argctx_features(build_argctx_gold(seed=1, edges_per_rel=15, cooccur_frac=1.0))
    clusters = resolve_distributional(feats)
    cmap = {p: i for i, c in enumerate(clusters) for p in c}
    for p in ("works at", "is on staff at", "acquired", "authored", "part of"):
        assert p in cmap, f"{p!r} absent -- a type bucket was starved; lower edges_per_rel or reseed"
    assert cmap["works at"] == cmap["is on staff at"]  # synonyms merged
    assert cmap["acquired"] != cmap["authored"]  # distinct (different type sig)
    assert cmap["acquired"] != cmap["part of"]  # HARD case: same (org,org) sig, only pair-set separates


def test_bcubed_perfect_and_imperfect():
    gold = {"a": "R1", "b": "R1", "c": "R2"}
    assert bcubed([["a", "b"], ["c"]], gold) == (1.0, 1.0)
    p, r = bcubed([["a", "b", "c"]], gold)
    assert p < 1.0 and r == 1.0


def test_must_pass_cases_helper():
    ok, _ = must_pass_cases([["works at", "is on staff at"], ["acquired"], ["authored"], ["part of"]])
    assert ok is True
    bad, _ = must_pass_cases([["works at", "is on staff at"], ["acquired", "part of"], ["authored"]])
    assert bad is False  # acquired/part_of wrongly merged


def test_gm_resolver_runs_and_returns_partition():
    feats = argctx_features(build_argctx_gold(seed=1, edges_per_rel=10, cooccur_frac=1.0))
    clusters = resolve_gm(feats)
    flat = [p for c in clusters for p in c]
    assert sorted(flat) == sorted(feats)  # partition; fail-open never drops a phrasing
