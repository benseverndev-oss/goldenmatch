"""J2 lane gate: a score from a Spark executor's JVM equals the Python one.

This is the claim the whole arc rests on. Every binding in this repo -- pyo3
``native``, ``datafusion-udf``, ``score-wasm``, ``score-cabi`` and now
``score-jni`` -- wraps the SAME ``score_one``, so a score is identical across
surfaces *by construction* rather than by several implementations being kept in
step. A test is what turns that from an intention into a property.

Equality here is **exact**, not approximate. Both sides run the same Rust
function over the same bytes; anything but bit-identical means the marshaling
changed the input, and a tolerance would hide precisely the class of bug this
exists to catch (a truncated multi-byte value, a slice off by one, a batch that
drifted out of alignment). Those produce *plausible* scores, which is what makes
them dangerous.

The oracle is ``score-wasm``-free plain Python/Rust on the client side; where the
client has the compiled kernel installed the two are the same code, and where it
does not the pure-Python fallback is still the number this package promises its
users. Either way the JVM must agree with it.

Skips where no Spark Connect client or no built jar is present; the
``spark_connect`` lane provides both.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

from goldenmatch.spark.jvm import (  # noqa: E402
    FALLBACK_IMPL,
    NATIVE_IMPL,
    JvmScorerUnavailable,
    find_jar,
    implementation,
    install,
    scorer_id,
)

#: Chosen to exercise the ways marshaling breaks rather than to be pretty:
#: near-misses (where a truncation still scores high and looks fine), multi-byte
#: values, empty strings, values whose byte length differs from their char
#: length, and one pair that must score exactly 1.0 and exactly 0.0.
PAIRS: list[tuple[str, str]] = [
    ("jonathan smith", "jonathon smyth"),
    ("Acme Corporation", "Acme Corp"),
    ("identical", "identical"),
    ("totally", "different"),
    ("", ""),
    ("", "nonempty"),
    ("café", "cafe"),
    ("Zoë Müller", "Zoe Muller"),
    ("日本語テキスト", "日本語テスト"),
    ("O'Brien", "OBrien"),
    ("  leading", "leading"),
    ("MIXED case", "mixed CASE"),
]

#: The scorers the Spark tier's config surface permits. `exact` included on
#: purpose: J0 could only run that one, so it is the control -- if it diverged,
#: the problem would be the plumbing rather than the kernel.
SCORERS = ["jaro_winkler", "levenshtein", "token_sort", "exact"]


@pytest.fixture(scope="module")
def jar():
    try:
        return find_jar()
    except JvmScorerUnavailable as exc:
        pytest.skip(f"no JVM scorer jar built: {exc}")


@pytest.fixture()
def registered(spark, jar):
    return install(spark, jar=jar)


def _python_scores(scorer: str, pairs: list[tuple[str, str]]) -> list[float]:
    """The client-side answer, through the SHIPPED scorer.

    ``goldenmatch.spark.scorers.score_batch`` is the function the tier's own
    Python UDF calls, so this compares the JVM against what a user actually gets
    today -- not against a lookalike assembled out of ``strsim`` primitives that
    happens to agree. (Building the oracle by hand is how you end up measuring
    something adjacent to the shipped path and calling it parity.)

    ``exact`` is not one of ``score_batch``'s scorers -- it is string equality,
    which is why J0 could implement it in Java without forking the kernel -- so
    it is defined here directly, matching ``score_one``'s id 3.
    """
    if scorer == "exact":
        return [1.0 if a == b else 0.0 for a, b in pairs]

    from goldenmatch.spark.scorers import score_batch

    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    return [float(v) for v in score_batch(scorer, xs, ys)]


def _jvm_scores(spark, udf, scorer: str, pairs: list[tuple[str, str]]) -> list[float]:
    """The executor-side answer, through the registered batch UDF."""
    from pyspark.sql import functions as F

    xs = [a for a, _ in pairs]
    ys = [b for _, b in pairs]
    df = spark.createDataFrame([(xs, ys)], ["xs", "ys"])
    got = df.select(
        F.call_udf(udf, F.lit(scorer_id(scorer)), F.col("xs"), F.col("ys")).alias("s")
    ).collect()[0]["s"]
    return [float(v) for v in got]


def test_the_executor_resolved_the_native_scorer(spark, registered):
    """Everything below would pass against the `exact`-only fallback.

    So this runs first and is the load-bearing assertion of the file: the jar
    falls back when the library will not load, which keeps a job alive and would
    otherwise leave this whole suite green while testing J0. The probe answers
    from an EXECUTOR -- a driver that loads the library says nothing about a
    cluster whose executors cannot.
    """
    name, diagnostics = implementation(spark)
    assert name == NATIVE_IMPL, (
        f"the executor resolved {name!r}, not {NATIVE_IMPL!r}. Diagnostics: "
        f"{diagnostics}. A {FALLBACK_IMPL} here means the native library did not "
        f"load, and every parity assertion below would be testing the J0 jar."
    )
    assert diagnostics, "the probe returned no diagnostics"


@pytest.mark.parametrize("scorer", SCORERS)
def test_jvm_scores_match_python_exactly(spark, registered, scorer):
    """The parity claim, one scorer at a time.

    Exact equality: both sides are the same Rust function over the same bytes.
    """
    want = _python_scores(scorer, PAIRS)
    got = _jvm_scores(spark, registered, scorer, PAIRS)

    assert len(got) == len(want)
    mismatches = [
        (PAIRS[i], want[i], got[i]) for i in range(len(want)) if got[i] != want[i]
    ]
    assert not mismatches, (
        f"{scorer}: JVM and Python disagree on {len(mismatches)} pair(s). "
        f"Both call score_one over the same bytes, so a difference is the "
        f"marshaling, not the algorithm: {mismatches}"
    )


def test_a_scorer_j0_could_not_run_now_runs(spark, registered):
    """J2's actual deliverable, stated as a test.

    J0's jar refused every id but `exact`. If this passes, the kernel arrived --
    and it is asserted against the Python answer rather than a hardcoded
    constant, so it cannot pass by agreeing with a number somebody typed.
    """
    pair = [("jonathan", "jonothan")]
    got = _jvm_scores(spark, registered, "jaro_winkler", pair)[0]
    want = _python_scores("jaro_winkler", pair)[0]
    assert got == want
    # And it is really doing the work, not returning an `exact` 0.0 by accident.
    assert 0.0 < got < 1.0, f"expected a partial similarity, got {got}"


def test_multibyte_values_survive_the_boundary(spark, registered):
    """The marshaling bug that produces plausible numbers instead of failures.

    Offsets are BYTE offsets. Treating them as character indices truncates every
    multi-byte value AND shifts every value after it, so the batch scores
    confidently and wrongly. Identity is the sharpest probe: a truncated string
    stops matching itself.
    """
    values = ["Zoë", "日本語", "naïve café", "plain", "🎯 emoji"]
    pairs = [(v, v) for v in values]
    got = _jvm_scores(spark, registered, "exact", pairs)
    assert got == [1.0] * len(values), (
        f"a value stopped matching itself across the JNI boundary: "
        f"{list(zip(values, got))}"
    )


def test_nulls_are_not_scored_as_empty_strings(spark, registered):
    """Absence of evidence must not read as evidence.

    ``score-cabi`` carries no validity bitmap by design -- null policy belongs to
    the host -- so this is the JVM side's presence mask doing its job. Getting it
    wrong makes null-vs-null a perfect 1.0, and two records whose only shared
    evidence is a shared absence then merge at every threshold. That is a bug
    this tier already shipped once.
    """
    from pyspark.sql import functions as F

    xs = [None, "smith", None, "same"]
    ys = [None, None, "jones", "same"]
    df = spark.createDataFrame([(xs, ys)], ["xs", "ys"])
    got = df.select(
        F.call_udf(
            registered, F.lit(scorer_id("jaro_winkler")), F.col("xs"), F.col("ys")
        ).alias("s")
    ).collect()[0]["s"]
    assert list(got) == [None, None, None, 1.0]


def test_a_ten_thousand_pair_batch_crosses_in_one_call(spark, registered):
    """The property that makes going native worth anything.

    Connect only permits row-shaped UDFs, so without batching every pair would
    cost a downcall. This is `batched.DEFAULT_BATCH_SIZE`, and the arrays the
    JNI layer pins are proportionally large -- a size limit would surface here
    rather than in a customer's executor.
    """
    n = 10_000
    xs = [f"value-{i}" for i in range(n)]
    ys = [xs[i] if i % 3 == 0 else f"other-{i}" for i in range(n)]
    df = spark.createDataFrame([(xs, ys)], ["xs", "ys"])
    from pyspark.sql import functions as F

    got = df.select(
        F.call_udf(
            registered, F.lit(scorer_id("exact")), F.col("xs"), F.col("ys")
        ).alias("s")
    ).collect()[0]["s"]
    assert len(got) == n
    assert sum(1 for v in got if v == 1.0) == len(range(0, n, 3))
