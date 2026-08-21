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


def test_many_pairs_do_not_exhaust_the_heap(spark, registered):
    """The regression the bench found.

    J1 keyed the batch on `spark_partition_id()` alone, so the group was however
    many pairs a partition held and `collect_list` materialised all of them as
    one array in JVM heap. 1.9M pairs gave
    `java.lang.OutOfMemoryError: Java heap space` (bench run 31625487603) --
    not a slow path, no path.

    A small `batch_size` here forces MANY batches over a modest fixture, which is
    the same shape as a large fixture with the default size and runs in seconds.
    The assertion is that every pair still comes back correctly scored: bounding
    the batch must not drop or duplicate rows.
    """
    from goldenmatch.spark.batched import score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    df = spark.createDataFrame(_rows(20, 10), _SCHEMA)
    pairs = _candidate_pairs(spark, df)
    want = _row_shaped(df, pairs)

    scored = score_pairs_batched(
        pairs, df, id_col=_ID, value_col="name",
        scorer_id=scorer_id("exact"), udf_name=registered,
        batch_size=7,  # deliberately tiny: forces many batches
    )
    got = {(int(r["a"]), int(r["b"])): r["score"] for r in scored.collect()}

    assert len(got) == len(want), (
        f"bounding the batch changed the row count: {len(got)} vs {len(want)}"
    )
    assert got == want, "scores changed when the batch was split"


def test_batch_size_does_not_change_the_answer(spark, registered):
    """Batch size is a memory/throughput choice and must be answer-invariant.

    If it were not, the number would be a correctness parameter -- and nobody
    would know which value was the correct one.
    """
    from goldenmatch.spark.batched import score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    df = spark.createDataFrame(_rows(8, 8), _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    answers = []
    for size in (3, 11, 10_000):
        scored = score_pairs_batched(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=registered, batch_size=size,
        )
        answers.append(
            {(int(r["a"]), int(r["b"])): r["score"] for r in scored.collect()}
        )
    assert answers[0] == answers[1] == answers[2], (
        "batch size changed the answer; it is a memory knob, not a semantic one"
    )


def test_the_threshold_returns_exactly_what_a_post_filter_would(spark, registered):
    """Filtering inside the array must be answer-identical to filtering rows.

    The parameter exists for a PLAN reason -- keep rejected pairs out of the
    generator, because J4's bisect attributed the batched arm's 2.4x to the
    explode rather than the kernel -- so the one thing it must not do is change
    the answer. `exact` scores are 0.0 or 1.0, so a threshold of 0.5 splits them
    cleanly and an off-by-one in the predicate shows up as a whole class
    vanishing.
    """
    from goldenmatch.spark.batched import score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    df = spark.createDataFrame(_rows(6, 8), _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    def _run(threshold):
        return score_pairs_batched(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=registered,
            threshold=threshold,
        )

    from pyspark.sql import functions as F

    want = {
        (int(r["a"]), int(r["b"])): r["score"]
        for r in _run(None).where(F.col("score") >= F.lit(0.5)).collect()
    }
    got = {(int(r["a"]), int(r["b"])): r["score"] for r in _run(0.5).collect()}

    assert got == want, (
        f"array-filter and row-filter disagree: only-array={set(got) - set(want)}, "
        f"only-row={set(want) - set(got)}"
    )
    # The test is only meaningful if the threshold actually rejected something.
    assert want, "no pair cleared 0.5; the fixture proves nothing"
    everything = {(int(r["a"]), int(r["b"])) for r in _run(None).collect()}
    assert len(want) < len(everything), (
        "nothing was rejected, so this would pass with the filter removed"
    )


def test_a_null_score_does_not_survive_the_threshold(spark, registered):
    """Comparability nulls must not leak through the array predicate.

    `null >= 0.5` is NULL, not false, and `filter` keeps only elements where the
    predicate is TRUE -- so nulls drop. That is the same thing `where` does, but
    it is worth pinning: a null score reaching the output would be a pair the
    scorer declined to judge, presented as a match.
    """
    from goldenmatch.spark.batched import score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    rows = [(0, "b", None), (1, "b", None), (2, "b", "x"), (3, "b", "x")]
    df = spark.createDataFrame(rows, _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    scored = score_pairs_batched(
        pairs, df, id_col=_ID, value_col="name",
        scorer_id=scorer_id("exact"), udf_name=registered, threshold=0.5,
    )
    scores = [r["score"] for r in scored.collect()]
    assert None not in scores, f"a null score cleared the threshold: {scores}"


# ── the row-shaped JVM path ──────────────────────────────────────────

def test_rowwise_matches_the_batched_answer_pair_for_pair(spark, registered):
    """One kernel, two plan shapes, one answer.

    `score_pairs_rowwise` exists to measure whether J1's batching pays for
    itself, and a performance experiment is worthless if the arms compute
    different things. Both reach the same `ScorerSelection.scorer()`, so any
    disagreement here is the PLAN -- which is exactly the class of bug the
    batched path's construction guards against (a score attached to the wrong
    pair does not crash).
    """
    from goldenmatch.spark.batched import score_pairs_batched, score_pairs_rowwise
    from goldenmatch.spark.jvm import ROW_UDF_NAME, scorer_id

    df = spark.createDataFrame(_rows(6, 8), _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    batched = {
        (int(r["a"]), int(r["b"])): r["score"]
        for r in score_pairs_batched(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=registered,
        ).collect()
    }
    rowwise = {
        (int(r["a"]), int(r["b"])): r["score"]
        for r in score_pairs_rowwise(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=ROW_UDF_NAME,
        ).collect()
    }

    assert set(rowwise) == set(batched), (
        f"pair sets differ: only-rowwise={set(rowwise) - set(batched)}, "
        f"only-batched={set(batched) - set(rowwise)}"
    )
    mismatched = {k: (rowwise[k], batched[k]) for k in batched if rowwise[k] != batched[k]}
    assert not mismatched, f"scores differ for {len(mismatched)} pair(s): {mismatched}"
    assert batched, "no pairs scored; the fixture proves nothing"


def test_rowwise_matches_the_row_shaped_python_answer(spark, registered):
    """And against the reference the batched path is also gated on.

    Transitivity would give this, but stating it directly means a future change
    to `score_pairs_batched` cannot quietly move BOTH JVM arms off the Python
    answer together while the test above still passes.
    """
    from goldenmatch.spark.batched import score_pairs_rowwise
    from goldenmatch.spark.jvm import ROW_UDF_NAME, scorer_id

    df = spark.createDataFrame(_rows(4, 6), _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    want = _row_shaped(df, pairs)
    got = {
        (int(r["a"]), int(r["b"])): r["score"]
        for r in score_pairs_rowwise(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=ROW_UDF_NAME,
        ).collect()
    }
    assert set(got) == set(want)
    mismatched = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not mismatched, f"scores misaligned for {len(mismatched)} pair(s): {mismatched}"


def test_rowwise_keeps_nulls_null_rather_than_scoring_them_1(spark, registered):
    """A missing value must not become a perfect match.

    The kernel maps a missing value to "" and would score null-vs-null as 1.0,
    merging two records whose only shared evidence is that both are missing the
    field. The batched UDF returns null for such a pair; the row-shaped one has
    to do the same, and it is a fresh code path so it gets its own assertion.
    """
    from goldenmatch.spark.batched import score_pairs_rowwise
    from goldenmatch.spark.jvm import ROW_UDF_NAME, scorer_id

    rows = [(0, "b", None), (1, "b", None), (2, "b", "x"), (3, "b", "x")]
    df = spark.createDataFrame(rows, _SCHEMA)
    pairs = _candidate_pairs(spark, df)

    got = {
        (int(r["a"]), int(r["b"])): r["score"]
        for r in score_pairs_rowwise(
            pairs, df, id_col=_ID, value_col="name",
            scorer_id=scorer_id("exact"), udf_name=ROW_UDF_NAME,
        ).collect()
    }
    assert got[(0, 1)] is None, f"null-vs-null scored {got[(0, 1)]!r}, not None"
    assert got[(2, 3)] == 1.0
