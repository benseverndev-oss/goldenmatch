"""Link-cut rule resolution: pinned link_cut_rule > GOLDENMATCH_FS_LINEAR_CUT default."""

from __future__ import annotations

import math
from types import SimpleNamespace

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_EVIDENCE_RULE,
    LINK_THRESHOLD_FALLBACK,
    _fs_link_threshold,
    _fs_resolved_cut,
    link_threshold_source,
    resolve_thresholds,
)


def _mk(**update) -> MatchkeyConfig:
    mk = MatchkeyConfig(
        name="fs",
        type="probabilistic",
        fields=[
            MatchkeyField(field="name", scorer="jaro_winkler", levels=3, partial_threshold=0.8),
            MatchkeyField(field="zip", scorer="exact", levels=2),
        ],
    )
    return mk.model_copy(update=update) if update else mk


def _em(lam: float = 0.01, calibrated=None):
    # lo = -10, hi = 14, midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=calibrated,
    )


def _norm(bits: float) -> float:
    return (bits + 10.0) / 24.0


def test_pinned_rule_beats_the_default_rule(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_resolved_cut(_mk(link_cut_rule="evidence_12"), _em(), calibrated=False)
    assert got.rule == "evidence_12"
    assert got.reason == "pinned by link_cut_rule"
    assert math.isclose(got.normalized, _norm(12.0), rel_tol=1e-12)


def test_pinned_rule_applies_when_the_default_is_off(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    got = _fs_resolved_cut(_mk(link_cut_rule="evidence_9"), _em(), calibrated=False)
    assert got.rule == "evidence_9"
    assert math.isclose(got.normalized, _norm(9.0), rel_tol=1e-12)


def test_pinned_midpoint_is_a_chosen_rule_not_a_fallback(monkeypatch):
    """The env value `midpoint` stays an alias for off, as #2939 shipped it; a pinned
    `midpoint` is a decision about this matchkey, so it reports as a rule."""
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    mk = _mk(link_cut_rule="midpoint")
    got = _fs_resolved_cut(mk, _em(), calibrated=False)
    assert got.rule == "midpoint" and got.normalized == 0.5
    assert link_threshold_source(mk, _em()) == LINK_THRESHOLD_EVIDENCE_RULE


def test_default_off_and_nothing_pinned_resolves_nothing(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert _fs_resolved_cut(_mk(), _em(), calibrated=False) is None
    assert link_threshold_source(_mk(), _em()) == LINK_THRESHOLD_FALLBACK


def test_env_default_accepts_any_rule_name(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "evidence_5")
    got = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert got.rule == "evidence_5"
    assert got.reason == "default rule"
    assert math.isclose(got.normalized, _norm(5.0), rel_tol=1e-12)


def test_uncomputable_pin_falls_back_to_the_default_with_a_reason(monkeypatch):
    """KNOWN-POSITIVE: a pinned otsu on a model with no training histogram."""
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_resolved_cut(_mk(link_cut_rule="otsu"), _em(), calibrated=False)
    assert got.rule == "prior_mid"
    assert got.reason.startswith("otsu unavailable: no training histogram")


def test_uncomputable_pin_with_default_off_resolves_nothing(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert _fs_resolved_cut(_mk(link_cut_rule="otsu"), _em(), calibrated=False) is None


def test_posterior_mode_ignores_rules(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    assert _fs_resolved_cut(_mk(link_cut_rule="evidence_9"), _em(), calibrated=True) is None


def test_explicit_threshold_and_calibrated_cutoff_still_win(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    pinned = _mk(link_cut_rule="evidence_12", link_threshold=0.42)
    assert _fs_link_threshold(pinned, _em(), calibrated=False) == 0.42
    assert _fs_link_threshold(_mk(link_cut_rule="evidence_12"), _em(calibrated=0.61), False) == 0.61


def test_both_resolvers_and_the_source_agree_on_a_pinned_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)
    monkeypatch.delenv("GOLDENMATCH_FS_EVIDENCE_CUT", raising=False)
    mk, em = _mk(link_cut_rule="evidence_9"), _em()
    link, review = resolve_thresholds(mk, em)
    assert math.isclose(link, _fs_link_threshold(mk, em, calibrated=False), rel_tol=1e-12)
    assert math.isclose(link, _norm(9.0), rel_tol=1e-12)
    assert review <= link
    assert link_threshold_source(mk, em) == LINK_THRESHOLD_EVIDENCE_RULE
