"""Parity: the native ``duplicate_signatures`` kernel must produce the SAME four
counts the ``approx_duplicate`` relation profiler derives from Polars
(``group_by(signature).len()`` over ``_exact_signature`` / ``_normalized_signature``):
``(exact_dup_rows, exact_dup_groups, near_dup_rows, near_dup_groups)``.

Per the W3 spec the counts depend only on WHICH rows collide (equal
signatures), not the literal signature bytes -- so the kernel's own
deterministic cast-to-string yields identical counts as long as it induces the
same equality partition as Polars' ``cast(Utf8)``. For int/string/bool/date that
holds exactly. This is asserted as ONE exact component (empty divergence).

Load-bearing cases exercised: null and ``""`` collide (``fill_null("")``);
``"!!!"`` normalizes to ``""`` and near-collides; Unicode lowercase (``İ`` / ``Σ``);
mixed dtype; single column; all-unique.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import random

import polars as pl
import pytest
from goldencheck.core._native_loader import native_available, native_module
from goldencheck.relations.approx_duplicate import _exact_signature, _normalized_signature

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _polars_counts(df: pl.DataFrame) -> tuple[int, int, int, int]:
    """Ground truth: replicate the profiler's Polars group_by/join count logic."""
    work = pl.DataFrame(
        {
            "__norm__": _normalized_signature(df),
            "__exact__": _exact_signature(df),
        }
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


def _native_counts(df: pl.DataFrame) -> tuple[int, int, int, int]:
    is_string = [dt == pl.Utf8 for dt in df.dtypes]
    arrays = [df[c].to_arrow() for c in df.columns]
    return tuple(native_module().duplicate_signatures(arrays, is_string))


def _check(df: pl.DataFrame) -> None:
    assert _native_counts(df) == _polars_counts(df), df.to_dict(as_series=False)


# ---------------------------------------------------------------------------
# Adversarial hand-built fixtures (each isolates one behaviour).
# ---------------------------------------------------------------------------
@native_only
def test_pure_string_exact_and_near() -> None:
    _check(
        pl.DataFrame(
            {"name": ["Acme, Inc.", "acme inc", "ACME  Inc", "Acme, Inc.", "Beta", "beta"]},
            schema={"name": pl.Utf8},
        )
    )


@native_only
def test_null_vs_empty_string_collide() -> None:
    # null, "", and "!!!" (normalizes to "") — the fill_null("") + normalize
    # collision that must NOT use intern_column.
    _check(pl.DataFrame({"name": [None, "", "!!!", "???", "x"]}, schema={"name": pl.Utf8}))


@native_only
def test_int_dups() -> None:
    _check(pl.DataFrame({"code": [1, 2, 1, 1, 3, None, None]}, schema={"code": pl.Int64}))


@native_only
def test_mixed_dtype() -> None:
    _check(
        pl.DataFrame(
            {
                "name": ["Acme", "acme", "ACME", "Beta", "Beta"],
                "code": [1, 1, 1, 2, 2],
                "flag": [True, True, True, False, False],
            },
            schema={"name": pl.Utf8, "code": pl.Int64, "flag": pl.Boolean},
        )
    )


@native_only
def test_all_unique() -> None:
    _check(pl.DataFrame({"a": ["p", "q", "r"], "b": [1, 2, 3]}, schema={"a": pl.Utf8, "b": pl.Int64}))


@native_only
def test_single_col_bool() -> None:
    _check(pl.DataFrame({"flag": [True, False, True, None, None]}, schema={"flag": pl.Boolean}))


@native_only
def test_unicode_lowercase() -> None:
    # "İ" / "Σ" — Rust std to_lowercase (Unicode) must match Polars str.to_lowercase.
    _check(
        pl.DataFrame(
            {"s": ["İstanbul", "istanbul", "ΣIGMA", "sigma", "ΣIGMA"]},
            schema={"s": pl.Utf8},
        )
    )


@native_only
def test_float_col_partition() -> None:
    # Floats: Rust Display is injective on finite values; NaN collapses to one
    # group — same partition as Polars, so counts match despite byte differences.
    _check(
        pl.DataFrame(
            {"f": [1.0, 1.0, 2.5, float("nan"), float("nan"), None]},
            schema={"f": pl.Float64},
        )
    )


@native_only
def test_categorical_not_normalized() -> None:
    # A Categorical is Dictionary(_,Utf8) in Arrow but NOT pl.Utf8 -> is_string
    # is False -> the kernel must NOT normalize it (case-sensitive equality).
    df = pl.DataFrame({"c": ["A", "a", "A", "b"]}, schema={"c": pl.Categorical})
    _check(df)


@native_only
def test_empty_and_single_row() -> None:
    # < 2 rows: the kernel still returns all-zero (the profiler bails on n<2).
    assert _native_counts(pl.DataFrame({"a": ["x"]}, schema={"a": pl.Utf8})) == (0, 0, 0, 0)


# ---------------------------------------------------------------------------
# Randomized fuzz — string + int + bool columns with planted dup/near-dup rows.
# ---------------------------------------------------------------------------
@native_only
@pytest.mark.parametrize("seed", range(25))
def test_random(seed: int) -> None:
    rng = random.Random(seed)
    names = [
        "Acme, Inc.",
        "acme inc",
        "ACME  Inc",
        "Beta LLC",
        "beta llc",
        "Gamma",
        "gamma",
        "Delta",
        None,
        "",
        "!!!",
    ]
    n = rng.randint(2, 60)
    df = pl.DataFrame(
        {
            "name": [rng.choice(names) for _ in range(n)],
            "code": [rng.choice([1, 2, 3, None]) for _ in range(n)],
            "flag": [rng.choice([True, False, None]) for _ in range(n)],
        },
        schema={"name": pl.Utf8, "code": pl.Int64, "flag": pl.Boolean},
    )
    _check(df)
