"""The weighted scorer must renormalize by the OBSERVED field weight.

A null field is absence of evidence, not disagreement. The live weighted path
(native ``score.rs``, mirrored in ``score_buckets.py``) accumulated
``weight_sum`` over the observed fields, used it only for a ``> 0`` guard, then
divided by ``total_weight`` -- so a null field silently scored as though that
field DISAGREED.

The consequence is arithmetic, not statistical: with fields
``first_name 0.3 / surname 0.4 / dob 0.3`` and threshold 0.85, a pair with a
null ``dob`` caps at 0.70 and is UNMATCHABLE however perfectly the names agree.

Measured on the ER head-to-head person fixture (100K, 5% of duplicate rows carry
a null dob): recall 0.8751, of which 6.99% of blocked true pairs died here --
83% of those to a null dob. ``core/scorer.py::score_pair`` already did the right
thing (``weighted_sum / weight_sum``) but is not the live path; these tests pin
the live path to the same contract.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig, MatchkeyField
from goldenmatch.core.scorer import score_pair


def _mk(threshold: float = 0.85) -> MatchkeyConfig:
    return MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=threshold,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.3),
            MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.4),
            MatchkeyField(field="dob", scorer="jaro_winkler", weight=0.3),
        ],
    )


# A duplicate pair: names agree exactly, dob missing on one side.
# NOTE: cluster members are ROW INDICES, so record_id is chosen to coincide (0,1).
A = {"record_id": 0, "first_name": "john", "surname": "smith", "dob": "1980-01-01"}
B = {"record_id": 1, "first_name": "john", "surname": "smith", "dob": None}


class TestScorePairIsTheContract:
    """score_pair already renormalizes; it defines the behavior the live path
    must match."""

    def test_null_field_is_excluded_from_the_denominator(self):
        assert score_pair(A, B, _mk().fields) == pytest.approx(1.0), (
            "names agree exactly; the missing dob must not count against the pair"
        )

    def test_all_fields_observed_is_unaffected(self):
        b = dict(B, dob="1980-01-01")
        assert score_pair(A, b, _mk().fields) == pytest.approx(1.0)

    def test_disagreement_still_scores_down(self):
        """Renormalizing must not make DISAGREEMENT free -- only absence."""
        b = dict(B, dob="1999-12-31", surname="jones")
        assert score_pair(A, b, _mk().fields) < 0.85


class TestLiveWeightedPathMatchesScorePair:
    """The end-to-end contract: a duplicate whose only defect is a NULL field
    must still cluster. This is what the ER bench measured as lost recall."""

    @staticmethod
    def _cluster_pairs(df: pl.DataFrame, backend: str) -> set[tuple[int, int]]:
        import itertools

        import goldenmatch
        from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig

        # Block on surname so BOTH rows land in one block -- this test is about
        # SCORING, so blocking must not be the thing that separates them.
        cfg = GoldenMatchConfig(
            matchkeys=[_mk()],
            blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["surname"])]),
        )
        object.__setattr__(cfg, "backend", backend)
        res = goldenmatch.dedupe_df(df, config=cfg)
        clusters = res.clusters if hasattr(res, "clusters") else res
        out: set[tuple[int, int]] = set()
        for _cid, c in clusters.items():
            members = c["members"] if isinstance(c, dict) else c.members
            for pr in itertools.combinations(sorted(members), 2):
                out.add(pr)
        return out

    @pytest.mark.parametrize("backend", ["bucket", "polars-direct"])
    def test_null_field_duplicate_still_matches(self, backend):
        """The regression: dob null on one side -> max score 0.70 < 0.85 ->
        the pair could never match, regardless of the names."""
        df = pl.DataFrame([A, B])
        assert (0, 1) in self._cluster_pairs(df, backend), (
            f"{backend}: duplicate with a null dob was not matched -- the null is "
            f"being scored as disagreement"
        )

    @pytest.mark.parametrize("backend", ["bucket", "polars-direct"])
    def test_genuine_disagreement_still_does_not_match(self, backend):
        """Guard the other side: renormalizing must not turn a non-match into a
        match. Different people, one with a null dob."""
        # Same surname so blocking still co-locates them -- otherwise this test
        # would pass because they never met, not because scoring rejected them.
        a = {"record_id": 0, "first_name": "john", "surname": "smith", "dob": "1980-01-01"}
        b = {"record_id": 1, "first_name": "zachary", "surname": "smith", "dob": None}
        df = pl.DataFrame([a, b])
        assert (0, 1) not in self._cluster_pairs(df, backend), (
            f"{backend}: renormalization must not make DISAGREEMENT free"
        )
