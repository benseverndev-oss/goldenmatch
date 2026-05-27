"""Zero-label Phase 2: env-gated commit-selection tests.

Proves `pick_committed(use_zero_label_confidence=True)` commits the
higher-confidence config over the one with higher naive mass_separation, that
the default (flag off) behavior is unchanged, and that the precision-collapse
guard still precedes confidence.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
from goldenmatch.core.complexity_profile import (
    ComplexityProfile,
    ScoringProfile,
    ZeroLabelConfidenceProfile,
)


def _entry(iteration, *, mass_above, mass_borderline, confidence) -> HistoryEntry:
    prof = ComplexityProfile(
        scoring=ScoringProfile(
            n_pairs_scored=100,
            candidates_compared=100,
            mass_above_threshold=mass_above,
            mass_in_borderline=mass_borderline,
            dip_statistic=0.0,  # -> RED health (keeps both entries same rank)
        ),
        zero_label=ZeroLabelConfidenceProfile(overall_confidence=confidence),
    )
    return HistoryEntry(
        iteration=iteration, config=None, profile=prof,
        decision=None, error=None, wall_clock_ms=0,
    )


def _history(*entries) -> RunHistory:
    return RunHistory(entries=list(entries))


# A: higher mass_separation (0.10) but LOW confidence.
# B: lower  mass_separation (0.05) but HIGH confidence.
def _ab():
    a = _entry(0, mass_above=0.5, mass_borderline=0.4, confidence=0.3)
    b = _entry(1, mass_above=0.45, mass_borderline=0.4, confidence=0.8)
    return a, b


def test_flag_off_picks_by_mass_separation():
    a, b = _ab()
    picked = _history(a, b).pick_committed(precision_collapse_floor=0.9)
    assert picked is a  # higher sep wins by default (unchanged behavior)


def test_flag_on_picks_by_confidence():
    a, b = _ab()
    picked = _history(a, b).pick_committed(
        precision_collapse_floor=0.9, use_zero_label_confidence=True,
    )
    assert picked is b  # higher confidence wins when enabled


def test_flag_on_without_zero_label_falls_back_to_sep():
    # Profiles with no zero_label -> flag-on must not crash; falls back to -sep.
    a = HistoryEntry(0, None, ComplexityProfile(
        scoring=ScoringProfile(n_pairs_scored=100, candidates_compared=100,
                               mass_above_threshold=0.5, mass_in_borderline=0.4)),
        None, None, 0)
    b = HistoryEntry(1, None, ComplexityProfile(
        scoring=ScoringProfile(n_pairs_scored=100, candidates_compared=100,
                               mass_above_threshold=0.45, mass_in_borderline=0.4)),
        None, None, 0)
    picked = _history(a, b).pick_committed(
        precision_collapse_floor=0.9, use_zero_label_confidence=True,
    )
    assert picked is a  # higher sep (no zero_label to use)


def test_precision_collapse_guard_precedes_confidence():
    # C is precision-collapsed (mass_above>floor) WITH high confidence; A is not.
    # The collapse demotion (rank=3) must beat C's confidence -> A wins.
    a = _entry(0, mass_above=0.5, mass_borderline=0.4, confidence=0.3)
    c = _entry(1, mass_above=0.95, mass_borderline=0.0, confidence=0.99)
    picked = _history(a, c).pick_committed(
        precision_collapse_floor=0.9, use_zero_label_confidence=True,
    )
    assert picked is a


# Controller default (issue #489): zero-label commit is ON unless explicitly
# opted out with GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT=0.
def test_controller_default_enables_zero_label(monkeypatch):
    from goldenmatch.core.autoconfig_controller import _zero_label_commit_enabled

    monkeypatch.delenv("GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT", raising=False)
    assert _zero_label_commit_enabled() is True


def test_controller_optout_disables_zero_label(monkeypatch):
    from goldenmatch.core.autoconfig_controller import _zero_label_commit_enabled

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT", "0")
    assert _zero_label_commit_enabled() is False


def test_controller_explicit_one_enables_zero_label(monkeypatch):
    from goldenmatch.core.autoconfig_controller import _zero_label_commit_enabled

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT", "1")
    assert _zero_label_commit_enabled() is True
