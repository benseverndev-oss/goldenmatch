"""J0 lane gate: the jar reaches a real Spark session and scores a batch.

Runs in the Spark lanes; skips where no Spark Connect client or no built jar is
present.

This is the half the unit tests cannot reach: `addArtifact` against a real
session, `registerJavaFunction` against a real catalog, and the array marshaling
that the probe measured but this code now depends on.

J0 deliberately proved the PLUMBING with no native call in the picture: the jar
implemented `exact` only -- string equality, identical by inspection in any
language -- so nothing here could be a kernel divergence, and a misalignment
found by J1's plan reshape could not be blamed on the scorer.

That separation has done its job and this file stays on the plumbing side of it.
J2 put the Rust kernel behind the same UDF (JNI -> `score-cabi` -> `score_one`),
but whether the JVM's number EQUALS Python's is a different question with a
different oracle, and it lives in `test_spark_jvm_native_parity.py`. Here the
questions are still: does the jar ship, does the UDF register, does a batch come
back aligned, and does a bad request fail loudly instead of returning a plausible
number.
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


@pytest.fixture(scope="module")
def jar():
    try:
        return find_jar()
    except JvmScorerUnavailable as exc:
        pytest.skip(f"no JVM scorer jar built: {exc}")


@pytest.fixture()
def registered(spark, jar):
    return install(spark, jar=jar)


def test_the_jar_ships_and_the_udf_registers(spark, jar):
    """The two Connect capabilities the whole approach rests on."""
    name = install(spark, jar=jar)
    assert name == "golden_score_batch"


def test_a_batch_is_scored_in_one_call(spark, registered):
    """The property that makes going native worth it.

    Connect only permits row-shaped UDFs, so without batching every pair would
    cost a downcall into native code. One call, many pairs.
    """
    df = spark.createDataFrame(
        [(["a", "b", "c", "d"], ["a", "x", "c", "y"])], ["xs", "ys"]
    )
    got = df.selectExpr(
        f"{registered}({scorer_id('exact')}, xs, ys) AS s"
    ).collect()[0]["s"]
    assert list(got) == [1.0, 0.0, 1.0, 0.0]


def test_scores_stay_aligned_with_their_pairs(spark, registered):
    """The failure mode J1 has to survive, checked here while it is still cheap.

    A misalignment would not crash -- it would score pair i with pair j's
    answer. So the fixture is built so that EVERY position has a distinct
    expected value: any rotation or reversal changes the result.
    """
    xs = ["m", "m", "n", "n", "o"]
    ys = ["m", "z", "n", "z", "z"]
    expected = [1.0, 0.0, 1.0, 0.0, 0.0]
    df = spark.createDataFrame([(xs, ys)], ["xs", "ys"])
    got = list(
        df.selectExpr(f"{registered}({scorer_id('exact')}, xs, ys) AS s")
        .collect()[0]["s"]
    )
    assert got == expected
    # A reversed result would also contain two 1.0s and three 0.0s, so assert
    # position rather than multiset -- counting would pass on a rotation.
    assert got != list(reversed(expected)) or expected == list(reversed(expected))


def test_an_unobserved_pair_comes_back_null_not_one(spark, registered):
    """Null policy travels intact through the Java boundary.

    Two records that are both MISSING the compared value must not read as a
    perfect match -- the substitution that made null-vs-null score 1.0 is the
    defect this project has already fixed twice.
    """
    df = spark.createDataFrame([([None, "a"], [None, "a"])], ["xs", "ys"])
    got = list(
        df.selectExpr(f"{registered}({scorer_id('exact')}, xs, ys) AS s")
        .collect()[0]["s"]
    )
    assert got[0] is None, f"null-vs-null came back as {got[0]!r}"
    assert got[1] == 1.0, "a real match must still score 1.0"


def test_a_large_batch_survives(spark, registered):
    """A four-element array proves the signature; it does not prove Spark will
    carry a batch worth amortising a downcall over."""
    n = 10_000
    xs = [f"v{i}" for i in range(n)]
    df = spark.createDataFrame([(xs, list(xs))], ["xs", "ys"])
    got = df.selectExpr(
        f"{registered}({scorer_id('exact')}, xs, ys) AS s"
    ).collect()[0]["s"]
    assert len(got) == n
    assert all(v == 1.0 for v in got)


def test_a_scorer_j0_refused_now_runs(spark, registered):
    """J0 refused every scorer but `exact`, on purpose: a Java jaro-winkler would
    have been a fourth implementation of a kernel that exists once in Rust.

    This test used to assert that refusal, and asserting it now would pin the jar
    to the limitation J2 exists to remove -- so it is inverted rather than
    deleted. The refusal is gone because the kernel ARRIVED (JNI ->
    ``score-cabi`` -> ``score_one``), not because the restriction was relaxed;
    the jar still carries no algorithms of its own.

    Exact agreement with the Python answer is
    ``test_spark_jvm_native_parity.py``'s job. What matters here is only that the
    call crosses the boundary and comes back with real work done.
    """
    df = spark.createDataFrame([(["jonathan"], ["jonothan"])], ["xs", "ys"])
    got = df.selectExpr(
        f"{registered}({scorer_id('jaro_winkler')}, xs, ys) AS s"
    ).collect()[0]["s"]
    assert len(got) == 1
    # A partial similarity: not `exact`'s 0.0, and not a trivially perfect 1.0.
    assert 0.0 < got[0] < 1.0, (
        f"expected a real jaro-winkler score, got {got[0]}. A 0.0 here would "
        f"mean the id fell through to the kernel's catch-all arm."
    )


def test_an_id_the_kernel_does_not_know_is_still_refused(spark, registered):
    """The refusal that must NOT go away.

    ``score_one``'s catch-all returns 0.0 for an unknown id -- a confident wrong
    answer rather than an error. J2 widened what the jar accepts to the loaded
    kernel's own id range; anything outside it still has to fail loudly, and the
    failure has to survive the Spark boundary rather than arriving as a plausible
    number.
    """
    df = spark.createDataFrame([(["a"], ["b"])], ["xs", "ys"])
    with pytest.raises(Exception) as err:  # noqa: PT011 - Spark wraps the cause
        df.selectExpr(f"{registered}(9999, xs, ys) AS s").collect()
    assert "9999" in str(err.value), (
        f"the refusal did not survive the boundary: {err.value}"
    )
