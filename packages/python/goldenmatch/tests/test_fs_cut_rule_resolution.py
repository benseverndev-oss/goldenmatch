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


def _clear_cut_env(monkeypatch):
    for var in (
        "GOLDENMATCH_FS_LINEAR_CUT", "GOLDENMATCH_FS_CALIBRATED",
        "GOLDENMATCH_FS_EVIDENCE_CUT", "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
    ):
        monkeypatch.delenv(var, raising=False)


def test_link_cut_report_names_the_rule_and_reason(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    report = link_cut_report(_mk(link_cut_rule="evidence_3"), _em())
    assert report["cut_rule"] == "evidence_3"
    assert report["cut_reason"] == "pinned by link_cut_rule"


def test_link_cut_report_is_empty_when_a_threshold_decided_first(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    configured = link_cut_report(_mk(link_cut_rule="evidence_3", link_threshold=0.4), _em())
    assert configured["cut_rule"] is None and configured["cut_reason"] is None
    calibrated = link_cut_report(_mk(), _em(calibrated=0.6))
    assert calibrated["cut_rule"] is None and calibrated["cut_reason"] is None


def test_link_cut_report_is_silent_when_no_rule_was_asked_for(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    assert link_cut_report(_mk(), _em()) == {"cut_rule": None, "cut_reason": None}


def test_link_cut_report_says_why_a_requested_rule_did_not_apply(monkeypatch):
    """KNOWN-POSITIVE: a degenerate model, and an uncomputable pin with the default off."""
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    flat = SimpleNamespace(
        match_weights={"name": [1.0, 1.0, 1.0], "zip": [0.0, 0.0]},
        proportion_matched=0.01, calibrated_link_threshold=None,
    )
    degenerate = link_cut_report(_mk(), flat)
    assert degenerate["cut_rule"] is None
    assert degenerate["cut_reason"].startswith("degenerate model")

    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    otsu = link_cut_report(_mk(link_cut_rule="otsu"), _em())
    assert otsu["cut_rule"] is None
    assert otsu["cut_reason"] == (
        "otsu unavailable: no training histogram on this model; fixed 0.50 cut applies"
    )


def test_dedupe_result_reports_the_cut_rule(monkeypatch):
    """A pinned rule reaches the run's cutoff report. Frame and config shape are the ones
    tests/test_link_threshold_stamp_2637.py already drives through FS scoring."""
    import warnings

    import goldenmatch as gm
    import polars as pl
    from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig, GoldenMatchConfig

    _clear_cut_env(monkeypatch)
    first = ["ada", "grace", "alan", "edsger", "barbara", "donald"]
    last = ["lovelace", "hopper", "turing", "dijkstra", "liskov", "knuth"]
    df = pl.DataFrame([
        {
            "rid": f"r{i}",
            "name": (f"{first[i % 6]} {last[(i // 2) % 6]}" if i % 3
                     else f"{first[i % 6][0]}. {last[(i // 2) % 6]}"),
            "city": ["leeds", "york", "hull"][i % 3],
        }
        for i in range(240)
    ])
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="p", type="probabilistic", link_cut_rule="evidence_3",
            fields=[MatchkeyField(field="name", scorer="jaro_winkler")],
        )],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["name"], transforms=["lowercase"])]
        ),
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        res = gm.dedupe_df(df, config=cfg)
    thresholds = (res.stats or {}).get("fs_link_thresholds") or {}
    assert "p" in thresholds, f"run never reported an FS cutoff: {thresholds!r}"
    entry = thresholds["p"]
    assert entry["cut_rule"] == "evidence_3"
    assert entry["cut_reason"] == "pinned by link_cut_rule"
    assert entry["source"] == LINK_THRESHOLD_EVIDENCE_RULE
