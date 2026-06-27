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
