"""Per-column FS domain-comparator decision (spec 2026-08-01-fs-lever-enablement,
Phase 2).

The global ``GOLDENMATCH_FS_DOMAIN_COMPARATORS`` flip is a net LOSS on the panel:
magnitude-aware ``date_diff`` HELPS clean date columns (febrl3 +0.0014) but HURTS
messy ones (historical_50k -0.0130), because a true match whose recorded dates
differ by months/years drops from a near-match (levenshtein) to a weak partial.
So ``date_diff`` is now a PER-COLUMN decision: emitted only when the date column
is reliable (high parse rate), keeping ``levenshtein`` otherwise.

These tests lock the decision at the three seams: the profiler populates the
``date_parse_rate`` reliability signal, the reliability predicate reads it, and
the FS builder honors it under the flag (and is byte-identical to ``levenshtein``
when the flag is off, whatever the parse rate).
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.autoconfig import (
    _DATE_DIFF_MIN_PARSE_RATE,
    ColumnProfile,
    _date_column_reliable_for_diff,
    build_probabilistic_matchkeys,
    profile_columns,
)


@pytest.fixture(autouse=True)
def _force_fs_v2(monkeypatch):
    """v2 gates whether date columns are admitted at all; a stray
    ``GOLDENMATCH_FS_AUTOCONFIG_V2=0`` in the environment would fail this module
    for reasons unrelated to the reliability decision under test. Pin it on so
    the module is hermetic (Copilot #2337)."""
    monkeypatch.setenv("GOLDENMATCH_FS_AUTOCONFIG_V2", "1")


def _date_field_scorer(profiles):
    """The scorer the FS builder assigns to the (single) date column, or None."""
    date_names = {p.name for p in profiles if p.col_type == "date"}
    for mk in build_probabilistic_matchkeys(profiles):
        for f in mk.fields:
            if f.field in date_names:
                return f.scorer
    return None


# ── Reliability predicate ────────────────────────────────────────────────────


class TestReliabilityPredicate:
    def _date_profile(self, rate):
        return ColumnProfile(
            name="dob", dtype="str", col_type="date", confidence=0.9,
            cardinality_ratio=0.5, date_parse_rate=rate,
        )

    def test_none_is_reliable(self):
        # No measured signal (hand-built profile) -> preserve flag-on behavior.
        assert _date_column_reliable_for_diff(self._date_profile(None)) is True

    def test_at_and_above_threshold_is_reliable(self):
        assert _date_column_reliable_for_diff(self._date_profile(_DATE_DIFF_MIN_PARSE_RATE)) is True
        assert _date_column_reliable_for_diff(self._date_profile(1.0)) is True

    def test_below_threshold_is_unreliable(self):
        assert _date_column_reliable_for_diff(self._date_profile(_DATE_DIFF_MIN_PARSE_RATE - 0.001)) is False
        assert _date_column_reliable_for_diff(self._date_profile(0.5)) is False

    def test_threshold_sits_in_the_panel_separating_band(self):
        # Calibrated against the corpora: historical_50k (hurts) ~0.92 must be
        # BELOW and febrl3 (helps) ~0.99 ABOVE the cut.
        assert 0.926 < _DATE_DIFF_MIN_PARSE_RATE < 0.989


# ── Profiler populates the signal ────────────────────────────────────────────


class TestProfilerPopulatesRate:
    def test_clean_date_column_high_rate(self):
        df = pl.DataFrame({
            "id": [str(i) for i in range(20)],
            "dob": [f"19{50 + i:02d}-06-15" for i in range(20)],  # all ISO
        })
        profiles = profile_columns(df)
        dob = next(p for p in profiles if p.name == "dob")
        assert dob.col_type == "date"
        assert dob.date_parse_rate is not None
        assert dob.date_parse_rate == pytest.approx(1.0)

    def test_messy_date_column_lower_rate(self):
        # Half the values are unparseable junk -> parse rate ~0.5, below the cut.
        good = [f"1985-0{i % 9 + 1}-1{i % 8 + 1}" for i in range(10)]
        junk = ["not-a-date", "??", "N/A", "0", "unknown", "1985", "19", "x", "  ", "-"]
        df = pl.DataFrame({"id": [str(i) for i in range(20)], "dob": good + junk})
        profiles = profile_columns(df)
        dob = next(p for p in profiles if p.name == "dob")
        # "1985" (bare year) parses; the other 9 junk values do not.
        assert dob.date_parse_rate is not None
        assert dob.date_parse_rate < _DATE_DIFF_MIN_PARSE_RATE

    def test_non_date_column_rate_is_none(self):
        df = pl.DataFrame({
            "id": [str(i) for i in range(10)],
            "name": [f"Person {i}" for i in range(10)],
        })
        for p in profile_columns(df):
            if p.col_type != "date":
                assert p.date_parse_rate is None

    def test_blank_strings_lower_the_rate(self):
        # Non-null blank/whitespace values are unparseable and MUST count against
        # the rate (Copilot #2337); filtering them would inflate a messy column
        # to look reliable. 10 ISO dates + 10 blanks -> ~0.5, well below the cut.
        dates = ["1985-06-15", "1990-01-02", "1972-11-30", "1968-04-04", "1991-09-09"] * 2
        blanks = ["", "   ", "\t", "", " ", "", "  ", "", " ", ""]
        df = pl.DataFrame({"id": [str(i) for i in range(20)], "dob": dates + blanks})
        dob = next(p for p in profile_columns(df) if p.name == "dob")
        assert dob.date_parse_rate == pytest.approx(0.5)
        assert dob.date_parse_rate < _DATE_DIFF_MIN_PARSE_RATE
        assert _date_column_reliable_for_diff(dob) is False


# ── Builder honors the decision under the flag ───────────────────────────────


class TestBuilderHonorsDecision:
    def _profiles(self, parse_rate):
        # A minimal person-shape FS profile set (names give the builder an atomic
        # pair so v2 fires) plus one date column with the given parse rate.
        return [
            ColumnProfile(name="first_name", dtype="str", col_type="name",
                          confidence=0.9, cardinality_ratio=0.6),
            ColumnProfile(name="last_name", dtype="str", col_type="name",
                          confidence=0.9, cardinality_ratio=0.6),
            ColumnProfile(name="dob", dtype="str", col_type="date",
                          confidence=0.9, cardinality_ratio=0.5,
                          date_parse_rate=parse_rate),
        ]

    def test_flag_on_reliable_uses_date_diff(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        assert _date_field_scorer(self._profiles(0.99)) == "date_diff"

    def test_flag_on_unreliable_keeps_levenshtein(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        # The historical_50k regime: messy dates keep the forgiving edit scorer.
        assert _date_field_scorer(self._profiles(0.90)) == "levenshtein"

    def test_flag_off_is_levenshtein_regardless_of_rate(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", raising=False)
        assert _date_field_scorer(self._profiles(0.99)) == "levenshtein"
        assert _date_field_scorer(self._profiles(0.50)) == "levenshtein"

    def test_flag_on_none_rate_uses_date_diff(self, monkeypatch):
        # No signal -> reliable-by-default so a caller that didn't populate the
        # rate keeps the flag's date_diff intent.
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        assert _date_field_scorer(self._profiles(None)) == "date_diff"


# ── End-to-end through profile_columns + builder ─────────────────────────────


class TestEndToEnd:
    def test_messy_dates_stay_levenshtein_under_flag(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        # Repeated values so cardinality_ratio < 1.0 (a card-1.0 date column is a
        # per-record surrogate the builder skips before the scorer decision).
        # ~half parseable -> parse rate below the cut.
        good = ["1985-03-11", "1990-07-22", "1985-03-11", "1990-07-22"]
        junk = ["junk", "??", "N/A", "unknown", "junk", "??", "N/A", "unknown"]
        dob = good + junk
        df = pl.DataFrame({
            "first_name": [f"Name{i % 4}" for i in range(len(dob))],
            "last_name": [f"Sur{i % 4}" for i in range(len(dob))],
            "dob": dob,
        })
        profiles = profile_columns(df)
        dob_p = next(p for p in profiles if p.name == "dob")
        assert dob_p.date_parse_rate is not None and dob_p.date_parse_rate < _DATE_DIFF_MIN_PARSE_RATE
        assert _date_field_scorer(profiles) == "levenshtein"

    def test_clean_dates_use_date_diff_under_flag(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        # A handful of distinct ISO dates, each repeated -> card < 1.0, parse 1.0.
        dates = ["1960-03-11", "1971-06-04", "1982-09-19", "1993-12-25"]
        dob = [dates[i % len(dates)] for i in range(16)]
        df = pl.DataFrame({
            "first_name": [f"Name{i % 4}" for i in range(len(dob))],
            "last_name": [f"Sur{i % 4}" for i in range(len(dob))],
            "dob": dob,
        })
        profiles = profile_columns(df)
        assert next(p for p in profiles if p.name == "dob").date_parse_rate == pytest.approx(1.0)
        assert _date_field_scorer(profiles) == "date_diff"
