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
    assert f.column("b").dtype == "bool"


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


def test_column_scalar_reductions():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [3, 1, 2, 5, 4]})).column("x")
    assert col.min() == 1
    assert col.max() == 5
    assert col.mean() == 3.0
    assert col.std() == pl.Series([3, 1, 2, 5, 4]).std()   # ddof=1 preserved


def test_column_diff_and_is_sorted():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 2, 4]})).column("x")
    assert col.diff().drop_nulls().to_list() == [1, 2]     # leading null dropped
    assert col.is_sorted() is True
    unsorted = to_frame(pl.DataFrame({"x": [3, 1, 2]})).column("x")
    assert unsorted.is_sorted() is False


def test_column_count_gt_and_count_eq():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 1, 2, 3, 0]})).column("x")
    assert col.count_gt(0) == 4
    assert col.count_eq(1) == 2
    assert isinstance(col.count_gt(0), int)


def test_column_count_gt_datetime_scalar():
    import datetime as dt

    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [dt.date(2020, 1, 1), dt.date(2999, 1, 1)]})).column("x")
    assert col.count_gt(dt.date(2100, 1, 1)) == 1


def test_column_filter_outside():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [1, 5, 10, 50, 100])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    # values < 5 or > 50 -> [1, 100], original order preserved
    assert col.filter_outside(5, 50).to_list() == s.filter((s < 5) | (s > 50)).to_list()
    assert col.filter_outside(5, 50).to_list() == [1, 100]


def test_column_slice_positional_halves():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [10, 20, 30, 40, 50])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    mid = 5 // 2   # 2
    # first half == s[:mid], second half == s[mid:]
    assert col.slice(0, mid).to_list() == s.slice(0, mid).to_list() == [10, 20]
    assert col.slice(mid).to_list() == s.slice(mid).to_list() == [30, 40, 50]
    # slice(mid) with no length runs to the end (matches s[mid:])
    assert col.slice(mid).to_list() == s[mid:].to_list()

def test_column_cast_str():
    import polars as pl
    from goldencheck.core.frame import to_frame
    col = to_frame(pl.DataFrame({"x": [1, 2, 3]})).column("x")
    assert col.cast("str", strict=True).to_list() == pl.Series([1, 2, 3]).cast(pl.String).to_list()
    assert col.cast("str", strict=True).to_list() == ["1", "2", "3"]


def test_column_str_replace_all_chain():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", ["A1", "bc23", "Z9z"])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    # letters -> L, then digits -> D (letters first, matching _generalize_series)
    got = col.str_replace_all(r"\p{L}", "L").str_replace_all(r"\d", "D").to_list()
    assert got == s.str.replace_all(r"\p{L}", "L").str.replace_all(r"\d", "D").to_list()
    assert got == ["LD", "LLDD", "LDL"]

def test_column_value_counts_desc():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", ["a", "a", "a", "b", "b", "c"])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    got = col.value_counts_desc()
    raw = s.value_counts().sort("count", descending=True)
    assert got == list(zip(raw["x"].to_list(), raw["count"].to_list()))
    assert got[0] == ("a", 3)   # most frequent first
    assert all(isinstance(cnt, int) for _, cnt in got)

def test_column_eq_and_filter_by():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({"val": ["keep1", "drop", "keep2"], "pat": ["A", "B", "A"]}))
    val = frame.column("val")
    pat = frame.column("pat")
    # eq -> boolean mask; filter_by selects val rows where pat == "A"
    assert val.filter_by(pat.eq("A")).to_list() == ["keep1", "keep2"]
    # equivalence to the raw cross-column filter
    assert val.filter_by(pat.eq("A")).to_list() == val._s.filter(pat._s == "A").to_list()


def test_column_dtype_bool_and_repr():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({
        "b": [True, False, True],
        "f": [1.0, 2.0, 3.0],
        "i": [1, 2, 3],
        "s": ["a", "b", "c"],
    }))
    # neutral dtype: Boolean now maps to "bool" (was "other"); float unchanged
    assert frame.column("b").dtype == "bool"
    assert frame.column("f").dtype == "float"
    # dtype_repr renders the raw Polars dtype string, byte-identical to str(dtype)
    assert frame.column("f").dtype_repr() == str(pl.Series([1.0]).dtype)   # "Float64"
    assert frame.column("f").dtype_repr() == "Float64"
    assert frame.column("b").dtype_repr() == "Boolean"
    assert frame.column("i").dtype_repr() == "Int64"
    assert frame.column("s").dtype_repr() == "String"


def test_column_to_arrow():
    import polars as pl
    from goldencheck.core.frame import to_frame
    s = pl.Series("x", [1, 2, 3])
    col = to_frame(pl.DataFrame({"x": s})).column("x")
    got = col.to_arrow()
    assert got.to_pylist() == s.to_arrow().to_pylist() == [1, 2, 3]


def test_column_get():
    import polars as pl
    from goldencheck.core.frame import to_frame
    frame = to_frame(pl.DataFrame({"n": [10, 20, 30], "s": ["a", "b", "c"]}))
    assert frame.column("n").get(0) == 10
    assert frame.column("n").get(2) == 30
    assert frame.column("s").get(1) == "b"
    # byte-identical to raw Series indexing (what df[c][r] does)
    assert frame.column("s").get(1) == pl.Series(["a", "b", "c"])[1]
