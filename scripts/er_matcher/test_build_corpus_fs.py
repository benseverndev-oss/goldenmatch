"""Box tests for the PURE adapters in the FS-enrichment layer (`fs_scorer`,
honest-yardstick Task 6): `_pairs_from_blocks` (block-pair -> gold-tagged
candidate), `_gold_components` (match-row union-find + transitive closure),
`_rebalance_negatives` (swap easy synth negatives for hard mined ones),
`_sample_labeled_pairs`, `_check_separation` (the posterior-vs-weighted gate),
and the disk-cache helpers (`_pairs_hash` / `_save_fs_cache` / `_load_fs_cache`).

Box-safe: `fs_scorer` keeps every goldenmatch/polars import function-local, so
importing it here touches NEITHER goldenmatch NOR polars NOR yaml. No
`--fs-enrich` codepath runs -- the goldenmatch-backed halves (`_fit_fs_posterior`,
`_make_candidates`, ...) are verified by a real corpus build, not here."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))

from fs_scorer import (  # noqa: E402
    _check_separation,
    _gold_components,
    _load_fs_cache,
    _pairs_from_blocks,
    _pairs_hash,
    _rebalance_negatives,
    _record_signature,
    _sample_labeled_pairs,
    _save_fs_cache,
)


def _rec(name: str, entity: str) -> dict:
    return {"name": name, "eid": entity}


# ── _pairs_from_blocks ──────────────────────────────────────────────────────


def test_pairs_from_blocks_enumerates_within_block_pairs_and_tags_gold():
    records_by_id = {
        "0": _rec("alice", "e1"),
        "1": _rec("alyce", "e1"),  # same entity as 0
        "2": _rec("bob", "e2"),
    }
    # One block of all three ids; gold links 0<->1 only.
    gold_linked = lambda a, b: (a["eid"], b["eid"]) == ("e1", "e1")  # noqa: E731

    out = _pairs_from_blocks([["0", "1", "2"]], records_by_id, gold_linked)

    pairs = {(c["eid_a"], c["eid_b"]): c["gold_match"] for c in out}
    assert pairs == {("0", "1"): True, ("0", "2"): False, ("1", "2"): False}
    # every candidate carries the actual record dicts for scoring
    assert all("a" in c and "b" in c for c in out)


def test_pairs_from_blocks_dedups_across_passes_canonical_order():
    records_by_id = {"0": _rec("a", "e1"), "1": _rec("b", "e2")}
    never_gold = lambda a, b: False  # noqa: E731
    # Same pair appears in two blocks, one in reversed id order.
    out = _pairs_from_blocks([["0", "1"], ["1", "0"]], records_by_id, never_gold)
    assert len(out) == 1
    assert (out[0]["eid_a"], out[0]["eid_b"]) == ("0", "1")


def test_pairs_from_blocks_skips_self_pairs():
    records_by_id = {"0": _rec("a", "e1")}
    out = _pairs_from_blocks([["0", "0"]], records_by_id, lambda a, b: False)
    assert out == []


# ── _gold_components (transitive closure) ───────────────────────────────────


def test_gold_components_recovers_star_topology_transitive_closure():
    """FEBRL emits only anchor-vs-dup positives; dup0-vs-dup1 is same-entity but
    never a match row. The component closure must still mark it gold-linked."""
    anchor, dup0, dup1 = _rec("anchor", "e1"), _rec("dup0", "e1"), _rec("dup1", "e1")
    other = _rec("other", "e2")
    pairs = [
        {"a": anchor, "b": dup0, "label": "match"},
        {"a": anchor, "b": dup1, "label": "match"},
        {"a": anchor, "b": other, "label": "no_match"},  # negatives are ignored
    ]
    gold_linked = _gold_components(pairs)

    assert gold_linked(dup0, dup1) is True  # closed transitively via the anchor
    assert gold_linked(anchor, dup1) is True
    assert gold_linked(dup0, other) is False
    # a record never seen in any match row links to nothing
    assert gold_linked(other, _rec("stranger", "e3")) is False


def test_gold_components_matches_by_value_not_identity():
    a1 = _rec("alice", "e1")
    a1_copy = dict(a1)  # different object, same values (record_pools strips ids)
    b1 = _rec("bob", "e1")
    gold_linked = _gold_components([{"a": a1, "b": b1, "label": "match"}])
    assert gold_linked(a1_copy, b1) is True


def test_record_signature_is_order_independent():
    assert _record_signature({"x": "1", "y": "2"}) == _record_signature({"y": "2", "x": "1"})


# ── _rebalance_negatives ────────────────────────────────────────────────────


def test_rebalance_drops_one_synth_negative_per_mined_negative():
    enriched = [
        {"label": "match", "confidence": 0.9},
        {"label": "no_match", "confidence": 0.1},  # original synth neg (droppable)
        {"label": "no_match", "confidence": 0.1},  # original synth neg (droppable)
        {"label": "no_match", "confidence": 0.4, "negative_kind": "fs_mined"},
        {"label": "no_match", "confidence": 0.4, "negative_kind": "fs_mined"},
    ]
    out = _rebalance_negatives(enriched, mined_count=2)
    # both original synth negatives dropped; positives + mined survive
    assert [r["label"] for r in out] == ["match", "no_match", "no_match"]
    assert all(r.get("negative_kind") == "fs_mined" for r in out if r["label"] == "no_match")


def test_rebalance_noop_when_no_mined():
    enriched = [{"label": "no_match", "confidence": 0.1}]
    assert _rebalance_negatives(enriched, 0) == enriched


def test_rebalance_never_drops_mined_or_positives_even_if_short_on_synth():
    enriched = [
        {"label": "match"},
        {"label": "no_match", "confidence": 0.1},  # only ONE droppable synth neg
        {"label": "no_match", "negative_kind": "fs_mined"},
    ]
    out = _rebalance_negatives(enriched, mined_count=5)  # asks for more than available
    assert {r["label"] for r in out} == {"match", "no_match"}
    assert any(r.get("negative_kind") == "fs_mined" for r in out)
    assert sum(1 for r in out if r["label"] == "match") == 1


# ── _sample_labeled_pairs ───────────────────────────────────────────────────


def test_sample_labeled_pairs_balances_and_tags_match_flag():
    pairs = (
        [{"a": {"n": i}, "b": {"n": i}, "label": "match"} for i in range(5)]
        + [{"a": {"n": i}, "b": {"n": -i}, "label": "no_match"} for i in range(3)]
    )
    sample = _sample_labeled_pairs(pairs, k=10)
    is_match = [m for _a, _b, m in sample]
    assert sum(is_match) == 5
    assert sum(1 for m in is_match if not m) == 3
    assert all(isinstance(a, dict) and isinstance(b, dict) for a, b, _m in sample)


# ── _check_separation (the posterior-vs-weighted gate) ──────────────────────


def _sample(scores):
    """A labeled sample as (a, b, is_match) where scorer reads a['s'] as the
    injected score -- lets these tests drive `_check_separation` with no
    goldenmatch."""
    return [({"s": s}, {"s": s}, is_m) for s, is_m in scores]


def _fake_scorer(a, b):
    return a["s"]


def test_check_separation_passes_when_margin_cleared_and_in_range():
    sample = _sample([(0.95, True), (0.9, True), (0.05, False), (0.1, False)])
    ok, mean_m, mean_n, in_range = _check_separation(_fake_scorer, sample)
    assert ok is True
    assert in_range is True
    assert mean_m > mean_n


def test_check_separation_fails_when_out_of_unit_range():
    # match mean beats non-match mean by a mile, but a score sits outside [0,1]
    sample = _sample([(1.4, True), (0.9, True), (0.05, False)])
    ok, _mean_m, _mean_n, in_range = _check_separation(_fake_scorer, sample)
    assert in_range is False
    assert ok is False


def test_check_separation_fails_when_margin_not_met():
    # both classes ~0.5: mean(match) does NOT beat mean(non-match) by the margin
    sample = _sample([(0.52, True), (0.5, True), (0.5, False), (0.48, False)])
    ok, mean_m, mean_n, in_range = _check_separation(_fake_scorer, sample)
    assert in_range is True
    assert mean_m - mean_n < 0.1  # below _SEPARATION_MARGIN
    assert ok is False


def test_check_separation_fails_when_a_class_is_empty():
    # no non-match scores at all -> cannot verify separation -> not ok
    sample = _sample([(0.9, True), (0.95, True)])
    ok, _mean_m, _mean_n, _in_range = _check_separation(_fake_scorer, sample)
    assert ok is False


# ── pure disk-cache helpers (round-trip) ────────────────────────────────────


def test_pairs_hash_deterministic_and_order_sensitive():
    p1 = [{"a": {"n": "x"}, "b": {"n": "y"}, "label": "match", "eid_a": "0", "eid_b": "1"}]
    p2 = [{"a": {"n": "x"}, "b": {"n": "z"}, "label": "match", "eid_a": "0", "eid_b": "1"}]
    assert _pairs_hash(p1) == _pairs_hash(p1)  # stable
    assert _pairs_hash(p1) != _pairs_hash(p2)  # content-sensitive


def test_fs_cache_save_load_round_trip(tmp_path: Path):
    key = "deadbeef"
    payload = {
        "train": [{"label": "match", "confidence": 0.97}],
        "val": [],
        "test": [{"label": "no_match", "confidence": 0.03, "negative_kind": "fs_mined"}],
    }
    assert _load_fs_cache(tmp_path, key) is None  # miss before write
    _save_fs_cache(tmp_path, key, payload)
    assert _load_fs_cache(tmp_path, key) == payload  # hit after write
    assert (tmp_path / ".fs_cache" / f"{key}.json").exists()
