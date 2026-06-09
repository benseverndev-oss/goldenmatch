"""Tests for ensemble scorer."""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


class TestEnsembleScorer:
    def test_ensemble_score_matrix(self):
        from goldenmatch.core.scorer import _fuzzy_score_matrix

        values = ["John Smith", "Smith John", "Jane Doe"]
        matrix = _fuzzy_score_matrix(values, "ensemble")

        assert matrix.shape == (3, 3)
        # "John Smith" vs "Smith John" — token_sort should catch this
        assert matrix[0, 1] > 0.8
        # Diagonal should be 1.0
        assert matrix[0, 0] == pytest.approx(1.0, abs=0.01)

    def test_ensemble_beats_single_scorer(self):
        from goldenmatch.core.scorer import _fuzzy_score_matrix

        values = ["John Smith", "Smith, John"]

        ensemble = _fuzzy_score_matrix(values, "ensemble")
        jw = _fuzzy_score_matrix(values, "jaro_winkler")

        # Ensemble should be >= jaro_winkler for reordered names
        assert ensemble[0, 1] >= jw[0, 1]

    def test_ensemble_null_handling(self):
        from goldenmatch.core.scorer import _fuzzy_score_matrix

        values = ["John", None, "Jane"]
        matrix = _fuzzy_score_matrix(values, "ensemble")
        assert matrix.shape == (3, 3)

    def test_ensemble_in_find_fuzzy(self):
        from goldenmatch.core.scorer import find_fuzzy_matches

        df = pl.DataFrame({
            "__row_id__": [0, 1, 2],
            "name": ["John Smith", "Smith John", "Jane Doe"],
        })
        mk = MatchkeyConfig(
            name="ens",
            type="weighted",
            threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="ensemble", weight=1.0)],
        )
        results = find_fuzzy_matches(df, mk)
        pair_ids = {(r[0], r[1]) for r in results}
        # Reordered name should match
        assert (0, 1) in pair_ids

    def test_ensemble_schema_valid(self):
        f = MatchkeyField(field="name", scorer="ensemble", weight=1.0)
        assert f.scorer == "ensemble"


class TestEnsembleScalarParity:
    """The scalar score_field('ensemble') must match the NxN matrix branch.

    Regression for the Fellegi-Sunter training crash: EM training routes
    through the SCALAR score_field (comparison_vector -> _build_comparison_matrix),
    while scoring uses the vectorized _fuzzy_score_matrix. score_field lacked an
    `ensemble` case, so any probabilistic matchkey whose auto-config assigned
    `ensemble` (every `name` field) raised `Unknown scorer: 'ensemble'` at train
    time and the FS path could not run at all.
    """

    @pytest.mark.parametrize(
        "a,b",
        [
            ("John Smith", "Smith John"),   # token_sort wins (word reorder)
            ("Jon", "John"),                # jaro_winkler / soundex
            ("Catherine", "Kathryn"),       # soundex bridges spelling
            ("Smith", "Smith"),             # identical -> 1.0
            ("Smith", "Jones"),             # unrelated -> low
        ],
    )
    def test_scalar_matches_matrix_offdiagonal(self, a, b):
        from goldenmatch.core.scorer import _fuzzy_score_matrix, score_field

        scalar = score_field(a, b, "ensemble")
        matrix = _fuzzy_score_matrix([a, b], "ensemble")
        assert scalar == pytest.approx(float(matrix[0, 1]), abs=1e-6)

    def test_scalar_none_returns_none(self):
        from goldenmatch.core.scorer import score_field

        assert score_field(None, "Smith", "ensemble") is None
        assert score_field("Smith", None, "ensemble") is None

    def test_scalar_non_alpha_does_not_raise(self):
        # jellyfish.soundex can choke on non-alpha input; the soundex component
        # must degrade to 0.0 rather than blow up the whole pair score.
        from goldenmatch.core.scorer import score_field

        s = score_field("12345", "12345", "ensemble")
        assert s == pytest.approx(1.0, abs=1e-6)  # token_sort/jw still see equality


class TestFSEnsembleEndToEnd:
    """Probabilistic (Fellegi-Sunter) dedupe must train+score when a field's
    scorer is `ensemble` (the auto-config default for `name` columns)."""

    def test_probabilistic_dedupe_with_ensemble_name_field(self):
        from goldenmatch import dedupe_df
        from goldenmatch.core.autoconfig import auto_configure_probabilistic_df

        df = pl.DataFrame(
            {
                "name": [
                    "John Smith", "Jon Smith", "Mary Jones", "Marie Jones",
                    "Robert Brown", "Bob Brown", "Linda Davis", "Lynda Davis",
                    "James Wilson", "Jim Wilson", "Patricia Moore", "Pat Moore",
                ],
                "city": [
                    "Boston", "Boston", "Denver", "Denver",
                    "Austin", "Austin", "Seattle", "Seattle",
                    "Portland", "Portland", "Chicago", "Chicago",
                ],
            }
        )
        cfg = auto_configure_probabilistic_df(df)
        # Guard the regression's premise: at least one field is ensemble-scored.
        scorers = {
            f.scorer
            for mk in cfg.get_matchkeys()
            if mk.type == "probabilistic"
            for f in (mk.fields or [])
        }
        assert "ensemble" in scorers, f"expected an ensemble field, got {scorers}"

        # Must not raise (was: ValueError: Unknown scorer: 'ensemble' at EM train)
        result = dedupe_df(df, config=cfg)
        assert result.dupes is not None and result.dupes.height > 0
