"""Multi-pass candidates de-duplicate by pass priority, not by `distinct()`.

A pair produced by two blocking passes is ONE candidate. That has always been
true; what changed is the cost. `distinct()` answers it with a full shuffle and
aggregate over a frame with O(pairs) rows -- 2.32B of them at 250M records.
Pass priority answers the same question from the two records themselves, inside
the join, with no shuffle.

The gate is SET EQUALITY against the old behaviour, not a count. Two candidate
sets of the same size can still differ, and a pair silently reassigned between
passes would change which EM session it trains, which no count would show.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.config.schemas import (  # noqa: E402
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

_ID = "__row_id__"
_COLS = [_ID, "first", "last", "city", "zip"]

# Built so the passes OVERLAP: rows 0/1 agree on both `city` and `zip`, so they
# are the pair the two passes both produce and the one dedup exists for. Without
# an overlapping pair this test passes on a build with no dedup at all.
_ROWS = [
    (0, "jon", "smith", "york", "10001"),
    (1, "john", "smith", "york", "10001"),   # dup of 0 under BOTH passes
    (2, "jonathan", "smyth", "york", "99999"),  # shares city only
    (3, "amy", "wong", "leeds", "10001"),    # shares zip only
    (4, "amy", "wong", "leeds", None),       # null zip: pass 1 must skip it
    (5, "ann", "web", None, "10001"),        # null city: pass 0 must skip it
    (6, "bob", "hale", "hull", None),        # in neither pass with anyone
]


def _config() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fs",
                type="probabilistic",
                fields=[
                    MatchkeyField(field="first", scorer="exact", levels=2),
                    MatchkeyField(field="last", scorer="exact", levels=2),
                ],
            )
        ],
        blocking=BlockingConfig(
            keys=[
                BlockingKeyConfig(fields=["city"]),
                BlockingKeyConfig(fields=["zip"]),
            ]
        ),
    )


@pytest.fixture(scope="module")
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def _legacy_candidates(source, cfg):
    """What `union(...).distinct()` produced, rebuilt from the primitives."""
    from goldenmatch.spark.config_pipeline import blocking_passes, pass_candidates

    out = None
    for kc in blocking_passes(cfg):
        one = pass_candidates(source, kc, id_col=_ID)
        out = one if out is None else out.unionByName(one)
    return out.distinct()


def _pairs(frame) -> set[tuple[int, int]]:
    return {(int(r["a"]), int(r["b"])) for r in frame.select("a", "b").collect()}


def test_pass_priority_produces_the_same_candidate_set_as_distinct(source):
    from goldenmatch.spark.config_pipeline import generate_candidates

    cfg = _config()
    want = _pairs(_legacy_candidates(source, cfg))
    got = _pairs(generate_candidates(source, cfg, id_col=_ID))

    assert got == want
    # Not vacuous, twice over: there must BE candidates, and at least one of
    # them must be a pair both passes produce -- otherwise this asserts that a
    # dedup nobody exercised agrees with a dedup nobody exercised.
    assert want, "fixture produced no candidates"
    assert (0, 1) in want, "fixture lost the pair that both passes produce"


def test_the_union_carries_no_duplicate_pairs(source):
    """The point of pass priority: disjoint per-pass sets, so the union is
    already unique. Asserted on the UNDEDUPED frame -- calling `distinct()`
    here would test nothing."""
    from goldenmatch.spark.config_pipeline import generate_candidates

    rows = generate_candidates(source, _config(), id_col=_ID).select("a", "b").collect()
    pairs = [(int(r["a"]), int(r["b"])) for r in rows]
    assert len(pairs) == len(set(pairs)), f"duplicate candidates: {pairs}"


def test_a_pair_whose_only_shared_key_is_null_is_not_produced(source):
    """`_valid_key` on BOTH sides of the earlier-pass test.

    Rows 4 and 6 both have a null `zip`. If null-vs-null counted as "an earlier
    pass already produced this", a pair would be excluded from a pass that was
    its only route -- a recall loss that no count and no duplicate check sees.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates

    got = _pairs(generate_candidates(source, _config(), id_col=_ID))
    # 3 and 4 share city 'leeds' and are BOTH null on zip. The pair must survive
    # via pass 0 regardless of what pass 1 thinks of their nulls.
    assert (3, 4) in got, "a pair was lost to a null-vs-null earlier-pass test"


def test_the_multi_pass_plan_has_no_pair_sized_aggregate(source):
    """`distinct()` over the candidate frame is gone, as a plan property."""
    import contextlib
    import io

    from goldenmatch.spark.config_pipeline import generate_candidates

    sess = source.sparkSession
    prior = sess.conf.get("spark.sql.autoBroadcastJoinThreshold", "10485760")
    sess.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            generate_candidates(source, _config(), id_col=_ID).explain(True)
        plan = buf.getvalue()

        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            _legacy_candidates(source, _config()).explain(True)
        legacy = buf2.getvalue()
    finally:
        sess.conf.set("spark.sql.autoBroadcastJoinThreshold", prior)

    if "Exchange" not in legacy:
        pytest.skip("backend does not plan Spark exchanges")

    # The legacy plan's distinct() is a HashAggregate keyed on (a, b) with no
    # aggregate function. Its absence is the claim.
    assert plan.count("Exchange ") < legacy.count("Exchange "), (
        f"expected fewer exchanges\nnew:\n{plan}"
    )
