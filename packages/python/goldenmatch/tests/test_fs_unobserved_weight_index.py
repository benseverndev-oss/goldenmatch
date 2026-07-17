"""An unobserved FS field must contribute NO weight, not weights[-1] (#1859).

``comparison_vector`` returns ``-1`` for a field unobserved on either side (the
``missing="unobserved"`` sentinel). The Splink-upgrade calibration paths summed
``em.match_weights[name][vec[k]]`` unguarded -- and ``weights[-1]`` is the LAST
element, i.e. the HIGHEST-agreement weight. So a MISSING field contributed the
maximal positive match evidence, feeding the fan-out NE posteriors
(``splink_upgrade_fanout``) and the learned link/review thresholds
(``splink_upgrade``). ``fs_regular_weight_sum`` guards ``vec[k] >= 0``, matching
every runtime FS scorer (which all ``continue`` on ``vec[k] < 0``).
"""

from __future__ import annotations

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import comparison_vector, fs_regular_weight_sum

# 3-level weights: [disagree, partial, agree]. weights[-1] == the agree weight.
WEIGHTS = {"name": [-3.0, 0.0, 5.0], "dob": [-4.0, 0.0, 6.0]}
INDEXED = [(0, "name"), (1, "dob")]


class TestFsRegularWeightSum:
    def test_unobserved_field_contributes_zero(self):
        vec = [2, -1]  # name agrees; dob unobserved
        got = fs_regular_weight_sum(WEIGHTS, vec, INDEXED)
        assert got == 5.0, (
            "the unobserved dob must add 0, not weights['dob'][-1]=+6 -- a missing "
            "field is absence of evidence, not maximal agreement"
        )

    def test_matches_the_buggy_sum_when_all_observed(self):
        """When nothing is unobserved the guard is a no-op -- no behavior change
        for the common case."""
        vec = [2, 0]  # name agrees, dob disagrees
        assert fs_regular_weight_sum(WEIGHTS, vec, INDEXED) == 5.0 + (-4.0)

    def test_all_unobserved_is_zero(self):
        assert fs_regular_weight_sum(WEIGHTS, [-1, -1], INDEXED) == 0.0

    def test_partial_and_agree_levels(self):
        assert fs_regular_weight_sum(WEIGHTS, [1, 2], INDEXED) == 0.0 + 6.0


class TestComparisonVectorEmitsUnobserved:
    def test_missing_field_yields_minus_one(self):
        """The upstream contract the guard depends on: a null field -> -1."""
        mk = MatchkeyConfig(
            name="m", type="probabilistic", threshold=0.9,
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=3),
                MatchkeyField(field="dob", scorer="jaro_winkler", levels=3),
            ],
        )
        vec = comparison_vector(
            {"name": "john smith", "dob": "1980-01-01"},
            {"name": "john smith", "dob": None},
            mk,
        )
        assert vec[0] == 2  # name agrees exactly
        assert vec[1] == -1  # dob unobserved

    def test_end_to_end_missing_adds_no_spurious_evidence(self):
        """The full bug: comparison_vector -> fs_regular_weight_sum. A missing dob
        must not inflate the pair's total FS weight."""
        mk = MatchkeyConfig(
            name="m", type="probabilistic", threshold=0.9,
            fields=[
                MatchkeyField(field="name", scorer="jaro_winkler", levels=3),
                MatchkeyField(field="dob", scorer="jaro_winkler", levels=3),
            ],
        )
        vec_missing = comparison_vector(
            {"name": "a b", "dob": "1980-01-01"}, {"name": "a b", "dob": None}, mk
        )
        assert vec_missing == [2, -1]  # name agrees exactly, dob unobserved
        w_missing = fs_regular_weight_sum(WEIGHTS, vec_missing, INDEXED)
        # The missing dob contributes 0: the pair scores the name weight alone.
        # The bug added weights["dob"][-1]=+6 (the agree weight) on top.
        assert w_missing == 5.0
        assert w_missing < 5.0 + WEIGHTS["dob"][-1]  # never reaches the buggy +6
