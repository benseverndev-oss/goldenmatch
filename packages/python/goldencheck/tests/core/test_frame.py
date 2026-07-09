import polars as pl
from goldencheck.core.frame import Column, Frame, to_frame


def _f():
    return to_frame(pl.DataFrame({"a": [1, 1, 2, None], "b": ["x", "y", "x", "z"]}))


def test_frame_basics():
    f = _f()
    assert set(f.columns) == {"a", "b"}
    assert f.height == 4
    assert f.native.shape == (4, 2)                 # escape hatch = the pl.DataFrame
    assert isinstance(f, Frame)                       # runtime_checkable


def test_column_reductions_match_polars():
    f = _f()
    a = f.column("a")
    assert isinstance(a, Column)
    assert len(a) == 4                                # __len__
    assert a.null_count() == 1
    assert a.drop_nulls().n_unique() == 2
    assert a.drop_nulls().unique().sort().to_list() == [1, 2]
    assert f.column("b").n_unique() == 3


def test_to_frame_idempotent_and_rejects_other():
    f = _f()
    assert to_frame(f) is f                           # PolarsFrame passes through unchanged
    import pytest
    with pytest.raises(TypeError):
        to_frame([1, 2, 3])                            # not a DataFrame/Frame


def test_column_dtype_neutral_mapping():
    import polars as pl
    from goldencheck.core.frame import to_frame
    f = to_frame(pl.DataFrame({
        "s": ["a"], "i": pl.Series([1], dtype=pl.Int64), "u": pl.Series([1], dtype=pl.UInt32),
        "f": [1.5], "b": [True],
    }))
    assert f.column("s").dtype == "str"
    assert f.column("i").dtype == "int"
    assert f.column("u").dtype == "uint"      # DISTINCT from int (byte-identity for type_inference)
    assert f.column("f").dtype == "float"
    assert f.column("b").dtype == "other"


def test_column_cast_uncastable_to_null():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["1", "2", "oops"]})).column("x")
    casted = col.cast("float", strict=False)
    assert casted.null_count() == 1            # "oops" -> null
    assert len(casted) - casted.null_count() == 2
    assert to_frame(pl.DataFrame({"x": ["1", "2"]})).column("x").cast("int", strict=False).to_list() == [1, 2]


def test_column_member_count():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a", "b", "a", "c", None]})).column("x")
    assert col.member_count(["a", "c"]) == 3   # a,a,c ; matches int(s.is_in(v).sum())


def test_column_str_match_count():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a@b.com", "nope", "c@d.org"]})).column("x")
    assert col.str_match_count(r"@") == 2
    assert col.str_match_count(r"^z") == 0


def test_column_str_filter_matching_and_complement():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": ["a@b", "nope", "c@d"]})).column("x")
    assert col.str_filter(r"@", matching=True).to_list() == ["a@b", "c@d"]
    assert col.str_filter(r"@", matching=False).to_list() == ["nope"]
    col2 = to_frame(pl.DataFrame({"x": ["http://x", "e@f.com", "plain"]})).column("x")
    assert col2.str_filter(r"^https?://", matching=False).str_match_count(r"@") == 1
