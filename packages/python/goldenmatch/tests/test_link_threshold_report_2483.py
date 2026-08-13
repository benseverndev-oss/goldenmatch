"""The FS link cutoff must say what it was and where it came from (#2483).

A probabilistic run resolved its link cutoff through three precedence steps --
configured `mk.link_threshold`, then the EM-calibrated per-dataset cutoff, then
a fixed default -- and reported none of it. When the fixed default was used,
nothing in the config or the result recorded that no decision had been made
about where to cut.

The reporter's corpus linked 94.4% of records into multi-member clusters off
that default, fusing 2,554 distinct names into one entity. Before finding it
they swept a 16-cell matrix over four unrelated FS env knobs, because
`mk.threshold` (the WEIGHTED matchkey's field, inert here) appeared to do
nothing and there was no other thread to pull.

Measured on DBLP-ACM with this change: the fallback run reports
`{'link_threshold': 0.5, 'source': 'fallback'}` at a 25.3% match rate, and an
explicit 0.95 reports `source='configured'` at 1.24%.
"""
from __future__ import annotations

import pytest
from goldenmatch._api import (
    _IMPLAUSIBLE_FALLBACK_MATCH_RATE,
    _extract_stats,
    _warn_on_implausible_fallback_match_rate,
)
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_CALIBRATED,
    LINK_THRESHOLD_CONFIGURED,
    LINK_THRESHOLD_FALLBACK,
    EMResult,
    link_threshold_source,
)


def _EM(calibrated: float | None = None) -> EMResult:
    """A real EMResult, not a stub -- `link_threshold_source` reads the same
    attribute the resolver does, so the test should exercise the real shape."""
    return EMResult(
        m_probs={"a": [0.1, 0.9]}, u_probs={"a": [0.9, 0.1]},
        match_weights={"a": [-3.0, 3.0]}, converged=True, iterations=5,
        proportion_matched=0.02, calibrated_link_threshold=calibrated,
    )


def _mk(link_threshold: float | None = None) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic", link_threshold=link_threshold,
        fields=[MatchkeyField(field="a", scorer="jaro_winkler")],
    )


# ---- provenance mirrors the resolver's precedence ----


def test_configured_wins() -> None:
    assert link_threshold_source(_mk(0.9), _EM(0.7)) == LINK_THRESHOLD_CONFIGURED


def test_calibrated_when_nothing_configured() -> None:
    assert link_threshold_source(_mk(), _EM(0.7)) == LINK_THRESHOLD_CALIBRATED


def test_fallback_when_neither() -> None:
    assert link_threshold_source(_mk(), _EM(None)) == LINK_THRESHOLD_FALLBACK


def test_precedence_matches_resolve_thresholds() -> None:
    """The reporting helper and the resolver must not drift: whenever the source
    is reported as `configured`, the resolved cutoff must be the configured one."""
    from goldenmatch.core.probabilistic import resolve_thresholds

    mk, em = _mk(0.9), _EM(0.7)
    assert link_threshold_source(mk, em) == LINK_THRESHOLD_CONFIGURED
    link, _ = resolve_thresholds(mk, em)
    assert link == 0.9

    mk2 = _mk()
    assert link_threshold_source(mk2, em) == LINK_THRESHOLD_CALIBRATED
    link2, _ = resolve_thresholds(mk2, em)
    assert link2 == 0.7


# ---- the warning ----


def _fallback(cut: float = 0.5) -> dict:
    return {"fs": {"link_threshold": cut, "source": LINK_THRESHOLD_FALLBACK}}


def _configured(cut: float = 0.5) -> dict:
    return {"fs": {"link_threshold": cut, "source": LINK_THRESHOLD_CONFIGURED}}


def test_fallback_with_runaway_match_rate_warns(caplog: pytest.LogCaptureFixture) -> None:
    """THE regression: the reporter's shape. 94.4% of records linked off a cutoff
    nothing about the data chose."""
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(_fallback(), 0.944)
    assert "link_threshold" in caplog.text
    assert "#2483" in caplog.text
    assert "94.4%" in caplog.text
    assert "0.5000" in caplog.text, "the warning must carry the cutoff that produced it"
    assert "'fs'" in caplog.text, "and name the matchkey"


def test_a_configured_cutoff_never_warns(caplog: pytest.LogCaptureFixture) -> None:
    """A caller who chose the cut owns the outcome, however high the rate."""
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(_configured(), 0.99)
    assert "#2483" not in caplog.text


def test_a_calibrated_cutoff_never_warns(caplog: pytest.LogCaptureFixture) -> None:
    """EM chose it from this data -- that is the opposite of the failure."""
    cal = {"fs": {"link_threshold": 0.5, "source": LINK_THRESHOLD_CALIBRATED}}
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(cal, 0.99)
    assert "#2483" not in caplog.text


def test_a_plausible_match_rate_does_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """DBLP-ACM measures 25.3% off the fallback -- normal, must stay quiet."""
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(_fallback(), 0.253)
    assert "#2483" not in caplog.text


def test_the_bound_is_a_warning_not_a_gate() -> None:
    """Pin the intent so this does not quietly become a hard reject: nothing
    filters or raises on the bound."""
    assert 0.0 < _IMPLAUSIBLE_FALLBACK_MATCH_RATE < 1.0


def test_warns_at_the_boundary_only_above_it(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(
            _fallback(), _IMPLAUSIBLE_FALLBACK_MATCH_RATE
        )
    assert "#2483" not in caplog.text


def test_mixed_sources_reports_only_the_fallback_matchkeys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    mixed = {
        "chosen": {"link_threshold": 0.9, "source": LINK_THRESHOLD_CONFIGURED},
        "defaulted": {"link_threshold": 0.5, "source": LINK_THRESHOLD_FALLBACK},
    }
    with caplog.at_level("WARNING"):
        _warn_on_implausible_fallback_match_rate(mixed, 0.9)
    assert "'defaulted'" in caplog.text
    assert "'chosen'" not in caplog.text


# ---- the stats surface ----


def _result(fs: dict | None, matched: int = 10, total: int = 100) -> dict:
    return {
        "cluster_stats": {
            "multi_member_cluster_count": 3,
            "matched_record_count": matched,
        },
        "golden": None,
        "dupes": None,
        "unique": None,
        "clusters": {},
        "_total_records": total,
        "fs_link_thresholds": fs,
    }


def test_stats_carry_the_applied_threshold() -> None:
    stats = _extract_stats(_result(_fallback(0.5)))
    assert stats["fs_link_thresholds"]["fs"]["link_threshold"] == 0.5
    assert stats["fs_link_thresholds"]["fs"]["source"] == LINK_THRESHOLD_FALLBACK


def test_non_probabilistic_runs_carry_no_threshold_key() -> None:
    """Weighted/exact runs have no FS cutoff; the key must not appear at all
    rather than appear empty or zero."""
    assert "fs_link_thresholds" not in _extract_stats(_result(None))
    assert "fs_link_thresholds" not in _extract_stats(_result({}))
