"""Tests for the structured functional_dependencies(df) API."""
from __future__ import annotations

import polars as pl
from goldencheck import FunctionalDependency, functional_dependencies


def test_detects_strict_fd() -> None:
    n = 120
    zips = [i % 6 for i in range(n)]
    z2c = {0: 0, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4}
    df = pl.DataFrame({"zip": zips, "city": [z2c[z] for z in zips], "amt": [i % 5 for i in range(n)]})
    fds = functional_dependencies(df)
    assert any(isinstance(f, FunctionalDependency) for f in fds)
    zip_fd = next((f for f in fds if f.determinant == "zip"), None)
    assert zip_fd is not None
    assert "city" in zip_fd.dependents
    assert zip_fd.confidence == 1.0


def test_surfaces_high_but_not_unique_determinant() -> None:
    # 'acct' cardinality 0.6 (in [0.5, 1.0)) -> name (strict). This is the case
    # door #3 targets: an identity anchor that isn't a perfectly-unique key.
    accts = [1000 + k for k in range(48)] + [n for k in range(24) for n in (2000 + k,) * 3]
    a2name = {a: f"name_{a}" for a in set(accts)}
    df = pl.DataFrame({"acct": accts, "name": [a2name[a] for a in accts], "amt": [i % 5 for i in range(len(accts))]})
    fds = functional_dependencies(df)
    assert any(f.determinant == "acct" and "name" in f.dependents for f in fds)


def test_perfectly_unique_key_excluded() -> None:
    # Documented limitation: a unique key is a trivial determinant -> not surfaced.
    n = 120
    df = pl.DataFrame({"pk": list(range(n)), "grp": [i % 4 for i in range(n)]})
    assert all(f.determinant != "pk" for f in functional_dependencies(df))


def test_clean_independent_columns_empty() -> None:
    n = 120
    df = pl.DataFrame({"a": [i % 5 for i in range(n)], "b": [(i * 3) % 7 for i in range(n)]})
    assert functional_dependencies(df) == []


def test_trivial_frames() -> None:
    assert functional_dependencies(pl.DataFrame({"a": [1, 2, 3]})) == []
    assert functional_dependencies(pl.DataFrame()) == []
