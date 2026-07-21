"""Arrow-lane parity + polars-free guarantee for the bucket fuzzy fallback.

``score_buckets._score_block_frame`` used to convert a ``pa.Table`` block to
polars (``pl.from_arrow``) before handing it to ``find_fuzzy_matches`` -- and the
``isinstance(block_df, pl.DataFrame)`` probe itself forced the polars import.
That broke the arrow lane's polars-free guarantee: goldengraph's zero-config
resolve installs goldenmatch WITHOUT polars, so any weighted-fuzzy config (the
common tiny-N zero-config shape) crashed with ``ModuleNotFoundError: polars``
deep in the controller iteration.

``find_fuzzy_matches`` already accepts BOTH reps (via ``core.frame.to_frame`` +
a ``to_pylist`` NE dual-rep), so the block is now passed through as-is. These
tests lock (1) that the arrow input yields the SAME pairs as the polars-converted
input the old code produced -- including negative-evidence penalties -- and
(2) that scoring an arrow block never imports polars.
"""

from __future__ import annotations

import pytest

pa = pytest.importorskip("pyarrow")

from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField, NegativeEvidenceField
from goldenmatch.core.scorer import find_fuzzy_matches


def _block() -> pa.Table:
    # Near-duplicate names + a discriminating city so scores span the range and
    # the NE penalty actually fires on some pairs.
    return pa.table(
        {
            "__row_id__": [0, 1, 2, 3],
            "name": ["Acme Inc", "Acme Inc", "Acme Incorporated", "Beta LLC"],
            "city": ["Boston", "Boston", "Austin", "Boston"],
        }
    )


def _weighted_mk(*, with_ne: bool) -> MatchkeyConfig:
    ne = (
        [NegativeEvidenceField(field="city", scorer="exact", threshold=1.0, penalty=0.4)]
        if with_ne
        else None
    )
    return MatchkeyConfig(
        name="name_mk",
        type="weighted",
        threshold=0.6,
        fields=[MatchkeyField(field="name", scorer="token_sort", weight=1.0)],
        negative_evidence=ne,
    )


@pytest.mark.parametrize("with_ne", [False, True])
def test_arrow_block_matches_polars_conversion(with_ne):
    """find_fuzzy_matches on a pa.Table == on the pl.from_arrow of the same
    block (the exact substitution _score_block_frame now makes)."""
    pl = pytest.importorskip("polars")
    blk = _block()
    mk = _weighted_mk(with_ne=with_ne)

    pairs_arrow = find_fuzzy_matches(blk, mk, exclude_pairs=frozenset())
    pairs_polars = find_fuzzy_matches(pl.from_arrow(blk), mk, exclude_pairs=frozenset())

    assert pairs_arrow == pairs_polars
    # Sanity: the block is built so real pairs clear the threshold.
    assert pairs_arrow, "expected at least one fuzzy pair"


def test_arrow_block_scoring_is_polars_free(monkeypatch):
    """Scoring an arrow block must not import polars. Simulate the polars-free
    lane by making ``import polars`` fail even if it is installed."""
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, "polars", raising=False)
    real_import = builtins.__import__

    def _no_polars(name, *args, **kwargs):
        if name == "polars" or name.startswith("polars."):
            raise ModuleNotFoundError("No module named 'polars'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_polars)

    pairs = find_fuzzy_matches(_block(), _weighted_mk(with_ne=True), exclude_pairs=frozenset())

    assert "polars" not in sys.modules
    # (0,1) are identical "Acme Inc"/"Boston" -> highest-scoring pair survives NE.
    assert (0, 1) in {(a, b) for a, b, _ in pairs}
