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
