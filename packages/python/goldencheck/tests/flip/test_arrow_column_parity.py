"""Stage-0 Flip parity gate: ArrowColumn == PolarsColumn on the corpus.

For every column of every corpus parquet, load it BOTH as a ``pl.DataFrame`` and
a ``pyarrow.Table`` and assert ``ArrowColumn`` returns the SAME result as
``PolarsColumn`` for every applicable method. ``PolarsColumn`` is the parity
oracle. This MUST be green before the fused differential (Task 3) is meaningful.

The only intentional divergence is ``dtype_repr`` (owned-contract neutral
vocabulary vs raw ``str(pl.dtype)``) -- it is deliberately NOT asserted here and
is measured by the differential instead.
"""
from __future__ import annotations

import math
import subprocess
import sys
from pathlib import Path

import pytest

# The parity oracle is PolarsColumn, and the corpus generator (scripts/flip_corpus.py,
# run via subprocess) needs numpy + pyarrow. Skip cleanly rather than error in a
# minimal-deps CI job where any of these is absent (e.g. numpy is not a base dep).
pl = pytest.importorskip("polars")
pytest.importorskip("numpy")  # corpus generator dependency
pq = pytest.importorskip("pyarrow.parquet")
from goldencheck.core.frame import ArrowColumn, PolarsColumn  # noqa: E402

CORPUS = Path(__file__).parent / "corpus"
NUMERIC = {"int", "uint", "float"}


def _ensure_corpus() -> list[Path]:
    files = sorted(CORPUS.glob("*.parquet"))
    if not files:
        script = Path(__file__).parent.parent / "scripts" / "flip_corpus.py"
        subprocess.run([sys.executable, str(script)], check=True)
        files = sorted(CORPUS.glob("*.parquet"))
    return files


CORPUS_FILES = _ensure_corpus()


def _sorted_str(values) -> list[str]:
    return sorted("<NULL>" if v is None else str(v) for v in values)


def _both_none_or_close(pv, av, *, rel=1e-9) -> None:
    if pv is None or av is None:
        assert pv is None and av is None, f"None mismatch: pl={pv!r} arrow={av!r}"
        return
    assert math.isclose(float(pv), float(av), rel_tol=rel, abs_tol=1e-12), (
        f"float mismatch: pl={pv!r} arrow={av!r}"
    )


def _params():
    out = []
    for path in CORPUS_FILES:
        df = pl.read_parquet(path)
        for name in df.columns:
            out.append((path.stem, name))
    return out


@pytest.mark.parametrize("dataset,column", _params())
def test_arrow_matches_polars(dataset: str, column: str) -> None:
    path = CORPUS / f"{dataset}.parquet"
    df = pl.read_parquet(path)
    tbl = pq.read_table(path)

    pol = PolarsColumn(df[column])
    arr = ArrowColumn(tbl.column(column))

    # --- universal (every dtype) ---------------------------------------------
    assert len(pol) == len(arr)
    assert pol.null_count() == arr.null_count()
    assert pol.n_unique() == arr.n_unique()
    assert pol.dtype == arr.dtype, f"neutral dtype: pl={pol.dtype} arrow={arr.dtype}"
    assert _sorted_str(pol.drop_nulls().to_list()) == _sorted_str(arr.drop_nulls().to_list())
    assert pol.is_sorted() == arr.is_sorted(), "is_sorted diverged"
    # min/max work for all dtypes (kernel for numeric, pyarrow for temporal/str/bool)
    pmin, amin = pol.min(), arr.min()
    if isinstance(pmin, (int, float)) and not isinstance(pmin, bool):
        _both_none_or_close(pmin, amin)
    else:
        assert pmin == amin, f"min mismatch: pl={pmin!r} arrow={amin!r}"
    pmax, amax = pol.max(), arr.max()
    if isinstance(pmax, (int, float)) and not isinstance(pmax, bool):
        _both_none_or_close(pmax, amax)
    else:
        assert pmax == amax, f"max mismatch: pl={pmax!r} arrow={amax!r}"

    # --- numeric-only --------------------------------------------------------
    if pol.dtype in NUMERIC:
        _both_none_or_close(pol.mean(), arr.mean())
        # std is a variance reduction: Polars and the Rust kernel use different
        # summation, so they agree to ~7 sig-figs (looser than the mean epsilon),
        # widened further on float32/uint32 source.
        _both_none_or_close(pol.std(), arr.std(), rel=1e-6)
        # sum: exact for int/uint, close for float
        if pol.dtype in ("int", "uint"):
            assert pol.sum() == arr.sum(), f"sum: pl={pol.sum()} arrow={arr.sum()}"
        else:
            # float sum accumulates: Polars sums in the source precision, the
            # kernel in f64, so float32 columns diverge at ~1e-6 rel (stat epsilon).
            _both_none_or_close(pol.sum(), arr.sum(), rel=1e-6)

        # filter_outside using the population's own 3-sigma band
        m, s = pol.mean(), pol.std()
        if s is not None and s > 0:
            lo, hi = m - 3 * s, m + 3 * s
            p_out = _sorted_str(pol.filter_outside(lo, hi).to_list())
            a_out = _sorted_str(arr.filter_outside(lo, hi).to_list())
            assert len(p_out) == len(a_out), f"filter_outside count: {len(p_out)} vs {len(a_out)}"
            # element-wise float-close on sorted values
            for pv, av in zip(sorted(pol.filter_outside(lo, hi).to_list()),
                              sorted(arr.filter_outside(lo, hi).to_list())):
                _both_none_or_close(pv, av)

        # count_gt(median)
        med = df[column].median()
        if med is not None:
            assert pol.count_gt(med) == arr.count_gt(med), "count_gt diverged"

        # diff().to_list()
        p_diff = pol.diff().to_list()
        a_diff = arr.diff().to_list()
        assert len(p_diff) == len(a_diff)
        for pv, av in zip(p_diff, a_diff):
            _both_none_or_close(pv, av)

        # cast('float') round-trips numeric
        for pv, av in zip(pol.cast("float").to_list(), arr.cast("float").to_list()):
            _both_none_or_close(pv, av)

    # --- string cast('float', strict=False): unparseable -> null -------------
    if pol.dtype == "str":
        p_cast = pol.cast("float", strict=False).to_list()
        a_cast = arr.cast("float", strict=False).to_list()
        assert len(p_cast) == len(a_cast)
        for pv, av in zip(p_cast, a_cast):
            _both_none_or_close(pv, av)

        # str_len_chars(): per-value character count (nulls preserved) + its mean
        # (the seam op the semantic classifier's avg_length signals rely on).
        assert pol.str_len_chars().to_list() == arr.str_len_chars().to_list(), "str_len_chars diverged"
        _both_none_or_close(pol.str_len_chars().mean(), arr.str_len_chars().mean())


def test_dtype_repr_is_owned_divergence() -> None:
    """dtype_repr is the ONE intentional divergence: ArrowColumn returns the
    neutral category, PolarsColumn returns raw str(pl.dtype)."""
    df = pl.read_parquet(CORPUS / "mixed_dtypes.parquet")
    tbl = pq.read_table(CORPUS / "mixed_dtypes.parquet")
    col = "i8"
    pol = PolarsColumn(df[col])
    arr = ArrowColumn(tbl.column(col))
    assert pol.dtype_repr() == "Int8"          # raw polars vocabulary
    assert arr.dtype_repr() == "int"           # owned neutral vocabulary
    assert pol.dtype == arr.dtype == "int"     # ...but neutral dtype agrees
