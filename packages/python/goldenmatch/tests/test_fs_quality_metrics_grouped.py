"""`ranking_metrics_grouped` must equal `ranking_metrics` on the same data.

That equivalence is the whole justification for the grouped form. It exists
because the ungrouped one cannot run at 50M: its caller collects one
`(score, is_true)` tuple PER CANDIDATE PAIR to the driver, which is 5.5M at the
1M scale the harness was written for and **275M** at 50M -- tens of GB of Python
objects on a driver in a container. A head-to-head with no accuracy number is
not a head-to-head, so the metric has to survive the scale.

Grouping is exact rather than approximate because `ranking_metrics` already
consumes its input in tie-groups: it advances to the next distinct score, admits
every pair at that score, and only then computes precision/recall. The per-pair
identity inside a tie group is never used -- only the counts. So if these two
ever disagree, the grouped form is wrong, and a benchmark comparing two engines
on a metric that quietly changed meaning is worse than no benchmark.

These tests are the proof, so they compare against the REAL function on shared
inputs rather than against hand-computed constants. A hand-computed expectation
would only prove I can do the arithmetic the same wrong way twice.
"""
from __future__ import annotations

import importlib.util
import random
from collections import Counter
from pathlib import Path

import pytest

_MOD = Path(__file__).resolve().parents[4] / "scripts" / "_fs_quality_metrics.py"


def _load():
    if not _MOD.exists():
        pytest.skip(f"{_MOD} not present")
    spec = importlib.util.spec_from_file_location("_fs_quality_metrics", _MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _group(scored: list[tuple[float, bool]]) -> list[tuple[float, int, int]]:
    """The exact aggregation the Spark `groupBy(score)` produces."""
    total: Counter = Counter()
    trues: Counter = Counter()
    for s, t in scored:
        total[s] += 1
        if t:
            trues[s] += 1
    return [(s, trues[s], total[s]) for s in total]


def _assert_same(m, scored):
    a = m.ranking_metrics(scored)
    b = m.ranking_metrics_grouped(_group(scored))
    assert a == b, f"grouped != ungrouped\nungrouped={a}\ngrouped  ={b}"
    return a


# ── equivalence on the shapes that actually occur ──────────────────────────

def test_agrees_on_a_clean_separation():
    m = _load()
    scored = [(0.9, True)] * 10 + [(0.1, False)] * 90
    out = _assert_same(m, scored)
    assert out["average_precision"] == 1.0


def test_agrees_when_ties_span_both_classes():
    """The case grouping could plausibly get wrong: one score carrying both
    true and false pairs. `ranking_metrics` admits the whole tie group before
    measuring, and the grouped form must do the same."""
    m = _load()
    scored = [(0.5, True)] * 3 + [(0.5, False)] * 7 + [(0.9, True)] * 2
    _assert_same(m, scored)


def test_agrees_on_interleaved_scores():
    m = _load()
    scored = [(0.9, True), (0.8, False), (0.7, True), (0.6, False),
              (0.5, True), (0.4, False)]
    _assert_same(m, scored)


@pytest.mark.parametrize("seed", [1, 7, 42, 1337])
def test_agrees_on_random_data_with_heavy_ties(seed):
    """Randomised, with a deliberately SMALL score alphabet so tie groups are
    large -- which is the real shape here, since a pair's weight is a sum of
    per-field match weights over bounded gamma levels."""
    m = _load()
    rng = random.Random(seed)
    alphabet = [round(x * 0.05, 2) for x in range(21)]
    scored = [
        (rng.choice(alphabet), rng.random() < 0.3)
        for _ in range(2000)
    ]
    _assert_same(m, scored)


@pytest.mark.parametrize("seed", [3, 11])
def test_agrees_when_almost_every_score_is_distinct(seed):
    """The opposite extreme: near-unique scores, so grouping is nearly a no-op.
    Included because a bug that only shows with large groups would be missed by
    the tie-heavy case above, and vice versa."""
    m = _load()
    rng = random.Random(seed)
    scored = [(rng.random(), rng.random() < 0.25) for _ in range(500)]
    _assert_same(m, scored)


# ── the degenerate inputs, which must not diverge either ───────────────────

def test_empty_matches():
    m = _load()
    assert m.ranking_metrics([]) == m.ranking_metrics_grouped([])


def test_no_true_pairs_matches():
    m = _load()
    _assert_same(m, [(0.5, False)] * 10)


def test_all_true_pairs_matches():
    m = _load()
    _assert_same(m, [(0.5, True)] * 10)


def test_a_single_pair_matches():
    m = _load()
    _assert_same(m, [(0.5, True)])


# ── the property that makes the Spark form viable ──────────────────────────

def test_grouped_input_is_orders_of_magnitude_smaller():
    """Not a correctness test -- the reason the grouped form exists at all.

    A pair's weight is a sum of per-field match weights over bounded gamma
    levels, so the reachable score set is bounded by `prod(levels + 1)`. That is
    the same bound that makes GoldenMatch's counting GROUP BY small, which is
    the property this whole benchmark is about.
    """
    rng = random.Random(0)
    alphabet = [round(x * 0.05, 2) for x in range(21)]
    scored = [(rng.choice(alphabet), rng.random() < 0.3) for _ in range(100_000)]
    grouped = _group(scored)
    assert len(grouped) <= len(alphabet)
    assert len(grouped) < len(scored) / 1000, (
        "grouping must collapse the collect by orders of magnitude, or it does "
        "not solve the driver-memory problem it was written for"
    )
