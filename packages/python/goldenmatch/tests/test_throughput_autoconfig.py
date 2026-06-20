"""Tests for throughput blocking forcing + kwarg threading in auto-config (#1083)."""
import polars as pl
import pytest
from goldenmatch.core import autoconfig
from goldenmatch.core.throughput_verify import ThroughputNotApplicableError


def _corpus_df():
    return pl.DataFrame({"body": ["the cat sat", "the cat sat on the mat",
                                  "an entirely separate sentence about dogs"] * 5})


def test_throughput_forces_lsh_on_text_column(monkeypatch):
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)
    cfg = autoconfig.auto_configure_df(_corpus_df(), throughput=0.95)
    assert cfg.blocking.strategy == "lsh"
    assert cfg.blocking.lsh.column == "body"


def test_throughput_uses_simhash_when_embedder_available(monkeypatch):
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: True)
    cfg = autoconfig.auto_configure_df(_corpus_df(), throughput=True)
    assert cfg.blocking.strategy == "simhash"


def test_throughput_raises_without_text_column():
    df = pl.DataFrame({"zip": [10001, 10002, 10003], "age": [20, 30, 40]})
    with pytest.raises(ThroughputNotApplicableError):
        autoconfig.auto_configure_df(df, throughput=True)