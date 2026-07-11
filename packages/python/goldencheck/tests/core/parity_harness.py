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

import os
import random
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl
import pyarrow as pa
from goldencheck import cell_quality as _cell_quality
from goldencheck import functional_dependencies as _fd_bridge
from goldencheck.core._native_loader import native_module
from goldencheck.engine.csv_infer import infer_and_type
from goldencheck.profilers import fuzzy_values as fv
from goldencheck.relations import approx_fd as afd
from goldencheck.relations import composite_key as ck
from goldencheck.relations import functional_dependency as fd

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
]
