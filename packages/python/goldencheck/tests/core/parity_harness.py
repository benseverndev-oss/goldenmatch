"""Reusable parity-oracle harness. The Rust kernel is the source of truth; the
pure-Python/Polars fallback is asserted 'conforms or is documented-lossy'. Every
divergence must appear in ACCEPTED_DIVERGENCES (with a rationale + product-decision
ref) or the harness fails. Wave 0: the registry is EMPTY (all 5 kernels are
byte/set-exact), which validates the harness mechanics on known-exact code.

New waves add kernels by appending a Component to REGISTERED_COMPONENTS -- each
reuses the generators/fallbacks the hard-coded test_native_parity.py already
defines, so parity checking of a new surface is a few lines, not a new test file.
"""
from __future__ import annotations

import datetime as _dt
import os
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
import pyarrow as pa
from scipy import stats as _scipy_stats
from goldencheck import cell_quality as _cell_quality
from goldencheck import functional_dependencies as _fd_bridge
from goldencheck.core._native_loader import native_module
from goldencheck.engine.csv_infer import infer_and_type
from goldencheck.profilers import fuzzy_values as fv
from goldencheck.relations import approx_fd as afd
from goldencheck.relations import composite_key as ck
from goldencheck.relations import functional_dependency as fd
from goldencheck.relations.approx_duplicate import _exact_signature, _normalized_signature

# Reuse the fixture generators + Python-fallback helpers the hard-coded parity
# test already defines instead of duplicating them.
from tests.core import test_native_parity as tp
from tests.core.test_csv_infer_parity import _cells_to_csv_bytes, _random_cell_matrix


@dataclass(frozen=True)
class Divergence:
    component: str
    rationale: str
    decision_ref: str


# Empty in Wave 0. A future wave adds an entry when a kernel is deemed "more
# correct" than the Polars fallback AND the product decision is signed off.
ACCEPTED_DIVERGENCES: tuple[Divergence, ...] = ()


@dataclass
class Component:
    name: str                                  # loader component key
    run_native: Callable[[Any], Any]           # fixture -> native result (normalized)
    run_fallback: Callable[[Any], Any]         # fixture -> Python fallback result (normalized)
    fixtures: Callable[[int], list[Any]]       # seed -> list of input fixtures


def _accepted(name: str) -> bool:
    return any(d.component == name for d in ACCEPTED_DIVERGENCES)


def compare(component: Component, seed: int) -> list[str]:
    """Return unexpected-divergence descriptions (empty list = parity)."""
    problems: list[str] = []
    for fx in component.fixtures(seed):
        nat = component.run_native(fx)
        fb = component.run_fallback(fx)
        if nat != fb and not _accepted(component.name):
            problems.append(
                f"{component.name}: native={nat!r} fallback={fb!r} on fixture={fx!r}"
            )
    return problems


@contextmanager
def _native_env(value: str) -> Iterator[None]:
    """Temporarily force GOLDENCHECK_NATIVE (the loader re-reads it every call)."""
    prev = os.environ.get("GOLDENCHECK_NATIVE")
    os.environ["GOLDENCHECK_NATIVE"] = value
    try:
        yield
    finally:
        if prev is None:
            os.environ.pop("GOLDENCHECK_NATIVE", None)
        else:
            os.environ["GOLDENCHECK_NATIVE"] = prev


# ---------------------------------------------------------------------------
# benford
# ---------------------------------------------------------------------------
def _benford_fixtures(seed: int) -> list[np.ndarray]:
    rng = random.Random(seed)
    random_arr = np.array(
        [rng.uniform(1e-4, 1e7) for _ in range(8000)]
        + [rng.lognormvariate(0, 4) for _ in range(2000)],
        dtype=np.float64,
    )
    # Include the adversarial float edge-cases once (seed-invariant), so every
    # seed exercises the powers-of-ten / null-magnitude boundary too.
    adversarial = np.array(
        [10.0**k for k in range(-12, 13)]
        + [9.999999999, 1.0000001, 99.9, 100.0, 999999.0]
        + [0.0, -1.0, -1e6, float("nan"), float("inf"), float("-inf")]
        + [1e-300, 1e300, 5e-1, 4.4],
        dtype=np.float64,
    )
    return [random_arr, adversarial]


def _benford_native(vals: np.ndarray) -> list[int]:
    return list(native_module().benford_leading_digits(pa.array(vals)))


def _benford_fallback(vals: np.ndarray) -> list[int]:
    return tp._python_histogram(vals)


# ---------------------------------------------------------------------------
# composite_keys
# ---------------------------------------------------------------------------
def _keys_fixtures(seed: int) -> list[pl.DataFrame]:
    df = tp._random_keyless_df(seed).drop("d")
    candidates = ck._select_candidates(df, df.height)
    if len(candidates) < 2:
        return []  # both paths would be trivially empty; nothing to compare
    return [df]


def _keys_native(df: pl.DataFrame) -> set[tuple]:
    candidates = ck._select_candidates(df, df.height)
    single_unique = [False] * len(candidates)
    arrays = [df[c].to_arrow() for c in candidates]
    nat = native_module().composite_key_search(arrays, ck.MAX_KEY_SIZE, single_unique)
    return {tuple(k) for k in nat}


def _keys_fallback(df: pl.DataFrame) -> set[tuple]:
    candidates = ck._select_candidates(df, df.height)
    py = ck._python_search(df, candidates, df.height, ck.MAX_KEY_SIZE)
    return {tuple(k) for k in py}


# ---------------------------------------------------------------------------
# functional_dependencies (kernel)
# ---------------------------------------------------------------------------
def _fd_fixtures(seed: int) -> list[pl.DataFrame]:
    rng = random.Random(seed)
    n = 400
    zips = [rng.randint(0, 30) for _ in range(n)]
    z2c: dict[int, int] = {}
    city = [z2c.setdefault(z, rng.randint(0, 20)) for z in zips]  # zip -> city strict
    df = pl.DataFrame({
        "zip": zips,
        "city": city,
        "flag": [rng.randint(0, 1) for _ in range(n)],
        "amt": [rng.randint(0, 9) for _ in range(n)],
    })
    if len(fd._select_candidates(df, df.height)) < 2:
        return []
    return [df]


def _fd_native(df: pl.DataFrame) -> set[tuple[int, int]]:
    cols = fd._select_candidates(df, df.height)
    arrays = [df[c].to_arrow() for c in cols]
    return set(native_module().discover_functional_dependencies(arrays))


def _fd_fallback(df: pl.DataFrame) -> set[tuple[int, int]]:
    cols = fd._select_candidates(df, df.height)
    return set(fd._discover_python(df, cols, df.height))


# ---------------------------------------------------------------------------
# approximate_fd (discovery + violation rows)
# ---------------------------------------------------------------------------
def _afd_fixtures(seed: int) -> list[pl.DataFrame]:
    rng = random.Random(seed)
    n = 400
    zips = [rng.randint(0, 15) for _ in range(n)]
    z2c: dict[int, int] = {}
    city = [z2c.setdefault(z, rng.randint(0, 12)) for z in zips]
    for _ in range(rng.randint(1, 6)):
        city[rng.randrange(n)] = rng.randint(13, 20)  # inject violations
    df = pl.DataFrame(
        {"zip": zips, "city": city, "noise": [rng.randint(0, 5) for _ in range(n)]}
    )
    return [df]


def _afd_native(df: pl.DataFrame) -> tuple:
    cols = afd._select_candidates(df)
    arrays = [df[c].to_arrow() for c in cols]
    triples = {
        (i, j, v)
        for i, j, v in native_module().discover_approximate_fds(arrays, afd._MIN_CONFIDENCE)
    }
    # Also fold in the per-pair violation rows so a divergence in either the
    # discovered pairs OR the flagged rows trips the oracle.
    rows = frozenset(
        (i, j, tuple(native_module().fd_violation_rows(arrays[i], arrays[j])))
        for i, j, _v in triples
    )
    return (frozenset(triples), rows)


def _afd_fallback(df: pl.DataFrame) -> tuple:
    cols = afd._select_candidates(df)
    cols_ids = [afd._intern(df[c].to_list()) for c in cols]
    triples = set(afd._discover_python(cols_ids, df.height, afd._MIN_CONFIDENCE))
    rows = frozenset(
        (i, j, tuple(afd._violation_rows(cols_ids[i], cols_ids[j]))) for i, j, _v in triples
    )
    return (frozenset(triples), rows)


# ---------------------------------------------------------------------------
# fuzzy_values
# ---------------------------------------------------------------------------
def _fuzzy_fixtures(seed: int) -> list[list[str]]:
    rng = random.Random(seed)
    bases = ["California", "Texas", "New York", "Florida", "Washington", "Arizona"]
    values: list[str] = []
    for b in bases:
        values.append(b)
        for _ in range(rng.randint(0, 3)):
            s = list(b.lower())
            if len(s) > 3 and rng.random() < 0.7:
                del s[rng.randrange(len(s))]
            values.append("".join(s).upper() if rng.random() < 0.3 else "".join(s))
    return [list(dict.fromkeys(values))]  # distinct, order-preserving


def _fuzzy_native(values: list[str]) -> set[frozenset]:
    # The native pyfunction signature was preserved as ``Vec<String>`` (not
    # Arrow-in) -- pass the plain list directly, matching the real call site
    # in goldencheck/profilers/fuzzy_values.py.
    nat = native_module().near_duplicate_value_clusters(values, fv._MIN_SIMILARITY)
    return {frozenset(c) for c in nat}


def _fuzzy_fallback(values: list[str]) -> set[frozenset]:
    py = fv._python_clusters(values, fv._MIN_SIMILARITY)
    return {frozenset(c) for c in py}


# ---------------------------------------------------------------------------
# cell_quality (public bridge) -- native-on vs native-off of the SAME public
# API. Real comparison (env-toggle), and transitively covers the fuzzy kernel.
# ---------------------------------------------------------------------------
def _cell_quality_fixtures(seed: int) -> list[pl.DataFrame]:
    # A near-dup categorical column where each canonical spelling STRICTLY
    # dominates its variants. Deterministic + tie-free on purpose: cell_quality
    # picks the canonical via ``max(members, key=freq)``, whose tie-break is
    # sensitive to polars ``.unique()`` member order (not stable across
    # processes). With a strict frequency winner per cluster, the penalized-set
    # (= every non-canonical variant) is order-invariant, so this stays a
    # meaningful native-vs-fallback parity check (cluster MEMBERSHIP divergence
    # would still be caught) instead of tie-break noise. Seed-invariant: the
    # bridge is transitively covered by the fuzzy_values kernel.
    del seed
    comp: dict[str, int] = {
        "California": 40, "Californa": 3, "CALIFORNIA": 2,  # canonical: California
        "Texas": 30, "texas": 3,                            # canonical: Texas
        "New York": 25, "New Yrok": 2,                      # canonical: New York
        "Florida": 10, "Washington": 5,                     # clean singletons
    }
    col: list[str] = []
    for value, count in comp.items():
        col.extend([value] * count)
    df = pl.DataFrame({"state": col, "id": list(range(len(col)))})
    return [df]


def _cell_quality_native(df: pl.DataFrame) -> dict:
    with _native_env("1"):
        return _cell_quality(df)


def _cell_quality_fallback(df: pl.DataFrame) -> dict:
    with _native_env("0"):
        return _cell_quality(df)


# ---------------------------------------------------------------------------
# functional_dependencies (public bridge) -- native-on vs native-off of the
# public FD API. Light smoke fixture; transitively covered by the FD kernels.
# ---------------------------------------------------------------------------
def _fd_bridge_fixtures(seed: int) -> list[pl.DataFrame]:
    rng = random.Random(seed)
    n = 300
    zips = [rng.randint(0, 20) for _ in range(n)]
    z2c: dict[int, int] = {}
    city = [z2c.setdefault(z, rng.randint(0, 15)) for z in zips]  # strict zip -> city
    area = [z2c[z] for z in zips]
    for _ in range(rng.randint(2, 8)):
        area[rng.randrange(n)] = rng.randint(16, 25)  # a few violations => approx FD
    df = pl.DataFrame({"zip": zips, "city": city, "area": area})
    return [df]


def _fd_bridge_records(df: pl.DataFrame) -> list[tuple[str, tuple[str, ...], float]]:
    recs = _fd_bridge(df)
    return [(r.determinant, tuple(r.dependents), r.confidence) for r in recs]


def _fd_bridge_native(df: pl.DataFrame) -> list:
    with _native_env("1"):
        return _fd_bridge_records(df)


def _fd_bridge_fallback(df: pl.DataFrame) -> list:
    with _native_env("0"):
        return _fd_bridge_records(df)


# ---------------------------------------------------------------------------
# column_aggregate (fused len/null_count/n_unique_nonnull/dtype scan)
# ---------------------------------------------------------------------------
def _column_aggregate_fixtures(seed: int) -> list[pl.Series]:
    rng = random.Random(seed)
    n = rng.randint(0, 200)
    int_vals = [rng.choice([None, rng.randint(-50, 50)]) for _ in range(n)]
    float_pool = [None, 0.0, -0.0, float("nan")] + [rng.uniform(-50, 50) for _ in range(20)]
    float_vals = [rng.choice(float_pool) for _ in range(n)]
    str_pool = [None, "a", "b", "c", "aa", "bb", ""]
    str_vals = [rng.choice(str_pool) for _ in range(n)]
    return [
        pl.Series("i", int_vals, dtype=pl.Int64),
        pl.Series("f", float_vals, dtype=pl.Float64),
        pl.Series("s", str_vals, dtype=pl.Utf8),
    ]


def _column_aggregate_native(s: pl.Series) -> tuple:
    from goldencheck.core.frame import _neutral_dtype

    ln, nc, nu, dt = native_module().column_aggregate(s.to_arrow())
    return (ln, nc, nu, dt, _neutral_dtype(s.dtype))


def _column_aggregate_fallback(s: pl.Series) -> tuple:
    from goldencheck.core.frame import _neutral_dtype

    dt = _neutral_dtype(s.dtype)
    return (len(s), s.null_count(), s.drop_nulls().n_unique(), dt, dt)


# ---------------------------------------------------------------------------
# csv_infer (owned CSV type-inference)
# ---------------------------------------------------------------------------
def _csv_infer_fixtures(seed: int) -> list[tuple[list[str], list[list[str]]]]:
    return [_random_cell_matrix(seed)]


def _csv_infer_native(fx: tuple[list[str], list[list[str]]]) -> dict:
    header, cells = fx
    csv_bytes = _cells_to_csv_bytes(header, cells)
    return native_module().csv_infer_columns(csv_bytes, ord(","))


def _csv_infer_fallback(fx: tuple[list[str], list[list[str]]]) -> dict:
    header, cells = fx
    return infer_and_type(cells, header)


# ---------------------------------------------------------------------------
# numeric_stats (column_numeric_stats + count_outside) -- range_distribution.
#
# `mean`/`std` are float reductions, so the harness compares them via a
# significant-figure canonical form (the float-epsilon "divergence class" for
# this kernel) rather than bare `!=`; `min`/`max`/`count` are exact but NaN is
# canonicalised (Polars `None` for empty/all-NaN min-max and `n<2` std maps to
# the kernel's NaN). Because both run_native and run_fallback apply the SAME
# normalisation, a real divergence beyond ~9 sig-figs still trips the oracle --
# so this stays an exact-`!=` check and ACCEPTED_DIVERGENCES remains empty.
# ---------------------------------------------------------------------------
_STATS_SIG = 9


def _canon_float(x: object) -> str:
    """Canonical token for a possibly-None / NaN / inf float, rounded to
    ``_STATS_SIG`` significant figures so float-reduction noise collapses."""
    import math

    if x is None:
        return "NAN"
    v = float(x)
    if math.isnan(v):
        return "NAN"
    if math.isinf(v):
        return "inf" if v > 0 else "-inf"
    if v == 0.0:
        return "0"
    d = _STATS_SIG - 1 - math.floor(math.log10(abs(v)))
    return repr(round(v, d))


def _numeric_stats_fixtures(seed: int) -> list[pl.Series]:
    rng = random.Random(seed)
    n = rng.randint(0, 250)
    int_pool = [None] + list(range(-40, 41)) + [5000, -5000]
    int_vals = [rng.choice(int_pool) for _ in range(n)]
    float_pool = (
        [None, 0.0, -0.0] + [rng.uniform(-50, 50) for _ in range(20)] + [9999.5, -8888.25]
    )
    float_vals = [rng.choice(float_pool) for _ in range(n)]
    uint_pool = [None] + list(range(0, 60)) + [9000]
    uint_vals = [rng.choice(uint_pool) for _ in range(n)]
    # A NaN/inf-bearing float column too (min/max ignore NaN; mean/std propagate).
    nan_vals = [rng.choice([None, 1.0, 2.0, float("nan"), float("inf")]) for _ in range(n)]
    return [
        pl.Series("i", int_vals, dtype=pl.Int64),
        pl.Series("f", float_vals, dtype=pl.Float64),
        pl.Series("u", uint_vals, dtype=pl.UInt32),
        pl.Series("nan", nan_vals, dtype=pl.Float64),
    ]


def _numeric_stats_normalized(
    count: int,
    mn: object,
    mx: object,
    mean: object,
    std: object,
    outlier: tuple[int, list[str]],
) -> tuple:
    return (
        count,
        _canon_float(mn),
        _canon_float(mx),
        _canon_float(mean),
        _canon_float(std),
        outlier[0],
        tuple(outlier[1]),
    )


def _numeric_stats_outlier_bounds(s: pl.Series) -> tuple[float, float] | None:
    import math

    mean = s.mean()
    std = s.std()
    if mean is None or std is None or not math.isfinite(std) or std <= 0:
        return None
    return (mean - 3 * std, mean + 3 * std)


def _numeric_stats_native(s: pl.Series) -> tuple:
    count, mn, mx, mean, std, _sum = native_module().column_numeric_stats(s.to_arrow())
    bounds = _numeric_stats_outlier_bounds(s)
    if bounds is None:
        outlier = (0, [])
    else:
        outlier = tuple(native_module().count_outside(s.to_arrow(), bounds[0], bounds[1]))
    return _numeric_stats_normalized(count, mn, mx, mean, std, outlier)


def _numeric_stats_fallback(s: pl.Series) -> tuple:
    count = s.len() - s.null_count()
    bounds = _numeric_stats_outlier_bounds(s)
    if bounds is None:
        outlier = (0, [])
    else:
        non_null = s.drop_nulls()
        out = non_null.filter((non_null < bounds[0]) | (non_null > bounds[1]))
        outlier = (len(out), [str(v) for v in out.to_list()[:5]])
    return _numeric_stats_normalized(count, s.min(), s.max(), s.mean(), s.std(), outlier)


# ---------------------------------------------------------------------------
# sequence_analysis (fused order-preserving gap scan) -- sequence_detection.
# All signals are integer/bool exact (int/uint only, NaN-free), so this is an
# exact-`!=` check with an EMPTY divergence class. The fallback mirrors the
# profiler's per-field Polars computation; the gap fields use the arithmetic
# `expected - present_size` count + a lazy first-10 sample so the i64 min/max
# fixture's 2^64-wide span never materialises.
# ---------------------------------------------------------------------------
def _sequence_fixtures(seed: int) -> list[pl.Series]:
    rng = random.Random(seed)
    n = rng.randint(2, 300)
    int_pool = [None] + list(range(0, 120))
    int_vals = [rng.choice(int_pool) for _ in range(n)]
    uint_pool = [None] + list(range(0, 200))
    uint_vals = [rng.choice(uint_pool) for _ in range(n)]
    fixtures = [
        pl.Series("i", int_vals, dtype=pl.Int64),
        pl.Series("u", uint_vals, dtype=pl.UInt32),
        # Seed-invariant adversarials: gapped, unsorted-with-dups, i64 min/max.
        pl.Series("gap", [1, 2, 4, 7, 8, 12, 20], dtype=pl.Int64),
        pl.Series("dup", [3, 1, 2, 9, 5, 4, 3], dtype=pl.Int64),
        pl.Series("mm", [-(2**63), 2**63 - 1], dtype=pl.Int64),
    ]
    # Drop any fixture that would leave <2 non-null values (kernel declines).
    return [s for s in fixtures if s.drop_nulls().len() >= 2]


def _sequence_normalized(s: pl.Series, res: object) -> tuple | None:
    if res is None:
        return None
    (n_diffs, unit, pos, is_sorted, mn, mx, present_size, gap_count, gap_sample) = res
    return (n_diffs, unit, pos, is_sorted, mn, mx, present_size, gap_count, tuple(gap_sample))


def _sequence_native(s: pl.Series) -> tuple | None:
    return _sequence_normalized(s, native_module().sequence_analysis(s.to_arrow()))


def _sequence_fallback(s: pl.Series) -> tuple | None:
    import itertools

    non_null = s.drop_nulls()
    total = len(non_null)
    if total < 2:
        return None
    diffs = non_null.diff().drop_nulls()
    n_diffs = len(diffs)
    unit = int((diffs == 1).sum())
    pos = int((diffs > 0).sum())
    is_sorted = bool(non_null.is_sorted())
    col_min = int(non_null.min())
    col_max = int(non_null.max())
    present = set(non_null.unique().to_list())
    present_size = len(present)
    expected = col_max - col_min + 1
    if expected <= total:
        gap_count = 0
        gap_sample: list[int] = []
    else:
        gap_count = expected - present_size
        gap_sample = list(
            itertools.islice((v for v in range(col_min, col_max + 1) if v not in present), 10)
        )
    return (n_diffs, unit, pos, is_sorted, col_min, col_max, present_size, gap_count, tuple(gap_sample))


# ---------------------------------------------------------------------------
# date_freshness (fused count_gt(now) + max) -- freshness. Both signals are
# exact integers (count + raw epoch), so this is an exact-`!=` check with an
# EMPTY divergence class. `now_epoch` is computed offset-free in the array's
# native Arrow unit (spec review B2 -- NEVER `datetime.timestamp()`).
# ---------------------------------------------------------------------------
import datetime as _dt_mod  # noqa: E402

_FRESH_EPOCH_DATE = _dt_mod.date(1970, 1, 1)
_FRESH_EPOCH_DT = _dt_mod.datetime(1970, 1, 1)
_FRESH_REF_DATE = _dt_mod.date(2000, 1, 1)
_FRESH_REF_DT = _dt_mod.datetime(2000, 1, 1, 12, 0, 0)


def _fresh_unit(arr: pa.Array) -> str:
    t = arr.type
    if pa.types.is_date32(t):
        return "day"
    if pa.types.is_date64(t):
        return "ms"
    if pa.types.is_timestamp(t):
        return t.unit
    raise AssertionError(f"non-temporal array type: {t}")


def _fresh_epoch(ref: object, unit: str) -> int:
    if unit == "day":
        return (ref - _FRESH_EPOCH_DATE).days  # type: ignore[operator]
    dt = ref if isinstance(ref, _dt_mod.datetime) else _dt_mod.datetime(ref.year, ref.month, ref.day)  # type: ignore[union-attr]
    delta = dt - _FRESH_EPOCH_DT
    if unit == "s":
        return delta // _dt_mod.timedelta(seconds=1)
    if unit == "ms":
        return delta // _dt_mod.timedelta(milliseconds=1)
    if unit == "us":
        return delta // _dt_mod.timedelta(microseconds=1)
    if unit == "ns":
        return (delta // _dt_mod.timedelta(microseconds=1)) * 1000
    raise AssertionError(f"unknown unit: {unit}")


def _freshness_fixtures(seed: int) -> list[tuple[pl.Series, object]]:
    rng = random.Random(seed)
    n = rng.randint(0, 60)
    ref_day = (_FRESH_REF_DATE - _FRESH_EPOCH_DATE).days
    day_pool = [None] + [ref_day + rng.randint(-4000, 4000) for _ in range(20)]
    dates = [rng.choice(day_pool) for _ in range(n)]
    date_vals = [None if d is None else _FRESH_EPOCH_DATE + _dt_mod.timedelta(days=d) for d in dates]
    us_pool = [None] + [rng.randint(-10_000_000, 10_000_000) for _ in range(20)]
    micros = [rng.choice(us_pool) for _ in range(n)]
    dt_vals = [None if m is None else _FRESH_REF_DT + _dt_mod.timedelta(microseconds=m) for m in micros]
    return [
        (pl.Series("d", date_vals, dtype=pl.Date), _FRESH_REF_DATE),
        (pl.Series("t", dt_vals, dtype=pl.Datetime("us")), _FRESH_REF_DT),
        # Seed-invariant adversarials.
        (pl.Series("d0", [], dtype=pl.Date), _FRESH_REF_DATE),
        (pl.Series("dn", [None, None], dtype=pl.Date), _FRESH_REF_DATE),
    ]


def _freshness_native(fx: tuple[pl.Series, object]) -> tuple | None:
    s, ref = fx
    arr = s.to_arrow()
    now_epoch = _fresh_epoch(ref, _fresh_unit(arr))
    res = native_module().date_freshness(arr, now_epoch)
    return None if res is None else (res[0], res[1])


def _freshness_fallback(fx: tuple[pl.Series, object]) -> tuple | None:
    s, ref = fx
    non_null = s.drop_nulls()
    if len(non_null) == 0:
        return None
    unit = _fresh_unit(s.to_arrow())
    return (non_null.filter(non_null > ref).len(), _fresh_epoch(non_null.max(), unit))


# ---------------------------------------------------------------------------
# duplicate_signatures (exact + near duplicate-row signature scan)
# ---------------------------------------------------------------------------
def _dupsig_fixtures(seed: int) -> list[pl.DataFrame]:
    rng = random.Random(seed)
    names = [
        "Acme, Inc.", "acme inc", "ACME  Inc", "Beta LLC", "beta llc",
        "Gamma", "gamma", "Delta", None, "", "!!!",
    ]
    n = rng.randint(2, 50)
    return [
        pl.DataFrame(
            {
                "name": [rng.choice(names) for _ in range(n)],
                "code": [rng.choice([1, 2, 3, None]) for _ in range(n)],
                "flag": [rng.choice([True, False, None]) for _ in range(n)],
            },
            schema={"name": pl.Utf8, "code": pl.Int64, "flag": pl.Boolean},
        )
    ]


def _dupsig_native(df: pl.DataFrame) -> tuple[int, int, int, int]:
    is_string = [dt == pl.Utf8 for dt in df.dtypes]
    arrays = [df[c].to_arrow() for c in df.columns]
    return tuple(native_module().duplicate_signatures(arrays, is_string))


def _dupsig_fallback(df: pl.DataFrame) -> tuple[int, int, int, int]:
    work = pl.DataFrame(
        {"__norm__": _normalized_signature(df), "__exact__": _exact_signature(df)}
    )
    norm_counts = work.group_by("__norm__").len().rename({"len": "__nc__"})
    exact_counts = work.group_by("__exact__").len().rename({"len": "__ec__"})
    work = work.join(norm_counts, on="__norm__").join(exact_counts, on="__exact__")
    exact_dups = work.filter(pl.col("__ec__") >= 2)
    edr = exact_dups.height
    edg = exact_dups["__exact__"].n_unique() if edr else 0
    near_dups = work.filter((pl.col("__nc__") >= 2) & (pl.col("__ec__") < 2))
    ndr = near_dups.height
    ndg = near_dups["__norm__"].n_unique() if ndr else 0
    return (edr, edg, ndr, ndg)


# ---------------------------------------------------------------------------
# age_mismatch (fused age-vs-DOB mismatch scan)
# ---------------------------------------------------------------------------
_AGE_EPOCH = _dt.date(1970, 1, 1)
_AGE_REF = _dt.date(2020, 1, 1)


def _agesig_fixtures(seed: int) -> list[tuple[pl.Series, pl.Series]]:
    rng = random.Random(seed)
    n = rng.randint(0, 60)
    ages: list[float | None] = []
    dobs: list[_dt.date | None] = []
    for _ in range(n):
        true_age = rng.uniform(0, 95)
        dob = _AGE_REF - _dt.timedelta(days=round(true_age * 365.25))
        roll = rng.random()
        if roll < 0.15:
            ages.append(None)
        elif roll < 0.30:
            ages.append(true_age + rng.choice([-20, -5, 5, 20]))
        elif roll < 0.40:
            ages.append(float("nan"))
        else:
            ages.append(round(true_age))
        dobs.append(None if rng.random() < 0.1 else dob)
    return [
        (
            pl.Series("age", ages, dtype=pl.Float64),
            pl.Series("dob", dobs, dtype=pl.Date),
        )
    ]


def _agesig_native(fx: tuple[pl.Series, pl.Series]) -> tuple[int, list]:
    age, dob = fx
    ref_epoch_days = (_AGE_REF - _AGE_EPOCH).days
    actual = age.cast(pl.Float64)
    dob_date32 = dob.cast(pl.Date, strict=False)
    count, indices = native_module().age_mismatch(
        actual.to_arrow(), dob_date32.to_arrow(), ref_epoch_days
    )
    # Stringify sample values (the profiler stores str(v)) so a NaN age compares
    # equal across lanes (raw nan != nan).
    return (count, [str(age[i]) for i in indices])


def _agesig_fallback(fx: tuple[pl.Series, pl.Series]) -> tuple[int, list]:
    age, dob = fx
    df = pl.DataFrame({"age": age, "dob": dob})
    result = df.select(
        actual=pl.col("age").cast(pl.Float64),
        expected=(
            (pl.lit(_AGE_REF).cast(pl.Date) - pl.col("dob").cast(pl.Date, strict=False))
            .dt.total_days()
            / 365.25
        ),
    )
    actual = result["actual"]
    expected = result["expected"]
    diff = (actual - expected).abs()
    mismatch_mask = (diff > 2.0) & actual.is_not_null() & expected.is_not_null()
    count = int(mismatch_mask.sum())
    return (count, [str(v) for v in age.filter(mismatch_mask).head(5).to_list()])


# ---------------------------------------------------------------------------
# pearson_r + chi2_contingency (correlation.py) -- W4 baseline-stat kernels.
#
# These kernels have NO pure-Python fallback: the profiler consumes the scipy
# STATISTIC directly (`pearsonr(a,b)[0]`, `chi2_contingency(m)[0]`), so the
# harness's `run_fallback` calls SCIPY itself as the parity oracle. Both are
# pure-arithmetic (deterministic), so a float-reduction-tight canonicalisation
# (`_canon_float`, ~9 sig-figs, applied in BOTH lanes) collapses the last-digit
# noise while still tripping the oracle on any real divergence -- so this stays
# an exact-`!=` check and ACCEPTED_DIVERGENCES remains EMPTY.
# ---------------------------------------------------------------------------


def _pearson_fixtures(seed: int) -> list[tuple[list[float], list[float]]]:
    rng = random.Random(seed)
    n = rng.randint(30, 250)
    # Correlated pair: y = slope*x + noise, so r lands across the whole range.
    xs = [rng.uniform(-100, 100) for _ in range(n)]
    slope = rng.choice([-2.0, -0.5, 0.3, 1.5])
    ys = [slope * x + rng.gauss(0, 20) for x in xs]
    # Adversarial, seed-invariant: perfect +1 / perfect -1 (must clamp exactly).
    pos = [float(i) for i in range(40)]
    perfect_pos = ([v for v in pos], [2.0 * v + 1.0 for v in pos])
    perfect_neg = ([v for v in pos], [-3.0 * v + 5.0 for v in pos])
    # A near-zero-correlation symmetric pair.
    zx = [float(i) for i in range(1, 41)]
    zy = [1.0 if i % 2 else -1.0 for i in range(40)]
    return [(xs, ys), perfect_pos, perfect_neg, (zx, zy)]


def _pearson_native(fx: tuple[list[float], list[float]]) -> str:
    a, b = fx
    r = native_module().pearson_r(pa.array(a, type=pa.float64()), pa.array(b, type=pa.float64()))
    return _canon_float(r)


def _pearson_fallback(fx: tuple[list[float], list[float]]) -> str:
    a, b = fx
    r = _scipy_stats.pearsonr(a, b)[0]
    return _canon_float(r)


def _chi2_contingency_fixtures(seed: int) -> list[tuple[list[list[float]], int, int]]:
    rng = random.Random(seed)
    fixtures: list[tuple[list[list[float]], int, int]] = []
    # A random 2x2 (Yates path) and a random 3x3 / 2x4 (no correction).
    m2 = [[float(rng.randint(5, 60)) for _ in range(2)] for _ in range(2)]
    fixtures.append((m2, 2, 2))
    m3 = [[float(rng.randint(5, 80)) for _ in range(3)] for _ in range(3)]
    fixtures.append((m3, 3, 3))
    m24 = [[float(rng.randint(5, 50)) for _ in range(4)] for _ in range(2)]
    fixtures.append((m24, 2, 4))
    # Seed-invariant adversarials: 2x2 with all |obs-exp| < 0.5 (Yates clips each
    # residual to 0 -> chi2 == 0), a strong 2x2, and a clean 3x2.
    fixtures.append(([[5.0, 5.0], [5.0, 6.0]], 2, 2))
    fixtures.append(([[1.0, 9.0], [9.0, 1.0]], 2, 2))
    fixtures.append(([[10.0, 20.0], [30.0, 20.0], [10.0, 30.0]], 3, 2))
    return fixtures


def _chi2_contingency_native(fx: tuple[list[list[float]], int, int]) -> str:
    matrix, nrows, ncols = fx
    flat = [v for row in matrix for v in row]
    return _canon_float(native_module().chi2_contingency_stat(flat, nrows, ncols))


def _chi2_contingency_fallback(fx: tuple[list[list[float]], int, int]) -> str:
    matrix, _nrows, _ncols = fx
    return _canon_float(_scipy_stats.chi2_contingency(matrix)[0])


REGISTERED_COMPONENTS: list[Component] = [
    Component("benford", _benford_native, _benford_fallback, _benford_fixtures),
    Component("composite_keys", _keys_native, _keys_fallback, _keys_fixtures),
    Component("functional_dependencies", _fd_native, _fd_fallback, _fd_fixtures),
    Component("approximate_fd", _afd_native, _afd_fallback, _afd_fixtures),
    Component("fuzzy_values", _fuzzy_native, _fuzzy_fallback, _fuzzy_fixtures),
    Component("cell_quality", _cell_quality_native, _cell_quality_fallback, _cell_quality_fixtures),
    Component(
        "functional_dependencies_bridge",
        _fd_bridge_native,
        _fd_bridge_fallback,
        _fd_bridge_fixtures,
    ),
    Component("csv_infer", _csv_infer_native, _csv_infer_fallback, _csv_infer_fixtures),
    Component(
        "numeric_stats",
        _numeric_stats_native,
        _numeric_stats_fallback,
        _numeric_stats_fixtures,
    ),
    Component(
        "column_aggregate",
        _column_aggregate_native,
        _column_aggregate_fallback,
        _column_aggregate_fixtures,
    ),
    Component(
        "sequence_analysis",
        _sequence_native,
        _sequence_fallback,
        _sequence_fixtures,
    ),
    Component(
        "date_freshness",
        _freshness_native,
        _freshness_fallback,
        _freshness_fixtures,
    ),
    Component("duplicate_signatures", _dupsig_native, _dupsig_fallback, _dupsig_fixtures),
    Component("age_mismatch", _agesig_native, _agesig_fallback, _agesig_fixtures),
    Component("pearson_r", _pearson_native, _pearson_fallback, _pearson_fixtures),
    Component(
        "chi2_contingency",
        _chi2_contingency_native,
        _chi2_contingency_fallback,
        _chi2_contingency_fixtures,
    ),
]
