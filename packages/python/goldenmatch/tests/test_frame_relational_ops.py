"""W2b relational seam ops: delegation parity + cross-backend semantics.

Two test families, per the seam discipline (frame.py docstring):

1. **Delegation parity** -- PolarsFrame ops must equal the RAW Polars calls
   the engine sites use today, byte-for-byte (same rows, same order, same
   columns). PolarsFrame is regression protection; it may not drift.
2. **Cross-backend semantics** -- ArrowFrame must produce the same canonical
   result. Join/group row ORDER is NOT contractual (canonicalized here);
   sort/partition/slice/take order IS. The named hazard fixtures pin the
   engine-default differences the Arrow impls normalize: null keys never
   match, null-mask rows drop, sort puts nulls FIRST, suffix only on
   collision, left_on/right_on drops the right key.
"""

from __future__ import annotations

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.core.frame import (
    ArrowFrame,
    PolarsFrame,
    concat_frames,
    empty_frame,
    frame_from_columns,
)

# ---- helpers ---------------------------------------------------------------


def _pair(tbl: pa.Table) -> tuple[PolarsFrame, ArrowFrame]:
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


def _canon(frame) -> tuple[frozenset, list]:
    """(column set, row multiset) -- order-free canonical form."""
    cols = sorted(frame.columns)
    rows = sorted(
        tuple((c, repr(v)) for c, v in zip(cols, row))  # repr: None sorts vs strings
        for row in zip(*(frame.column(c).to_list() for c in cols))
    )
    return frozenset(cols), rows


def _rows_in_order(frame) -> list[tuple]:
    cols = list(frame.columns)
    return list(zip(*(frame.column(c).to_list() for c in cols)))


def _assert_backend_parity(pf_result, af_result) -> None:
    assert _canon(pf_result) == _canon(af_result)


# ---- corpus ----------------------------------------------------------------

_LEFT = pa.table(
    {
        "k": pa.array(["a", "b", None, "a", "c"], type=pa.string()),
        "v": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
    }
)
_RIGHT = pa.table(
    {
        "k": pa.array(["a", None, "c", "c"], type=pa.string()),
        "w": pa.array([10, 20, 30, 40], type=pa.int64()),
        "v": pa.array([100, 200, 300, 400], type=pa.int64()),  # collision col
    }
)


# ---- joins: cross-backend semantics ----------------------------------------


def test_join_inner_null_keys_never_match() -> None:
    pf_l, af_l = _pair(_LEFT)
    pf_r, af_r = _pair(_RIGHT)
    got_p = pf_l.join_inner(pf_r, on="k")
    got_a = af_l.join_inner(af_r, on="k")
    _assert_backend_parity(got_p, got_a)
    # null-k rows on either side never join.
    assert all(v is not None for v in got_p.column("k").to_list())


def test_join_inner_duplicate_keys_multiply() -> None:
    # "c" x2 on the right -> left "c" row appears twice.
    pf_l, af_l = _pair(_LEFT)
    pf_r, af_r = _pair(_RIGHT)
    got_p = pf_l.join_inner(pf_r, on="k")
    assert got_p.column("k").to_list().count("c") == 2
    _assert_backend_parity(got_p, af_l.join_inner(af_r, on="k"))


def test_join_inner_suffix_only_on_collision() -> None:
    pf_l, af_l = _pair(_LEFT)
    pf_r, af_r = _pair(_RIGHT)
    got_p = pf_l.join_inner(pf_r, on="k")
    got_a = af_l.join_inner(af_r, on="k")
    for got in (got_p, got_a):
        assert set(got.columns) == {"k", "v", "w", "v_right"}


def test_join_inner_left_on_right_on_drops_right_key() -> None:
    left = pa.table({"__row_id__": pa.array([0, 1, 2], type=pa.int64())})
    right = pa.table(
        {
            "member_id": pa.array([1, 2, 5], type=pa.int64()),
            "cid": pa.array([7, 8, 9], type=pa.int64()),
        }
    )
    pf_l, af_l = _pair(left)
    pf_r, af_r = _pair(right)
    got_p = pf_l.join_inner(pf_r, left_on="__row_id__", right_on="member_id")
    got_a = af_l.join_inner(af_r, left_on="__row_id__", right_on="member_id")
    # Polars drops the right key column on left_on/right_on inner joins
    # (golden.py:1306 relies on it); the Arrow impl must match.
    assert set(got_p.columns) == {"__row_id__", "cid"}
    assert set(got_a.columns) == {"__row_id__", "cid"}
    _assert_backend_parity(got_p, got_a)


def test_join_left_unmatched_rows_null_fill() -> None:
    pf_l, af_l = _pair(_LEFT)
    pf_r, af_r = _pair(_RIGHT)
    got_p = pf_l.join_left(pf_r, on="k")
    got_a = af_l.join_left(af_r, on="k")
    _assert_backend_parity(got_p, got_a)
    # every left row survives; "b" and null-k rows carry null w.
    assert got_p.height == got_a.height
    assert got_p.height >= _LEFT.num_rows


def test_join_empty_inputs() -> None:
    empty = pa.table({"k": pa.array([], type=pa.string()), "w": pa.array([], type=pa.int64())})
    pf_l, af_l = _pair(_LEFT)
    pf_e, af_e = _pair(empty)
    _assert_backend_parity(pf_l.join_inner(pf_e, on="k"), af_l.join_inner(af_e, on="k"))
    _assert_backend_parity(pf_l.join_left(pf_e, on="k"), af_l.join_left(af_e, on="k"))
    assert pf_l.join_inner(pf_e, on="k").height == 0
    assert pf_l.join_left(pf_e, on="k").height == _LEFT.num_rows


def test_self_join_on_pairs_once_each() -> None:
    tbl = pa.table(
        {
            "mk": pa.array(["x", "x", "x", "y", "y", None], type=pa.string()),
            "__row_id__": pa.array([0, 1, 2, 3, 4, 5], type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    want = {(0, 1), (0, 2), (1, 2), (3, 4)}  # null keys never self-match

    for frame in (pf, af):
        got = frame.self_join_on("mk", "__row_id__")
        pairs = set(
            zip(got.column("__row_id__").to_list(), got.column("__row_id___right").to_list())
        )
        assert pairs == want


def test_self_join_matches_raw_scorer_shape() -> None:
    # Delegation parity vs the literal scorer.py:391 snippet.
    df = pl.DataFrame({"mk": ["x", "x", "y"], "__row_id__": [0, 1, 2]})
    raw = df.join(df, on="mk", suffix="_right").filter(
        pl.col("__row_id__") < pl.col("__row_id___right")
    )
    got = PolarsFrame(df).self_join_on("mk", "__row_id__")
    assert got.native.equals(raw)


# ---- filters ----------------------------------------------------------------


def test_filter_valid_key_sentinels() -> None:
    tbl = pa.table(
        {
            "key": pa.array(
                ["ok", None, "nan", " NULL ", "NoNe", "", "  ", "nanx"], type=pa.string()
            ),
            "i": pa.array(list(range(8)), type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    # Keep: "ok", "" and "  " (empty is a real value -- PR #390), "nanx".
    want = [0, 5, 6, 7]
    for frame in (pf, af):
        assert frame.filter_valid_key("key").column("i").to_list() == want


def test_filter_valid_key_matches_raw_blocker_guard() -> None:
    df = pl.DataFrame({"key": ["ok", None, "nan", " NULL ", ""]})
    raw = df.filter(
        pl.col("key").is_not_null()
        & ~pl.col("key").str.strip_chars().str.to_lowercase().is_in(["nan", "null", "none"])
    )
    got = PolarsFrame(df).filter_valid_key("key")
    assert got.native.equals(raw)


def test_filter_mask_null_rows_drop() -> None:
    tbl = pa.table({"i": pa.array([0, 1, 2, 3], type=pa.int64())})
    mask_tbl = pa.table({"m": pa.array([True, False, None, True], type=pa.bool_())})
    pf, af = _pair(tbl)
    pf_m, af_m = _pair(mask_tbl)
    assert pf.filter_mask(pf_m.column("m")).column("i").to_list() == [0, 3]
    assert af.filter_mask(af_m.column("m")).column("i").to_list() == [0, 3]


# ---- group / partition / order ----------------------------------------------


def test_group_len_values() -> None:
    tbl = pa.table({"k": pa.array(["a", "b", "a", "a", None], type=pa.string())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        got = frame.group_len(["k"])
        counts = dict(zip(got.column("k").to_list(), got.column("len").to_list()))
        assert counts == {"a": 3, "b": 1, None: 1}


def test_group_len_matches_raw_polars() -> None:
    df = pl.DataFrame({"k": ["a", "b", "a"]})
    raw = df.group_by(["k"]).agg(pl.len())
    got = PolarsFrame(df).group_len(["k"])
    assert sorted(got.native.rows()) == sorted(raw.rows())
    assert got.native.columns == raw.columns


def test_sort_stable_nulls_first() -> None:
    tbl = pa.table(
        {
            "k": pa.array(["b", None, "a", "b", "a"], type=pa.string()),
            "i": pa.array([0, 1, 2, 3, 4], type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    for frame in (pf, af):
        got = frame.sort(["k"])
        assert got.column("k").to_list() == [None, "a", "a", "b", "b"]
        # stability: ties keep input order.
        assert got.column("i").to_list() == [1, 2, 4, 0, 3]
    # delegation parity: byte-equal to the raw polars sort.
    raw = pf.native.sort(["k"], maintain_order=True)
    assert pf.sort(["k"]).native.equals(raw)


def test_partition_by_key_presorted_runs() -> None:
    tbl = pa.table(
        {
            "cid": pa.array([1, 1, 2, 3, 3, 3], type=pa.int64()),
            "i": pa.array([0, 1, 2, 3, 4, 5], type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    for frame in (pf, af):
        parts = frame.partition_by_key("cid")
        assert [k for k, _ in parts] == [1, 2, 3]
        assert [p.column("i").to_list() for _, p in parts] == [[0, 1], [2], [3, 4, 5]]


def test_slice_and_take_rows() -> None:
    tbl = pa.table({"i": pa.array([10, 11, 12, 13, 14], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        assert frame.slice(1, 3).column("i").to_list() == [11, 12, 13]
        assert frame.take_rows([4, 0, 2]).column("i").to_list() == [14, 10, 12]


# ---- rename / drop -----------------------------------------------------------


def test_rename_and_drop() -> None:
    tbl = pa.table(
        {
            "a": pa.array([1], type=pa.int64()),
            "b": pa.array([2], type=pa.int64()),
            "c": pa.array([3], type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    for frame in (pf, af):
        got = frame.rename({"a": "x"}).drop(["b"])
        assert got.columns == ["x", "c"]
        assert got.column("x").to_list() == [1]


# ---- Column ops ---------------------------------------------------------------


def test_column_unique_first_appearance() -> None:
    tbl = pa.table({"c": pa.array(["b", "a", "b", None, "a"], type=pa.string())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        assert frame.column("c").unique().to_list() == ["b", "a", None]


def test_column_max_and_to_numpy() -> None:
    tbl = pa.table({"c": pa.array([3, 1, 7, 5], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        assert frame.column("c").max() == 7
        assert list(frame.column("c").to_numpy()) == [3, 1, 7, 5]


# ---- constructors --------------------------------------------------------------


def test_concat_frames_both_backends() -> None:
    a = pa.table({"i": pa.array([1, 2], type=pa.int64())})
    b = pa.table({"i": pa.array([3], type=pa.int64())})
    pf = concat_frames([PolarsFrame(pl.from_arrow(a)), PolarsFrame(pl.from_arrow(b))])
    af = concat_frames([ArrowFrame(a), ArrowFrame(b)])
    assert pf.column("i").to_list() == af.column("i").to_list() == [1, 2, 3]


def test_concat_frames_rejects_mixed_backends() -> None:
    a = pa.table({"i": pa.array([1], type=pa.int64())})
    with pytest.raises(TypeError):
        concat_frames([ArrowFrame(a), PolarsFrame(pl.from_arrow(a))])


def test_frame_from_columns_and_empty_frame() -> None:
    schema = {"id": "int64", "name": "utf8", "score": "float64", "flag": "bool"}
    data = {"id": [1, 2], "name": ["x", None], "score": [0.5, 1.5], "flag": [True, False]}
    for backend in ("polars", "arrow"):
        f = frame_from_columns(data, schema, backend=backend)
        assert f.height == 2
        assert f.column("name").to_list() == ["x", None]
        e = empty_frame(schema, backend=backend)
        assert e.height == 0
        assert set(e.columns) == set(schema)


def test_frame_from_columns_rejects_unknown_dtype() -> None:
    with pytest.raises(ValueError):
        frame_from_columns({"x": [1]}, {"x": "int32"})


def test_frame_from_columns_numpy_buffers() -> None:
    import numpy as np

    data = {"a": np.array([1, 2, 3], dtype=np.int64), "b": np.array([0.1, 0.2, 0.3])}
    schema = {"a": "int64", "b": "float64"}
    for backend in ("polars", "arrow"):
        f = frame_from_columns(data, schema, backend=backend)
        assert f.column("a").to_list() == [1, 2, 3]
