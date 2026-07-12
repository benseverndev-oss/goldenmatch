"""#1207 PR1: per-identifier blocking-union on null-sparse multi-source person data."""
from __future__ import annotations

from collections import defaultdict

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    _build_strong_identifier_union,
    _union_coverage,
    build_blocking,
    profile_columns,
)
from goldenmatch.core.blocker import build_blocks
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
    """Post-fix: the null-sparse shape now yields a per-identifier union."""
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(cfg)


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


def test_union_excludes_perfect_surrogate_id():
    """#876 guard: a perfect-surrogate id (unique per row, card_ratio 1.0) makes
    only singleton blocks (0 candidate pairs) -- it must NOT become a union pass,
    while genuinely repeating strong ids still do."""
    n = 100
    df = pl.DataFrame({
        # perfect surrogate: unique per row -> cardinality_ratio 1.0
        "rec_id": [f"{1000000000 + i}" for i in range(n)],
        # two genuinely repeating strong ids (card < 1.0), fully non-null so the
        # union still has >=2 passes AND clears the 0.95 coverage gate.
        "id_a": [f"{2000000000 + (i % 20)}" for i in range(n)],
        "id_b": [f"{3000000000 + (i % 25)}" for i in range(n)],
    })
    profiles = profile_columns(df)
    by_name = {p.name: p for p in profiles}
    # sanity: rec_id must profile as a STRONG-ID type with card_ratio >= 1.0,
    # else the guard is never exercised (the test would pass trivially).
    assert by_name["rec_id"].col_type in ("identifier", "email", "phone")
    assert by_name["rec_id"].cardinality_ratio >= 1.0
    cfg = _build_strong_identifier_union(profiles, df, n_rows_full=df.height)
    assert cfg is not None
    fieldsets = {tuple(p.fields) for p in cfg.passes}
    assert ("rec_id",) not in fieldsets, f"perfect surrogate leaked into {fieldsets}"
    # the two genuinely repeating ids DID survive as passes
    assert ("id_a",) in fieldsets
    assert ("id_b",) in fieldsets


def test_build_blocking_emits_union_on_null_sparse_shape():
    df = _null_sparse_person_df()
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(cfg), (
        f"expected a per-identifier union, got strategy={cfg.strategy} "
        f"keys={[k.fields for k in (cfg.keys or [])]} "
        f"passes={[p.fields for p in (cfg.passes or [])]}"
    )
    cov = _union_coverage(df, [p.fields for p in cfg.passes])
    assert cov >= 0.95


def _null_sparse_with_dupes_df(n_entities: int = 1500, seed: int = 99) -> pl.DataFrame:
    """Like _null_sparse_person_df but plants TRUE duplicates: ~1/3 of entities
    appear as 2 records that SHARE a strong id (npi or email) but have a
    DIFFERENT surname (data-entry/name-change), so name/soundex blocking can't
    co-locate them — only an id pass can. Returns one row per record with a
    ground-truth column `__entity__` (NOT a blocking/scoring field — drop it
    before profiling)."""
    import random

    rng = random.Random(seed)
    surnames._load()
    if surnames._state is None:
        pytest.skip("surname refdata unavailable")
    last_pool = [s.title() for s in list(surnames._state.ranks.keys())[:400]]
    first_pool = ["John", "Jane", "Robert", "Mary", "Michael", "Linda", "James", "Susan"]
    rows = []
    rec = 0
    for e in range(n_entities):
        npi = f"{1000000000 + e}"          # entity's shared strong id
        email = f"person{e}@example.com"
        first = rng.choice(first_pool)
        last_a = rng.choice(last_pool)
        # base record (always present)
        rows.append({"__entity__": e, "first_name": first, "last_name": last_a,
                     "npi": npi, "email": email,
                     "phone": None if rng.random() < 0.71 else f"555{rng.randint(1000000,9999999)}",
                     "zip": None if rng.random() < 0.69 else f"{rng.randint(10000,99999)}"})
        rec += 1
        if e % 3 == 0:
            # planted duplicate: SAME npi+email, DIFFERENT surname, sparse other ids
            last_b = rng.choice([s for s in last_pool if s != last_a])
            rows.append({"__entity__": e, "first_name": first, "last_name": last_b,
                         "npi": npi if rng.random() < 0.7 else None,   # id sometimes only in one record
                         "email": email,
                         "phone": None, "zip": None})
            rec += 1
    return pl.DataFrame(rows)


def _truth_pairs(df_full: pl.DataFrame) -> set[tuple[int, int]]:
    """All (min,max) record-position pairs that share a ground-truth __entity__."""
    ent = df_full["__entity__"].to_list()
    groups: dict[int, list[int]] = defaultdict(list)
    for idx, e in enumerate(ent):
        groups[e].append(idx)
    pairs: set[tuple[int, int]] = set()
    for members in groups.values():
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                pairs.add((members[i], members[j]))
    return pairs


def _membership_recall(df: pl.DataFrame, cfg, truth: set[tuple[int, int]]) -> float:
    """Fraction of true-dup pairs whose two records co-occur in >=1 emitted block.

    Aligns `__row_id__` to the record's POSITION index (the same index
    `_truth_pairs` uses), so a co-membership lookup maps straight back to truth.
    """
    if not truth:
        return 0.0
    df_rid = df if "__row_id__" in df.columns else df.with_row_index("__row_id__")
    blocks = build_blocks(df_rid.lazy(), cfg)
    row_to_blocks: dict[int, set[int]] = defaultdict(set)
    for bi, b in enumerate(blocks):
        bdf = b.materialize().native if hasattr(b.df, "collect") else b.df
        for rid in bdf["__row_id__"].to_list():
            row_to_blocks[int(rid)].add(bi)
    covered = sum(1 for a, c in truth if row_to_blocks[a] & row_to_blocks[c])
    return covered / len(truth)


def test_union_lifts_blocking_recall_vs_name_only():
    df_full = _null_sparse_with_dupes_df()
    truth = _truth_pairs(df_full)
    assert truth, "fixture planted no true-duplicate pairs"
    df = df_full.drop("__entity__")          # __entity__ must NOT be a blocking field
    profiles = profile_columns(df)

    union_cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert _has_union_over_identifiers(union_cfg), (
        f"expected a per-identifier union, got strategy={union_cfg.strategy} "
        f"passes={[p.fields for p in (union_cfg.passes or [])]}"
    )

    union_recall = _membership_recall(df, union_cfg, truth)
    # a name-only baseline (single last_name soundex pass) cannot co-locate the
    # divergent-surname dupes
    name_only = BlockingConfig(
        keys=[BlockingKeyConfig(fields=["last_name"], transforms=["lowercase", "soundex"])]
    )
    name_recall = _membership_recall(df, name_only, truth)

    assert union_recall >= 0.95, f"union_recall={union_recall:.3f} (name_recall={name_recall:.3f})"
    assert union_recall > name_recall, (
        f"union did not lift recall: union={union_recall:.3f} name={name_recall:.3f}"
    )


def test_union_does_not_displace_a_good_single_key():
    """Guard: when a low-null, non-surrogate exact key exists, the single-key
    path wins; the union does not fire (we only add the union on the
    fall-through)."""
    df = pl.DataFrame({
        # REPEAT email so it's a legitimate exact key, not a surrogate: card_ratio
        # 0.5, 0% null. (A unique-per-row email has card_ratio 1.0 and is DROPPED
        # by the surrogate guard, which would let the union fire -- a vacuous test.)
        "email": [f"u{i % 100}@x.com" for i in range(200)],
        "first_name": ["A"] * 200, "last_name": ["B"] * 200,
    })
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df, n_rows_full=df.height)
    assert cfg.strategy == "static"
    assert [k.fields for k in (cfg.keys or [])] == [["email"]]
