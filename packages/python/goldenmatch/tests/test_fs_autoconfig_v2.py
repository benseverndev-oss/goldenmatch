"""FS auto-config v2 — comparison-set + blocking curation (GOLDENMATCH_FS_AUTOCONFIG_V2).

Three levers, all scoped to the PROBABILISTIC path, gated default-OFF behind
GOLDENMATCH_FS_AUTOCONFIG_V2 (the repo pattern for unswept auto-config levers):

  #1  admit date columns (dob) as `levenshtein` comparison fields
  #2a drop redundant person-name composites when atomic given+family present
  #2b floor fuzzy fields at low cardinality (drop gender-like); exact ids exempt
  #3  diversify blocking onto orthogonal stable keys (date-year, postcode/zip)

Measured impact (scripts/bench_er_headtohead, GM probabilistic vs Splink): v2
beats Splink on every measurable PII set — historical_50k F1 0.624->0.779,
febrl3 0.983->0.991, synthetic 0.972->0.998. See _fs_autoconfig_v2_enabled.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.autoconfig import (
    ColumnProfile,
    _bound_probabilistic_blocking_pairs,
    _diversify_probabilistic_blocking,
    _fs_total_pair_budget,
    _project_pass_pairs,
    auto_configure_probabilistic_df,
    build_probabilistic_matchkeys,
)
from goldenmatch.core.frame import to_frame

ON = "GOLDENMATCH_FS_AUTOCONFIG_V2"


def _p(name, col_type, card=0.5, null=0.0):
    return ColumnProfile(
        name=name, dtype="Utf8", col_type=col_type, confidence=0.9,
        null_rate=null, cardinality_ratio=card, avg_len=8,
    )


def _person_profiles():
    """historical_50k-shaped: 4 name fields (2 composite), a date, a low-card
    categorical, a zip, and an occupation."""
    return [
        _p("full_name", "name", card=0.96),          # composite
        _p("first_and_surname", "name", card=0.95),  # composite
        _p("first_name", "name", card=0.42),         # atomic given
        _p("surname", "name", card=0.74),            # atomic family
        _p("dob", "date", card=0.58, null=0.24),     # date (lever #1)
        _p("gender", "name", card=0.002),            # low-card (lever #2b)
        _p("postcode", "zip", card=0.71, null=0.24),
        _p("occupation", "name", card=0.135),
    ]


def _fields(mks):
    assert len(mks) == 1
    return [f.field for f in mks[0].fields]


def _scorer_of(mks, field):
    return next(f.scorer for f in mks[0].fields if f.field == field)


# ── domain comparators (GOLDENMATCH_FS_DOMAIN_COMPARATORS, spec 2026-07-23) ──

def test_date_column_is_levenshtein_by_default(monkeypatch):
    # v2 on (admits dates), domain-comparators flag unset -> edit-distance, the
    # byte-identical-to-today admission.
    monkeypatch.setenv(ON, "1")
    monkeypatch.delenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", raising=False)
    mks = build_probabilistic_matchkeys(_person_profiles())
    assert _scorer_of(mks, "dob") == "levenshtein"


def test_date_column_is_date_diff_under_flag(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
    mks = build_probabilistic_matchkeys(_person_profiles())
    assert _scorer_of(mks, "dob") == "date_diff"
    # non-date fields are unchanged by the flag
    assert _scorer_of(mks, "surname") != "date_diff"


# ── Phase 2: numeric_diff + geo_haversine admission (same flag) ──────────────

def _numeric_geo_profiles():
    return [
        _p("first_name", "name", card=0.42),
        _p("surname", "name", card=0.74),
        _p("balance", "numeric", card=0.6),  # numeric (Phase 2)
        ColumnProfile(  # single combined lat,long column (Phase 2)
            name="coordinates", dtype="Utf8", col_type="string", confidence=0.5,
            null_rate=0.0, cardinality_ratio=0.9, avg_len=20,
            sample_values=["40.71,-74.00", "34.05,-118.24", "41.88,-87.63",
                           "29.76,-95.37", "33.45,-112.07", "39.95,-75.16"],
        ),
    ]


def test_numeric_and_geo_skipped_by_default(monkeypatch):
    # v2 on, domain-comparators flag unset -> numeric skipped entirely, and a
    # coordinate column is admitted only as a PLAIN string field (never as
    # geo_haversine). Byte-identical to today: the flag adds no new scorers.
    monkeypatch.setenv(ON, "1")
    monkeypatch.delenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", raising=False)
    mks = build_probabilistic_matchkeys(_numeric_geo_profiles())
    fields = _fields(mks)
    assert "balance" not in fields  # numeric is not an FS comparison field
    scorers = [f.scorer for f in mks[0].fields]
    assert "geo_haversine" not in scorers
    assert "numeric_diff:pct:0.1" not in scorers


def test_numeric_is_numeric_diff_under_flag(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
    mks = build_probabilistic_matchkeys(_numeric_geo_profiles())
    assert _scorer_of(mks, "balance") == "numeric_diff:pct:0.1"


def test_latlong_column_is_geo_haversine_under_flag(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
    mks = build_probabilistic_matchkeys(_numeric_geo_profiles())
    assert _scorer_of(mks, "coordinates") == "geo_haversine"


def test_non_coordinate_string_not_detected_as_geo(monkeypatch):
    # A plain free-text column must NOT be admitted as geo_haversine even under
    # the flag -- the sample-parse floor guards against misfire.
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
    profs = [
        _p("first_name", "name", card=0.42),
        ColumnProfile(
            name="notes", dtype="Utf8", col_type="description", confidence=0.5,
            null_rate=0.0, cardinality_ratio=0.9, avg_len=30,
            sample_values=["hello world", "a, b, c", "1, 2, 3, 4", "x", "y", "z"],
        ),
    ]
    mks = build_probabilistic_matchkeys(profs)
    scorers = [f.scorer for f in mks[0].fields] if mks else []
    assert "geo_haversine" not in scorers


# ── default ON; explicit =0 restores byte-identical legacy field set ──────────

def test_default_unset_is_v2(monkeypatch):
    monkeypatch.delenv(ON, raising=False)  # default flipped ON 2026-06-09
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    # v2 curates: dob admitted, name composites + low-card gender dropped.
    assert "dob" in fields
    assert "full_name" not in fields and "first_and_surname" not in fields
    assert "gender" not in fields


def test_card1_shared_identifier_admitted_pk_excluded():
    """A card==1.0 exact field is ambiguous: a shared identity-bearing VALUE
    (email/phone a duplicate carries verbatim -- F-S's single strongest signal)
    OR a per-record surrogate (a row PK). The ratio is measured on a SAMPLE that
    under-represents duplicates, so a blanket >= 1.0 exclusion silently drops the
    best comparison field and collapses EM to zero matches at scale (measured:
    zero-config FS F1 0.0 at 1M realistic person data). Admit email/phone;
    keep excluding the ambiguous bare `identifier` (covers row PKs)."""
    profiles = [
        _p("first_name", "name", card=0.42),
        _p("surname", "name", card=0.74),
        _p("email", "email", card=1.0),          # shared identifier -> ADMIT
        _p("phone", "phone", card=1.0),           # shared identifier -> ADMIT
        _p("record_id", "identifier", card=1.0),  # row PK -> EXCLUDE (hygiene)
    ]
    mks = build_probabilistic_matchkeys(profiles)
    fields = _fields(mks)
    assert "email" in fields, "card==1.0 email is the strongest F-S signal, must admit"
    assert "phone" in fields, "card==1.0 phone must admit"
    assert "record_id" not in fields, "per-record surrogate PK stays excluded"
    assert _scorer_of(mks, "email") == "exact"


def test_explicit_off_is_legacy(monkeypatch):
    monkeypatch.setenv(ON, "0")
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    # legacy keeps all 4 name fields + gender, and drops the date.
    assert "full_name" in fields and "first_and_surname" in fields
    assert "gender" in fields
    assert "dob" not in fields


@pytest.mark.parametrize("val", ["0", "false", "off", "no", "disabled"])
def test_falsey_values_keep_legacy(monkeypatch, val):
    monkeypatch.setenv(ON, val)
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    assert "dob" not in fields and "gender" in fields


# ── v2 ON: the three levers ───────────────────────────────────────────────────

def test_lever1_admits_date_as_levenshtein(monkeypatch):
    monkeypatch.setenv(ON, "1")
    mks = build_probabilistic_matchkeys(_person_profiles())
    fields = _fields(mks)
    assert "dob" in fields
    assert _scorer_of(mks, "dob") == "levenshtein"


def test_lever1_skips_perfectly_unique_date(monkeypatch):
    monkeypatch.setenv(ON, "1")
    profs = [_p("first_name", "name", card=0.4), _p("surname", "name", card=0.7),
             _p("ts", "date", card=1.0)]  # per-record timestamp -> no shared signal
    assert "ts" not in _fields(build_probabilistic_matchkeys(profs))


def test_lever2a_drops_composites_when_atomic_present(monkeypatch):
    monkeypatch.setenv(ON, "1")
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    assert "full_name" not in fields
    assert "first_and_surname" not in fields
    assert "first_name" in fields and "surname" in fields
    # birth-place-like `name` cols that aren't person-name composites are kept.
    assert "occupation" in fields


def test_lever2a_keeps_composite_when_no_atomic(monkeypatch):
    monkeypatch.setenv(ON, "1")
    # full_name present but NO atomic first/surname -> keep it (it's all we have).
    profs = [_p("full_name", "name", card=0.96), _p("city", "name", card=0.5)]
    assert "full_name" in _fields(build_probabilistic_matchkeys(profs))


def test_lever2b_drops_low_card_fuzzy(monkeypatch):
    monkeypatch.setenv(ON, "1")
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    assert "gender" not in fields            # card 0.002 < floor
    assert "occupation" in fields            # card 0.135 > floor


def test_lever2b_exact_identifier_exempt_from_floor(monkeypatch):
    monkeypatch.setenv(ON, "1")
    # An exact-scorer identifier at very low cardinality is NOT floored (#721:
    # F-S self-regulates a weak identifier via its u-probability).
    profs = [_p("first_name", "name", card=0.4), _p("surname", "name", card=0.7),
             _p("region_code", "identifier", card=0.003)]
    assert "region_code" in _fields(build_probabilistic_matchkeys(profs))


def test_curated_set_matches_expected(monkeypatch):
    monkeypatch.setenv(ON, "1")
    fields = set(_fields(build_probabilistic_matchkeys(_person_profiles())))
    assert fields == {"first_name", "surname", "dob", "postcode", "occupation"}


# ── lever #4: bibliographic free-text + multi-name admission ──────────────────

def _biblio_profiles():
    """DBLP-ACM-shaped: title (description), authors (multi_name), venue (name,
    low-card), year (year, blocking-only)."""
    return [
        _p("title", "description", card=0.95),
        _p("authors", "multi_name", card=0.92),
        _p("venue", "name", card=0.010),   # just above the 0.01 floor -> kept
        _p("year", "year", card=0.010),
        _p("record_id", "identifier", card=1.0),
    ]


def test_lever4_off_drops_freetext(monkeypatch):
    monkeypatch.setenv(ON, "0")
    fields = set(_fields(build_probabilistic_matchkeys(_biblio_profiles())))
    # legacy: title/authors dropped -> only venue survives (the mega-match bug).
    assert "title" not in fields and "authors" not in fields
    assert fields == {"venue"}


def test_lever4_admits_title_and_authors(monkeypatch):
    monkeypatch.setenv(ON, "1")
    mks = build_probabilistic_matchkeys(_biblio_profiles())
    fields = set(_fields(mks))
    assert "title" in fields and "authors" in fields
    assert _scorer_of(mks, "title") == "token_sort"
    assert _scorer_of(mks, "authors") == "token_sort"
    # venue (card 0.010) survives the low-card floor; year stays blocking-only.
    assert "venue" in fields
    assert "year" not in fields


def test_lever4_freetext_not_floored_at_high_cardinality(monkeypatch):
    monkeypatch.setenv(ON, "1")
    # A near-unique title (card 0.999) must NOT be floored — high cardinality is
    # HIGH discrimination for a fuzzy F-S field, not a surrogate-key exclusion.
    profs = [_p("title", "description", card=0.999)]
    assert "title" in _fields(build_probabilistic_matchkeys(profs))


# ── lever #3: blocking diversification ────────────────────────────────────────

def _base_blocking():
    return BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=["full_name"], transforms=["lowercase", "soundex"])],
    )


def test_lever3_off_is_noop(monkeypatch):
    monkeypatch.setenv(ON, "0")
    b = _base_blocking()
    out = _diversify_probabilistic_blocking(b, _person_profiles())
    assert [p.fields for p in (out.passes or [])] == [["full_name"]]


def test_lever3_adds_orthogonal_passes(monkeypatch):
    monkeypatch.setenv(ON, "1")
    out = _diversify_probabilistic_blocking(_base_blocking(), _person_profiles())
    sigs = {(tuple(p.fields), tuple(p.transforms or [])) for p in (out.passes or [])}
    assert (("dob",), ("substring:0:4",)) in sigs        # birth YEAR
    assert (("postcode",), ("strip",)) in sigs           # zip
    # original pass preserved
    assert (("full_name",), ("lowercase", "soundex")) in sigs


def test_lever3_skips_high_null_and_unique(monkeypatch):
    monkeypatch.setenv(ON, "1")
    profs = [
        _p("dob", "date", card=0.6, null=0.8),          # too null -> skip
        _p("ssn", "identifier", card=1.0),              # perfectly unique -> skip
        _p("zip", "zip", card=0.7, null=0.1),           # added
    ]
    out = _diversify_probabilistic_blocking(_base_blocking(), profs)
    sigs = {tuple(p.fields) for p in (out.passes or [])}
    assert ("zip",) in sigs
    assert ("dob",) not in sigs and ("ssn",) not in sigs


def test_lever3_no_duplicate_passes(monkeypatch):
    monkeypatch.setenv(ON, "1")
    # blocking already has the postcode pass -> don't add it twice.
    b = BlockingConfig(strategy="multi_pass", passes=[
        BlockingKeyConfig(fields=["postcode"], transforms=["strip"]),
    ])
    out = _diversify_probabilistic_blocking(b, _person_profiles())
    pc = [p for p in (out.passes or []) if p.fields == ["postcode"]]
    assert len(pc) == 1


def test_lever3_drops_oversized_low_cardinality_pass_at_scale(monkeypatch):
    # #1857: a birth-YEAR pass is fine at small N but becomes a ~15K-row-block
    # memory bomb at scale. When df is supplied, a single-field pass whose
    # projected max block exceeds the FS scorer row cap is dropped; the same
    # pass on the same profile is KEPT when the cap is high enough.
    monkeypatch.setenv(ON, "1")
    # 30 rows, birth-year column with ONE dominant year (26 rows) -> max block 26.
    df = pl.DataFrame({
        "dob": ["1980-01-01"] * 26 + ["1981-02-02", "1982-03-03", "1983-04-04", "1984-05-05"],
        "postcode": [f"{i:05d}" for i in range(30)],
    })
    profs = [_p("dob", "date", card=0.6, null=0.0), _p("postcode", "zip", card=0.9)]

    # Cap the FS block scorer at 100 elements -> row cap 10; the 26-row year
    # block exceeds it and the pass is dropped.
    monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "100")
    out = _diversify_probabilistic_blocking(_base_blocking(), profs, df)
    sigs = {(tuple(p.fields), tuple(p.transforms or [])) for p in (out.passes or [])}
    assert (("dob",), ("substring:0:4",)) not in sigs   # oversized -> dropped
    assert (("postcode",), ("strip",)) in sigs          # small -> kept

    # With a generous cap the same year pass is kept (small-N behavior preserved).
    monkeypatch.setenv("GOLDENMATCH_FS_VEC_MAX_ELEMS", "50000000")
    out2 = _diversify_probabilistic_blocking(_base_blocking(), profs, df)
    sigs2 = {(tuple(p.fields), tuple(p.transforms or [])) for p in (out2.passes or [])}
    assert (("dob",), ("substring:0:4",)) in sigs2


def test_lever3_no_df_is_backward_compatible(monkeypatch):
    # Older callers pass no df -> the scale guard is a no-op (pass still added).
    monkeypatch.setenv(ON, "1")
    out = _diversify_probabilistic_blocking(_base_blocking(), _person_profiles())
    sigs = {(tuple(p.fields), tuple(p.transforms or [])) for p in (out.passes or [])}
    assert (("dob",), ("substring:0:4",)) in sigs


# ── pair-budget gate: bound passes by candidate PAIRS, not just rows ──────────
#
# The 1M person auto-config emitted a `dob substring:0:4` (birth-YEAR) pass and
# `first_name`/`surname` soundex passes summing to ~12.9B candidate pairs — each
# under the max_safe_block ROW ceiling but quadratic in pairs — and OOM-killed
# gm_probabilistic. The row gate measures the wrong axis; this gate measures
# Σ C(block,2). Tests drive it at unit scale via GOLDENMATCH_FS_MAX_PASS_PAIRS.


def _one_block_frame():
    """6 rows sharing one `city` block (15 pairs) but distinct surnames whose
    initials all differ (a surname-initial reducer splits it to singletons), plus
    a distinct `rid` for a selective survivor pass."""
    return pl.DataFrame({
        "city": ["Xtown"] * 6,
        "surname": ["Ash", "Bell", "Cole", "Dean", "East", "Frost"],
        "rid": [f"r{i}" for i in range(6)],
    })


def _coarse_blocking(*fields):
    return BlockingConfig(
        strategy="multi_pass",
        passes=[BlockingKeyConfig(fields=[f], transforms=["strip"]) for f in fields],
    )


def test_pair_budget_is_flat_in_n_but_memory_aware(monkeypatch):
    # The budget is a memory-derived TOTAL across passes (not per-pass): it does
    # NOT scale with N (candidate pairs grow ~N² while the memory ceiling is a
    # property of the box), but it DOES scale with available RAM. RAM is injected
    # here for determinism.
    monkeypatch.delenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", raising=False)
    # Flat in N at fixed RAM.
    assert _fs_total_pair_budget(1_000_000, available_ram_gb=8.0) == _fs_total_pair_budget(
        10, available_ram_gb=8.0
    )
    # Small box: floored at 300M (8 GB * 40M = 320M > floor; 4 GB * 40M = 160M -> floor).
    assert _fs_total_pair_budget(50_000, available_ram_gb=4.0) == 300_000_000
    # Big box: memory-aware lift above the floor (64 GB -> 2.56B pairs), so the
    # recall-critical coarse passes survive at the 25M-on-64GB envelope.
    assert _fs_total_pair_budget(50_000, available_ram_gb=64.0) == 2_560_000_000
    assert _fs_total_pair_budget(50_000, available_ram_gb=64.0) > 1_000_000_000
    # The env override wins over everything and is read as an integer.
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    assert _fs_total_pair_budget(1_000_000, available_ram_gb=64.0) == 5


def test_pair_gate_bounds_coarse_pass_to_compound(monkeypatch):
    # A coarse `city` pass (15 pairs) over a tiny budget is rescued as a bounded
    # [city, surname-initial] compound (splits to singletons -> 0 pairs), NOT
    # dropped -- the recall lever is preserved. This is item 1: gate on PAIRS.
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    profs = [_p("city", "geo"), _p("surname", "name")]
    out = _bound_probabilistic_blocking_pairs(
        _coarse_blocking("city"), profs, _one_block_frame()
    )
    compound = next((p for p in (out.passes or []) if set(p.fields) == {"city", "surname"}), None)
    assert compound is not None
    assert compound.field_transforms == {"city": ["strip"], "surname": ["substring:0:1"]}


def test_projection_no_phantom_pairs_for_near_unique_key_at_scale():
    # THE 30M recall-collapse root cause: extrapolating a block's SIZE by the full
    # row ratio invents ~C(ratio, 2) phantom pairs per sample singleton. A
    # NEAR-UNIQUE key (all-distinct) should project to ~0 candidate pairs at any
    # scale (its blocks stay singletons, the COUNT grows), NOT quadratically.
    bframe = to_frame(pl.DataFrame({
        "uid": [f"u{i}" for i in range(10)],      # all distinct (near-unique)
        "const": ["x"] * 10,                      # one giant block (low cardinality)
    }))
    # sample_n=10, effective_n_full=10_000_000 -> ratio 1e6.
    near_unique = _project_pass_pairs(bframe, [("uid", ())], 10_000_000, 10)
    assert near_unique is not None
    # Near-unique: singletons stay singletons -> ~0 pairs (the OLD code projected
    # ~10 * C(1e6, 2) ~= 5e12 phantom pairs and dropped the pass).
    assert near_unique[1] == 0
    # Contrast: a saturated low-cardinality key DOES grow quadratically (one block
    # of 10 -> ~1e7 rows -> ~5e13 pairs), so real coarse passes are still gated.
    saturated = _project_pass_pairs(bframe, [("const", ())], 10_000_000, 10)
    assert saturated is not None
    assert saturated[1] > 10_000_000_000  # quadratic growth preserved


def test_pair_gate_prefers_identity_field_reducer(monkeypatch):
    # THE 30M recall-collapse fix: when an exact-agreement identity field (email)
    # is present, an over-budget coarse pass is compounded with EMAIL AT FULL VALUE
    # -- duplicates share email exactly, so the compound keeps every true pair
    # together (recall-safe) -- NOT with a corruption-prone name-initial, which
    # SPLITS true pairs on any typo'd name (measured 30M person: zip+first-initial
    # = recall 0.82; zip+email = recall 1.0). Email is tried FIRST.
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    df = pl.DataFrame({
        "city": ["Xtown"] * 6,
        "surname": ["Ash", "Bell", "Cole", "Dean", "East", "Frost"],
        "email": [f"u{i}@x.com" for i in range(6)],
    })
    profs = [_p("city", "geo"), _p("surname", "name"), _p("email", "email")]
    out = _bound_probabilistic_blocking_pairs(_coarse_blocking("city"), profs, df)
    compound = next((p for p in (out.passes or []) if "email" in p.fields), None)
    assert compound is not None, "coarse pass must be compounded with the identity field"
    assert set(compound.fields) == {"city", "email"}
    # Email at FULL value (empty transform), NOT the surname initial.
    assert compound.field_transforms == {"city": ["strip"], "email": []}


def test_pair_gate_drops_coarse_pass_when_no_reducer_helps(monkeypatch):
    # surname is constant -> the [city, surname] compound is STILL one 15-pair
    # block, so no reducer brings city under budget -> the coarse pass is DROPPED,
    # while the selective `rid` pass survives (recall falls back to it).
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    df = pl.DataFrame({"city": ["X"] * 6, "surname": ["Same"] * 6, "rid": [f"r{i}" for i in range(6)]})
    profs = [_p("city", "geo"), _p("surname", "name")]
    out = _bound_probabilistic_blocking_pairs(_coarse_blocking("city", "rid"), profs, df)
    fields = [p.fields for p in (out.passes or [])]
    assert ["city"] not in fields  # coarse megablock pass removed
    assert ["rid"] in fields       # selective pass preserved


def test_pair_gate_keeps_selective_passes(monkeypatch):
    # Every pass is selective (distinct keys -> 0 pairs) -> nothing changes even
    # under a tiny budget (the gate drops ONLY over-budget passes).
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    b = _coarse_blocking("rid")
    out = _bound_probabilistic_blocking_pairs(b, [_p("rid", "identifier", card=1.0)], _one_block_frame())
    assert [p.fields for p in (out.passes or [])] == [["rid"]]


def test_pair_gate_never_strips_to_empty(monkeypatch):
    # Both passes over budget with no discriminating reducer -> keep the single
    # most selective pass rather than return zero blocking.
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    df = pl.DataFrame({"city": ["X"] * 6, "town": ["Y"] * 6})
    out = _bound_probabilistic_blocking_pairs(_coarse_blocking("city", "town"), [], df)
    assert len(out.passes or []) == 1


def test_pair_gate_off_when_v2_disabled(monkeypatch):
    monkeypatch.setenv(ON, "0")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    b = _coarse_blocking("city")
    out = _bound_probabilistic_blocking_pairs(b, [_p("city", "geo")], _one_block_frame())
    assert out is b  # untouched


def test_pair_gate_noop_without_df(monkeypatch):
    monkeypatch.setenv(ON, "1")
    monkeypatch.setenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", "5")
    b = _coarse_blocking("city")
    out = _bound_probabilistic_blocking_pairs(b, [_p("city", "geo")], None)
    assert out is b  # can't measure -> skip, same object


def test_pair_gate_default_budget_is_noop_at_unit_scale(monkeypatch):
    # Without the override, the 150M flat total means a 15-pair block never trips
    # -> the gate is inert on small data (only large configs are bounded).
    monkeypatch.setenv(ON, "1")
    monkeypatch.delenv("GOLDENMATCH_FS_MAX_PASS_PAIRS", raising=False)
    b = _coarse_blocking("city")
    out = _bound_probabilistic_blocking_pairs(b, [_p("city", "geo"), _p("surname", "name")], _one_block_frame())
    assert [p.fields for p in (out.passes or [])] == [["city"]]


# ── end-to-end: v2 config trains + scores ─────────────────────────────────────

def test_v2_config_runs_end_to_end(monkeypatch):
    monkeypatch.setenv(ON, "1")
    from goldenmatch import dedupe_df

    df = pl.DataFrame({
        "first_name": ["John", "Jon", "Mary", "Marie", "Robert", "Bob",
                       "Linda", "Lynda", "James", "Jim", "Patricia", "Pat"],
        "surname": ["Smith", "Smith", "Jones", "Jones", "Brown", "Brown",
                    "Davis", "Davis", "Wilson", "Wilson", "Moore", "Moore"],
        "dob": ["1980-01-01", "1980-01-01", "1975-05-05", "1975-05-05",
                "1990-09-09", "1990-09-09", "1985-03-03", "1985-03-03",
                "1970-07-07", "1970-07-07", "1995-02-02", "1995-02-02"],
    })
    cfg = auto_configure_probabilistic_df(df)
    fields = [f.field for f in cfg.get_matchkeys()[0].fields]
    assert "dob" in fields  # lever #1 active under v2
    result = dedupe_df(df, config=cfg)
    assert result.dupes is not None and result.dupes.num_rows > 0


def test_auto_config_threads_n_rows_full_to_pair_bound(monkeypatch):
    """auto_configure_probabilistic_df MUST pass n_rows_full to the pair-budget
    bound so it extrapolates each pass's Sum C(block,2) to the FULL population and
    prunes oversized passes at scale. Without the thread the bound measures pairs
    at SAMPLE scale (a 66M-at-1.2M pass reads as ~1.8M at a 200K sample), never
    fires, and the zero-config FS wall is ~6x the pruned config (410s vs 71s at
    1.2M). Locks the wiring so it cannot be silently dropped."""
    seen: dict = {}
    orig = _bound_probabilistic_blocking_pairs

    def spy(blocking, profiles, df=None, *, n_rows_full=None):
        seen["n_rows_full"] = n_rows_full
        return orig(blocking, profiles, df, n_rows_full=n_rows_full)

    monkeypatch.setattr(
        "goldenmatch.core.autoconfig._bound_probabilistic_blocking_pairs", spy
    )
    df = pl.DataFrame({
        "first_name": ["ana", "bob", "cat", "dan"] * 25,
        "last_name": ["xu", "yi", "zed", "wu"] * 25,
        "email": [f"u{i}@e.com" for i in range(100)],
    })
    auto_configure_probabilistic_df(df, n_rows_full=5_000_000)
    assert seen.get("n_rows_full") == 5_000_000
