"""J1 gate: the batched plan must produce exactly the row-shaped plan's answer.

Runs in the Spark lanes; skips without a Spark Connect client or a built jar.

J1 changes only the SHAPE of the plan -- group into arrays, score once, explode
back. So the bar is not "sensible output", it is "identical output", and the
comparison is against the row-shaped path computing the same thing.

The scorer is `exact`, because that is all the J0 jar implements. That costs
nothing here: alignment is scorer-independent, and a trivial scorer makes a
misalignment MORE visible rather than less, since every expected value is 0.0 or
1.0 and a swap shows up immediately.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.spark.jvm import (  # noqa: E402
    JvmScorerUnavailable,
    find_jar,
    install,
    scorer_id,
)

_ID = "__row_id__"
_SCHEMA = f"{_ID} long, blk string, name string"


@pytest.fixture(scope="module")
def jar():
    try:
        return find_jar()
    except JvmScorerUnavailable as exc:
        pytest.skip(f"no JVM scorer jar built: {exc}")


@pytest.fixture()
def registered(spark, jar):
    return install(spark, jar=jar)


def _rows(n_blocks: int, per_block: int):
    """Rows whose expected pairing is distinct per position.

    Every record's `name` encodes its own id, and exactly one PARTNER per block
    shares a name. So each pair has a knowable, non-uniform answer -- a
    misalignment cannot hide behind a run of identical scores.
    """
    rows = []
    rid = 0
    for b in range(n_blocks):
        for i in range(per_block):
            # Two records per block share a name (i and i+1 when i is even).
            name = f"n{b}_{i // 2}"
            rows.append((rid, f"blk{b}", name))
            rid += 1
    return rows


def _candidate_pairs(spark, df):
    """Block self-join, a < b -- the same candidate set both paths score."""
    from pyspark.sql import functions as F

    a, b = df.alias("a"), df.alias("b")
    return a.join(
        b,
        (F.col("a.blk") == F.col("b.blk"))
        & (F.col(f"a.{_ID}") < F.col(f"b.{_ID}")),
    ).select(F.col(f"a.{_ID}").alias("a"), F.col(f"b.{_ID}").alias("b"))


def _row_shaped(df, pairs):
    """The reference: score each pair individually, in Python, from collected
    values. Deliberately NOT the Spark row UDF -- a reference sharing machinery
    with the thing under test proves only that the machinery is consistent."""
    vals = {r[_ID]: r["name"] for r in df.collect()}
    out = {}
    for r in pairs.collect():
        a, b = int(r["a"]), int(r["b"])
        x, y = vals[a], vals[b]
        out[(a, b)] = None if x is None or y is None else (1.0 if x == y else 0.0)
    return out


def _batched(spark, df, pairs, udf_name):
    from goldenmatch.spark.batched import score_pairs_batched

    scored = score_pairs_batched(
        pairs,
        df,
        id_col=_ID,
        value_col="name",
        scorer_id=scorer_id("exact"),
        udf_name=udf_name,
    )
    return {
        (int(r["a"]), int(r["b"])): r["score"] for r in scored.collect()
    }


def test_batched_matches_the_row_shaped_answer(spark, registered):
    """The J1 gate. Same pairs, same scores, pair for pair."""
    df = spark.createDataFrame(_rows(4, 6), _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    want = _row_shaped(df, pairs)
    got = _batched(spark, df, pairs, registered)

    assert set(got) == set(want), (
        f"pair sets differ: only-batched={set(got) - set(want)}, "
        f"only-reference={set(want) - set(got)}"
    )
    mismatched = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not mismatched, f"scores misaligned for {len(mismatched)} pair(s): {mismatched}"


def test_every_score_lands_on_its_own_pair(spark, registered):
    """States the alignment property directly rather than inferring it.

    For each returned pair, recompute the expected score from that pair's OWN
    values. A rotation, reversal, or cross-batch swap fails here even if the
    multiset of scores is unchanged -- which is exactly what a misalignment
    looks like.
    """
    df = spark.createDataFrame(_rows(5, 4), _SCHEMA)
    vals = {r[_ID]: r["name"] for r in df.collect()}
    pairs = _candidate_pairs(spark, df)
    got = _batched(spark, df, pairs, registered)

    for (a, b), score in got.items():
        expected = 1.0 if vals[a] == vals[b] else 0.0
        assert score == expected, (
            f"pair ({a},{b}) has values {vals[a]!r}/{vals[b]!r} -> expected "
            f"{expected}, got {score}"
        )


def test_the_multiset_check_would_not_have_caught_it(spark, registered):
    """Guard on the TEST, not the code.

    A misalignment preserves the multiset of scores, so a test comparing sorted
    score lists would pass through it. This asserts the fixture actually has a
    mix -- if every pair scored the same, the test above would be vacuous.
    """
    df = spark.createDataFrame(_rows(5, 4), _SCHEMA)
    got = _batched(spark, df, _candidate_pairs(spark, df), registered)

    distinct = set(got.values())
    assert distinct == {0.0, 1.0}, (
        f"fixture produced only {distinct}; with a single score value the "
        f"alignment assertions cannot fail and prove nothing"
    )


def test_batching_spans_more_than_one_batch(spark, registered):
    """If everything lands in one batch, cross-batch misalignment is untested.

    Repartitioning forces several partitions, and `batch_key` groups by
    partition -- so this is the case where a per-batch score array could be
    attached to the wrong batch's rows.
    """
    df = spark.createDataFrame(_rows(6, 4), _SCHEMA)
    pairs = _candidate_pairs(spark, df).repartition(4)

    want = _row_shaped(df, pairs)
    got = _batched(spark, df, pairs, registered)
    assert got == want


def test_nulls_survive_the_round_trip(spark, registered):
    """A null value must come back null, not 1.0 and not dropped.

    Two records missing the compared field share an absence, not a value -- the
    substitution this project has already had to fix twice.
    """
    rows = [(0, "b", None), (1, "b", None), (2, "b", "x"), (3, "b", "x")]
    df = spark.createDataFrame(rows, _SCHEMA)
    got = _batched(spark, df, _candidate_pairs(spark, df), registered)

    assert got[(0, 1)] is None, f"null-vs-null came back {got[(0, 1)]!r}"
    assert got[(2, 3)] == 1.0
    assert got[(0, 2)] is None


def test_dedup_max_matches_the_row_shaped_contract(spark, registered):
    """The MAX-per-canonical-pair contract, unchanged by the reshape."""
    from goldenmatch.spark.batched import dedup_max, score_pairs_batched

    df = spark.createDataFrame(_rows(3, 4), _SCHEMA)
    pairs = _candidate_pairs(spark, df)
    scored = score_pairs_batched(
        pairs, df, id_col=_ID, value_col="name",
        scorer_id=scorer_id("exact"), udf_name=registered,
    )
    deduped = {(int(r["a"]), int(r["b"])): r["score"] for r in dedup_max(scored).collect()}
    raw = {(int(r["a"]), int(r["b"])): r["score"] for r in scored.collect()}

    assert set(deduped) == set(raw), "dedup changed the pair set"
    assert deduped == raw, "no duplicate pairs here, so dedup must be a no-op"
