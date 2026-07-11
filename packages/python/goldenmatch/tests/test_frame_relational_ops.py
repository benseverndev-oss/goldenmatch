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


# ---- W2c ops: columnar-spine port fixtures -----------------------------------


def _meta_tbl() -> pa.Table:
    # cluster-metadata shaped corpus: split-preserved, weak-by-gap, strong,
    # size-1, and a null-edge row (pins the when()-null fall-through).
    return pa.table(
        {
            "cluster_id": pa.array([1, 2, 3, 4, 5], type=pa.int64()),
            "size": pa.array([3, 2, 2, 1, 2], type=pa.int64()),
            "confidence": pa.array([0.9, 0.8, 0.7, 1.0, 0.6], type=pa.float64()),
            "quality": pa.array(["split", "strong", "strong", "strong", "strong"]),
            "oversized": pa.array([True, False, False, False, False], type=pa.bool_()),
            "bottleneck_pair_a": pa.array([0, 0, 0, 0, 0], type=pa.int64()),
            "bottleneck_pair_b": pa.array([0, 0, 0, 0, 0], type=pa.int64()),
            "min_edge": pa.array([0.0, 0.5, 0.85, 0.0, None], type=pa.float64()),
            "avg_edge": pa.array([0.0, 0.9, 0.9, 0.0, None], type=pa.float64()),
        }
    )


def test_select_and_delegation() -> None:
    tbl = pa.table({"a": pa.array([1]), "b": pa.array([2]), "c": pa.array([3])})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        got = frame.select(["c", "a"])
        assert got.columns == ["c", "a"]
    assert pf.select(["c", "a"]).native.equals(pf.native.select(["c", "a"]))


def test_filter_eq_and_not_in() -> None:
    tbl = pa.table({"cid": pa.array([1, 2, 1, None, 3], type=pa.int64()),
                    "i": pa.array([0, 1, 2, 3, 4], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        assert frame.filter_eq("cid", 1).column("i").to_list() == [0, 2]
        # null cid drops on BOTH ops (polars null-mask semantics).
        assert frame.filter_not_in("cid", [1]).column("i").to_list() == [1, 4]


def test_filter_ne_cols_null_drops() -> None:
    tbl = pa.table({"a": pa.array(["x", "x", None, "y"], type=pa.string()),
                    "b": pa.array(["x", "z", "x", None], type=pa.string()),
                    "i": pa.array([0, 1, 2, 3], type=pa.int64())})
    pf, af = _pair(tbl)
    # Columnar-engine parity: null comparison -> row DROPS (rows 2 and 3).
    for frame in (pf, af):
        assert frame.filter_ne_cols("a", "b").column("i").to_list() == [1]


def test_filter_nonblank_key_semantics() -> None:
    tbl = pa.table({"mk": pa.array(["ok", "", "  ", None, "x||y"], type=pa.string()),
                    "i": pa.array([0, 1, 2, 3, 4], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        # DROPS "" and whitespace-only (opposite of filter_valid_key).
        assert frame.filter_nonblank_key("mk").column("i").to_list() == [0, 4]


def test_filter_nonblank_key_nonstring_casts() -> None:
    # strict=False cast contract: int keys stringify, so they survive.
    tbl = pa.table({"mk": pa.array([7030, None, 12], type=pa.int64()),
                    "i": pa.array([0, 1, 2], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        assert frame.filter_nonblank_key("mk").column("i").to_list() == [0, 2]


def test_filter_target_split_xor() -> None:
    tbl = pa.table({"id_a": pa.array([1, 1, 3, 4], type=pa.int64()),
                    "id_b": pa.array([5, 2, 1, 6], type=pa.int64()),
                    "i": pa.array([0, 1, 2, 3], type=pa.int64())})
    pf, af = _pair(tbl)
    targets = [1, 2]
    # row0: a in, b out -> KEEP; row1: BOTH in -> drop;
    # row2: a out, b in -> KEEP; row3: BOTH out -> drop.
    for frame in (pf, af):
        assert frame.filter_target_split("id_a", "id_b", targets).column("i").to_list() == [0, 2]


def test_with_fill_null() -> None:
    tbl = pa.table({"min_edge": pa.array([0.5, None], type=pa.float64()),
                    "avg_edge": pa.array([None, 0.9], type=pa.float64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        got = frame.with_fill_null(["min_edge", "avg_edge"], 0.0)
        assert got.column("min_edge").to_list() == [0.5, 0.0]
        assert got.column("avg_edge").to_list() == [0.0, 0.9]


def test_map_column_maps_and_raises_on_unmapped() -> None:
    tbl = pa.table({"id_a": pa.array([10, 20], type=pa.int64())})
    pf, af = _pair(tbl)
    mapping = {10: 1, 20: 2}
    for frame in (pf, af):
        got = frame.map_column("id_a", "__cid__", mapping)
        assert got.column("__cid__").to_list() == [1, 2]
    for frame in (pf, af):
        with pytest.raises(Exception):  # replace_strict raises; arrow twin raises ValueError
            frame.map_column("id_a", "__cid__", {10: 1})


def test_apply_weak_quality_parity() -> None:
    pf, af = _pair("m", _meta_tbl()) if False else (
        PolarsFrame(pl.from_arrow(_meta_tbl())), ArrowFrame(_meta_tbl()))
    threshold = 0.3
    want_q = ["split", "weak", "strong", "strong", "strong"]  # null gap falls through
    want_conf = [0.9, 0.8 * 0.7, 0.7, 1.0, 0.6]
    for frame in (pf, af):
        got = frame.apply_weak_quality(threshold)
        assert got.column("quality").to_list() == want_q
        assert got.column("confidence").to_list() == pytest.approx(want_conf)
    # delegation parity: byte-equal to the raw Step-3 expression.
    raw = pf.native.with_columns(
        pl.when(pl.col("quality") == "split").then(pl.col("quality"))
        .when((pl.col("size") > 1) & ((pl.col("avg_edge") - pl.col("min_edge")) > threshold))
        .then(pl.lit("weak")).otherwise(pl.lit("strong")).alias("quality"),
    ).with_columns(
        pl.when(pl.col("quality") == "weak")
        .then(pl.col("confidence") * 0.7).otherwise(pl.col("confidence"))
        .alias("confidence"),
    )
    assert pf.apply_weak_quality(threshold).native.equals(raw)


def test_select_eligible_clusters() -> None:
    pf, af = PolarsFrame(pl.from_arrow(_meta_tbl())), ArrowFrame(_meta_tbl())
    # size>1 AND not oversized: clusters 2, 3, 5 (1 is oversized, 4 is size-1).
    for frame in (pf, af):
        got = frame.select_eligible_clusters()
        assert got.columns == ["cluster_id"]
        assert sorted(got.column("cluster_id").to_list()) == [2, 3, 5]


def test_frame_from_rows_tuples_and_dicts() -> None:
    from goldenmatch.core.frame import frame_from_rows

    schema = {"cluster_id": "int64", "member_id": "int64"}
    for backend in ("polars", "arrow"):
        f = frame_from_rows([(1, 10), (1, 11)], schema, backend=backend)
        assert f.column("member_id").to_list() == [10, 11]
        g = frame_from_rows(
            [{"cluster_id": 2, "member_id": 20}], schema, backend=backend
        )
        assert g.column("cluster_id").to_list() == [2]
        e = frame_from_rows([], schema, backend=backend)
        assert e.height == 0 and set(e.columns) == set(schema)


def test_concat_columns_unique() -> None:
    from goldenmatch.core.frame import concat_columns

    tbl = pa.table({"id_a": pa.array([1, 2], type=pa.int64()),
                    "id_b": pa.array([2, 3], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        merged = concat_columns([frame.column("id_a"), frame.column("id_b")])
        assert sorted(merged.unique().to_list()) == [1, 2, 3]


def test_schema_specs_exported() -> None:
    from goldenmatch.core.frame import CLUSTER_METADATA_SCHEMA_SPEC, PAIR_STREAM_SCHEMA_SPEC

    assert PAIR_STREAM_SCHEMA_SPEC == {"id_a": "int64", "id_b": "int64", "score": "float64"}
    assert list(CLUSTER_METADATA_SCHEMA_SPEC) == [
        "cluster_id", "size", "confidence", "quality", "oversized",
        "bottleneck_pair_a", "bottleneck_pair_b", "min_edge", "avg_edge",
    ]


def test_cross_source_filter_dual_backend() -> None:
    # W2c arrow twin of scorer._cross_source_filter_df (the shared helper
    # that replaced the mirror-flagged twins): identical pair survival on
    # both backends. E2e arrow arrives with W2d; this pins the helper.
    from goldenmatch.core.scorer import _cross_source_filter_df

    lookup = {1: "a", 2: "b", 3: "a", 4: "a"}
    pairs = pa.table(
        {
            "id_a": pa.array([1, 1, 3], type=pa.int64()),
            "id_b": pa.array([2, 3, 4], type=pa.int64()),
            "score": pa.array([0.9, 0.8, 0.7], type=pa.float64()),
        }
    )
    got_pl = _cross_source_filter_df(pl.from_arrow(pairs), lookup)
    got_pa = _cross_source_filter_df(pairs, lookup)
    # only (1,2) crosses sources; (1,3) and (3,4) are same-source.
    assert got_pl["id_a"].to_list() == [1] and got_pl["id_b"].to_list() == [2]
    assert got_pa.column("id_a").to_pylist() == [1]
    assert got_pa.column("id_b").to_pylist() == [2]
    assert set(got_pl.columns) == set(got_pa.column_names) == {"id_a", "id_b", "score"}


# ---- W2d ops -----------------------------------------------------------------


def test_with_column_and_literal() -> None:
    tbl = pa.table({"a": pa.array([1, 2], type=pa.int64())})
    pf, af = _pair(tbl)
    for frame in (pf, af):
        derived = frame.derive_transformed_column("a", [])
        got = frame.with_column("__key__", derived).with_literal_column("__source__", "s1")
        assert got.column("__key__").to_list() == ["1", "2"]
        assert got.column("__source__").to_list() == ["s1", "s1"]
    # overwrite semantics: attaching an existing name replaces the column.
    for frame in (pf, af):
        replaced = frame.with_column("a", frame.derive_transformed_column("a", []))
        assert replaced.column("a").to_list() == ["1", "2"]
        assert replaced.columns == ["a"]


def test_group_partitions_unsorted_and_nulls() -> None:
    # THE trap group_partitions exists for: a key recurring NON-adjacently
    # must form ONE group (partition_by_key's run-slicing would split it).
    tbl = pa.table(
        {
            "k": pa.array(["x", "y", "x", None, "y", "x"], type=pa.string()),
            "i": pa.array([0, 1, 2, 3, 4, 5], type=pa.int64()),
        }
    )
    pf, af = _pair(tbl)
    for frame in (pf, af):
        parts = frame.group_partitions("k")
        got = {k: p.column("i").to_list() for k, p in parts}
        assert got == {"x": [0, 2, 5], "y": [1, 4], None: [3]}
        # first-appearance order
        assert [k for k, _ in parts] == ["x", "y", None]


# ---- W3a ops: controller/profiling reductions ---------------------------------


def _w3_pair(cols: dict) -> tuple[PolarsFrame, ArrowFrame]:
    tbl = pa.table(cols)
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


def test_w3_column_reductions_parity() -> None:
    pf, af = _w3_pair({"n": pa.array([1.0, 2.0, 3.0, None], type=pa.float64())})
    for frame in (pf, af):
        c = frame.column("n")
        assert c.drop_nulls().to_list() == [1.0, 2.0, 3.0]
        assert c.sum() == 6.0
        assert c.mean() == 2.0
        assert c.min() == 1.0
        assert c.std() == 1.0  # ddof=1 pinned
        assert c.fill_null(0.0).to_list() == [1.0, 2.0, 3.0, 0.0]


def test_w3_value_counts_desc_includes_nulls_pinned_order() -> None:
    pf, af = _w3_pair({"c": pa.array(["a", "b", "a", None, "a", "b"], type=pa.string())})
    want = [("a", 3), ("b", 2), (None, 1)]  # count desc; null in the tail tie
    for frame in (pf, af):
        assert frame.column("c").value_counts_desc() == want


def test_w3_value_counts_tie_order() -> None:
    pf, af = _w3_pair({"c": pa.array(["x", "y", "x", "y"], type=pa.string())})
    for frame in (pf, af):
        assert frame.column("c").value_counts_desc() == [("x", 2), ("y", 2)]


def test_w3_str_len_chars_codepoints() -> None:
    pf, af = _w3_pair({"c": pa.array(["héllo", "", None, "aé£€"], type=pa.string())})
    for frame in (pf, af):
        assert frame.column("c").str_len_chars().to_list() == [5, 0, None, 4]


def test_w3_blank_count() -> None:
    pf, af = _w3_pair({"c": pa.array(["x", "", "  ", None, "\t"], type=pa.string())})
    for frame in (pf, af):
        assert frame.column("c").blank_count() == 3


def test_w3_cast_str_both_strict_flavors() -> None:
    pf, af = _w3_pair({"n": pa.array([7030, None], type=pa.int64())})
    for strict in (True, False):
        want = pf.column("n").cast_str(strict=strict).to_list()
        got = af.column("n").cast_str(strict=strict).to_list()
        assert got == want == ["7030", None]


def test_w3_semantic_dtype() -> None:
    import datetime as dt

    pf, af = _w3_pair(
        {
            "t": pa.array(["x"], type=pa.string()),
            "f": pa.array([1.5], type=pa.float64()),  # arrow says "double"
            "i": pa.array([1], type=pa.int64()),
            "d": pa.array([dt.date(2020, 1, 1)], type=pa.date32()),
            "b": pa.array([True], type=pa.bool_()),
        }
    )
    for frame in (pf, af):
        assert frame.column("t").semantic_dtype() == "text"
        assert frame.column("f").semantic_dtype() == "numeric"  # THE double fix
        assert frame.column("i").semantic_dtype() == "numeric"
        assert frame.column("d").semantic_dtype() == "date"
        assert frame.column("b").semantic_dtype() == "bool"


def test_w3_sample_contract() -> None:
    tbl = pa.table({"i": pa.array(list(range(100)), type=pa.int64())})
    pf, af = PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)
    for frame in (pf, af):
        s1 = frame.sample(10, seed=42)
        s2 = frame.sample(10, seed=42)
        assert s1.height == s2.height == 10
        # deterministic per (seed, backend)
        assert s1.column("i").to_list() == s2.column("i").to_list()
        # no duplicates
        assert len(set(s1.column("i").to_list())) == 10
        # n > height raises (polars ShapeError parity)
        with pytest.raises(Exception):
            frame.sample(1000, seed=1)


def test_w3_with_row_index_and_head() -> None:
    pf, af = _w3_pair({"c": pa.array(["a", "b", "c"], type=pa.string())})
    for frame in (pf, af):
        idx = frame.with_row_index("__row__")
        assert idx.columns[0] == "__row__"
        assert idx.column("__row__").to_list() == [0, 1, 2]
        assert frame.head(2).column("c").to_list() == ["a", "b"]


def test_w3_joint_n_unique_null_combos() -> None:
    pf, af = _w3_pair(
        {
            "a": pa.array([1, 1, None, 1], type=pa.int64()),
            "b": pa.array([2, 3, 2, 2], type=pa.int64()),
        }
    )
    for frame in (pf, af):
        assert frame.joint_n_unique(["a", "b"]) == 3  # (1,2) dup; null combo counts


def test_w3_group_nunique_drops_either_null() -> None:
    pf, af = _w3_pair(
        {
            "k": pa.array(["x", "x", "y", None, "y"], type=pa.string()),
            "v": pa.array(["s1", "s2", "s1", "s1", None], type=pa.string()),
        }
    )
    for frame in (pf, af):
        got = frame.group_nunique("k", "v")
        d = dict(zip(got.column("k").to_list(), got.column("n_unique").to_list()))
        # null-k row and null-v row both dropped BEFORE grouping.
        assert d == {"x": 2, "y": 1}


def test_w3_coverage_ratio_edges() -> None:
    pf, af = _w3_pair(
        {
            "a": pa.array(["x", None, "z", None], type=pa.string()),
            "b": pa.array([1.0, 2.0, None, float("nan")], type=pa.float64()),
        }
    )
    for frame in (pf, af):
        # pass [a]: rows 0,2; pass [b]: rows 0,1,3 (NaN is non-null!) -> union all 4
        assert frame.coverage_ratio([["a"], ["b"]]) == 1.0
        # single pass [a,b]: both non-null -> row 0 and row 3? a[3]=None -> just row 0
        assert frame.coverage_ratio([["a", "b"]]) == 0.25
        # missing column: pass contributes nothing
        assert frame.coverage_ratio([["nope"]]) == 0.0
        # empty pass list
        assert frame.coverage_ratio([]) == 0.0
        # empty fields INSIDE a pass covers everything (pinned; unreachable today)
        assert frame.coverage_ratio([[]]) == 1.0
    # height == 0
    e = pa.table({"a": pa.array([], type=pa.string())})
    assert ArrowFrame(e).coverage_ratio([["a"]]) == 0.0
    assert PolarsFrame(pl.from_arrow(e)).coverage_ratio([["a"]]) == 0.0


def test_w3_distinct_row_count() -> None:
    pf, af = _w3_pair(
        {
            "a": pa.array([1, 1, 2, None], type=pa.int64()),
            "b": pa.array(["x", "x", "y", "z"], type=pa.string()),
        }
    )
    for frame in (pf, af):
        assert frame.distinct_row_count() == 3
