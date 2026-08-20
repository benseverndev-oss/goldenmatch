"""The counts stage's blocking join, fused: same pairs, three fewer shuffles.

`pass_candidates` projects the block self-join down to `(a, b)` ids, and
`join_candidates_to_sources` then pays two more joins to fetch back the columns
that projection dropped. `pass_candidates_joined` keeps the records it has
already co-located.

Two claims, tested separately because they fail differently:

* **Same answer.** The counts must be IDENTICAL, not close. A model trained on
  subtly different counts is still a valid model, so a tolerance here would hide
  exactly the bug this exists to catch.
* **Fewer shuffles.** A structural property of the PLAN, so it is visible on six
  rows and does not need a cluster. The wall-clock claim does need one, and is
  not made here.
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
_COLS = [_ID, "first", "last", "city"]

# Nulls deliberate: rows 4 and 5 make some comparisons UNOBSERVED (-1), which is
# a distinct gamma level. A fusion that changed which rows reach the comparison
# would move those counts, and they are the ones a bare "same number of pairs"
# assertion would miss.
_ROWS = [
    (0, "jon", "smith", "york"),
    (1, "john", "smith", "york"),
    (2, "jonathan", "smyth", "york"),
    (3, "amy", "wong", "leeds"),
    (4, "amy", None, "leeds"),
    (5, None, None, "leeds"),
    (6, "jon", "smyth", "york"),
    (7, "amie", "wong", "leeds"),
    # A block of one: it contributes no pairs, and must not contribute a row
    # either. The unfused path drops it at the self-join; so must the fused one.
    (8, "solo", "singleton", "hull"),
    # A null blocking key: `_valid_key` filters it BEFORE the self-join on both
    # paths, so it must appear in neither.
    (9, "ghost", "nokey", None),
]


def _config() -> GoldenMatchConfig:
    """`exact` scorers on purpose.

    The fusion is about the JOIN, not about scoring, and `exact` is `a = b` in
    Catalyst -- so the plan under test carries no UDF and the assertion is about
    exchanges rather than about which scorer route happened to be available.
    """
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
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
    )


@pytest.fixture(scope="module")
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def _both_frames(source, cfg):
    from goldenmatch.spark.config_pipeline import (
        blocking_passes,
        join_candidates_to_sources,
        pass_candidates,
        pass_candidates_joined,
    )

    kc = blocking_passes(cfg)[0]
    unfused = join_candidates_to_sources(
        pass_candidates(source, kc, id_col=_ID), source, id_col=_ID
    )
    fused = pass_candidates_joined(source, kc, id_col=_ID)
    return unfused, fused


def test_fused_counts_are_identical_to_the_unfused_counts(source):
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import agreement_pattern_counts

    cfg = _config()
    mk = cfg.get_matchkeys()[0]
    unfused, fused = _both_frames(source, cfg)

    want = agreement_pattern_counts(unfused, mk, lhs=CAND_LHS, rhs=CAND_RHS)
    got = agreement_pattern_counts(fused, mk, lhs=CAND_LHS, rhs=CAND_RHS)

    assert got == want, (
        "fused blocking join produced different agreement patterns; the two "
        "paths must generate the same pairs and compare the same values"
    )
    # Not vacuous: the fixture must actually produce pairs, or the assertion
    # above passes on two empty lists.
    assert sum(c for _, c in want) > 0


def test_the_fused_pass_carries_both_record_sides_under_the_same_aliases(source):
    """The aliases are the contract every gamma and score expression resolves
    against, so a fused frame that named its sides differently would not fail --
    it would silently resolve columns to the wrong side of the pair."""
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from pyspark.sql import functions as F

    _unfused, fused = _both_frames(source, _config())
    rows = fused.select(
        F.col(f"{CAND_LHS}.{_ID}").alias("a"),
        F.col(f"{CAND_RHS}.{_ID}").alias("b"),
        F.col(f"{CAND_LHS}.first").alias("a_first"),
        F.col(f"{CAND_RHS}.first").alias("b_first"),
    ).collect()

    assert rows, "no pairs"
    for r in rows:
        assert r["a"] < r["b"], "the a < b half of the join condition was lost"
    ids = {(int(r["a"]), int(r["b"])) for r in rows}
    assert (8, 8) not in ids and not any(8 in p for p in ids), (
        "the singleton block contributed a pair"
    )
    assert not any(9 in p for p in ids), "the null blocking key was not filtered"


def test_the_fused_pass_plans_strictly_fewer_shuffles(source):
    """The whole point, as a property of the plan rather than of a stopwatch.

    Skips where the backend does not plan Spark exchanges at all (pysail), which
    is a different engine and not a failure of this claim.
    """
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import gamma_columns
    from pyspark.sql import functions as F

    cfg = _config()
    mk = cfg.get_matchkeys()[0]
    unfused, fused = _both_frames(source, cfg)
    names = [f"gamma_{f.resolved_field}" for f in mk.fields]

    # Broadcast OFF for the duration. Ten rows fit in a broadcast, so without
    # this every join plans as a BroadcastHashJoin and the plan under test is
    # not the plan the claim is about -- at 250M records nothing broadcasts.
    # Forcing the sort-merge shape makes the six-row plan structurally the same
    # as the cluster's, which is what lets a fixture this small test it at all.
    sess = source.sparkSession
    prior = sess.conf.get("spark.sql.autoBroadcastJoinThreshold", "10485760")
    sess.conf.set("spark.sql.autoBroadcastJoinThreshold", "-1")

    def plan(frame: object) -> str:
        import contextlib
        import io

        g = gamma_columns(mk, CAND_LHS, CAND_RHS)
        grouped = (
            frame.select(*g)
            .groupBy(*[F.col(n) for n in names])
            .agg(F.count(F.lit(1)).alias("n"))
        )
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            grouped.explain(True)
        return buf.getvalue()

    try:
        before, after = plan(unfused), plan(fused)
    finally:
        sess.conf.set("spark.sql.autoBroadcastJoinThreshold", prior)
    if "Exchange" not in before:
        pytest.skip("backend does not plan Spark exchanges")

    n_before, n_after = before.count("Exchange "), after.count("Exchange ")
    j_before, j_after = before.count("SortMergeJoin"), after.count("SortMergeJoin")

    # ONE join is the load-bearing assertion, not the exchange count. The
    # counts stage needs exactly one -- the block self-join -- and any join
    # above it consumes a frame with O(pairs) rows rather than O(records).
    assert j_after == 1, f"expected 1 join, got {j_after}\n{after}"
    # At most one shuffle per blocking side plus one for the aggregate. Fewer
    # is legitimate -- both sides read the same scan, so Spark may reuse an
    # exchange -- which is why this is a ceiling and not an equality.
    assert n_after <= 3, f"expected at most 3 exchanges, got {n_after}\n{after}"
    assert n_before > n_after and j_before > j_after


def test_the_escape_hatch_selects_the_legacy_three_join_path(source, monkeypatch):
    """A default that cannot be turned off is a default nobody can measure.

    Asserts the SHAPE the flag selects, not just that the flag is read: an
    escape hatch that silently returned the fused frame anyway would leave the
    A/B comparing a build against itself.
    """
    from goldenmatch.spark.config_pipeline import (
        blocking_passes,
        fused_block_join_enabled,
        pass_joined,
    )

    cfg = _config()
    kc = blocking_passes(cfg)[0]

    monkeypatch.setenv("GOLDENMATCH_SPARK_FUSED_BLOCK_JOIN", "0")
    assert not fused_block_join_enabled()
    legacy = pass_joined(source, kc, id_col=_ID)
    # The legacy route joins the candidate frame back to the source, so its
    # columns include the candidate frame's own `a`/`b`. The fused route never
    # builds that frame.
    assert "a" in legacy.columns and "b" in legacy.columns

    monkeypatch.delenv("GOLDENMATCH_SPARK_FUSED_BLOCK_JOIN")
    assert fused_block_join_enabled()
    fused = pass_joined(source, kc, id_col=_ID)
    assert "a" not in fused.columns and "b" not in fused.columns
