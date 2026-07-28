"""Box tests for the PURE adapters in `build_corpus`'s FS-enrichment path
(honest-yardstick Task 6): `_pairs_from_blocks` (block-pair -> gold-tagged
candidate), `_gold_components` (match-row union-find + transitive closure),
`_rebalance_negatives` (swap easy synth negatives for hard mined ones), and
`_sample_labeled_pairs`.

Box-safe: these functions are stdlib-only, so importing `build_corpus` here
touches NEITHER goldenmatch NOR polars NOR yaml (every heavy import in
`build_corpus` is function-local). No `--fs-enrich` codepath runs -- the
goldenmatch-backed halves (`_fit_fs_posterior`, `_make_candidates`, ...) are
verified by a real corpus build, not here."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from build_corpus import (  # noqa: E402
    _gold_components,
    _pairs_from_blocks,
    _rebalance_negatives,
    _record_signature,
    _sample_labeled_pairs,
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
    gold = {("0", "1")}
    gold_linked = lambda a, b: (a["eid"], b["eid"]) == ("e1", "e1")  # noqa: E731

    out = _pairs_from_blocks([["0", "1", "2"]], records_by_id, gold_linked)

    pairs = {(c["eid_a"], c["eid_b"]): c["gold_match"] for c in out}
    assert pairs == {("0", "1"): True, ("0", "2"): False, ("1", "2"): False}
    # every candidate carries the actual record dicts for scoring
    assert all("a" in c and "b" in c for c in out)
    del gold


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
