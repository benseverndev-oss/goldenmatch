import polars as pl
import pytest
from goldenpipe.adapters import match as match_mod
from goldenpipe.adapters.match import DedupeStage
from goldenpipe.models.context import PipeContext


class _Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, df, **kwargs):
        self.calls.append(kwargs)

        class _R:
            clusters = pl.DataFrame({"cluster_id": [0]})
            golden = pl.DataFrame({"x": [1]})
            unique = pl.DataFrame({"x": [1]})
        return _R()


def _ctx(stage_config):
    ctx = PipeContext(df=pl.DataFrame({"x": ["a", "b"]}))
    ctx.stage_config = stage_config
    return ctx


def test_hint_routes_to_throughput_not_override(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}))
    assert len(rec.calls) == 1
    kw = rec.calls[0]
    assert kw.get("throughput") is not None
    assert kw.get("config") is None


def test_full_config_still_overrides(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({"exact": ["x"]}))
    assert len(rec.calls) == 1
    kw = rec.calls[0]
    assert kw.get("config") is not None
    assert kw.get("throughput") is None


def test_no_config_uses_auto(monkeypatch):
    rec = _Recorder()
    monkeypatch.setattr(match_mod, "_dedupe", rec)
    DedupeStage().run(_ctx({}))
    assert len(rec.calls) == 1
    assert rec.calls[0].get("config") is None
    assert rec.calls[0].get("throughput") is None


def test_throughput_type_accepted_end_to_end():
    pytest.importorskip("goldenmatch")
    df = pl.DataFrame({
        "name": ["Ann", "Ann", "Bob", "Bob", "Cara"] * 60,
        "city": ["NY", "NY", "LA", "LA", "SF"] * 60,
    })
    ctx = PipeContext(df=df)
    ctx.stage_config = {"_dedupe_hints": {"throughput": {"recall_target": 0.95}}}
    res = DedupeStage().run(ctx)
    assert res.status.name == "SUCCESS"
    assert "golden" in ctx.artifacts
