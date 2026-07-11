# tests/test_frame_w4_ops.py
"""W4a seam ops -- fixtures-first semantics pins for the tail/distributed ports.

Every op is pinned on BOTH backends (PolarsFrame byte-equal to the raw
polars snippet it replaces; ArrowFrame value-parity). Edge cases below were
EMPIRICALLY PROBED against polars 2026-07-11:
- replace_strict(mapping, default=d): null input maps to d (not passthrough)
- min_horizontal/max_horizontal SKIP nulls ((None,2) -> 2 for BOTH)
- is_in: null rows DROP even when None is in the values list; pc.is_in
  returns False for null -> same drop
- unique(subset, keep) result ORDER is engine-defined -> SET contract,
  kept-row identity pinned
- vertical_relaxed int->float promotion == pa concat promote_options=permissive
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.frame import (
    ArrowFrame,
    PolarsFrame,
    concat_frames,
    empty_frame,
    frame_from_records,
)


def _mk(data: dict, backend: str):
    df = pl.DataFrame(data)
    if backend == "polars":
        return PolarsFrame(df)
    return ArrowFrame(df.to_arrow())


BACKENDS = ["polars", "arrow"]


# -- filter_in ---------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_filter_in_keeps_only_members(backend):
    f = _mk({"id": [1, 2, 3, 4], "v": ["a", "b", "c", "d"]}, backend)
    out = f.filter_in("id", [3, 1])
    assert sorted(zip(out.column("id").to_list(), out.column("v").to_list())) == [
        (1, "a"),
        (3, "c"),
    ]


@pytest.mark.parametrize("backend", BACKENDS)
def test_filter_in_drops_nulls_even_when_none_listed(backend):
    # probe P6/P10: polars null.is_in -> null -> drop; pc.is_in null -> False.
    f = _mk({"id": [1, None, 3]}, backend)
    assert f.filter_in("id", [1, None]).column("id").to_list() == [1]


@pytest.mark.parametrize("backend", BACKENDS)
def test_filter_in_empty_values(backend):
    f = _mk({"id": [1, 2]}, backend)
    assert f.filter_in("id", []).height == 0


# -- with_row_index_int64 / with_int64_offset --------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_row_index_int64(backend):
    f = _mk({"v": ["a", "b", "c"]}, backend)
    out = f.with_row_index_int64("__row_id__")
    assert out.column("__row_id__").to_list() == [0, 1, 2]
    out2 = f.with_row_index_int64("__row_id__", offset=100)
    assert out2.column("__row_id__").to_list() == [100, 101, 102]


def test_with_row_index_int64_dtype_is_int64():
    # web/preview.py:189 uses pl.int_range(..., dtype=pl.Int64) -- pin Int64,
    # NOT the uint32 of the W3a with_row_index op.
    f = PolarsFrame(pl.DataFrame({"v": [1]}))
    assert f.with_row_index_int64("__row_id__").native["__row_id__"].dtype == pl.Int64


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_int64_offset(backend):
    # chunked.py:91-93 shape: (col + offset).cast(Int64), in place.
    f = _mk({"__row_id__": [0, 1, 2], "v": ["a", "b", "c"]}, backend)
    out = f.with_int64_offset("__row_id__", 1000)
    assert out.column("__row_id__").to_list() == [1000, 1001, 1002]
    assert out.columns == ["__row_id__", "v"]  # replaced in place, position kept


# -- map_column default= extension --------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_map_column_default_maps_unmapped_and_null(backend):
    # probe P1: replace_strict(mapping, default=-1) maps BOTH unmapped values
    # AND nulls to the default. pipeline.py:411-413 filters the sentinel after.
    f = _mk({"x": [1, 2, None, 9]}, backend)
    out = f.map_column("x", "y", {1: 10, 2: 20}, default=-1)
    assert out.column("y").to_list() == [10, 20, -1, -1]


@pytest.mark.parametrize("backend", BACKENDS)
def test_map_column_without_default_still_raises(backend):
    f = _mk({"x": [1, 7]}, backend)
    with pytest.raises(Exception):
        f.map_column("x", "y", {1: 10})


# -- window ops ---------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_group_len_over(backend):
    # clustering.py ~430: pl.len().over("label") per-row group size.
    f = _mk({"label": ["a", "b", "a", "a"]}, backend)
    assert f.with_group_len_over("label", "n").column("n").to_list() == [3, 1, 3, 3]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_group_len_over_null_key_is_a_group(backend):
    f = _mk({"label": ["a", None, None]}, backend)
    assert f.with_group_len_over("label", "n").column("n").to_list() == [1, 2, 2]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_group_min_over(backend):
    # clustering.py ~1234: pl.col(v).min().over(k).
    f = _mk({"cur": ["a", "a", "b"], "lbl": [5, 2, 9]}, backend)
    assert f.with_group_min_over("cur", "lbl", "mn").column("mn").to_list() == [2, 2, 9]


# -- pair canonicalization -----------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_pair_canonical(backend):
    # scoring.py ~560: min_horizontal/max_horizontal(id_a, id_b) in place.
    f = _mk({"id_a": [5, 1, 3], "id_b": [2, 4, 3]}, backend)
    out = f.with_pair_canonical("id_a", "id_b")
    assert out.column("id_a").to_list() == [2, 1, 3]
    assert out.column("id_b").to_list() == [5, 4, 3]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_pair_canonical_null_skips(backend):
    # probe P2/P11: horizontal min AND max both skip nulls -> (None, 2) -> 2/2.
    f = _mk({"id_a": [None], "id_b": [2]}, backend)
    out = f.with_pair_canonical("id_a", "id_b")
    assert out.column("id_a").to_list() == [2]
    assert out.column("id_b").to_list() == [2]


# -- unique_by ------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_unique_by_keep_first_set_contract(backend):
    # probe P3: result ORDER is engine-defined; the SET of kept rows is the pin.
    f = _mk({"k": [1, 2, 1, 3, 2], "v": ["a", "b", "c", "d", "e"]}, backend)
    out = f.unique_by(["k"])  # ONE call: row order differs between calls
    kept = sorted(zip(out.column("k").to_list(), out.column("v").to_list()))
    assert kept == [(1, "a"), (2, "b"), (3, "d")]


@pytest.mark.parametrize("backend", BACKENDS)
def test_unique_by_keep_last(backend):
    # pipeline.py:1970 shape: unique(subset=["__row_id__"], keep="last").
    f = _mk({"k": [1, 2, 1, 3, 2], "v": ["a", "b", "c", "d", "e"]}, backend)
    out = f.unique_by(["k"], keep="last")
    kept = sorted(zip(out.column("k").to_list(), out.column("v").to_list()))
    assert kept == [(1, "c"), (2, "e"), (3, "d")]


# -- concat_frames relaxed -------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_concat_frames_relaxed_promotes_int_to_float(backend):
    # probe P4/P12: vertical_relaxed int64+float64 -> float64; pa permissive same.
    a = _mk({"x": [1, 2]}, backend)
    b = _mk({"x": [1.5]}, backend)
    out = concat_frames([a, b], relaxed=True)
    assert out.column("x").to_list() == [1.0, 2.0, 1.5]


@pytest.mark.parametrize("backend", BACKENDS)
def test_concat_frames_strict_unchanged(backend):
    a = _mk({"x": [1]}, backend)
    b = _mk({"x": [2]}, backend)
    assert concat_frames([a, b]).column("x").to_list() == [1, 2]


# -- frame_from_records -----------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_frame_from_records_inference_parity(backend):
    # db connectors / a2a / tui shape: pl.DataFrame(list_of_dicts) inference.
    rows = [{"a": 1, "b": "x"}, {"a": None, "b": "y"}]
    f = frame_from_records(rows, backend=backend)
    assert f.columns == ["a", "b"]
    assert f.column("a").to_list() == [1, None]
    assert f.column("b").to_list() == ["x", "y"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_frame_from_records_empty(backend):
    f = frame_from_records([], backend=backend)
    assert f.height == 0
    assert f.columns == []


# -- datetime_us schema vocabulary -------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_empty_frame_datetime_us(backend):
    # identity/resolve.py graph-bootstrap frames use bare pl.Datetime (== us,
    # no tz); arrow twin is pa.timestamp("us").
    f = empty_frame({"id": "utf8", "created_at": "datetime_us"}, backend=backend)
    assert f.height == 0
    assert f.columns == ["id", "created_at"]
    if backend == "polars":
        assert f.native.schema["created_at"] == pl.Datetime(time_unit="us")
    else:
        import pyarrow as pa

        assert f.native.schema.field("created_at").type == pa.timestamp("us")


# -- frame_from_column_data (W4c) ------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_frame_from_column_data_inference(backend):
    from goldenmatch.core.frame import frame_from_column_data

    f = frame_from_column_data({"a": [1, None], "b": ["x", "y"]}, backend=backend)
    assert f.columns == ["a", "b"]
    assert f.column("a").to_list() == [1, None]
    assert f.column("b").to_list() == ["x", "y"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_frame_from_column_data_empty_columns(backend):
    # the connector empty-result shape: {col: [] for col in columns}
    from goldenmatch.core.frame import frame_from_column_data

    f = frame_from_column_data({"a": [], "b": []}, backend=backend)
    assert f.height == 0
    assert f.columns == ["a", "b"]


# -- with_mod_column (W4e) --------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_mod_column(backend):
    # identity_partition.py partition tag: cluster_id % num_partitions.
    f = _mk({"cluster_id": [10, 11, 12, None]}, backend)
    out = f.with_mod_column("cluster_id", 3, "__partition__")
    assert out.column("__partition__").to_list() == [1, 2, 0, None]


# -- W4e-2 clustering-kernel ops ---------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_group_min(backend):
    # clustering _wcc_groupby_min_label: group_by(id).agg(label.min()).
    f = _mk({"id": [1, 2, 1, 2], "label": [9, 4, 3, None]}, backend)
    out = f.group_min("id", "label")
    kept = sorted(zip(out.column("id").to_list(), out.column("label").to_list()))
    assert kept == [(1, 3), (2, 4)]


@pytest.mark.parametrize("backend", BACKENDS)
def test_select_cast(backend):
    f = _mk({"id": [1, 2], "label": [7, 8]}, backend)
    out = f.select_cast([("id", "int64", "member_id"), ("label", None, "cluster_id")])
    assert out.columns == ["member_id", "cluster_id"]
    assert out.column("member_id").to_list() == [1, 2]
    assert out.column("cluster_id").to_list() == [7, 8]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_gt_column(backend):
    # clustering oversized flag: (cluster_size > max).alias(...).
    f = _mk({"n": [1, 5, 3]}, backend)
    assert f.with_gt_column("n", 3, "big").column("big").to_list() == [False, True, False]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_coalesce(backend):
    # clustering shortcut/compose kernels: pl.coalesce([a, b]).
    f = _mk({"a": [None, 5, None], "b": [2, 9, None]}, backend)
    assert f.with_coalesce(["a", "b"], "c").column("c").to_list() == [2, 5, None]


@pytest.mark.parametrize("backend", BACKENDS)
def test_with_coalesce_replaces_existing_name(backend):
    # _rc_compose writes back into "cur" -- in-place replacement semantics.
    f = _mk({"rep": [None, 7], "cur": [3, 4]}, backend)
    out = f.with_coalesce(["rep", "cur"], "cur")
    assert out.column("cur").to_list() == [3, 7]
    assert out.columns == ["rep", "cur"]


@pytest.mark.parametrize("backend", BACKENDS)
def test_group_max(backend):
    # scoring dedup: group_by([id_a,id_b]).agg(score.max()), SET contract.
    f = _mk({"id_a": [1, 1, 2], "id_b": [2, 2, 3], "score": [0.5, 0.9, 0.7]}, backend)
    out = f.group_max(["id_a", "id_b"], "score")
    assert out.columns == ["id_a", "id_b", "score"]
    kept = sorted(
        zip(out.column("id_a").to_list(), out.column("id_b").to_list(), out.column("score").to_list())
    )
    assert kept == [(1, 2, 0.9), (2, 3, 0.7)]
