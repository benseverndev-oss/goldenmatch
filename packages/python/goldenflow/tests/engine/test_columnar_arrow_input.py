"""#2447: ``transform()`` accepts a pyarrow Table/RecordBatch Polars-free.

The engine is Polars-free and `transform()` already runs a covered config over a
``dict[str, list]``. It also reads ``transform("<path>.parquet")`` WITH pyarrow (the
``[parquet]`` extra) and transforms it Polars-free. What it rejected was the
``pa.Table`` you get from reading that same file yourself -- redirecting to
``transform_df()``, the one entry point that cannot serve a Polars-free caller. So
an Arrow-native pipeline got no standardization at all, and (per the report) full
runs went out with transforms silently off.

An Arrow frame IS a typed column dict, so `to_pydict()` is the whole conversion.
The detection is attribute-based so it never imports pyarrow -- an optional extra
here.
"""
from __future__ import annotations

import goldenflow
import pytest
from goldenflow.engine.columnar import _is_arrow_frame

pa = pytest.importorskip("pyarrow", reason="arrow input needs goldenflow[parquet]")


# ── detection ────────────────────────────────────────────────────────────────


def test_detects_table_and_record_batch():
    assert _is_arrow_frame(pa.table({"c": ["x"]}))
    assert _is_arrow_frame(pa.record_batch([pa.array(["x"])], names=["c"]))


def test_does_not_capture_a_polars_frame():
    """`pl.DataFrame` uses `to_dict`/`columns`, not `to_pydict`/`column_names`.
    If this ever inverted, polars frames would silently take the arrow path."""
    pl = pytest.importorskip("polars")
    assert not _is_arrow_frame(pl.DataFrame({"c": ["x"]}))


@pytest.mark.parametrize("obj", [{"c": ["x"]}, "data.csv", 42, None, ["x"]])
def test_does_not_capture_other_inputs(obj):
    assert not _is_arrow_frame(obj)


def test_detection_does_not_import_pyarrow(monkeypatch):
    """pyarrow is an OPTIONAL extra; a dict caller must not pay an import for the
    check. Guards the reason this is duck-typed instead of `isinstance`."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise AssertionError("detection must not import pyarrow")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert not _is_arrow_frame({"c": ["x"]})
    assert not _is_arrow_frame("data.csv")


# ── the reported case ────────────────────────────────────────────────────────


def test_arrow_table_transforms_with_a_full_manifest():
    """The exact shape from #2447: the columnar API produced cleaned values and a
    5-record manifest, while the same data as a pa.Table produced nothing."""
    res = goldenflow.transform(pa.table({"city": ["  St. Louis  ", "cincinnati"]}))
    assert res.columns == {"city": ["St. Louis", "cincinnati"]}
    assert res.manifest.records, "arrow input must produce an audit manifest"
    assert {r.transform for r in res.manifest.records} >= {"strip"}


def test_arrow_matches_the_equivalent_dict_exactly():
    """Arrow is a shape, not a semantic -- it must not change the result."""
    cols = {"city": ["  St. Louis  ", "cincinnati", None], "n": ["1", "2", "3"]}
    from_dict = goldenflow.transform(dict(cols))
    from_arrow = goldenflow.transform(pa.table(cols))
    assert from_arrow.columns == from_dict.columns
    assert [(r.column, r.transform, r.affected_rows) for r in from_arrow.manifest.records] == \
           [(r.column, r.transform, r.affected_rows) for r in from_dict.manifest.records]


def test_record_batch_works_too():
    rb = pa.record_batch([pa.array(["  x  "])], names=["c"])
    assert goldenflow.transform(rb).columns == {"c": ["x"]}


def test_nulls_survive_the_conversion():
    res = goldenflow.transform(pa.table({"c": ["  a  ", None]}))
    assert res.columns["c"] == ["a", None]


def test_dict_input_is_unaffected():
    assert goldenflow.transform({"city": ["  y  "]}).columns == {"city": ["y"]}


# ── transform_df redirects rather than dying deep ────────────────────────────


def test_transform_df_redirects_arrow_to_transform():
    """Deliberately NOT a second polars-free door. Before this, an arrow caller got
    `ModuleNotFoundError: No module named 'polars'` from inside the engine, which
    named the missing package but not the supported path two functions away."""
    with pytest.raises(TypeError) as ei:
        goldenflow.transform_df(pa.table({"city": ["a"]}))
    msg = str(ei.value)
    assert "transform()" in msg
    assert "polars-free" in msg


def test_the_rejection_message_lists_arrow_as_accepted():
    """An unsupported input should not send an arrow user to transform_df()."""
    with pytest.raises(TypeError) as ei:
        goldenflow.transform(42)
    assert "RecordBatch" in str(ei.value)
