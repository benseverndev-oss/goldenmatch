"""The bucket scorer must filter invalid block keys, like the blocker does.

``build_blocks`` derives ``__block_key__`` and then filters it
(``is_not_null`` + the ``nan``/``null``/``none`` stringified-missing sentinels,
keeping ``""`` -- the #390 semantics). ``score_buckets`` derived the key and
NEVER filtered, so every row with a NULL blocking key collapsed into one hash
bucket and was compared against every other such row.

Measured on the ER head-to-head person shape at 1M (9,846 rows carry a null
postcode; the largest LEGITIMATE postcode block is 25 rows):

    without the filter:  wall 31.81s  TP 223,575  FP 8,682  precision 0.9626
    with the filter:     wall 21.44s  TP 223,494  FP   111  precision 0.9995

The null block contributed +81 TP and +8,571 FP -- its output was 99% false. It
also cost ~20s of the ~40s dedupe: one 9,846-row block is 48.5M comparisons, and
with 256 buckets over 12 workers the whole scoring stage serialized behind that
single straggler (slowest kernel call 20.482s of 22.669s total).

A null blocking key means "this row cannot be blocked", not "this row belongs
with every other unblockable row".
"""

from __future__ import annotations

import itertools

import polars as pl
import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.blocker import build_blocks


def _cfg(backend: str) -> GoldenMatchConfig:
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="person",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(field="first_name", scorer="jaro_winkler", weight=0.5),
                    MatchkeyField(field="surname", scorer="jaro_winkler", weight=0.5),
                ],
            )
        ],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["postcode"])]),
    )
    object.__setattr__(cfg, "backend", backend)
    return cfg


def _pairs(df: pl.DataFrame, backend: str) -> set[tuple[int, int]]:
    import goldenmatch

    res = goldenmatch.dedupe_df(df, config=_cfg(backend))
    clusters = res.clusters if hasattr(res, "clusters") else res
    out: set[tuple[int, int]] = set()
    for _cid, c in clusters.items():
        members = c["members"] if isinstance(c, dict) else c.members
        for pr in itertools.combinations(sorted(members), 2):
            out.add(pr)
    return out


# Rows 0,1: identical names, BOTH postcodes NULL -> unblockable, must not pair.
# Rows 2,3: identical names AND a shared real postcode -> must pair (control).
NULLKEY_DF = pl.DataFrame(
    [
        {"record_id": 0, "first_name": "john", "surname": "smith", "postcode": None},
        {"record_id": 1, "first_name": "john", "surname": "smith", "postcode": None},
        {"record_id": 2, "first_name": "mary", "surname": "jones", "postcode": "AB1 2CD"},
        {"record_id": 3, "first_name": "mary", "surname": "jones", "postcode": "AB1 2CD"},
    ]
)


class TestBlockerIsTheContract:
    """build_blocks already filters invalid keys; that defines the behavior."""

    def test_blocker_drops_null_key_rows(self):
        blocks = build_blocks(
            NULLKEY_DF.with_row_index("__row_id__").lazy(),
            _cfg("bucket").blocking,
        )
        members = {
            tuple(sorted(b.materialize().native["__row_id__"].to_list())) for b in blocks
        }
        assert members == {(2, 3)}, (
            "the null-postcode rows must not form a block; only the real key does"
        )


class TestBucketScorerMatchesTheBlocker:
    @pytest.mark.parametrize("backend", ["bucket", "polars-direct"])
    def test_null_key_rows_are_not_compared(self, backend):
        """The regression: rows with a NULL block key collapsed into one bucket
        and were scored against each other. At 1M that was 9,846 rows -> 48.5M
        comparisons -> 8,571 false positives and ~20s."""
        pairs = _pairs(NULLKEY_DF, backend)
        assert (0, 1) not in pairs, (
            f"{backend}: rows with a NULL blocking key were compared to each other -- "
            f"a null key means 'cannot be blocked', not 'blocks with every other null'"
        )

    @pytest.mark.parametrize("backend", ["bucket", "polars-direct"])
    def test_real_key_rows_still_match(self, backend):
        """Guard the other side: filtering null keys must not drop real blocks."""
        assert (2, 3) in _pairs(NULLKEY_DF, backend)

    @pytest.mark.xfail(
        reason="KNOWN GAP (pre-existing): an empty-string key reaches the bucket "
               "scorer ALREADY NULL -- something upstream in prepare nulls \"\" -- so "
               "it cannot be told apart from a real null here and is dropped with "
               "them. Before the null filter these rows matched via the null "
               "mega-block, i.e. #390's intent was met by accident at the cost of "
               "8,571 FPs and ~20s. Preserving \"\"-vs-null through prepare is a "
               "separate fix; pinned here so it is not forgotten.",
        strict=True,
    )
    @pytest.mark.parametrize("backend", ["bucket", "polars-direct"])
    def test_empty_string_key_is_kept(self, backend):
        """#390: "" is a REAL value (an explicit empty cell), NOT missing."""
        df = pl.DataFrame(
            [
                {"record_id": 0, "first_name": "ann", "surname": "brown", "postcode": ""},
                {"record_id": 1, "first_name": "ann", "surname": "brown", "postcode": ""},
            ]
        )
        assert (0, 1) in _pairs(df, backend), (
            f"{backend}: an explicit empty-string key must still block (#390)"
        )
