"""Owned auto-detect profile kernel wiring tests (Phase 3).

Two lanes, following the ``test_native_parity.py`` convention:

- Pure-path contract guards (always run): assert the ``inferred_type`` decision
  and raw ``unique_count`` invariants hold on whichever path executes. They pass
  on the pure path today (native symbol absent) -- that is intentional.
- Native-lane equivalence tests (name contains ``native``): the fallback lane
  ``-k "not native"`` deselects them; they SKIP when the native kernel / symbol
  is absent, and only genuinely exercise the kernel in CI's native lane.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from goldenflow.core._native_loader import native_available, native_module
from goldenflow.engine.profiler_bridge import (
    _infer_type_list,
    _infer_type_list_native_or_pure,
    _profile_column,
    _profile_column_native_or_pure,
    profile_columns,
    profile_dataframe,
)
from goldenflow.engine.selector import select_transforms

# A battery of columns spanning every branch of the decision.
_BATTERY: dict[str, list] = {
    "email": ["a@b.co", "x@y.io", "p@q.net"],
    "zip": ["12345", "90210", "10001-1234"],
    "date": ["2020-01-02", "1999/12/31", "1/2/99"],
    "phone": ["(212) 555-1234", "+1 415 555 9999", "212-555-0000"],
    "name": ["John Smith", "Jane Doe", "Bob Roe"],
    "nums": [1, 2, 3],
    "floats": [1.5, 2.5, 3.5],
    "bools": [True, False, True],
    "mixed": [1, "1"],
    "strings": ["foo", "bar", "baz"],
    "with_nulls": ["a@b.co", None, "x@y.io", "p@q.net"],
    "all_null": [None, None],
    "blanks": ["   ", ""],
}


def test_profile_columns_inferred_type_matches_pure():
    cols = {
        "email": ["a@b.co", "x@y.io", "p@q.net"],
        "zip": ["12345", "90210", "10001-1234"],
        "nums": [1, 2, 3],
        "mixed": [1, "1"],  # -> string; unique_count must be 2 (raw)
        "name": ["John Smith", "Jane Doe", "Bob Roe"],
    }
    prof = profile_columns(cols)
    got = {c.name: c.inferred_type for c in prof.columns}
    assert got == {
        "email": "email",
        "zip": "zip",
        "nums": "numeric",
        "mixed": "string",
        "name": "name",
    }
    mixed = next(c for c in prof.columns if c.name == "mixed")
    assert mixed.unique_count == 2  # raw-value set, NOT stringified


def _native_profile_or_skip():
    if not native_available():
        pytest.skip("goldenflow-native not built/importable")
    nm = native_module()
    if nm is None or not hasattr(nm, "infer_type_list_arrow"):
        pytest.skip("installed goldenflow-native predates the profile kernel")
    return nm


def test_native_infer_type_list_equals_pure_native(monkeypatch):
    """Route through the PRODUCTION wiring (`_infer_type_list_native_or_pure`), not
    a re-derived direct kernel call -- so hint derivation, str(v)/None mapping, and
    dispatch are all exercised on the native path and asserted == the pure ref."""
    _native_profile_or_skip()
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    for name, values in _BATTERY.items():
        native_out = _infer_type_list_native_or_pure(values)
        pure_out = _infer_type_list(values)
        assert native_out == pure_out, f"{name}: native={native_out!r} pure={pure_out!r}"


# ---- Task 7: Path 1 (columnar built-in) ------------------------------------


def test_profile_dataframe_builtin_inferred_type():
    """Built-in fallback path (no file_path -> no GoldenCheck branch). Contract
    guard: passes on whichever path runs (pure locally; native in CI lane)."""
    pl = pytest.importorskip("polars")
    df = pl.DataFrame({"email": ["a@b.co", "x@y.io"], "n": [1, 2], "s": ["foo", "bar"]})
    prof = profile_dataframe(df)  # built-in fallback (empty file_path)
    got = {c.name: c.inferred_type for c in prof.columns}
    assert got == {"email": "email", "n": "numeric", "s": "string"}


def _column_cls_or_skip(nm):
    column_cls = getattr(nm, "Column", None)
    if column_cls is None or not hasattr(column_cls, "from_arrow"):
        pytest.skip("installed goldenflow-native predates Column.profile()")
    return column_cls


def test_native_column_profile_typed_stats_native(monkeypatch):
    """Step 4b: pin typed-column null/unique/samples byte-format on Path 1.

    ``samples`` must byte-match Polars ``cast(Utf8)`` -- Float64 ``1.0``/``-0.0``
    via float_to_polars_string, Boolean ``"true"``/``"false"``, plus a Utf8 column
    (cast(Utf8) is identity) so the string sample path is pinned too."""
    pl = pytest.importorskip("polars")
    nm = _native_profile_or_skip()
    column_cls = _column_cls_or_skip(nm)
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    cases = {
        "ints": pl.Series("ints", [1, 2, 2, 3, None], dtype=pl.Int64),
        "floats": pl.Series("floats", [1.0, -0.0, 2.5, 2.5, None], dtype=pl.Float64),
        "bools": pl.Series("bools", [True, False, True, None], dtype=pl.Boolean),
        "strs": pl.Series("strs", ["foo", "bar", "foo", None], dtype=pl.Utf8),
    }
    for name, series in cases.items():
        out = column_cls.from_arrow(series.to_frame()).profile()
        assert out["null_count"] == series.null_count(), name
        assert out["unique_count"] == series.drop_nulls().n_unique(), name
        expected_samples = series.drop_nulls().head(5).cast(pl.Utf8).to_list()
        assert list(out["samples"]) == expected_samples, name


def test_native_profile_column_wiring_equals_pure_native(monkeypatch):
    """Route Path 1 through the PRODUCTION wiring: assert the FULL ColumnProfile
    from `_profile_column_native_or_pure` (native path) equals the pure
    `_profile_column` -- covers the dict->ColumnProfile packing, list(samples),
    percentages, and the column-name override on the native path."""
    pl = pytest.importorskip("polars")
    _native_profile_or_skip()
    _column_cls_or_skip(native_module())
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    series_cases = [
        pl.Series("email", ["a@b.co", "x@y.io", "p@q.net"], dtype=pl.Utf8),
        pl.Series("zip", ["12345", "90210", None], dtype=pl.Utf8),
        pl.Series("n", [1, 2, 2, None], dtype=pl.Int64),
        pl.Series("f", [1.0, -0.0, 2.5, None], dtype=pl.Float64),
        pl.Series("flag", [True, False, True], dtype=pl.Boolean),
        pl.Series("plain", ["foo", "bar", "baz"], dtype=pl.Utf8),
    ]
    for series in series_cases:
        native_profile = _profile_column_native_or_pure(series)
        pure_profile = _profile_column(series)
        assert native_profile == pure_profile, series.name


# ---- Task 10: cross-surface parity corpus -----------------------------------

_CORPUS_PATH = Path(__file__).resolve().parent.parent / "parity" / "profile_corpus.jsonl"


def _load_corpus() -> list[dict]:
    text = _CORPUS_PATH.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def test_profile_corpus_pure_matches_expected():
    """Pure oracle contract (ALWAYS runs): the pure-Python reference reproduces
    every committed corpus row. Byte-pins ``_infer_type_list`` to the corpus the
    Rust kernel + TS/wasm surfaces are byte-copied from."""
    rows = _load_corpus()
    assert rows, "profile_corpus.jsonl is empty -- run scripts/gen_profile_corpus.py"
    for row in rows:
        values = row["values"]
        assert _infer_type_list(values) == row["expected_type"], row


def test_native_profile_corpus_matches_expected_native():
    """Native-lane variant (SKIPS when native absent): the owned kernel reproduces
    every corpus row over the FFI stringify/None-drop boundary, with the committed
    ``hint``. This is the assertion the >100-with-nulls row was authored to guard."""
    nm = _native_profile_or_skip()
    rows = _load_corpus()
    for row in rows:
        values = row["values"]
        strs = [None if v is None else str(v) for v in values]
        got = nm.infer_type_list_arrow(strs, row["hint"])
        assert got == row["expected_type"], row


# ---- Task 11: unique_pct gate decision-equivalence (float NaN/-0.0 edge) -----


def _float_edge_series(pl):
    """Float columns straddling ``unique_pct = 0.1`` and carrying the ``NaN``/
    ``-0.0`` values whose raw ``unique_count`` CAN differ native-vs-Polars (the
    native kernel folds ``-0.0``/``+0.0`` and all ``NaN``; Polars ``n_unique`` may
    not). These are Float64 -> ``inferred_type == "numeric"``, and the only
    unique_pct-gated transform (``category_auto_correct``) requires a ``string``
    input type, so the gate is moot BY TYPE here -- the selection can't move even if
    the counts diverge. That is exactly the invariant these tests lock (a
    forward-looking regression guard), NOT a demonstrated same-decision-on-
    divergent-count case."""
    nan = float("nan")
    return [
        # 10 rows, high-cardinality (unique_pct > 0.1): distinct floats + the edge.
        pl.Series("hi", [0.0, -0.0, nan, nan, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0], dtype=pl.Float64),
        # 20 rows, low-cardinality (unique_pct < 0.1): categorical-looking floats.
        pl.Series("lo", [1.0] * 9 + [-0.0, 0.0] + [nan] * 9, dtype=pl.Float64),
    ]


def test_unique_pct_gate_numeric_never_selects_autocorrect():
    """A numeric column (whatever its ``unique_pct``) never selects
    ``category_auto_correct`` -- so the NaN/-0.0 ``unique_count`` edge cannot change
    which transforms are selected."""
    pl = pytest.importorskip("polars")
    for series in _float_edge_series(pl):
        profile = _profile_column(series)
        assert profile.inferred_type == "numeric", series.name
        names = {t.name for t in select_transforms(profile)}
        assert "category_auto_correct" not in names, series.name


def test_unique_pct_gate_selection_equivalent_across_paths(monkeypatch):
    """Decision-equivalence: the native profiling path and the pure Polars path pick
    the SAME transforms on the float NaN/-0.0 edge. The type gate (numeric columns
    are never eligible for ``category_auto_correct``) makes the raw ``unique_count``
    edge UNABLE to move the selection -- so this is a forward-looking regression
    guard, not a demonstrated divergent-count/same-decision case. Locally it is
    pure-vs-pure (contract guard); the native ``Column.profile()`` path is exercised
    in CI's native lane."""
    pl = pytest.importorskip("polars")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "auto")
    for series in _float_edge_series(pl):
        pure = select_transforms(_profile_column(series))
        native_or_pure = select_transforms(_profile_column_native_or_pure(series))
        assert {t.name for t in pure} == {t.name for t in native_or_pure}, series.name
        # The gate-sensitive transform is absent on BOTH (numeric type).
        assert "category_auto_correct" not in {t.name for t in native_or_pure}


# ---- Task 12: engine smoke -- native on/off byte-identity --------------------


def _mixed_fixture() -> dict[str, list]:
    """A mixed-type frame exercising every inference branch + a null-heavy column."""
    return {
        "email": ["  a@b.co", "x@y.io ", "p@q.net"],
        "zip": ["12345", "90210", "10001"],
        "date": ["2020-01-02", "1999/12/31", "1/2/99"],
        "phone": ["(212) 555-1234", "+1 415 555 9999", "212-555-0000"],
        "name": ["John Smith", "Jane Doe", "Bob Roe"],
        "num": [1, 2, 3],
        "flag": [True, False, True],
        "nulls": [None, "  x  ", None],
    }


def _manifest_tuples(manifest) -> list[tuple]:
    return [
        (
            r.column,
            r.transform,
            r.affected_rows,
            r.total_rows,
            tuple(r.sample_before),
            tuple(r.sample_after),
        )
        for r in manifest.records
    ]


def _run_both_surfaces(fixture: dict[str, list]):
    """Zero-config on BOTH surfaces: ``transform_df`` (Polars) and
    ``transform_columns_public`` (Polars-free). Returns the two manifest-record
    lists (comparable tuples)."""
    import goldenflow
    from goldenflow.engine.columnar import transform_columns_public

    pl = pytest.importorskip("polars")
    df_result = goldenflow.transform_df(pl.DataFrame(fixture), config=None)
    col_result = transform_columns_public(dict(fixture), None)
    return _manifest_tuples(df_result.manifest), _manifest_tuples(col_result.manifest)


@pytest.mark.parametrize("native_env", ["0", "1"])
def test_autodetect_cross_surface_byte_identity(monkeypatch, native_env):
    """The zero-config auto-detect pipeline produces byte-identical Manifest records
    (hence identical selected transforms) on BOTH the Polars ``transform_df`` surface
    and the Polars-free ``transform_columns_public`` surface -- checked under
    ``GOLDENFLOW_NATIVE=0`` and ``=1``.

    Locally (native profile symbol absent) both env values run the pure path, so this
    is a pure-vs-pure contract guard; in CI's native lane ``=1`` exercises the owned
    kernel and ``=0`` the pure reference, asserting they agree. Forcing ``=1`` with no
    native module at all raises (reference-mode), so guard that leg with a skip."""
    pytest.importorskip("polars")
    if native_env == "1" and native_module() is None:
        pytest.skip("GOLDENFLOW_NATIVE=1 requires an importable native module")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", native_env)
    df_records, col_records = _run_both_surfaces(_mixed_fixture())
    assert df_records == col_records


def test_autodetect_pure_matches_across_env(monkeypatch):
    """Cross-env byte-identity: the auto-detect manifest under ``=1`` (native where a
    kernel exists) equals the manifest under ``=0`` (pure). Skips the ``=1`` leg when
    no native module is importable. The env is flipped mid-test via
    ``monkeypatch.setenv`` (exception-safe, auto-restored) -- the second call just
    overrides the first."""
    import goldenflow

    pl = pytest.importorskip("polars")
    fixture = _mixed_fixture()

    monkeypatch.setenv("GOLDENFLOW_NATIVE", "0")
    pure = _manifest_tuples(goldenflow.transform_df(pl.DataFrame(fixture), config=None).manifest)
    if native_module() is None:
        pytest.skip("GOLDENFLOW_NATIVE=1 requires an importable native module")
    monkeypatch.setenv("GOLDENFLOW_NATIVE", "1")
    native = _manifest_tuples(goldenflow.transform_df(pl.DataFrame(fixture), config=None).manifest)
    assert native == pure
