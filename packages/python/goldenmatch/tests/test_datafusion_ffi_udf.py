"""Stage B parity gate: the native string scorers, exposed as Rust-crate FFI
ScalarUDFs, register into the Python ``datafusion`` SessionContext via
datafusion-ffi (PyCapsule) and score byte-identically to the per-pair
``goldenmatch._native`` scorers.

``pyarrow`` and ``datafusion`` are soft deps (skip if absent), but
``goldenmatch_datafusion_udf`` is a HARD import on purpose: the CI lane builds
that crate before pytest, so an import failure here means the build broke and
MUST surface as a test FAILURE, not a silent skip. This is the loud guard for
the whole DataFusion spine.

The native ext is ALSO required (not importorskip): the FFI UDFs and the native
per-pair scorers both delegate to the shared ``goldenmatch-score-core`` crate,
so the whole point of this test is to diff the two surfaces. If ``_native`` is
missing the parity assertion is meaningless, so we FAIL rather than skip — the
CI lane builds ``_native`` (``scripts/build_native.py``) before pytest.
"""

import pytest

pa = pytest.importorskip("pyarrow")
datafusion = pytest.importorskip("datafusion")
import goldenmatch_datafusion_udf  # noqa: E402,F401  HARD import (loud guard, no importorskip)
from goldenmatch.core import _native_loader  # noqa: E402

# HARD requirement (not importorskip): the parity diff is meaningless without
# the native scorers to diff against. The native CI lane builds the ext.
assert _native_loader.native_available() is True, (
    "goldenmatch._native must be built for the FFI scorer-parity test "
    "(build via scripts/build_native.py)"
)

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


def test_ffi_string_scorers_match_native():
    from datafusion import SessionContext, udf
    from goldenmatch_datafusion_udf import JaroWinklerUDF, LevenshteinUDF, TokenSortUDF

    native = _native_loader.native_module()
    assert native is not None

    ctx = SessionContext()
    ctx.register_udf(udf(JaroWinklerUDF()))
    ctx.register_udf(udf(TokenSortUDF()))
    ctx.register_udf(udf(LevenshteinUDF()))
    ctx.from_arrow(_pairs_table(), name="pairs")

    # Row order is not guaranteed across collect(); carry the inputs through so
    # we compare each FFI row against the native score for the SAME pair.
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
        # NULL -> "" convention, matching both the FFI UDF and native.py's gate.
        sa = "" if a is None else a
        sb = "" if b is None else b

        # jaro_winkler / levenshtein are [0, 1] on both sides.
        assert jw == pytest.approx(
            native.jaro_winkler_similarity(sa, sb), abs=1e-6
        ), f"jaro_winkler mismatch for {(a, b)!r}"
        assert lev == pytest.approx(
            native.levenshtein_similarity(sa, sb), abs=1e-6
        ), f"levenshtein mismatch for {(a, b)!r}"
        # native token_sort_ratio is 0-100; the FFI token_sort is 0-1.
        assert ts == pytest.approx(
            native.token_sort_ratio(sa, sb) / 100.0, abs=1e-6
        ), f"token_sort mismatch for {(a, b)!r}"
