"""Parity: the native kernels must produce byte-identical output to the
pure-Python reference. This is the gate that lets a component sit in
``_native_loader._GATED_ON`` (run under ``GOLDENCHECK_NATIVE=auto``).

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import random
from collections import Counter

import numpy as np
import pytest
from goldencheck.baseline import statistical as st
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _python_histogram(values: np.ndarray) -> list[int]:
    """The pure-Python leading-digit histogram (digits 1..9)."""
    counts = Counter(st._extract_leading_digits(values))
    return [counts.get(d, 0) for d in range(1, 10)]


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_benford_histogram_parity_random(seed: int) -> None:
    import pyarrow as pa

    rng = random.Random(seed)
    values = np.array(
        [rng.uniform(1e-4, 1e7) for _ in range(8000)]
        + [rng.lognormvariate(0, 4) for _ in range(2000)],
        dtype=np.float64,
    )
    native_hist = list(native_module().benford_leading_digits(pa.array(values)))
    assert native_hist == _python_histogram(values)


@native_only
def test_benford_histogram_parity_adversarial() -> None:
    """Exact powers of 10, tiny/huge magnitudes, and skipped values -- the
    float edge cases most likely to diverge between Rust and Python ``log10``."""
    import pyarrow as pa

    values = np.array(
        [10.0**k for k in range(-12, 13)]  # exact powers of 10
        + [9.999999999, 1.0000001, 99.9, 100.0, 999999.0]
        + [0.0, -1.0, -1e6, float("nan"), float("inf"), float("-inf")]
        + [1e-300, 1e300, 5e-1, 4.4],
        dtype=np.float64,
    )
    native_hist = list(native_module().benford_leading_digits(pa.array(values)))
    assert native_hist == _python_histogram(values)


@native_only
def test_benford_handles_nulls() -> None:
    """Null slots must be dropped (their backing f64 is undefined), matching
    the Python path which only sees non-null values."""
    import pyarrow as pa

    arr = pa.array([1.5, None, 200.0, None, 9.9], type=pa.float64())
    native_hist = list(native_module().benford_leading_digits(arr))
    py = _python_histogram(np.array([1.5, 200.0, 9.9], dtype=np.float64))
    assert native_hist == py


@native_only
def test_compute_benford_native_matches_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: the chi-squared p-value dict is identical with the native
    kernel forced on vs forced off."""
    rng = random.Random(99)
    # Benford-ish: first digits weighted toward 1 across several magnitudes.
    values = np.array(
        [rng.choice([1, 1, 1, 2, 2, 3, 4, 5, 6, 7, 8, 9]) * 10.0 ** rng.randint(0, 5)
         + rng.random() for _ in range(5000)],
        dtype=np.float64,
    )

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    py_result = st._compute_benford(values)
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "1")
    native_result = st._compute_benford(values)

    assert py_result == native_result


def test_native_disabled_env_forces_python(monkeypatch: pytest.MonkeyPatch) -> None:
    """GOLDENCHECK_NATIVE=0 always uses the Python path, even when the ext is
    present -- so the result is unchanged whether or not native is installed."""
    from goldencheck.core._native_loader import native_enabled

    monkeypatch.setenv("GOLDENCHECK_NATIVE", "0")
    assert native_enabled("benford") is False


# ---------------------------------------------------------------------------
# Composite-key + functional-dependency parity
# ---------------------------------------------------------------------------

import polars as pl  # noqa: E402
from goldencheck.relations import composite_key as ck  # noqa: E402


def _random_keyless_df(seed: int, rows: int = 200) -> pl.DataFrame:
    """A frame with no single-column key but some composite keys."""
    rng = random.Random(seed)
    a = [rng.randint(0, 4) for _ in range(rows)]
    b = [rng.choice(["x", "y", "z"]) for _ in range(rows)]
    c = [rng.randint(0, 6) for _ in range(rows)]
    return pl.DataFrame({"a": a, "b": b, "c": c, "d": list(range(rows))})


@native_only
@pytest.mark.parametrize("seed", range(8))
def test_composite_key_search_parity(seed: int) -> None:
    """Native composite-key search returns the same minimal-key index sets as
    the pure-Python BFS, on identical candidate columns."""
    df = _random_keyless_df(seed)
    # Drop the unique 'd' so neither path early-exits on a single-column key.
    df = df.drop("d")
    candidates = ck._select_candidates(df, df.height)
    if len(candidates) < 2:
        pytest.skip("not enough candidates for this seed")
    single_unique = [False] * len(candidates)

    py = ck._python_search(df, candidates, df.height, ck.MAX_KEY_SIZE)
    arrays = [df[col].to_arrow() for col in candidates]
    nat = native_module().composite_key_search(arrays, ck.MAX_KEY_SIZE, single_unique)

    # Compare as sets of sorted tuples (order within a key is already sorted).
    assert {tuple(k) for k in nat} == {tuple(k) for k in py}


@native_only
def test_functional_dependency_parity() -> None:
    import pyarrow as pa

    def py_fd(lhs: list, rhs: list) -> bool:
        seen: dict = {}
        for left, right in zip(lhs, rhs):
            if left in seen and seen[left] != right:
                return False
            seen.setdefault(left, right)
        return True

    cases = [
        (["us", "us", "uk", "us"], ["NY", "CA", "LDN", "NY"]),     # not FD
        (["a", "a", "b", "b"], [1, 1, 2, 2]),                       # FD holds
        ([1, 2, 3, 1], [9, 9, 8, 9]),                               # FD holds
        ([None, None, 1], ["x", "y", "z"]),                         # null grouping
    ]
    for lhs, rhs in cases:
        l_arr = pa.array(lhs)
        r_arr = pa.array(rhs)
        assert native_module().functional_dependency_holds(l_arr, r_arr) == py_fd(lhs, rhs)


from goldencheck.relations import functional_dependency as fd  # noqa: E402


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_discover_fd_parity(seed: int) -> None:
    """Native FD discovery returns the same (det, dep) pairs as the Polars
    n_unique-identity fallback, on identical candidate columns."""
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
    cols = fd._select_candidates(df, df.height)
    if len(cols) < 2:
        pytest.skip("not enough candidates")
    py = set(fd._discover_polars(df, cols, df.height))
    arrays = [df[c].to_arrow() for c in cols]
    nat = set(native_module().discover_functional_dependencies(arrays))
    assert nat == py


from goldencheck.profilers import fuzzy_values as fv  # noqa: E402


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_fuzzy_value_clusters_parity(seed: int) -> None:
    """Native fuzzy value clustering matches the pure-Python fallback exactly
    (same normalization, blocking, Levenshtein metric, and union-find)."""
    rng = random.Random(seed)
    bases = ["California", "Texas", "New York", "Florida", "Washington", "Arizona"]
    values: list[str] = []
    for b in bases:
        values.append(b)
        # add a few typo'd / re-cased variants
        for _ in range(rng.randint(0, 3)):
            s = list(b.lower())
            if len(s) > 3 and rng.random() < 0.7:
                del s[rng.randrange(len(s))]  # drop a char (typo)
            values.append("".join(s).upper() if rng.random() < 0.3 else "".join(s))
    # de-dup to mimic the profiler's distinct-value input
    values = list(dict.fromkeys(values))

    py = fv._python_clusters(values, fv._MIN_SIMILARITY)
    nat = native_module().near_duplicate_value_clusters(values, fv._MIN_SIMILARITY)
    # Compare as sets of frozensets (cluster identity, order-independent).
    assert {frozenset(c) for c in py} == {frozenset(c) for c in nat}


from goldencheck.relations import approx_fd as afd  # noqa: E402


@native_only
@pytest.mark.parametrize("seed", range(6))
def test_approximate_fd_parity(seed: int) -> None:
    """Native approx-FD discovery + violation rows match the pure-Python fallback
    exactly (same interning, mode tie-break, avg-group guard)."""
    rng = random.Random(seed)
    n = 400
    zips = [rng.randint(0, 15) for _ in range(n)]
    z2c: dict[int, int] = {}
    city = [z2c.setdefault(z, rng.randint(0, 12)) for z in zips]
    # inject a few violations
    for _ in range(rng.randint(1, 6)):
        city[rng.randrange(n)] = rng.randint(13, 20)
    df = pl.DataFrame({"zip": zips, "city": city, "noise": [rng.randint(0, 5) for _ in range(n)]})
    cols = afd._select_candidates(df)

    arrays = [df[c].to_arrow() for c in cols]
    nat_triples = {(i, j, v) for i, j, v in
                   native_module().discover_approximate_fds(arrays, afd._MIN_CONFIDENCE)}
    cols_ids = [afd._intern(df[c].to_list()) for c in cols]
    py_triples = set(afd._discover_python(cols_ids, n, afd._MIN_CONFIDENCE))
    assert nat_triples == py_triples

    # And the violation row sets match for each discovered pair.
    for i, j, _v in nat_triples:
        nat_rows = native_module().fd_violation_rows(arrays[i], arrays[j])
        py_rows = afd._violation_rows(cols_ids[i], cols_ids[j])
        assert nat_rows == py_rows
