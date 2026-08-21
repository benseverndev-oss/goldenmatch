"""Per-column reliability gate for the numeric_diff / geo_haversine domain
comparators (spec 2026-08-01-fs-lever-enablement, Phase 2 — the second half of
the per-column domain-comparator decision, after `test_fs_date_diff_reliability`).

Unlike `date_diff` (a MEASURED −0.0130 panel regression on historical_50k), this
half is a DEFENSIVE consistency gate: the panel carries no lat/long column and
only one clean numeric column, so there is no panel regression to calibrate
against. Both scorers already exact-string fall back per-value on unparseable
input, so the gate's job is narrow — don't admit a column as a magnitude
comparator when it mostly doesn't parse as that domain. These tests lock exactly
that: the profiler populates the parse-rate signals, the predicates read them,
and the FS builder admits the comparator only for a column that parses cleanly
(falling through to the conservative v2 default otherwise). Flag-off stays
byte-identical whatever the rate.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core.autoconfig import (
    _NUMERIC_GEO_MIN_PARSE_RATE,
    ColumnProfile,
    _coord_column_reliable_for_haversine,
    _looks_like_latlong,
    _numeric_column_reliable_for_diff,
    build_probabilistic_matchkeys,
    profile_columns,
)


@pytest.fixture(autouse=True)
def _force_fs_v2(monkeypatch):
    """v2 gates whether these columns are admitted at all; pin it on so a stray
    GOLDENMATCH_FS_AUTOCONFIG_V2=0 can't fail the module for unrelated reasons."""
    monkeypatch.setenv("GOLDENMATCH_FS_AUTOCONFIG_V2", "1")


def _scorer_for(profiles, field):
    for mk in build_probabilistic_matchkeys(profiles):
        for f in mk.fields:
            if f.field == field:
                return f.scorer
    return None


# ── Reliability predicates ───────────────────────────────────────────────────


class TestPredicates:
    def _num(self, rate):
        return ColumnProfile(name="amt", dtype="str", col_type="numeric",
                             confidence=0.9, cardinality_ratio=0.5, numeric_parse_rate=rate)

    def _coord(self, rate):
        return ColumnProfile(name="loc", dtype="str", col_type="geo",
                             confidence=0.9, cardinality_ratio=0.5, coord_parse_rate=rate)

    def test_numeric_none_is_reliable(self):
        assert _numeric_column_reliable_for_diff(self._num(None)) is True

    def test_numeric_threshold(self):
        assert _numeric_column_reliable_for_diff(self._num(_NUMERIC_GEO_MIN_PARSE_RATE)) is True
        assert _numeric_column_reliable_for_diff(self._num(0.5)) is False

    def test_coord_none_is_reliable(self):
        assert _coord_column_reliable_for_haversine(self._coord(None)) is True

    def test_coord_threshold(self):
        assert _coord_column_reliable_for_haversine(self._coord(1.0)) is True
        assert _coord_column_reliable_for_haversine(self._coord(0.4)) is False


# ── Profiler populates the signals ───────────────────────────────────────────


class TestProfilerPopulates:
    def test_numeric_rate_and_others_none(self):
        df = pl.DataFrame({
            "id": [str(i) for i in range(20)],
            "amount": [str(100 + i % 5) for i in range(20)],  # numeric, repeats
        })
        profs = profile_columns(df)
        amt = next(p for p in profs if p.name == "amount")
        assert amt.col_type == "numeric"
        assert amt.numeric_parse_rate == pytest.approx(1.0)
        assert amt.coord_parse_rate is None

    def test_messy_numeric_lower_rate(self):
        good = [str(10 + i % 4) for i in range(6)]
        junk = ["n/a", "?", "", "tbd", "x", "--"]
        df = pl.DataFrame({"id": [str(i) for i in range(12)], "amount": good + junk})
        amt = next(p for p in profile_columns(df) if p.name == "amount")
        assert amt.numeric_parse_rate is not None
        assert amt.numeric_parse_rate < _NUMERIC_GEO_MIN_PARSE_RATE

    def test_coord_rate_populated(self):
        coords = ["40.71,-74.01", "34.05,-118.24", "41.88,-87.63", "29.76,-95.37"]
        df = pl.DataFrame({
            "id": [str(i) for i in range(16)],
            "loc": [coords[i % len(coords)] for i in range(16)],
        })
        loc = next((p for p in profile_columns(df) if p.coord_parse_rate is not None), None)
        assert loc is not None, "expected a coordinate-shaped column to carry coord_parse_rate"
        assert loc.coord_parse_rate == pytest.approx(1.0)


# ── Builder honors the gate ──────────────────────────────────────────────────


class TestBuilderGate:
    def _profiles(self, col, rate_field, rate, col_type):
        p = ColumnProfile(name=col, dtype="str", col_type=col_type,
                          confidence=0.9, cardinality_ratio=0.5)
        setattr(p, rate_field, rate)
        return [
            ColumnProfile(name="first_name", dtype="str", col_type="name",
                          confidence=0.9, cardinality_ratio=0.6),
            ColumnProfile(name="last_name", dtype="str", col_type="name",
                          confidence=0.9, cardinality_ratio=0.6),
            p,
        ]

    def test_clean_numeric_admitted(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        profs = self._profiles("amt", "numeric_parse_rate", 1.0, "numeric")
        assert _scorer_for(profs, "amt") == "numeric_diff:pct:0.1"

    def test_messy_numeric_withheld(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        profs = self._profiles("amt", "numeric_parse_rate", 0.4, "numeric")
        # Not admitted as numeric_diff -> falls through to the conservative default
        # (a bare numeric column is not otherwise an FS comparison field).
        assert _scorer_for(profs, "amt") is None

    def test_flag_off_never_admits_numeric(self, monkeypatch):
        monkeypatch.delenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", raising=False)
        for rate in (1.0, 0.4):
            profs = self._profiles("amt", "numeric_parse_rate", rate, "numeric")
            assert _scorer_for(profs, "amt") is None


# ── End-to-end through profile_columns + builder ─────────────────────────────


class TestEndToEnd:
    def test_messy_numeric_withheld_e2e(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        good = [str(10 + i % 3) for i in range(6)]
        junk = ["n/a", "?", "tbd", "x", "--", "?"]
        df = pl.DataFrame({
            "first_name": [f"N{i % 4}" for i in range(12)],
            "last_name": [f"S{i % 4}" for i in range(12)],
            "amount": good + junk,
        })
        profs = profile_columns(df)
        amt = next(p for p in profs if p.name == "amount")
        assert amt.numeric_parse_rate < _NUMERIC_GEO_MIN_PARSE_RATE
        assert _scorer_for(profs, "amount") is None

    def test_clean_numeric_admitted_e2e(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_DOMAIN_COMPARATORS", "1")
        df = pl.DataFrame({
            "first_name": [f"N{i % 4}" for i in range(16)],
            "last_name": [f"S{i % 4}" for i in range(16)],
            "amount": [str(100 + i % 5) for i in range(16)],  # clean, repeats -> card<1
        })
        profs = profile_columns(df)
        assert _scorer_for(profs, "amount") == "numeric_diff:pct:0.1"


# ── #2443: "two small comma-separated numbers" is not a coordinate column ────


class TestLatlongGeographicEvidence:
    """`_looks_like_latlong` used to test parseability alone, which admits any
    column of two small comma-separated numbers. On a real ~21k-row corpus that
    put `geo_haversine` on an `age_range` column holding packed `"lo,hi"`
    intervals: `"25,35"` parses as lat 25 / lon 35. The config was well-formed
    and the distances plausible, so nothing failed -- it took a field-by-field
    diff against a hand-built matchkey to find. These lock the positive-evidence
    requirement that closes it, in both directions."""

    def _prof(self, values, name="c"):
        return ColumnProfile(name=name, dtype="str", col_type="string",
                             confidence=0.9, cardinality_ratio=0.5,
                             sample_values=values)

    def test_the_age_interval_column_from_2443_is_rejected(self):
        ages = ["25,35", "30,40", "18,29", "45,55", "22,33", "60,70"]
        # Every value still parses -- the old floor passed at ~1.0.
        from goldenmatch.core.scorer import _parse_latlong
        assert all(_parse_latlong(v) is not None for v in ages)
        assert _looks_like_latlong(self._prof(ages, name="age_range")) is False

    @pytest.mark.parametrize("values", [
        pytest.param(["30,50", "40,60", "55,75", "20,35", "65,85"], id="salary_bands"),
        pytest.param(["1,5", "2,6", "3,7", "4,8", "2,9"], id="score_spans"),
        pytest.param(["10,20", "20,30", "30,40", "40,50", "50,60"], id="size_buckets"),
    ])
    def test_other_packed_interval_shapes_are_rejected(self, values):
        assert _looks_like_latlong(self._prof(values)) is False

    @pytest.mark.parametrize("values", [
        pytest.param(["38.9072,-77.0369", "40.7128,-74.0060", "51.5074,-0.1278",
                      "48.8566,2.3522", "35.6762,139.6503"], id="decimal_degrees"),
        pytest.param(["38,-77", "40,-74", "41,-87", "34,-118", "29,-95"],
                     id="negative_longitude"),
        pytest.param(["35,139", "1,103", "22,114", "13,100", "31,121"],
                     id="longitude_beyond_90"),
        pytest.param(["-33,18", "-34,151", "-23,-46", "-37,144", "-26,28"],
                     id="southern_hemisphere"),
    ])
    def test_real_coordinate_columns_are_still_admitted(self, values):
        assert _looks_like_latlong(self._prof(values)) is True

    def test_whole_degree_positive_quadrant_is_declined_deliberately(self):
        """Integer degrees in the NE quadrant are indistinguishable from a packed
        interval by the data alone. Declining them is the intended trade: whole
        degrees are ~111 km apart, so haversine similarity there is meaningless
        regardless of whether the column really is geographic."""
        assert _looks_like_latlong(self._prof(["45,10", "50,20", "48,15", "52,13", "47,8"])) is False

    def test_a_single_fractional_value_is_enough(self):
        """The evidence test is ANY, not ALL -- a column of mostly-integer
        coordinates with one fractional value is still a coordinate column."""
        assert _looks_like_latlong(
            self._prof(["45,10", "50,20", "48,15", "52,13", "47.5,8"])
        ) is True
