"""The verify gate must measure its baseline the same way it measures candidates.

`_verify_suggestions` keeps a suggestion when
``cand_health >= baseline_health - 1e-6``. Candidates always come from
``engine._run_pipeline``; the baseline used to come from whatever ``clusters``
the caller passed. For ``review_config`` those coincide. For
``suggest_from_result`` the caller passes ``DedupeResult.clusters``, produced by
``dedupe_df`` -- which standardizes the frame first and therefore clusters a
different population than the raw frame the candidates run against.

Measured on the 80-row person fixture before the fix:

    suggest_from_result : baseline_health=1.0000  n_clusters=39   <- saturated
    review_config       : baseline_health=0.8000  n_clusters=8

Both kept the suggestion, so the equivalence test usually passed -- but the
artifacts-in path was held only by the epsilon (1.0 vs 1.0), so any run whose
candidate clustering came out marginally worse flipped it to DROP and returned
``[]``. That is the intermittent failure in
``test_suggest_from_result_verified_matches_review_config`` (observed once in six
full-suite runs, never reproducible in isolation).

These are stubbed rather than driven through a real pipeline: the defect is in
WHICH clusters the baseline reads, so exercising that decision directly is both
faster and a sharper pin than a full dedupe whose flake shows up once in six runs.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.suggest.adapter import _verify_baseline_clusters
from goldenmatch.core.suggest.health import suggestion_health_from_clusters


def _clusters(confidence: float, *, size: int = 4, n: int = 3) -> dict:
    """Cluster dict whose cohesion (weakest intra-cluster edge) is `confidence`.

    Health is `cohesion x coverage`, so with membership held constant health is
    monotone in this value -- which is what lets the ordering assertions below
    stand without hard-coding the formula's constants.
    """
    return {
        i: {"size": size, "confidence": confidence, "members": list(range(i * size, (i + 1) * size))}
        for i in range(n)
    }


class _Engine:
    """Stand-in for MatchEngine: records calls and returns fixed clusters."""

    def __init__(self, clusters: dict | None = None, raises: bool = False) -> None:
        self._clusters = clusters or {}
        self._raises = raises
        self.calls = 0

    def _run_pipeline(self, df, config):  # noqa: ANN001 - mirrors the real signature
        self.calls += 1
        if self._raises:
            raise RuntimeError("pipeline exploded")
        return type("R", (), {"clusters": self._clusters})()


@pytest.fixture
def df() -> pl.DataFrame:
    return pl.DataFrame({"name": ["a", "b", "c", "d"]})


class TestBaselineComesFromTheEngine:
    def test_callers_clusters_are_ignored_when_the_engine_run_succeeds(self, df):
        """THE regression. A saturated caller baseline must not be used."""
        engine = _Engine(_clusters(0.50))
        got, source = _verify_baseline_clusters(df, object(), _clusters(0.95), engine)
        assert source == "engine"
        assert got == _clusters(0.50)
        assert engine.calls == 1

    def test_the_saturated_caller_baseline_would_have_flipped_the_decision(self, df):
        """Pin the CONSEQUENCE, not just the plumbing: with a candidate between
        the two, the caller baseline drops the suggestion and the engine
        baseline keeps it. That difference is the flake."""
        n = 4
        caller_health = suggestion_health_from_clusters(_clusters(0.95), n)
        engine_health = suggestion_health_from_clusters(_clusters(0.50), n)
        cand_health = suggestion_health_from_clusters(_clusters(0.70), n)

        eps = 1e-6
        assert not (cand_health >= caller_health - eps), "caller baseline would DROP"
        assert cand_health >= engine_health - eps, "engine baseline KEEPS"

        _, source = _verify_baseline_clusters(df, object(), _clusters(0.95), _Engine(_clusters(0.50)))
        assert source == "engine"


class TestFallbackIsConservative:
    def test_falls_back_to_the_caller_when_the_baseline_run_fails(self, df):
        """A broken baseline degrades to the previous behaviour rather than
        raising -- this gate keeps suggestions when verification fails."""
        caller = _clusters(0.95)
        got, source = _verify_baseline_clusters(df, object(), caller, _Engine(raises=True))
        assert source == "caller"
        assert got == caller

    def test_missing_clusters_degrade_to_empty_not_none(self, df):
        got, source = _verify_baseline_clusters(df, object(), None, _Engine(raises=True))
        assert source == "caller"
        assert got == {}

    def test_engine_returning_no_clusters_is_still_the_engine_baseline(self, df):
        """An empty engine result is a real measurement (health 0.0), not a
        failure to measure -- it must not silently fall back to the caller."""
        got, source = _verify_baseline_clusters(df, object(), _clusters(0.95), _Engine({}))
        assert source == "engine"
        assert got == {}
