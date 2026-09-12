"""FS link-cut router (spec 2026-09-11-fs-cut-rule-routing-design, P4)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core import fs_cut_rules as R
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_EVIDENCE_RULE,
    _fs_resolved_cut,
    link_cut_report,
    link_threshold_source,
)

_ENV = (
    "GOLDENMATCH_FS_CUT_ROUTER",
    "GOLDENMATCH_FS_LINEAR_CUT",
    "GOLDENMATCH_FS_CALIBRATED",
    "GOLDENMATCH_FS_EVIDENCE_CUT",
    "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in _ENV:
        monkeypatch.delenv(var, raising=False)


def _diag(**update) -> R.CutDiagnostics:
    fields = dict(
        proportion_matched=0.01,
        lo=-10.0,
        hi=14.0,
        midpoint_bits=2.0,
        prior_bits=6.63,
        n_fields=2,
        has_negative_evidence=False,
        field_weight_spans={"name": 16.0, "zip": 8.0},
        admitted_fraction=None,
    )
    fields.update(update)
    return R.CutDiagnostics(**fields)


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


def _em(lam: float = 0.01):
    # lo = -10, hi = 14, midpoint = 2 bits.
    return SimpleNamespace(
        match_weights={"name": [-6.0, 1.0, 10.0], "zip": [-4.0, 4.0]},
        proportion_matched=lam,
        calibrated_link_threshold=None,
    )


_SPARSE = R.CutRow(
    name="sparse", rule="evidence_12", reason="lambda under 0.05",
    when=lambda d: d.proportion_matched < 0.05,
)
_WIDE = R.CutRow(
    name="wide", rule="evidence_3", reason="more than one field", when=lambda d: d.n_fields > 1
)


def test_first_matching_row_wins_and_names_itself_in_the_reason():
    assert R.choose_cut_rule(_diag(), rows=(_SPARSE, _WIDE)) == (
        "evidence_12", "routed by sparse: lambda under 0.05",
    )
    assert R.choose_cut_rule(_diag(), rows=(_WIDE, _SPARSE)) == (
        "evidence_3", "routed by wide: more than one field",
    )


def test_no_matching_row_means_the_default():
    assert R.choose_cut_rule(_diag(proportion_matched=0.2, n_fields=1), rows=(_SPARSE, _WIDE)) is None


def test_no_diagnostics_or_a_degenerate_match_rate_routes_nothing():
    always = R.CutRow(name="always", rule="evidence_9", reason="x", when=lambda d: True)
    assert R.choose_cut_rule(None, rows=(always,)) is None
    assert R.choose_cut_rule(_diag(proportion_matched=0.0), rows=(always,)) is None
    assert R.choose_cut_rule(_diag(proportion_matched=1.0), rows=(always,)) is None


def test_a_row_must_name_a_real_rule():
    with pytest.raises(ValueError, match="unknown link cut rule"):
        R.CutRow(name="x", rule="evidence_7", reason="x", when=lambda d: True)


def test_choose_cut_rule_reads_the_shipped_table_by_default(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert R.choose_cut_rule(_diag()) == ("evidence_12", "routed by sparse: lambda under 0.05")


def test_every_shipped_row_is_a_uniquely_named_cut_row():
    assert all(isinstance(row, R.CutRow) and row.rule in R.CUT_RULES for row in R.ROWS)
    assert len({row.name for row in R.ROWS}) == len(R.ROWS)


def test_the_router_is_on_by_default(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("evidence_12", "routed by sparse: lambda under 0.05")


def test_the_kill_switch_turns_the_router_off(monkeypatch):
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    for value in ("off", "0", "false", " OFF "):
        monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", value)
        resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
        assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule"), value


def test_the_router_on_places_the_routed_rule_and_reports_it(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "evidence_12"
    assert resolved.normalized == pytest.approx((12.0 + 10.0) / 24.0)
    assert link_threshold_source(_mk(), _em()) == LINK_THRESHOLD_EVIDENCE_RULE
    report = link_cut_report(_mk(), _em())
    assert (report["cut_rule"], report["cut_reason"]) == (
        "evidence_12", "routed by sparse: lambda under 0.05",
    )


def test_a_pinned_rule_beats_the_router(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(link_cut_rule="evidence_5"), _em(), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("evidence_5", "pinned by link_cut_rule")


def test_no_matching_row_falls_through_to_the_default_rule(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(0.2), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule (router: no row matched)")


def test_router_off_keeps_the_plain_default_reason(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    resolved = _fs_resolved_cut(_mk(), _em(0.2), calibrated=False)
    assert (resolved.rule, resolved.reason) == ("prior_mid", "default rule")


def test_a_declined_route_is_reported(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    report = link_cut_report(_mk(), _em(0.2))
    assert (report["cut_rule"], report["cut_reason"]) == (
        "prior_mid", "default rule (router: no row matched)",
    )


def test_the_router_with_the_default_rule_off_places_only_routed_cuts(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert _fs_resolved_cut(_mk(), _em(0.01), calibrated=False).rule == "evidence_12"
    assert _fs_resolved_cut(_mk(), _em(0.2), calibrated=False) is None


def test_posterior_scoring_bypasses_the_router(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    assert _fs_resolved_cut(_mk(), _em(), calibrated=True) is None
    assert link_cut_report(_mk(), _em())["cut_rule"] is None


def test_a_routed_rule_the_model_cannot_supply_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    otsu = R.CutRow(name="needs_hist", rule="otsu", reason="x", when=lambda d: True)
    monkeypatch.setattr(R, "ROWS", (otsu,))
    resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "prior_mid"
    assert resolved.reason == (
        "otsu unavailable: needs a training histogram of more than 50 pairs; default rule"
    )


def test_an_unknown_router_value_warns_and_keeps_the_router_on(monkeypatch, caplog):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "maybe")
    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    with caplog.at_level(logging.WARNING):
        resolved = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    assert resolved.rule == "evidence_12"
    assert "GOLDENMATCH_FS_CUT_ROUTER" in caplog.text


#: (λ, midpoint bits, prior bits, n_fields) recorded by design matrix run 34638483397.
_DESIGN_SHAPES = {
    "dblp_acm": (0.00196, 2.87, 8.99, 3),
    "synth_biblio_d02": (0.0005, 5.39, 10.97, 3),
    "synth_person_d02": (0.00185, 0.0, 9.08, 5),
    "musicbrainz_20k": (0.0757, 17.3, 3.61, 5),
    "febrl3": (0.0826, 10.5, 3.47, 9),
    "febrl4": (0.042, 13.96, 4.51, 9),
    "dblp_scholar": (0.0646, 22.23, 3.86, 3),
    "historical_50k": (0.661, 10.82, -0.96, 6),
}

# Both rows shipped:
_EXPECTED_ROUTES = {
    "dblp_acm": "posterior_099", "synth_biblio_d02": "posterior_099", "synth_person_d02": None,
    "musicbrainz_20k": "evidence_5", "febrl3": "evidence_5", "febrl4": "evidence_5",
    "dblp_scholar": None, "historical_50k": None,
}


def test_the_shipped_rows_fire_where_the_design_matrix_says():
    """Known positives: removing a shipped row turns its datasets back to None and fails this."""
    routed = {}
    for name, (lam, mid, prior, n_fields) in _DESIGN_SHAPES.items():
        choice = R.choose_cut_rule(
            _diag(proportion_matched=lam, midpoint_bits=mid, prior_bits=prior, n_fields=n_fields)
        )
        routed[name] = None if choice is None else choice[0]
    assert routed == _EXPECTED_ROUTES


# ─── P5: the routing path is cheap and never reads admitted_fraction ──────────


def test_routing_diagnostics_skip_the_admitted_fraction_pass(monkeypatch):
    em = _em()
    em.training_score_histogram = {"counts": [5] * 100, "lo": -10.0, "hi": 14.0}
    calls: list[str] = []
    real = R.rule_bits
    monkeypatch.setattr(R, "rule_bits", lambda rule, *a, **k: calls.append(rule) or real(rule, *a, **k))

    lean = R.cut_diagnostics(_mk(), em, admitted=False)
    assert lean is not None and lean.admitted_fraction is None
    assert calls == [], "routing must not compute any rule cutoff for admitted_fraction"

    full = R.cut_diagnostics(_mk(), em)
    assert full.admitted_fraction is not None and calls
    same = {k: v for k, v in vars(lean).items() if k != "admitted_fraction"}
    assert same == {k: v for k, v in vars(full).items() if k != "admitted_fraction"}


def test_the_router_routes_on_diagnostics_without_admitted_fraction(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "on")
    em = _em()
    em.training_score_histogram = {"counts": [5] * 100, "lo": -10.0, "hi": 14.0}
    blind = R.CutRow(
        name="blind", rule="evidence_12", reason="admitted_fraction is absent",
        when=lambda d: d.admitted_fraction is None,
    )
    monkeypatch.setattr(R, "ROWS", (blind,))
    assert _fs_resolved_cut(_mk(), em, calibrated=False).rule == "evidence_12"


def test_route_false_keeps_the_router_out_even_when_it_is_on(monkeypatch):
    """P5: the Spark tier resolves with route=False; a firing row must not move its cut."""
    from goldenmatch.core.probabilistic import resolve_thresholds

    monkeypatch.setattr(R, "ROWS", (_SPARSE,))
    routed = _fs_resolved_cut(_mk(), _em(), calibrated=False)
    unrouted = _fs_resolved_cut(_mk(), _em(), calibrated=False, route=False)
    assert routed.rule == "evidence_12"
    assert (unrouted.rule, unrouted.reason) == ("prior_mid", "default rule")
    monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
    off_link, _ = resolve_thresholds(_mk(), _em())
    monkeypatch.delenv("GOLDENMATCH_FS_CUT_ROUTER")
    assert resolve_thresholds(_mk(), _em(), route=False)[0] == off_link
