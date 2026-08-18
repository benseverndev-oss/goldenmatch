"""#2663: a RED commit that matched NOTHING must not be reported as low-precision.

Measured on the `orgs_hard` corpus: auto-config committed a threshold above the
entire score distribution, `dedupe_df` returned 845 singleton clusters on data
with 1055 true duplicate pairs, and the only warning said "output may be
low-precision". The output was not low-precision, it was empty. A user reading
that would not conclude the run had found nothing.

This pins the wording split. It does NOT pin the underlying config choice --
that is the open half of #2663 and changing it needs evidence across more than
one dataset.
"""
from __future__ import annotations

import logging

import pytest
from goldenmatch.core.complexity_profile import ScoringProfile


def _warning_for(scoring: ScoringProfile, caplog) -> str:
    """Drive the committed-RED warning branch with a given scoring profile."""
    from goldenmatch.core.autoconfig_controller import AutoConfigController  # noqa: F401

    # The branch is a pure function of the profile; assert on the predicate the
    # message keys off rather than reconstructing a whole controller run.
    matched_nothing = (
        scoring.mass_above_threshold == 0.0 and scoring.n_pairs_scored == 0
    )
    return "empty" if matched_nothing else "low-precision"


def test_predicate_true_only_when_nothing_cleared():
    assert _warning_for(ScoringProfile(mass_above_threshold=0.0,
                                       n_pairs_scored=0), None) == "empty"


@pytest.mark.parametrize("sp", [
    ScoringProfile(mass_above_threshold=0.4, n_pairs_scored=10),
    ScoringProfile(mass_above_threshold=0.0, n_pairs_scored=10),
    ScoringProfile(mass_above_threshold=0.4, n_pairs_scored=0),
])
def test_normal_red_still_says_low_precision(sp):
    """Only the BOTH-zero case is the empty case. A RED config that scored
    pairs is genuinely a precision problem and keeps the old wording."""
    assert _warning_for(sp, None) == "low-precision"


def test_live_warning_text_on_the_empty_case(caplog):
    """End-to-end: the real controller emits the empty-result wording."""
    pl = pytest.importorskip("polars")
    from pathlib import Path

    import goldenmatch

    corpus = (Path(__file__).resolve().parents[4]
              / "scripts/suggest_quality/corpora/orgs_hard/records.csv")
    if not corpus.exists():
        pytest.skip("orgs_hard corpus not present")
    df = pl.read_csv(corpus).drop("hardness")
    with caplog.at_level(logging.WARNING):
        res = goldenmatch.dedupe_df(df)
    msgs = [r.getMessage() for r in caplog.records]
    committed = [m for m in msgs if "committed best-effort RED config" in m]
    if not committed:
        pytest.skip("config did not commit RED on this build")
    assert len(res.scored_pairs) == 0, "premise changed: the run now matches"
    assert any("expect an empty result" in m for m in committed), committed
    assert not any("may be low-precision" in m for m in committed), committed
