def test_lazy_proxy_returns_real_polars_objects():
    import polars as real_pl
    from goldencheck._polars_lazy import pl

    assert pl.DataFrame is real_pl.DataFrame          # attribute access returns the REAL class
    df = pl.DataFrame({"a": [1, 2]})
    assert isinstance(df, real_pl.DataFrame)          # isinstance works
    assert pl.Utf8 is real_pl.Utf8                    # dtype access works
