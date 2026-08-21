"""#2540: the blocking-recall warnings must claim only what the estimate supports.

`estimated_recall` is measured against the pairs the matchkey emits over an
UNBLOCKED sample, so it includes the scorer's false positives -- pairs blocking is
right to drop. Its ceiling is therefore that population's true-match fraction, not
1.0 (measured on DBLP-ACM: 40.5% true). The absolute floor warning used to assert
"expect most true matches to be missed", which the estimate cannot support:
`title[:5]` scores 0.037 there while genuinely retaining 98.2% of true pairs.

A second, RELATIVE warning carries the actionable signal, because it compares two
estimates over the same target population and so cancels that bias.
"""
from __future__ import annotations

import logging

from goldenmatch.core.block_analyzer import (
    _LOW_RECALL_WARN,
    _RECALL_TRADEOFF_RATIO,
    BlockingSuggestion,
    _warn_on_recall,
)

LOGGER = "goldenmatch.core.block_analyzer"


def _s(desc, recall, comparisons=1000, score=0.1):
    return BlockingSuggestion(
        keys=[{"key_fields": ["x"], "transforms": ["lowercase"], "description": desc}],
        group_count=10, max_group_size=5, mean_group_size=2.0,
        total_comparisons=comparisons, estimated_recall=recall,
        score=score, description=desc,
    )


def _msgs(caplog):
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


class TestRelativeTradeoffWarning:
    def test_fires_when_a_higher_recall_candidate_is_outranked(self, caplog):
        # DBLP-ACM's real shape: rank 1 is cheap and lossy, a much higher-recall
        # candidate sits in the same list and lost on comparison count.
        sugg = [_s("title[:5] + authors[:5]", 0.235, 1_667),
                _s("title[:3]", 0.595, 117_348)]
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall(sugg, coverage=0.997)
        m = " ".join(_msgs(caplog))
        assert "title[:3]" in m and "59.5%" in m
        assert "no recall floor" in m
        # It must name the cost, so the trade-off is judgeable.
        assert "1,667" in m and "117,348" in m

    def test_silent_when_the_pick_is_close_to_the_best(self, caplog):
        sugg = [_s("a", 0.80, 1_000), _s("b", 0.85, 90_000)]
        assert 0.80 >= _RECALL_TRADEOFF_RATIO * 0.85
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall(sugg, coverage=1.0)
        assert not [m for m in _msgs(caplog) if "no recall floor" in m]

    def test_silent_when_the_pick_IS_the_best_recall(self, caplog):
        sugg = [_s("a", 0.9), _s("b", 0.4)]
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall(sugg, coverage=1.0)
        assert not [m for m in _msgs(caplog) if "no recall floor" in m]

    def test_fires_independently_of_the_absolute_floor(self, caplog):
        # Both candidates well above the floor, but the pick still gives up 60%
        # of the available retention -- the absolute warning cannot see this.
        sugg = [_s("cheap", 0.35, 500), _s("thorough", 0.95, 500_000)]
        assert 0.35 > _LOW_RECALL_WARN
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall(sugg, coverage=1.0)
        m = _msgs(caplog)
        assert any("no recall floor" in x for x in m)
        assert not any("LOWER BOUND" in x for x in m)


class TestAbsoluteFloorWarning:
    def test_does_not_assert_true_matches_will_be_missed(self, caplog):
        # The retired claim. `title[:5]` scored 0.037 on DBLP-ACM while retaining
        # 98.2% of true pairs, so this assertion was simply false there.
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall([_s("k", 0.037)], coverage=0.997)
        m = " ".join(_msgs(caplog))
        assert "expect most true matches to be missed" not in m

    def test_states_the_estimate_is_a_lower_bound_and_why(self, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall([_s("k", 0.037)], coverage=0.997)
        m = " ".join(_msgs(caplog))
        assert "LOWER BOUND" in m
        assert "false positives" in m  # names the reason the denominator inflates
        assert "3.7%" in m

    def test_silent_above_the_floor(self, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall([_s("k", _LOW_RECALL_WARN + 0.01)], coverage=1.0)
        assert not [m for m in _msgs(caplog) if "LOWER BOUND" in m]

    def test_both_can_fire_together(self, caplog):
        sugg = [_s("cheap", 0.10, 500), _s("thorough", 0.60, 500_000)]
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            _warn_on_recall(sugg, coverage=1.0)
        m = _msgs(caplog)
        assert any("no recall floor" in x for x in m)
        assert any("LOWER BOUND" in x for x in m)
