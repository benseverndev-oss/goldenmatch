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
    _diversify_probabilistic_blocking,
    build_probabilistic_matchkeys,
)

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


# ── default ON; explicit =0 restores byte-identical legacy field set ──────────

def test_default_unset_is_v2(monkeypatch):
    monkeypatch.delenv(ON, raising=False)  # default flipped ON 2026-06-09
    fields = _fields(build_probabilistic_matchkeys(_person_profiles()))
    # v2 curates: dob admitted, name composites + low-card gender dropped.
    assert "dob" in fields
    assert "full_name" not in fields and "first_and_surname" not in fields
    assert "gender" not in fields


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


# ── end-to-end: v2 config trains + scores ─────────────────────────────────────

def test_v2_config_runs_end_to_end(monkeypatch):
    monkeypatch.setenv(ON, "1")
    from goldenmatch import dedupe_df
    from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

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
    assert result.dupes is not None and result.dupes.height > 0
