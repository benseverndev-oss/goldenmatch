"""Tests for the unsupervised convergence helper (Task 2 of the suggester-gym).

All four tests monkeypatch review_config / apply_suggestion so no native
kernel, no real pipeline, and no ground truth are needed.
"""
from __future__ import annotations

from scripts.suggest_quality import converge as C


class _Sugg:
    def __init__(self, sid): self.id = sid


def test_stops_when_no_suggestions(monkeypatch):
    monkeypatch.setattr(C, "review_config", lambda df, cfg, verify=True: [])
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert trail == []


def test_applies_until_empty(monkeypatch):
    calls = {"n": 0}
    def fake_review(df, cfg, verify=True):
        if calls["n"] >= 2: return []
        return [_Sugg(f"s{calls['n']}")]
    def fake_apply(cfg, s):
        calls["n"] += 1
        return {"applied": s.id}
    monkeypatch.setattr(C, "review_config", fake_review)
    monkeypatch.setattr(C, "apply_suggestion", fake_apply)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert [s.id for s in trail] == ["s0", "s1"]


def test_cycle_guard_breaks_on_repeated_id(monkeypatch):
    monkeypatch.setattr(C, "review_config", lambda df, cfg, verify=True: [_Sugg("same")])
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0})
    assert len(trail) == 1


def test_respects_step_cap(monkeypatch):
    counter = {"i": 0}
    def fake_review(df, cfg, verify=True):
        counter["i"] += 1
        return [_Sugg(f"s{counter['i']}")]
    monkeypatch.setattr(C, "review_config", fake_review)
    monkeypatch.setattr(C, "apply_suggestion", lambda cfg, s: cfg)
    final, trail = C.converge_unsupervised(df=object(), config={"v": 0}, step_cap=4)
    assert len(trail) == 4
