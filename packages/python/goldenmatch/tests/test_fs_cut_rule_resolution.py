"""Link-cut rule resolution: pinned link_cut_rule > GOLDENMATCH_FS_LINEAR_CUT default."""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    LINK_THRESHOLD_CALIBRATED,
    LINK_THRESHOLD_CONFIGURED,
    LINK_THRESHOLD_EVIDENCE_RULE,
    LINK_THRESHOLD_FALLBACK,
    _fs_calibration_mode,
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
    assert got.reason == "default rule (router: no row matched)"
    assert math.isclose(got.normalized, _norm(5.0), rel_tol=1e-12)


def test_uncomputable_pin_falls_back_to_the_default_with_a_reason(monkeypatch):
    """KNOWN-POSITIVE: a pinned otsu on a model with no training histogram."""
    monkeypatch.delenv("GOLDENMATCH_FS_LINEAR_CUT", raising=False)
    got = _fs_resolved_cut(_mk(link_cut_rule="otsu"), _em(), calibrated=False)
    assert got.rule == "prior_mid"
    assert got.reason.startswith(
        "otsu unavailable: needs a training histogram of more than 50 pairs"
    )


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


@pytest.mark.parametrize(
    "case",
    ["configured", "calibrated", "pinned", "default", "fallback", "posterior"],
)
def test_both_resolvers_and_the_source_agree_on_a_pinned_rule(monkeypatch, case):
    """M2: at every precedence step, resolve_thresholds and _fs_link_threshold must agree on
    the link cutoff, and link_threshold_source must name the step that actually decided."""
    _clear_cut_env(monkeypatch)
    expected_norm = None
    if case == "configured":
        mk, em = _mk(link_cut_rule="evidence_9", link_threshold=0.42), _em()
        expected_source, expected_norm = LINK_THRESHOLD_CONFIGURED, 0.42
    elif case == "calibrated":
        mk, em = _mk(link_cut_rule="evidence_9"), _em(calibrated=0.61)
        expected_source, expected_norm = LINK_THRESHOLD_CALIBRATED, 0.61
    elif case == "pinned":
        # Default rule is off, but a pinned rule applies regardless.
        monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
        mk, em = _mk(link_cut_rule="evidence_9"), _em()
        expected_source, expected_norm = LINK_THRESHOLD_EVIDENCE_RULE, _norm(9.0)
    elif case == "default":
        mk, em = _mk(), _em()  # GOLDENMATCH_FS_LINEAR_CUT unset -> prior_mid
        expected_source = LINK_THRESHOLD_EVIDENCE_RULE
    elif case == "fallback":
        monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
        mk, em = _mk(), _em()
        expected_source = LINK_THRESHOLD_FALLBACK
    else:  # posterior mode overrides even a pinned rule
        monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
        mk, em = _mk(link_cut_rule="evidence_9"), _em()
        expected_source = LINK_THRESHOLD_FALLBACK

    posterior = _fs_calibration_mode() == "posterior"
    link, review = resolve_thresholds(mk, em)
    assert math.isclose(link, _fs_link_threshold(mk, em, calibrated=posterior), rel_tol=1e-12)
    assert review <= link
    assert link_threshold_source(mk, em) == expected_source
    if expected_norm is not None:
        assert math.isclose(link, expected_norm, rel_tol=1e-12)


def _clear_cut_env(monkeypatch):
    for var in (
        "GOLDENMATCH_FS_LINEAR_CUT", "GOLDENMATCH_FS_CALIBRATED",
        "GOLDENMATCH_FS_EVIDENCE_CUT", "GOLDENMATCH_FS_CALIBRATE_THRESHOLD",
        "GOLDENMATCH_FS_CUT_ROUTER",
    ):
        monkeypatch.delenv(var, raising=False)


def test_link_cut_report_names_the_rule_and_reason(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    report = link_cut_report(_mk(link_cut_rule="evidence_3"), _em())
    assert report["cut_rule"] == "evidence_3"
    assert report["cut_reason"] == "pinned by link_cut_rule"


def test_link_cut_report_says_why_a_pinned_rule_did_not_apply_when_a_threshold_decided_first(
    monkeypatch,
):
    """I2: a pinned link_cut_rule an earlier precedence step overrode must not silently vanish
    from the report -- cut_rule stays None, but cut_reason says which step decided first. With
    no pin, all three paths keep cut_reason None too (unchanged)."""
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)

    configured = link_cut_report(_mk(link_cut_rule="evidence_3", link_threshold=0.4), _em())
    assert configured["cut_rule"] is None
    assert configured["cut_reason"] == "link_cut_rule=evidence_3 not applied: link_threshold is set"

    calibrated = link_cut_report(_mk(link_cut_rule="evidence_3"), _em(calibrated=0.6))
    assert calibrated["cut_rule"] is None
    assert calibrated["cut_reason"] == (
        "link_cut_rule=evidence_3 not applied: calibrated cutoff decided first"
    )

    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    posterior = link_cut_report(_mk(link_cut_rule="evidence_3"), _em())
    assert posterior["cut_rule"] is None
    assert posterior["cut_reason"] == "link_cut_rule=evidence_3 not applied: posterior scoring"
    monkeypatch.delenv("GOLDENMATCH_FS_CALIBRATED", raising=False)

    # No pin: all three paths still keep cut_reason None.
    no_pin_configured = link_cut_report(_mk(link_threshold=0.4), _em())
    assert no_pin_configured["cut_rule"] is None and no_pin_configured["cut_reason"] is None
    no_pin_calibrated = link_cut_report(_mk(), _em(calibrated=0.6))
    assert no_pin_calibrated["cut_rule"] is None and no_pin_calibrated["cut_reason"] is None
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    no_pin_posterior = link_cut_report(_mk(), _em())
    assert no_pin_posterior["cut_rule"] is None and no_pin_posterior["cut_reason"] is None


def test_link_cut_report_is_silent_when_no_rule_was_asked_for(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
    report = link_cut_report(_mk(), _em())
    assert report["cut_rule"] is None and report["cut_reason"] is None


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
        "otsu unavailable: needs a training histogram of more than 50 pairs; "
        "fixed 0.50 cut applies"
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


def test_link_cut_report_carries_diagnostics(monkeypatch):
    from goldenmatch.core.probabilistic import link_cut_report

    _clear_cut_env(monkeypatch)
    report = link_cut_report(_mk(), _em())
    assert report["cut_rule"] == "prior_mid"
    assert report["cut_diagnostics"]["midpoint_bits"] == 2.0
    assert report["cut_diagnostics"]["admitted_fraction"] is None
    configured = link_cut_report(_mk(link_threshold=0.4), _em())
    assert configured["cut_rule"] is None
    assert configured["cut_diagnostics"]["midpoint_bits"] == 2.0


# ─── one resolver (P4) ────────────────────────────────────────────────────────


def test_the_four_callers_have_no_precedence_of_their_own(monkeypatch):
    """P4: `_fs_link_threshold`, `resolve_thresholds`, `link_threshold_source` and
    `link_cut_report` all read `resolve_link_cut`. Replacing it must move every one of them;
    a caller that kept its own copy of the precedence would ignore the stub."""
    import goldenmatch.core.probabilistic as P

    _clear_cut_env(monkeypatch)
    stub = P.LinkCut(
        link=0.123, source=P.LINK_THRESHOLD_CONFIGURED, rule="evidence_5", reason="stub"
    )
    monkeypatch.setattr(P, "resolve_link_cut", lambda mk, em_result, calibrated, **_kw: stub)
    mk, em = _mk(), _em()
    assert P._fs_link_threshold(mk, em, calibrated=False) == 0.123
    assert P.resolve_thresholds(mk, em)[0] == 0.123
    assert P.link_threshold_source(mk, em) == P.LINK_THRESHOLD_CONFIGURED
    report = P.link_cut_report(mk, em)
    assert (report["cut_rule"], report["cut_reason"]) == ("evidence_5", "stub")


#: case -> (link, or None to skip the check; source; cut_rule; cut_reason). Pinned from the
#: pre-P4 behaviour, so the consolidation cannot move a cutoff or a report.
_EXPECTED_CUT = {
    "configured": (0.42, LINK_THRESHOLD_CONFIGURED, None, None),
    "configured_pinned": (
        0.42, LINK_THRESHOLD_CONFIGURED, None,
        "link_cut_rule=evidence_9 not applied: link_threshold is set",
    ),
    "calibrated": (0.61, LINK_THRESHOLD_CALIBRATED, None, None),
    "calibrated_pinned": (
        0.61, LINK_THRESHOLD_CALIBRATED, None,
        "link_cut_rule=evidence_9 not applied: calibrated cutoff decided first",
    ),
    "pinned": (_norm(9.0), LINK_THRESHOLD_EVIDENCE_RULE, "evidence_9", "pinned by link_cut_rule"),
    "pinned_unavailable": (
        _norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid",
        "otsu unavailable: needs a training histogram of more than 50 pairs; default rule",
    ),
    "default": (
        _norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid",
        "default rule (router: no row matched)",
    ),
    "router_off": (_norm(math.log2(99.0)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid", "default rule"),
    "routed_row_fires": (
        1.0, LINK_THRESHOLD_EVIDENCE_RULE, "posterior_099",
        "routed by sparse_prior_binds: match rate under 0.004, the prior cutoff above the "
        "midpoint, at most 3 fields",
    ),
    "router_off_row_would_fire": (
        _norm(math.log2(0.998 / 0.002)), LINK_THRESHOLD_EVIDENCE_RULE, "prior_mid", "default rule",
    ),
    "fallback": (0.50, LINK_THRESHOLD_FALLBACK, None, None),
    "posterior": (None, LINK_THRESHOLD_FALLBACK, None, None),
    "posterior_pinned": (
        None, LINK_THRESHOLD_FALLBACK, None,
        "link_cut_rule=evidence_9 not applied: posterior scoring",
    ),
}


def _resolver_case(monkeypatch, case: str):
    _clear_cut_env(monkeypatch)
    if case == "configured":
        return _mk(link_threshold=0.42), _em()
    if case == "configured_pinned":
        return _mk(link_cut_rule="evidence_9", link_threshold=0.42), _em()
    if case == "calibrated":
        return _mk(), _em(calibrated=0.61)
    if case == "calibrated_pinned":
        return _mk(link_cut_rule="evidence_9"), _em(calibrated=0.61)
    if case == "pinned":
        return _mk(link_cut_rule="evidence_9"), _em()
    if case == "pinned_unavailable":
        return _mk(link_cut_rule="otsu"), _em()  # no training histogram -> the default rule
    if case == "default":
        return _mk(), _em()
    if case == "fallback":
        monkeypatch.setenv("GOLDENMATCH_FS_LINEAR_CUT", "off")
        return _mk(), _em()
    if case == "router_off":
        monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
        return _mk(), _em()
    if case == "routed_row_fires":
        return _mk(), _em(0.002)
    if case == "router_off_row_would_fire":
        monkeypatch.setenv("GOLDENMATCH_FS_CUT_ROUTER", "off")
        return _mk(), _em(0.002)
    monkeypatch.setenv("GOLDENMATCH_FS_CALIBRATED", "posterior")
    return (_mk(link_cut_rule="evidence_9") if case == "posterior_pinned" else _mk()), _em()


@pytest.mark.parametrize("case", sorted(_EXPECTED_CUT))
def test_resolve_link_cut_is_what_every_caller_applies_and_reports(monkeypatch, case):
    from goldenmatch.core.probabilistic import link_cut_report, resolve_link_cut

    mk, em = _resolver_case(monkeypatch, case)
    link, source, rule, reason = _EXPECTED_CUT[case]
    posterior = _fs_calibration_mode() == "posterior"
    cut = resolve_link_cut(mk, em, calibrated=posterior)

    assert (cut.source, cut.rule, cut.reason) == (source, rule, reason)
    if link is not None:
        assert math.isclose(cut.link, link, rel_tol=1e-12)
    assert _fs_link_threshold(mk, em, calibrated=posterior) == cut.link
    assert resolve_thresholds(mk, em)[0] == float(cut.link)
    assert link_threshold_source(mk, em) == source
    report = link_cut_report(mk, em)
    assert (report["cut_rule"], report["cut_reason"]) == (rule, reason)
