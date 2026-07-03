"""Task 6: healer wired into dedupe_df (default surface + suggest=/heal=).

The no-op parity guard (default path, trigger off) is the load-bearing test:
the public dedupe path must stay byte-identical when the healer does not fire.
"""
import polars as pl
from goldenmatch import dedupe_df


def _df():
    return pl.DataFrame({"name": ["a a", "a a", "b b"], "email": ["a@x", "a@x", "b@y"]})


def test_default_dedupe_noop_when_no_headroom(monkeypatch):
    # Force the trigger off -> suggestions stays [], no kernel call, behavior unchanged.
    import goldenmatch.core.suggest.surface as surf
    monkeypatch.setattr(surf, "headroom_signal", lambda r: None)
    res = dedupe_df(_df())
    assert res.suggestions == []
    assert res.heal_trail is None


def test_default_attaches_serialized_candidates_when_triggered(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion
    monkeypatch.setattr(surf, "headroom_signal", lambda r: surf.HeadroomReason("dip"))
    s = Suggestion(id="x", kind="lower_threshold", target="t", current_value=None,
                   proposed_value=None, rationale="why", predicted_effect="",
                   confidence=1.0, patch={}, evidence={})
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result",
                        lambda result, df, *, verify=False: [s])
    res = dedupe_df(_df())
    assert res.suggestions and res.suggestions[0]["id"] == "x"
    assert res.suggestions[0]["verified"] is False   # default path = unverified candidates


def test_suggest_true_attaches_verified(monkeypatch):
    from goldenmatch.core.suggest.types import Suggestion
    s = Suggestion(id="v", kind="k", target="t", current_value=None, proposed_value=None,
                   rationale="", predicted_effect="", confidence=1.0, patch={}, evidence={})
    monkeypatch.setattr("goldenmatch.core.suggest.adapter.suggest_from_result",
                        lambda result, df, *, verify=False: [s])
    res = dedupe_df(_df(), suggest=True)
    assert res.suggestions[0]["verified"] is True


def test_heal_true_returns_healed(monkeypatch):
    import goldenmatch.core.suggest.surface as surf
    from goldenmatch.core.suggest.types import Suggestion
    s = Suggestion(id="h", kind="k", target="t", current_value=None, proposed_value=None,
                   rationale="", predicted_effect="", confidence=1.0, patch={}, evidence={})
    base = dedupe_df(_df())  # a real result to hand back as the "healed" one
    monkeypatch.setattr(surf, "heal",
                        lambda df, config, **k: surf.HealOutcome(config="HEALED_CFG", trail=[s], result=base))
    res = dedupe_df(_df(), heal=True)
    assert res.config == "HEALED_CFG"
    assert res.heal_trail and res.heal_trail[0]["id"] == "h" and res.heal_trail[0]["verified"] is True


# ── Production slowdown regression: the default path must not run the O(distinct^2)
#    goldencheck variant scan (blocking_risk). Fake the native kernel so the REAL
#    signal-build path executes even without the wheel; record any blocking_risk call.


class _FakeKernel:
    def suggest_config(self, *args):
        return "[]"          # valid-empty -> _parse_suggestions returns []


def _record_blocking_risk(monkeypatch):
    """Patch the SOURCE (blocking_risk is a function-local import inside
    _build_column_signals_batch, so the adapter attribute can't be patched)."""
    calls = {"n": 0}

    def _rec(df, *a, **k):
        calls["n"] += 1
        return {}

    monkeypatch.setattr("goldenmatch.core.quality.blocking_risk", _rec)
    return calls


def test_default_dedupe_df_does_not_call_blocking_risk(monkeypatch):
    """The default advisory `dedupe_df` path must NOT run goldencheck's
    O(distinct^2) fuzzy-variant scan — it was the production slowdown (350ms-950ms
    per moderate-cardinality column, on every run whose free trigger fired)."""
    import goldenmatch.core.suggest.adapter as adapter
    import goldenmatch.core.suggest.surface as surf

    # Force the free trigger to fire and fake the kernel so the real signal build runs.
    monkeypatch.setattr(surf, "headroom_signal", lambda r: surf.HeadroomReason("dip"))
    monkeypatch.setattr(adapter, "_require_kernel", lambda: _FakeKernel())
    calls = _record_blocking_risk(monkeypatch)

    res = dedupe_df(_df())
    assert calls["n"] == 0, "default dedupe_df must not run the goldencheck variant scan"
    assert res.suggestions == []


def test_suggest_true_still_computes_variant_signal(monkeypatch):
    """The opt-in verified path keeps full fidelity — it DOES compute variant_rate."""
    import goldenmatch.core.suggest.adapter as adapter

    monkeypatch.setattr(adapter, "_require_kernel", lambda: _FakeKernel())
    calls = _record_blocking_risk(monkeypatch)

    dedupe_df(_df(), suggest=True)   # verify=True -> full signal build
    assert calls["n"] >= 1, "suggest=True must still compute the variant signal"
