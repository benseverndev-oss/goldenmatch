"""Stage B parity gate: the native string scorers, exposed as Rust-crate FFI
ScalarUDFs, register into the Python ``datafusion`` SessionContext via
datafusion-ffi (PyCapsule) and score correctly.

``pyarrow``/``datafusion`` are soft deps (skip if absent), but
``goldenmatch_datafusion_udf`` is a HARD import: the CI lane builds that crate
before pytest, so an import failure means the build broke and MUST surface as a
test FAILURE, not a silent skip. This is the loud guard for the DataFusion spine.

The FFI UDFs delegate to the shared ``goldenmatch-score-core`` crate
(rapidfuzz 0.5.0) -- the SAME algorithms the Python ``rapidfuzz`` package wraps --
so we diff the FFI scores against ``rapidfuzz`` directly (``test_native_parity``
already proves rust-rapidfuzz == python-rapidfuzz at 1e-9). This needs no
``_native`` build, so it does not pull native-only tests into this lane.
"""

import pytest

pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")
rapidfuzz = pytest.importorskip("rapidfuzz")  # noqa: F841  core dep; present
import goldenmatch_datafusion_udf  # noqa: E402,F401  HARD import (loud guard, no importorskip)
from rapidfuzz import fuzz  # noqa: E402
from rapidfuzz.distance import JaroWinkler, Levenshtein  # noqa: E402

# Fixture covering: identical, single-char typo, reordered tokens, empty string,
# NULL (None -> "" convention), unicode, and fully-disjoint strings.
PAIRS = [
    ("john smith", "john smith"),
    ("john smith", "john smyth"),
    ("john michael smith", "smith john michael"),
    ("", ""),
    (None, "john smith"),
    ("jöhn smîth", "john smith"),
    ("abcdef", "zyxwvu"),
]


def _pairs_table():
    a = [p[0] for p in PAIRS]
    b = [p[1] for p in PAIRS]
    return pa.table({"a": pa.array(a, pa.string()), "b": pa.array(b, pa.string())})


def test_ffi_string_scorers_match_rapidfuzz():
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import JaroWinklerUDF, LevenshteinUDF, TokenSortUDF

    ctx = SessionContext()
    ctx.register_udf(udf(JaroWinklerUDF()))
    ctx.register_udf(udf(TokenSortUDF()))
    ctx.register_udf(udf(LevenshteinUDF()))
    ctx.from_arrow(_pairs_table(), name="pairs")

    # Row order is not guaranteed across collect(); carry the inputs through so
    # we compare each FFI row against the rapidfuzz score for the SAME pair.
    batches = ctx.sql(
        "SELECT a, b, "
        "jaro_winkler(a, b) AS jw, "
        "token_sort(a, b) AS ts, "
        "levenshtein(a, b) AS lev "
        "FROM pairs"
    ).collect()

    rows = []
    for batch in batches:
        cols = {name: batch.column(i).to_pylist() for i, name in enumerate(batch.schema.names)}
        for i in range(batch.num_rows):
            rows.append((cols["a"][i], cols["b"][i], cols["jw"][i], cols["ts"][i], cols["lev"][i]))

    assert len(rows) == len(PAIRS)

    for a, b, jw, ts, lev in rows:
        # NULL -> "" convention, matching the FFI UDF (and native.py's gate).
        sa = "" if a is None else a
        sb = "" if b is None else b

        # score-core jaro_winkler / levenshtein are normalized_similarity [0, 1];
        # the FFI UDF returns [0, 1] -> compare to rapidfuzz directly.
        assert jw == pytest.approx(
            JaroWinkler.normalized_similarity(sa, sb), abs=1e-6
        ), f"jaro_winkler mismatch for {(a, b)!r}"
        assert lev == pytest.approx(
            Levenshtein.normalized_similarity(sa, sb), abs=1e-6
        ), f"levenshtein mismatch for {(a, b)!r}"
        # FFI token_sort is [0, 1]; rapidfuzz fuzz.token_sort_ratio is [0, 100].
        assert ts == pytest.approx(
            fuzz.token_sort_ratio(sa, sb) / 100.0, abs=1e-6
        ), f"token_sort mismatch for {(a, b)!r}"


def test_ffi_connected_components():
    """The set-consuming graph kernel, exposed as an FFI ScalarUDF over List
    columns, returns the same components as the in-process graph-core kernel.

    One row carries the whole edge set + id universe as four List columns; the
    UDF returns one List<List<Int64>> per row (the list of components). Edge
    (1,2) + (2,3) transitively groups {1,2,3}; node 4 is an isolated singleton.
    """
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import ConnectedComponentsUDF

    ctx = SessionContext()
    ctx.register_udf(udf(ConnectedComponentsUDF()))
    t = pa.table(
        {
            "ia": pa.array([[1, 2]], pa.list_(pa.int64())),
            "ib": pa.array([[2, 3]], pa.list_(pa.int64())),
            "s": pa.array([[0.9, 0.8]], pa.list_(pa.float64())),
            "ids": pa.array([[1, 2, 3, 4]], pa.list_(pa.int64())),
        }
    )
    ctx.from_arrow(t, name="edges")
    out = ctx.sql(
        "SELECT goldenmatch_connected_components(ia, ib, s, ids) AS comps FROM edges"
    ).collect()
    comps = out[0].column(0).to_pylist()[0]  # list of components for row 0
    norm = sorted(sorted(c) for c in comps)
    assert norm == [[1, 2, 3], [4]], norm


def test_ffi_pair_dedup():
    """The pair-dedup graph kernel, exposed as an FFI ScalarUDF over List
    columns, canonicalizes each pair (min,max) and keeps the max score.

    Input pairs (2,1,0.5) and (1,2,0.9) canonicalize to the same (1,2); the
    0.9 score wins. (3,3,0.1) is a distinct self-pair. Output is one
    List<Struct<a,b,s>> per row.
    """
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import PairDedupUDF

    ctx = SessionContext()
    ctx.register_udf(udf(PairDedupUDF()))
    t = pa.table(
        {
            "ia": pa.array([[2, 1, 3]], pa.list_(pa.int64())),
            "ib": pa.array([[1, 2, 3]], pa.list_(pa.int64())),
            "s": pa.array([[0.5, 0.9, 0.1]], pa.list_(pa.float64())),
        }
    )
    ctx.from_arrow(t, name="pairs")
    out = ctx.sql("SELECT goldenmatch_pair_dedup(ia, ib, s) AS pd FROM pairs").collect()
    rows = out[0].column(0).to_pylist()[0]  # list of {a,b,s} structs for row 0
    got = sorted((r["a"], r["b"], r["s"]) for r in rows)
    assert got == [(1, 2, 0.9), (3, 3, 0.1)], got
