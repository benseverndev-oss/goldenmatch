"""W2a parity fixtures: the Arrow derivation (core/arrow_derive.py + the
ArrowFrame seam ops) must reproduce the Polars derivation value-for-value.

The PolarsFrame ops delegate to the pipeline's own expressions
(`_build_block_key_expr` / `_try_native_chain` / `apply_transforms`), so
PolarsFrame output IS the reference; every case here pins ArrowFrame == it.
Each recon hazard is a named test: LargeUtf8 target, float64/float32
stringification (the pc.cast divergence), Unicode-whitespace regex semantics,
substring length-vs-stop, concat null propagation, map_elements null skip,
dictionary/chunked inputs, null-typed columns.
"""

from __future__ import annotations

import datetime as dt

import polars as pl
import pyarrow as pa
import pytest
from goldenmatch.core.frame import ArrowFrame, PolarsFrame, to_frame

# ---- corpus --------------------------------------------------------------

_STRINGS = pa.array(
    [
        "  John SMITH ",
        "",
        None,
        "söundex Müller",
        "a\u00a0b",  # NBSP: Unicode-whitespace regex parity
        "x\ty\nz",
        "nan",
        "NULL",
        "one two  three",
        "#42 Main St.",
    ],
    type=pa.string(),
)

_CORPUS: dict[str, pa.Array] = {
    "utf8": _STRINGS,
    "large_utf8": _STRINGS.cast(pa.large_string()),
    "int64": pa.array([1, -5, 0, None], type=pa.int64()),
    "float64": pa.array(
        [1.0, 1.5, 0.1, float("nan"), float("inf"), float("-inf"), -0.0,
         1e20, 1e-05, 1e-06, 9.99e-05, 1234567.75, None],
        type=pa.float64(),
    ),
    "float32": pa.array([0.1, 1.0, float("nan"), None], type=pa.float32()),
    "bool": pa.array([True, False, None], type=pa.bool_()),
    "date32": pa.array([dt.date(2020, 1, 2), None], type=pa.date32()),
    "timestamp_us": pa.array([dt.datetime(2020, 1, 2, 3, 4, 5), None], type=pa.timestamp("us")),
    "null_typed": pa.array([None, None], type=pa.null()),
    "dict_str": pa.array(["x", "y", "x", None], type=pa.string()).dictionary_encode(),
    "empty_utf8": pa.array([], type=pa.string()),
}

_NATIVE_CHAINS = [
    [],
    ["lowercase"],
    ["uppercase"],
    ["strip"],
    ["substring:1:3"],
    ["normalize_whitespace"],
    ["strip_all"],
    ["digits_only"],
    ["alpha_only"],
    ["lowercase", "strip"],
    ["normalize_whitespace", "lowercase"],
]

# Fallback chains route BOTH backends through apply_transforms (Polars via
# map_elements, Arrow via to_pylist) -- parity plus the null-skip contract.
_FALLBACK_CHAINS = [
    ["soundex"],
    ["lowercase", "soundex"],
    ["metaphone"],
    ["token_sort"],
    ["qgram:2"],
    ["first_token"],
    ["last_token"],
]


def _pair(name: str, arr: pa.Array) -> tuple[PolarsFrame, ArrowFrame]:
    tbl = pa.table({name: arr})
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


def _ids(params: list) -> list[str]:
    return ["+".join(p) if p else "cast-only" for p in params]


# ---- derive_transformed_column parity ------------------------------------

@pytest.mark.parametrize("chain", _NATIVE_CHAINS, ids=_ids(_NATIVE_CHAINS))
@pytest.mark.parametrize("col", sorted(_CORPUS))
def test_transformed_column_parity_native(col: str, chain: list[str]) -> None:
    pf, af = _pair(col, _CORPUS[col])
    want = pf.derive_transformed_column(col, chain).to_list()
    got = af.derive_transformed_column(col, chain).to_list()
    assert got == want


@pytest.mark.parametrize("chain", _FALLBACK_CHAINS, ids=_ids(_FALLBACK_CHAINS))
@pytest.mark.parametrize("col", ["utf8", "large_utf8", "dict_str", "empty_utf8"])
def test_transformed_column_parity_fallback(col: str, chain: list[str]) -> None:
    pf, af = _pair(col, _CORPUS[col])
    want = pf.derive_transformed_column(col, chain).to_list()
    got = af.derive_transformed_column(col, chain).to_list()
    assert got == want


def test_transformed_column_returns_large_string() -> None:
    # Polars exports Utf8 as LargeUtf8; the kernel boundary type must match.
    _, af = _pair("utf8", _CORPUS["utf8"])
    pf, _ = _pair("utf8", _CORPUS["utf8"])
    assert af.derive_transformed_column("utf8", []).to_arrow().type == pa.large_string()
    assert pf.derive_transformed_column("utf8", []).to_arrow().type == pa.large_string()


def test_float64_stringification_pinned() -> None:
    # The pc.cast divergence made concrete: these exact renderings are what
    # Polars produces (probed 2026-07-10) and what score fields depend on.
    _, af = _pair("float64", _CORPUS["float64"])
    got = af.derive_transformed_column("float64", []).to_list()
    assert got == [
        "1.0", "1.5", "0.1", "NaN", "inf", "-inf", "-0.0",
        "1e+20", "0.00001", "1e-6", "0.0000999", "1234567.75", None,
    ]


def test_fallback_preserves_nulls() -> None:
    # map_elements never hands the UDF a null; the arrow fallback must not
    # stringify them either.
    pf, af = _pair("utf8", _CORPUS["utf8"])
    for frame in (pf, af):
        vals = frame.derive_transformed_column("utf8", ["soundex"]).to_list()
        assert vals[2] is None


# ---- derive_block_key parity ----------------------------------------------

def _key_pair(cols: dict[str, pa.Array]) -> tuple[PolarsFrame, ArrowFrame]:
    tbl = pa.table(cols)
    return PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)


@pytest.mark.parametrize(
    "transforms",
    [[], ["lowercase", "strip"], ["soundex"], ["substring:0:2"]],
    ids=["none", "lower+strip", "soundex-fallback", "substring"],
)
def test_block_key_single_field_parity(transforms: list[str]) -> None:
    pf, af = _key_pair({"k": _STRINGS})
    want = pf.derive_block_key(["k"], transforms).to_list()
    got = af.derive_block_key(["k"], transforms).to_list()
    assert got == want


@pytest.mark.parametrize(
    "transforms",
    [[], ["lowercase", "strip"], ["soundex"]],
    ids=["none", "lower+strip", "soundex-fallback"],
)
def test_block_key_composite_parity(transforms: list[str]) -> None:
    cols = {
        "a": pa.array(["x", None, "Smith", " Lee "], type=pa.string()),
        "b": pa.array(["1", "2", None, "3"], type=pa.string()),
    }
    pf, af = _key_pair(cols)
    want = pf.derive_block_key(["a", "b"], transforms).to_list()
    got = af.derive_block_key(["a", "b"], transforms).to_list()
    assert got == want


def test_block_key_composite_null_propagation() -> None:
    # pl.concat_str(ignore_nulls=False): ANY null field -> null key. Named
    # fixture per the plan (reviewer finding).
    cols = {
        "a": pa.array(["x", None], type=pa.string()),
        "b": pa.array(["y", "z"], type=pa.string()),
    }
    pf, af = _key_pair(cols)
    assert pf.derive_block_key(["a", "b"], []).to_list() == ["x||y", None]
    assert af.derive_block_key(["a", "b"], []).to_list() == ["x||y", None]


def test_block_key_mixed_dtype_fields_parity() -> None:
    # Non-string key fields ride the cast (zips-as-Int64 is the documented
    # ingest gotcha this guards).
    cols = {
        "zip": pa.array([7030, 7030, None], type=pa.int64()),
        "name": pa.array(["a", "b", "c"], type=pa.string()),
    }
    pf, af = _key_pair(cols)
    want = pf.derive_block_key(["zip", "name"], []).to_list()
    got = af.derive_block_key(["zip", "name"], []).to_list()
    assert got == want == ["7030||a", "7030||b", None]


# ---- utf8_values parity ----------------------------------------------------

@pytest.mark.parametrize("col", sorted(_CORPUS))
def test_utf8_values_parity(col: str) -> None:
    pf, af = _pair(col, _CORPUS[col])
    assert af.utf8_values(col) == pf.utf8_values(col)


def test_chunked_column_parity() -> None:
    chunked = pa.chunked_array([["a", "B "], ["  c", None]], type=pa.string())
    tbl = pa.table({"c": chunked})
    pf, af = PolarsFrame(pl.from_arrow(tbl)), ArrowFrame(tbl)
    assert af.utf8_values("c") == pf.utf8_values("c")
    got = af.derive_transformed_column("c", ["lowercase", "strip"]).to_list()
    want = pf.derive_transformed_column("c", ["lowercase", "strip"]).to_list()
    assert got == want


# ---- to_frame coercion (W2a additions) --------------------------------------

def test_to_frame_accepts_arrow_column_dict() -> None:
    f = to_frame({"a": pa.array([1, 2]), "b": pa.array(["x", "y"])})
    assert isinstance(f, ArrowFrame)
    assert f.columns == ["a", "b"]
    assert f.height == 2


def test_to_frame_dict_does_not_import_polars() -> None:
    # The reorder that keeps the arrow lane polars-free: coercing arrow-shaped
    # input must not touch the _LazyPolars proxy. Full-process proof lives in
    # test_fused_match.py's tripwire; this pins the to_frame-local contract.
    import subprocess
    import sys

    code = (
        "import sys\n"
        "import pyarrow as pa\n"
        "from goldenmatch.core.frame import to_frame\n"
        "f = to_frame(pa.table({'a': pa.array([1])}))\n"
        "g = to_frame({'a': pa.array([1])})\n"
        "assert 'polars' not in sys.modules, 'to_frame imported polars for arrow input'\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr


def test_to_frame_polars_still_coerces() -> None:
    df = pl.DataFrame({"a": [1, 2]})
    f = to_frame(df)
    assert isinstance(f, PolarsFrame)


def test_to_frame_rejects_junk() -> None:
    with pytest.raises(TypeError):
        to_frame([1, 2, 3])
