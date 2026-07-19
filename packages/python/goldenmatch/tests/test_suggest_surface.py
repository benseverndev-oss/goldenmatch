"""Tests for the free headroom trigger in goldenmatch.core.suggest.surface.

``headroom_signal(result)`` is PURE and FREE: it reads only
``result.postflight_report.controller_history`` (already computed by the
controller) and returns a ``HeadroomReason`` when the committed run shows
degraded health (RED/YELLOW) or a score-histogram dip; ``None`` otherwise.
It never raises and never calls the kernel / re-runs the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from goldenmatch.core.complexity_profile import (
    ComplexityProfile,
    HealthVerdict,
    ScoringProfile,
)
from goldenmatch.core.suggest.surface import HeadroomReason, headroom_signal

# --- minimal fakes for the controller_history shape -------------------------


@dataclass
class _FakeEntry:
    """Stands in for HistoryEntry: exposes a ``.profile``."""

    profile: object


class _FakeHistory:
    """Stands in for RunHistory: ``pick_committed()`` returns the entry."""

    def __init__(self, entry):
        self._entry = entry

    def pick_committed(self, *args, **kwargs):  # match the real no-arg-callable form
        return self._entry


class _FakeReport:
    def __init__(self, history):
        self.controller_history = history


class _FakeResult:
    def __init__(self, report):
        self.postflight_report = report


def _result_for_profile(profile) -> _FakeResult:
    return _FakeResult(_FakeReport(_FakeHistory(_FakeEntry(profile))))


def _profile(health: HealthVerdict, dip: float = 0.0) -> ComplexityProfile:
    """A real ComplexityProfile whose health() yields ``health`` and whose
    scoring carries ``dip``. We drive health() via a tiny subclass override so
    we don't have to hand-craft sub-profiles for every verdict."""

    class _P(ComplexityProfile):
        def health(self_inner) -> HealthVerdict:  # noqa: N805
            return health

    return _P(scoring=ScoringProfile(dip_statistic=dip, n_pairs_scored=100))


# --- tests ------------------------------------------------------------------


def test_committed_red_returns_health_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.RED)))
    assert reason is not None
    assert isinstance(reason, HeadroomReason)
    assert "health" in reason.kind
    assert "RED" in reason.kind or "red" in reason.kind


def test_committed_yellow_returns_health_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.YELLOW)))
    assert reason is not None
    assert "health" in reason.kind


def test_green_no_dip_returns_none():
    assert headroom_signal(_result_for_profile(_profile(HealthVerdict.GREEN, dip=0.0))) is None


def test_green_with_dip_returns_dip_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.GREEN, dip=0.2)))
    assert reason is not None
    assert reason.kind == "dip"


def test_postflight_report_none_returns_none():
    assert headroom_signal(_FakeResult(None)) is None


def test_controller_history_none_returns_none():
    # explicit-config path: controller never ran
    assert headroom_signal(_FakeResult(_FakeReport(None))) is None


def test_malformed_history_never_raises():
    class _Boom:
        def pick_committed(self, *a, **k):
            raise RuntimeError("boom")

    result = _FakeResult(_FakeReport(_Boom()))
    assert headroom_signal(result) is None


def test_committed_none_returns_none():
    # pick_committed() can legitimately return None (every entry errored)
    class _NoneHistory:
        def pick_committed(self, *a, **k):
            return None

    assert headroom_signal(_FakeResult(_FakeReport(_NoneHistory()))) is None


def test_result_without_postflight_attr_returns_none():
    assert headroom_signal(object()) is None


def test_maybe_suggest_skips_kernel_when_no_headroom(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    called = {"n": 0}
    def _spy(*a, **k):
        called["n"] += 1; return []
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result", _spy)
    monkeypatch.setattr(surf, "headroom_signal", lambda r: None)  # no headroom
    assert surf.maybe_suggest(object(), None) == []
    assert called["n"] == 0   # kernel/suggest_from_result NOT called when no headroom


def test_maybe_suggest_kill_switch(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_ON_DEDUPE", "0")
    monkeypatch.setattr(surf, "headroom_signal", lambda r: surf.HeadroomReason("health:RED"))
    called = {"n": 0}
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result",
                        lambda *a, **k: called.__setitem__("n", called["n"]+1) or [])
    assert surf.maybe_suggest(object(), None) == []
    assert called["n"] == 0   # kill-switch short-circuits before the kernel


def test_maybe_suggest_delegates_when_headroom(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_ON_DEDUPE", raising=False)
    monkeypatch.setattr(surf, "headroom_signal", lambda r: surf.HeadroomReason("dip"))
    sentinel = ["S"]
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result",
                        lambda result, df, verify=False: sentinel)
    assert surf.maybe_suggest("RES", "DF", verify=False) is sentinel


def test_serialize_suggestions_shape_and_verified_flag():
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion
    s = Suggestion(id="x", kind="lower_threshold", target="mk.threshold",
                   current_value=0.9, proposed_value=0.85, rationale="why",
                   predicted_effect="", confidence=0.7, patch={"a": 1}, evidence={})
    out = surf.serialize_suggestions([s], verified=True)
    assert out == [{"id": "x", "kind": "lower_threshold", "target": "mk.threshold",
                    "rationale": "why", "verified": True, "patch": {"a": 1}}]
    assert surf.serialize_suggestions([s], verified=False)[0]["verified"] is False


def test_heal_applies_in_order_then_stops(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion

    s1 = Suggestion(id="s1", kind="lower_threshold", target="t", current_value=None,
                    proposed_value=None, rationale="", predicted_effect="", confidence=1.0,
                    patch={}, evidence={})
    s2 = Suggestion(id="s2", kind="swap_scorer", target="t", current_value=None,
                    proposed_value=None, rationale="", predicted_effect="", confidence=1.0,
                    patch={}, evidence={})
    seq = [[s1], [s2], []]   # suggest returns s1, then s2, then nothing
    calls = {"sugg": 0, "apply": []}

    class _Res:  # stand-in DedupeResult
        config = "CFG0"
    monkeypatch.setattr("goldenmatch._api.dedupe_df", lambda df, *, config=None, **k: _Res())
    def _sugg(res, df, *, verify=False, max_verify=None):
        i = calls["sugg"]; calls["sugg"] += 1
        return seq[i] if i < len(seq) else []
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result", _sugg)
    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion",
                        lambda cfg, s: calls["apply"].append(s.id) or f"{cfg}+{s.id}")

    out = surf.heal("DF", "CFG0")
    assert [s.id for s in out.trail] == ["s1", "s2"]
    assert calls["apply"] == ["s1", "s2"]
    assert out.result is not None


def test_heal_cycle_guard_breaks_on_repeated_id(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion
    s1 = Suggestion(id="s1", kind="lower_threshold", target="t", current_value=None,
                    proposed_value=None, rationale="", predicted_effect="", confidence=1.0,
                    patch={}, evidence={})
    class _Res:
        config = "CFG"
    monkeypatch.setattr("goldenmatch._api.dedupe_df", lambda df, *, config=None, **k: _Res())
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result",
                        lambda res, df, *, verify=False, max_verify=None: [s1])   # ALWAYS returns s1
    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion",
                        lambda cfg, s: cfg)
    out = surf.heal("DF", "CFG", step_cap=5)
    assert [s.id for s in out.trail] == ["s1"]   # applied once, then cycle-guard breaks


def test_heal_step_cap(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion
    n = {"i": 0}
    def _sugg(res, df, *, verify=False, max_verify=None):
        n["i"] += 1
        return [Suggestion(id=f"s{n['i']}", kind="k", target="t", current_value=None,
                           proposed_value=None, rationale="", predicted_effect="",
                           confidence=1.0, patch={}, evidence={})]
    class _Res:
        config = "CFG"
    monkeypatch.setattr("goldenmatch._api.dedupe_df", lambda df, *, config=None, **k: _Res())
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result", _sugg)
    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion", lambda cfg, s: cfg)
    out = surf.heal("DF", "CFG", step_cap=3)
    assert len(out.trail) == 3   # stops at the cap (unique ids each step)


# --- #1404: bounded heal-loop cost (cache + tunable caps + early-stop) -------


def test_variant_risk_cache_scopes_one_scan_across_calls(monkeypatch):
    # blocking_risk is O(distinct^2) per column; the cache runs it once per frame
    # across a variant_risk_cache() scope. Assert it fires once for repeated
    # _cached_blocking_risk calls in-scope, and every call outside a scope.
    import goldenmatch.core.suggest.adapter as ad

    calls = {"n": 0}

    def _fake_blocking_risk(frame):
        calls["n"] += 1
        return {"name": 0.1}

    monkeypatch.setattr("goldenmatch.core.quality.blocking_risk", _fake_blocking_risk)

    class _DF:  # only .select is exercised by _cached_blocking_risk
        def select(self, cols):
            return cols

    df = _DF()
    cols = ["name", "city"]

    # Outside a scope: computed every call.
    ad._cached_blocking_risk(df, cols)
    ad._cached_blocking_risk(df, cols)
    assert calls["n"] == 2

    # Inside a scope: computed once, then served from cache.
    calls["n"] = 0
    with ad.variant_risk_cache():
        r1 = ad._cached_blocking_risk(df, cols)
        r2 = ad._cached_blocking_risk(df, cols)
        assert calls["n"] == 1
        assert r1 == r2 == {"name": 0.1}
        # A different column set is a distinct key -> a second scan.
        ad._cached_blocking_risk(df, ["name"])
        assert calls["n"] == 2

    # Cache is torn down on scope exit.
    calls["n"] = 0
    ad._cached_blocking_risk(df, cols)
    assert calls["n"] == 1


def test_cached_blocking_risk_fail_open(monkeypatch):
    import goldenmatch.core.suggest.adapter as ad

    def _boom(frame):
        raise RuntimeError("goldencheck exploded")

    monkeypatch.setattr("goldenmatch.core.quality.blocking_risk", _boom)

    class _DF:
        def select(self, cols):
            return cols

    with ad.variant_risk_cache():
        assert ad._cached_blocking_risk(_DF(), ["name"]) == {}


def test_resolve_max_verify_kwarg_env_default(monkeypatch):
    import goldenmatch.core.suggest.adapter as ad

    monkeypatch.delenv("GOLDENMATCH_SUGGEST_MAX_VERIFY", raising=False)
    assert ad._resolve_max_verify(None) == ad._MAX_VERIFY_CANDIDATES
    assert ad._resolve_max_verify(3) == 3
    assert ad._resolve_max_verify(0) == 1  # clamped to >= 1
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_MAX_VERIFY", "2")
    assert ad._resolve_max_verify(None) == 2
    assert ad._resolve_max_verify(5) == 5  # explicit kwarg beats env
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_MAX_VERIFY", "garbage")
    assert ad._resolve_max_verify(None) == ad._MAX_VERIFY_CANDIDATES


def test_verify_suggestions_honors_max_verify(monkeypatch):
    # _verify_suggestions caps the per-candidate pipeline re-runs at the resolved
    # fan-out; the tail passes through unverified. Prove max_verify=1 re-runs once.
    import goldenmatch.core.suggest.adapter as ad
    from goldenmatch.core.suggest.types import Suggestion

    def _mk(i):
        return Suggestion(id=f"s{i}", kind="lower_threshold", target="t",
                          current_value=None, proposed_value=None, rationale="",
                          predicted_effect="", confidence=1.0, patch={}, evidence={})

    sugs = [_mk(0), _mk(1), _mk(2)]
    runs = {"n": 0}

    class _Engine:
        def _run_pipeline(self, df, cfg):
            runs["n"] += 1
            class _R:
                clusters = {}
            return _R()

    class _DF:
        height = 10

    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion",
                        lambda cfg, s: cfg)
    monkeypatch.setattr(
        "goldenmatch.core.suggest.health.suggestion_health_from_clusters",
        lambda clusters, n: 1.0,
    )

    out = ad._verify_suggestions(sugs, _DF(), "CFG", {}, _Engine(), max_verify=1)
    assert runs["n"] == 1              # only the top candidate was re-run
    assert [s.id for s in out] == ["s0", "s1", "s2"]  # tail passes through


def test_resolve_step_cap_and_min_health_gain(monkeypatch):
    import goldenmatch.core.suggest.surface as surf

    monkeypatch.delenv("GOLDENMATCH_HEAL_STEP_CAP", raising=False)
    monkeypatch.delenv("GOLDENMATCH_HEAL_MIN_HEALTH_GAIN", raising=False)
    assert surf._resolve_step_cap(None) == surf._HEAL_STEP_CAP
    assert surf._resolve_step_cap(3) == 3
    assert surf._resolve_step_cap(0) == 1
    monkeypatch.setenv("GOLDENMATCH_HEAL_STEP_CAP", "2")
    assert surf._resolve_step_cap(None) == 2

    assert surf._resolve_min_health_gain(None) is None  # off by default
    assert surf._resolve_min_health_gain(0.0) == 0.0
    monkeypatch.setenv("GOLDENMATCH_HEAL_MIN_HEALTH_GAIN", "0.01")
    assert surf._resolve_min_health_gain(None) == 0.01


def test_heal_early_stop_disabled_by_default(monkeypatch):
    # Without min_health_gain (default), the health path is inert: heal runs to
    # step_cap even when clusters are present and health never improves.
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion

    monkeypatch.delenv("GOLDENMATCH_HEAL_MIN_HEALTH_GAIN", raising=False)

    class _Res:
        config = "CFG"
        clusters = {"h": 0.5}  # constant health -> would stop early IF enabled

    class _DF:
        height = 100

    monkeypatch.setattr("goldenmatch._api.dedupe_df",
                        lambda df, *, config=None, **k: _Res())
    monkeypatch.setattr(
        "goldenmatch.core.suggest.health.suggestion_health_from_clusters",
        lambda clusters, n: clusters["h"],
    )
    n = {"i": 0}
    def _sugg(res, df, *, verify=False, max_verify=None):
        n["i"] += 1
        return [Suggestion(id=f"s{n['i']}", kind="k", target="t", current_value=None,
                           proposed_value=None, rationale="", predicted_effect="",
                           confidence=1.0, patch={}, evidence={})]
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result", _sugg)
    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion",
                        lambda cfg, s: cfg)

    out = surf.heal(_DF(), "CFG", step_cap=3)
    assert len(out.trail) == 3  # ran the full cap; early-stop off by default


def test_heal_early_stop_strictly_below_threshold(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion

    # health improves by 0.005 each step; min_health_gain=0.01 -> stop on step 2.
    healths = iter([0.20, 0.205, 0.99, 0.99])

    class _Res:
        config = "CFG"
        def __init__(self):
            self.clusters = {"h": next(healths)}

    class _DF:
        height = 100

    monkeypatch.setattr("goldenmatch._api.dedupe_df",
                        lambda df, *, config=None, **k: _Res())
    monkeypatch.setattr(
        "goldenmatch.core.suggest.health.suggestion_health_from_clusters",
        lambda clusters, n: clusters["h"],
    )
    n = {"i": 0}
    def _sugg(res, df, *, verify=False, max_verify=None):
        n["i"] += 1
        return [Suggestion(id=f"s{n['i']}", kind="k", target="t", current_value=None,
                           proposed_value=None, rationale="", predicted_effect="",
                           confidence=1.0, patch={}, evidence={})]
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result", _sugg)
    monkeypatch.setattr("goldenmatch.core.suggest.apply.apply_suggestion",
                        lambda cfg, s: cfg)

    out = surf.heal(_DF(), "CFG", step_cap=10, min_health_gain=0.01)
    # step0: health 0.20, apply s1. step1: health 0.205, gain 0.005 < 0.01 -> STOP.
    assert [s.id for s in out.trail] == ["s1"]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
