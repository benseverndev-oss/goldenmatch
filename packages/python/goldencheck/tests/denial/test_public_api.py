def test_denial_exports_present():
    import goldencheck
    assert "discover_denial_constraints" in goldencheck.__all__
    assert "DenialConstraint" in goldencheck.__all__
    from goldencheck import DenialConstraint, discover_denial_constraints
    assert callable(discover_denial_constraints)
    assert DenialConstraint.__name__ == "DenialConstraint"


def test_discover_runs_from_top_level():
    import goldencheck
    import polars as pl
    df = pl.DataFrame({"a": list(range(120)), "b": list(range(120))})  # trivial, no DCs expected
    out = goldencheck.discover_denial_constraints(df)
    assert isinstance(out, list)
