"""#1207 PR1: per-identifier blocking-union on null-sparse multi-source person data."""
from __future__ import annotations

import polars as pl
import pytest

from goldenmatch.core.autoconfig import (
    _build_strong_identifier_union,
    _union_coverage,
    build_blocking,
    profile_columns,
)
from goldenmatch.refdata import surnames


def _null_sparse_person_df(n: int = 6000, seed: int = 1207) -> pl.DataFrame:
    """Null-sparse multi-source person/provider shape from #1207.

    - npi: highest-cardinality strong id, ~39% null
    - email ~25% null, phone ~71% null, zip ~69% null (none 1:1)
    - common-name collisions: many records share first+last across different npi
    Surnames drawn from the census refdata pool so soundex codes spread
    (else blocking hangs — project invariant).
    """
    import random

    rng = random.Random(seed)
    surnames._load()
    if surnames._state is None:  # refdata file missing -> can't guarantee soundex spread
        pytest.skip("surname refdata unavailable")
    last_pool = [s.title() for s in list(surnames._state.ranks.keys())[:400]]
    first_pool = ["John", "Jane", "Robert", "Mary", "Michael", "Linda",
                  "James", "Patricia", "David", "Jennifer", "William", "Susan"]
    cities = ["Springfield", "Riverton", "Fairview", "Greenville", "Madison"]

    rows = []
    for i in range(n):
        # ~1/3 of records reuse a small (first,last) space to force collisions
        first = rng.choice(first_pool)
        last = rng.choice(last_pool[:30]) if i % 3 == 0 else rng.choice(last_pool)
        # realistic 10-digit numeric NPI so the profiler classifies it as an
        # identifier (the "npi####" string form profiles as plain string).
        npi = None if rng.random() < 0.39 else f"{1000000000 + i}"
        email = None if rng.random() < 0.25 else f"user{i}@example.com"
        phone = None if rng.random() < 0.71 else f"555{rng.randint(1000000, 9999999)}"
        zipc = None if rng.random() < 0.69 else f"{rng.randint(10000, 99999)}"
        rows.append({
            "first_name": first, "last_name": last, "npi": npi,
            "email": email, "phone": phone, "zip": zipc,
            "city": rng.choice(cities),
        })
    return pl.DataFrame(rows)


def _has_union_over_identifiers(cfg) -> bool:
    """True if cfg is a multi_pass union with >=2 distinct single-id/name passes."""
    if cfg.strategy != "multi_pass" or not cfg.passes:
        return False
    pass_fieldsets = {tuple(p.fields) for p in cfg.passes}
    id_singletons = {("npi",), ("email",), ("phone",)}
    return len(pass_fieldsets & id_singletons) >= 2


def test_characterize_current_emission_is_not_a_union():
    """RED baseline: today build_blocking does NOT emit a per-identifier union
    on this shape (it returns a single-id compound or a name fallback)."""
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    # Document what it actually is, for the record:
    print("CURRENT strategy=", cfg.strategy, "keys=",
          [k.fields for k in (cfg.keys or [])],
          "passes=", [p.fields for p in (cfg.passes or [])])
    assert not _has_union_over_identifiers(cfg)


def test_union_coverage_is_or_over_passes():
    df = _null_sparse_person_df()
    cov = _union_coverage(df, [["npi"], ["email"], ["first_name", "last_name"]])
    assert cov >= 0.95


def test_build_union_includes_high_null_id_passes():
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = _build_strong_identifier_union(profiles, df, n_rows_full=df.height)
    assert cfg is not None
    assert cfg.strategy == "multi_pass"
    fieldsets = {tuple(p.fields) for p in cfg.passes}
    assert ("phone",) in fieldsets
    assert ("npi",) in fieldsets
    assert ("email",) in fieldsets
    assert any(p.fields == ["first_name", "last_name"] for p in cfg.passes)


def test_build_union_returns_none_when_too_few_passes():
    df = pl.DataFrame({"npi": [None, None, "x", None], "note": ["a", "b", "c", "d"]})
    profiles = profile_columns(df)
    assert _build_strong_identifier_union(profiles, df, n_rows_full=df.height) is None


def test_build_union_returns_none_when_under_coverage():
    df = pl.DataFrame({
        # both ids non-null on the SAME 40 of 100 rows -> OR-coverage = 0.40 < 0.95,
        # each 40% non-null (above the 2% floor) -> 2 strong-id passes survive.
        # No name/geo columns, so name passes can't form and rescue coverage.
        "id_a": [f"{1000000000 + i}" if i < 40 else None for i in range(100)],
        "id_b": [f"{2000000000 + i}" if i < 40 else None for i in range(100)],
    })
    profiles = profile_columns(df)
    # sanity: both columns must profile as a strong-id type AND form >=2 passes,
    # so the None result comes from the COVERAGE gate, not the <2-passes path.
    strong = [p.name for p in profiles
              if p.col_type in ("identifier", "email", "phone")]
    assert set(strong) == {"id_a", "id_b"}, f"expected 2 strong ids, got {strong}"
    assert _union_coverage(df, [["id_a"], ["id_b"]]) < 0.95
    assert _build_strong_identifier_union(profiles, df, n_rows_full=df.height) is None
