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
