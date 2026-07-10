# tests/test_polars_lazy.py
"""The lazy-Polars proxy: real attributes, cached module, no import at module load."""
from __future__ import annotations


def test_proxy_returns_real_polars_attributes():
    import polars as real_pl
    from goldenmatch._polars_lazy import pl

    assert pl.DataFrame is real_pl.DataFrame
    assert pl.Utf8 is real_pl.Utf8


def test_proxy_caches_module():
    from goldenmatch._polars_lazy import _LazyPolars

    proxy = _LazyPolars()
    assert proxy._mod is None
    _ = proxy.DataFrame
    assert proxy._mod is not None
    mod_after_first = proxy._mod
    _ = proxy.Series
    assert proxy._mod is mod_after_first


def test_isinstance_works_through_proxy():
    from goldenmatch._polars_lazy import pl

    df = pl.DataFrame({"a": [1, 2]})
    assert isinstance(df, pl.DataFrame)
