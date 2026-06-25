import copy
from goldenmatch.core.suggest import adapter as A

def _cfg():
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField, BlockingConfig, BlockingKeyConfig,
    )
    mk = MatchkeyConfig(name="person", type="weighted", threshold=0.85, fields=[
        MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
        MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
    ])
    return GoldenMatchConfig(matchkeys=[mk],
                             blocking=BlockingConfig(strategy="static",
                                                     keys=[BlockingKeyConfig(fields=["last_name"])]))

def test_full_dist_default_off(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_SUGGEST_FULL_DIST", raising=False)
    assert A._full_dist_enabled() is False

def test_full_dist_on_when_1(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_SUGGEST_FULL_DIST", "1")
    assert A._full_dist_enabled() is True

def test_diagnostic_config_forces_all_thresholds_to_zero():
    cfg = _cfg()
    diag = A._zero_threshold_config(cfg)
    assert all(mk.threshold == 0.0 for mk in diag.get_matchkeys())
    # original untouched (immutability)
    assert cfg.get_matchkeys()[0].threshold == 0.85
    # blocking unchanged (candidate set must be identical)
    assert diag.blocking == cfg.blocking
