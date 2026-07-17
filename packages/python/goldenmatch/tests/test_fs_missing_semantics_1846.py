"""Issue #1846 -- FS missing-value semantics are toggleable and auto-config picks.

#1819/#1834 made a missing value UNOBSERVED (absence of evidence) rather than
disagreement. That is textbook Fellegi-Sunter and correct when data is missing
at random -- but wrong when missingness is INFORMATIVE. On historical_50k
(8.9-50% null across comparison fields) it let pairs agreeing on their few
populated fields look certain, and f1_probabilistic collapsed 0.83 -> 0.33.

Neither semantics is universally right, so the library stops imposing one:
``MatchkeyConfig.missing`` selects, ``GOLDENMATCH_FS_MISSING`` overrides, and
auto-config picks per-dataset from the profiled null rates.

Measured on the quality corpora (scripts/autoconfig_quality):
  historical_50k  f1_probabilistic  0.3335 -> 0.8284  (== the pre-#1834 value)
  febrl3 / ncvr_synthetic           unchanged (clean data -> picks "unobserved")
"""

from __future__ import annotations

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import comparison_vector, fs_missing_mode


def _mk(missing=None) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs", type="probabilistic",
        fields=[
            MatchkeyField(field="a", scorer="jaro_winkler", levels=3),
            MatchkeyField(field="b", scorer="jaro_winkler", levels=3),
        ],
        missing=missing,
    )


class TestModeResolution:
    def test_defaults_to_unobserved(self):
        """#1819/#1834's semantics stay the default -- existing configs unchanged."""
        assert fs_missing_mode(_mk()) == "unobserved"
        assert fs_missing_mode(None) == "unobserved"

    def test_config_selects(self):
        assert fs_missing_mode(_mk("disagree")) == "disagree"
        assert fs_missing_mode(_mk("unobserved")) == "unobserved"

    @pytest.mark.parametrize("val,want", [
        ("disagree", "disagree"), ("level0", "disagree"), ("0", "disagree"),
        ("unobserved", "unobserved"), ("skip", "unobserved"), ("1", "unobserved"),
        ("DISAGREE", "disagree"), ("  disagree  ", "disagree"),
    ])
    def test_env_override(self, monkeypatch, val, want):
        monkeypatch.setenv("GOLDENMATCH_FS_MISSING", val)
        assert fs_missing_mode(_mk("unobserved")) == want  # env beats config

    def test_unknown_env_falls_back_to_config(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "banana")
        assert fs_missing_mode(_mk("disagree")) == "disagree"


class TestComparisonVector:
    """The semantics difference, at the one place it originates."""

    A = {"a": "smith", "b": None}   # b missing on one side
    B = {"a": "smith", "b": "x"}

    def test_unobserved_emits_minus_one(self):
        vec = comparison_vector(self.A, self.B, _mk("unobserved"))
        assert vec[0] == 2, "a agrees exactly"
        assert vec[1] == -1, "b unobserved: carries no evidence (#1819)"

    def test_disagree_emits_level_zero(self):
        vec = comparison_vector(self.A, self.B, _mk("disagree"))
        assert vec[0] == 2
        assert vec[1] == 0, "b missing: evidence AGAINST a match (pre-#1834)"

    def test_both_present_is_identical_under_both(self):
        """The toggle must ONLY affect missing comparisons -- observed pairs are
        untouched, which is why clean corpora (febrl3/ncvr) don't move."""
        a, b = {"a": "smith", "b": "x"}, {"a": "smith", "b": "y"}
        assert comparison_vector(a, b, _mk("unobserved")) == \
               comparison_vector(a, b, _mk("disagree"))

    def test_env_reaches_the_vector(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_FS_MISSING", "disagree")
        assert comparison_vector(self.A, self.B, _mk("unobserved"))[1] == 0


class TestAutoConfigPicks:
    """auto-config chooses from the profiled null rates. The cut is calibrated
    against the corpora, not derived -- see _pick_missing_semantics."""

    @staticmethod
    def _profiles(rates: dict[str, float]):
        from goldenmatch.core.autoconfig import ColumnProfile
        return [
            ColumnProfile(
                name=n, dtype="str", col_type="name", confidence=0.9,
                null_rate=r, cardinality_ratio=0.9,
            )
            for n, r in rates.items()
        ]

    @staticmethod
    def _fields(names):
        return [MatchkeyField(field=n, scorer="jaro_winkler") for n in names]

    def test_null_heavy_picks_disagree(self):
        """historical_50k's shape: occupation 50% null, dob 22.5%."""
        from goldenmatch.core.autoconfig import _pick_missing_semantics
        got = _pick_missing_semantics(
            self._profiles({"first_name": 0.001, "dob": 0.225, "occupation": 0.50}),
            self._fields(["first_name", "dob", "occupation"]),
        )
        assert got == "disagree"

    def test_clean_data_keeps_unobserved(self):
        """febrl3 / ncvr_synthetic: near-complete -> textbook FS semantics."""
        from goldenmatch.core.autoconfig import _pick_missing_semantics
        got = _pick_missing_semantics(
            self._profiles({"first_name": 0.001, "surname": 0.01, "dob": 0.02}),
            self._fields(["first_name", "surname", "dob"]),
        )
        assert got == "unobserved"

    def test_worst_field_decides_not_the_mean(self):
        """One heavily-null comparison field is enough to make missingness
        informative; averaging would let clean fields mask it."""
        from goldenmatch.core.autoconfig import _pick_missing_semantics
        got = _pick_missing_semantics(
            self._profiles({"a": 0.0, "b": 0.0, "c": 0.0, "d": 0.45}),
            self._fields(["a", "b", "c", "d"]),
        )
        assert got == "disagree"

    def test_no_profile_match_is_safe(self):
        from goldenmatch.core.autoconfig import _pick_missing_semantics
        assert _pick_missing_semantics([], self._fields(["x"])) == "unobserved"
