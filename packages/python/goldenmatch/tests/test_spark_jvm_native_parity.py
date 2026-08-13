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
    name, diagnostics, runtime = implementation(spark)
    print(f"\n  executor: {name} | {runtime}\n  {diagnostics}")
    assert name == NATIVE_IMPL, (
        f"the executor resolved {name!r}, not {NATIVE_IMPL!r}. Diagnostics: "
        f"{diagnostics}. A {FALLBACK_IMPL} here means the native library did not "
        f"load, and every parity assertion below would be testing the J0 jar."
    )
    assert diagnostics, "the probe returned no diagnostics"
    # The heap ceiling decides how a batched-path result is read -- that path
    # materialises groups in JVM heap -- and a Connect client cannot ask any
    # other way.
    assert "heap_max=" in runtime, f"no heap ceiling reported: {runtime!r}"


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


# ── record fingerprints (identity graph) ─────────────────────────────

def test_jvm_fingerprints_match_python_exactly(spark, registered):
    """The identity graph's ids, computed with no Python on the executor.

    Same `fingerprint-core` on both sides, so equality is exact. It has to be:
    a fingerprint that differs by one byte does not fail, it silently splits one
    identity into two -- there is no threshold to absorb it and no test that
    would notice downstream.
    """
    from goldenmatch.core._hashing import record_fingerprint
    from goldenmatch.spark.jvm import FINGERPRINT_UDF_NAME
    from pyspark.sql import functions as F

    rows = [
        ("jonathan smith", "boston", 42),
        ("", "", 0),
        ("Zoë Müller", "münchen", -1),
        ("O'Brien", "st. john's", 999999),
        ("日本語", "東京", 7),
    ]
    df = spark.createDataFrame(rows, "name string, city string, n long")
    got = [
        r[0]
        for r in df.select(
            F.call_udf(
                FINGERPRINT_UDF_NAME, F.to_json(F.struct("name", "city", "n"))
            )
        ).collect()
    ]
    want = [record_fingerprint({"name": a, "city": b, "n": c}) for a, b, c in rows]
    assert got == want, (
        f"JVM and Python fingerprints disagree. Both call fingerprint-core, so "
        f"a difference is the JSON encoding, not the hash: "
        f"{[(i, w, g) for i, (w, g) in enumerate(zip(want, got)) if w != g]}"
    )


def test_derive_record_ids_matches_the_python_path(spark, registered):
    """The whole function, JVM path against Python path, on one DataFrame."""
    from goldenmatch.spark.identity import derive_record_ids
    from goldenmatch.spark.jvm import FINGERPRINT_UDF_NAME
    from pyspark.sql import functions as F

    df = spark.createDataFrame(
        [("a", "x", 1), ("b", "y", 2), ("", None, 3)],
        "name string, city string, n long",
    ).withColumn("__source__", F.lit("probe"))

    py = {
        r["name"]: r["record_id"]
        for r in derive_record_ids(df, id_col="__row_id__").collect()
    }
    jvm = {
        r["name"]: r["record_id"]
        for r in derive_record_ids(
            df, id_col="__row_id__", fingerprint_udf=FINGERPRINT_UDF_NAME
        ).collect()
    }
    assert jvm == py, f"record_id differs between the two paths: {py} vs {jvm}"
    assert all(v.startswith("probe:h1:") for v in jvm.values()), jvm


def test_unproven_column_types_are_refused_not_guessed(spark, registered):
    """A float column must be REFUSED by the JVM path, not silently fingerprinted.

    Python hands the kernel a float value; Spark hands it a JSON literal, and the
    two only agree if every float renders identically. Guessing produces ids that
    are wrong in a way nothing downstream can detect, so the path refuses and
    names the column.
    """
    from goldenmatch.spark.identity import derive_record_ids
    from goldenmatch.spark.jvm import FINGERPRINT_UDF_NAME
    from pyspark.sql import functions as F

    df = spark.createDataFrame(
        [("a", 1.5)], "name string, score double"
    ).withColumn("__source__", F.lit("probe"))
    with pytest.raises(ValueError, match="score:double"):
        derive_record_ids(
            df, id_col="__row_id__", fingerprint_udf=FINGERPRINT_UDF_NAME
        )


# ── survivorship (golden records) ────────────────────────────────────

#: Clusters chosen for TIE-BREAKS, which is where this port could silently
#: diverge: Python's `Counter.most_common` keeps insertion order and `max()`
#: keeps the FIRST maximum, while Rust's `max_by_key` keeps the LAST. A tie
#: resolved the other way is a different golden record with no error attached.
SURVIVORSHIP_CLUSTERS: list[list[str | None]] = [
    ["a", "b"],
    ["b", "a", "a"],
    ["a", "a", "b", "b"],
    [None, "x", "y"],
    [None, None],
    ["a", None, "a"],
    ["ab", "abcd"],
    ["ab", "cd"],
    ["café", "abcde"],
    ["", "x"],
    ["same", "same", "same"],
    ["日本語", "ab"],
]

#: Every strategy the Spark call site can reach. `source_priority` and
#: `most_recent` are absent because Python RAISES for them without a sources or
#: dates list, which this call site does not pass.
SURVIVORSHIP_STRATEGIES = [
    "most_complete", "majority_vote", "first_non_null", "longest_value",
    "unanimous_or_null", "confidence_majority",
]


@pytest.mark.parametrize("strategy", SURVIVORSHIP_STRATEGIES)
def test_jvm_survivorship_matches_python_exactly(spark, registered, strategy):
    """The survivor chosen in the JVM must be the one Python chooses.

    ``survivorship-core`` had a committed differential dump from the day it was
    written, but that harness is MANUAL -- it needs cargo and a person to run
    it. Nothing in the suite re-checked it, so the port could drift from
    ``merge_field`` and CI would stay green. This is the automatic half.

    Exact equality, and no tolerance is meaningful here anyway: the result is a
    value, not a number. A survivor chosen by a different tie-break is a wrong
    golden record that raises nothing and looks right.
    """
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field
    from goldenmatch.spark.golden import merge_expr
    from goldenmatch.spark.jvm import SURVIVORSHIP_UDF_NAME
    from pyspark.sql import functions as F

    rows = [(i, list(vals)) for i, vals in enumerate(SURVIVORSHIP_CLUSTERS)]
    df = spark.createDataFrame(rows, "cid long, vals array<string>")
    got = {
        r["cid"]: r["v"]
        for r in df.select(
            "cid",
            merge_expr(F.col("vals"), strategy, SURVIVORSHIP_UDF_NAME).alias("v"),
        ).collect()
    }
    want = {
        i: (lambda m: None if m is None else str(m))(
            merge_field(list(vals), GoldenFieldRule(strategy=strategy))[0]
        )
        for i, vals in enumerate(SURVIVORSHIP_CLUSTERS)
    }
    mismatches = {
        i: (SURVIVORSHIP_CLUSTERS[i], want[i], got.get(i))
        for i in want
        if got.get(i) != want[i]
    }
    assert not mismatches, (
        f"{strategy}: JVM and Python chose different survivors. Both call the "
        f"same merge semantics, so a difference is a TIE-BREAK divergence: "
        f"{mismatches}"
    )


def test_strategies_python_refuses_are_refused_here_too(spark, registered):
    """The refusals are the load-bearing part.

    ``source_priority`` needs a sources list and ``most_recent`` needs dates --
    neither of which the Spark call site passes, so Python raises. The JVM path
    must refuse them at PLAN time rather than emitting a plausible survivor from
    every cluster, and it must name the strategy so the failure is actionable.
    """
    from goldenmatch.spark.golden import merge_expr
    from goldenmatch.spark.jvm import SURVIVORSHIP_UDF_NAME
    from pyspark.sql import functions as F

    for strategy in ("source_priority", "most_recent", "custom:whatever"):
        with pytest.raises(ValueError, match=strategy.split(":")[0]):
            merge_expr(F.col("vals"), strategy, SURVIVORSHIP_UDF_NAME)


# ── the whole scoring stage, JVM vs Python ───────────────────────────

def _parity_source(spark):
    """Rows chosen for the ways the BATCH construction breaks, not for realism.

    Nulls on one side and on both (comparability must be decided from the raw
    columns -- the kernel scores null-vs-null as a perfect 1.0), case that only
    a transform chain resolves, near-misses that still score high when a value
    is truncated, and multi-byte text whose byte length differs from its char
    length.
    """
    rows = [
        (0, "b", "jonathan", "smith", "boston"),
        (1, "b", "jonathon", "smyth", "Boston"),
        (2, "b", "jonathan", None, "boston"),
        (3, "b", None, None, "boston"),
        (4, "b", "Zoë", "Müller", "boston"),
        (5, "b", "Zoe", "Muller", "BOSTON"),
        (6, "b", "", "smith", "boston"),
        (7, "b", "alice", "smith", "boston"),
    ]
    return spark.createDataFrame(
        rows, "__row_id__ long, blk string, first string, last string, city string"
    )


def _parity_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["blk"])]),
        matchkeys=[
            # Two scorers in ONE matchkey: the batch must issue one UDF call per
            # SCORER and put each field's score back in the right place. A
            # single-scorer config would pass with the grouping logic inverted.
            MatchkeyConfig(
                name="mk_mixed", type="weighted", threshold=0.0,
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
                    MatchkeyField(field="last", scorer="levenshtein", weight=2.0),
                ],
            ),
            # An `exact` field inside a WEIGHTED matchkey: it carries no kernel
            # call but still needs a slot, because after `collect_list` there is
            # no raw column left to compare.
            MatchkeyConfig(
                name="mk_exact_field", type="weighted", threshold=0.0,
                fields=[
                    MatchkeyField(field="city", scorer="exact", weight=1.0),
                    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
                ],
            ),
            # Same column as mk_mixed but a DIFFERENT chain -> a different slot.
            # Collapsing these would score one comparison with the other's
            # settings, which is a wrong number rather than a crash.
            MatchkeyConfig(
                name="mk_chained", type="weighted", threshold=0.0,
                fields=[
                    MatchkeyField(field="city", scorer="jaro_winkler", weight=1.0,
                                  transforms=["lowercase"]),
                ],
            ),
        ],
    )


@pytest.mark.parametrize("batch_size", [1, 3, 10_000])
def test_jvm_scoring_matches_the_python_path_exactly(spark, registered, batch_size):
    """``score_candidates`` scored in the executor JVM equals the Python path.

    THE gate for the JVM scoring path. Both sides run the same Rust ``score_one``
    over the same bytes, so equality is exact -- a tolerance would hide the
    entire class of bug this exists to catch, because those bugs produce
    PLAUSIBLE scores. A batch whose scores drifted one position still returns
    numbers in [0, 1] for every pair.

    ``batch_size`` is parametrized because the reshape from a flat ``n*k`` score
    array back to ``n`` rows of ``k`` is the one piece with no analogue in the
    Python path, and it is trivially correct for a single batch. ``1`` puts
    every pair in its own batch, ``3`` forces several partial batches, ``10_000``
    is the production default and puts everything in one.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates, score_candidates
    from goldenmatch.spark.jvm import TRANSFORM_UDF_NAME, UDF_NAME

    src = _parity_source(spark)
    cfg = _parity_config()
    cands = generate_candidates(src, cfg, id_col="__row_id__")

    def collect(**kw):
        out = score_candidates(cands, src, cfg, id_col="__row_id__", **kw)
        return {(r["a"], r["b"]): r["score"] for r in out.collect()}

    want = collect()
    got = collect(
        scorer_udf=UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
        batch_size=batch_size,
        # EXPLICIT: `scorer_shape` now defaults to "row", which has no reshape
        # at all -- so without this the batch_size parametrize above would be
        # exercising nothing and the test would keep passing.
        scorer_shape="batch",
    )

    assert set(got) == set(want), (
        "the JVM path scored a different SET of pairs. Same candidates and same "
        "thresholds, so this is the batch losing or duplicating rows rather "
        "than a scoring difference: "
        f"only-jvm={sorted(set(got) - set(want))} "
        f"only-python={sorted(set(want) - set(got))}"
    )
    mismatches = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not mismatches, (
        f"batch_size={batch_size}: JVM and Python scored the same pair "
        f"differently. Both call the same score_one over the same bytes, so a "
        f"difference means the batch drifted out of alignment or a slot was "
        f"read from the wrong position -- (jvm, python): {mismatches}"
    )


def test_end_to_end_dedupe_runs_with_every_kernel_in_the_jar(spark, registered):
    """`run_config_pipeline` with all three UDFs equals the pure-Python run.

    The claim the arc exists for, end to end: blocking, scoring and survivorship
    in the executor JVM, clustering already pure Spark SQL. Compared against the
    Python path rather than asserted to "work", because a golden record that
    merely looks right is this project's recurring failure.
    """
    from goldenmatch.spark.config_pipeline import run_config_pipeline
    from goldenmatch.spark.jvm import (
        SURVIVORSHIP_UDF_NAME,
        TRANSFORM_UDF_NAME,
        UDF_NAME,
    )

    src = _parity_source(spark)
    cfg = _parity_config()

    def golden(**kw):
        out = run_config_pipeline(
            src, cfg, id_col="__row_id__", wcc="label_prop",
            golden_cols=["first", "last", "city"], **kw
        )
        return sorted(tuple(r) for r in out.collect())

    want = golden()
    got = golden(
        scorer_udf=UDF_NAME,
        transform_udf=TRANSFORM_UDF_NAME,
        survivorship_udf=SURVIVORSHIP_UDF_NAME,
    )
    assert got == want, (
        f"the jar-only run produced different golden records:\n"
        f"  jvm={got}\n  python={want}"
    )


def test_block_key_normalization_routes_to_the_jar_when_asked(spark, registered):
    """`_block_key_column` hands `transform_udf` down to `_transformed`.

    Threading matters more than the word "plumbing" suggests. A kernel
    reachable only from an internal is, from a caller's side, not reachable at
    all -- there is no argument to pass. `transform_udf` existed on
    `_transformed` for a while before any entry point could supply one, so a
    user running the documented pipeline got the Python path no matter what the
    jar could do.

    Blocking is where a normalization divergence does its damage: a value
    normalized differently lands in a different BLOCK and is never compared to
    its own duplicate, so the failure is a missing match that nothing
    downstream can detect.

    Lives HERE rather than in the session-free unit file because building a
    column expression needs an active session, not merely an importable
    pyspark: `pyspark.sql.functions` asserts `SparkContext._active_spark_context
    is not None`. `importorskip("pyspark")` passed locally, where the whole file
    skipped, and failed in the lane that actually has pyspark installed.
    """
    from goldenmatch.config.schemas import BlockingKeyConfig
    from goldenmatch.spark import config_pipeline

    seen = []
    real = config_pipeline._transformed

    def spy(col, chain, transform_udf=None):
        seen.append(transform_udf)
        return real(col, chain, transform_udf=transform_udf)

    config_pipeline._transformed = spy
    try:
        key = BlockingKeyConfig(fields=["city"], transforms=["lowercase"])
        config_pipeline._block_key_column(key, "golden_transform")
        config_pipeline._block_key_column(key)
    finally:
        config_pipeline._transformed = real

    assert seen == ["golden_transform", None], (
        "the block-key builder must pass the UDF name through, and must still "
        "default to the Python path when not given one"
    )


def test_the_probe_says_which_process_it_ran_in(spark, registered):
    """The runtime string must carry `exec=<id>`.

    Without it the file's central assertion cannot be made at all. Everything
    here talks about what an EXECUTOR resolved, and under `local[*]` the
    executor IS the driver -- Spark attributes a failed task there to
    `executor driver`. So `test_the_executor_resolved_the_native_scorer` was
    true and proved nothing about a cluster, and no care in the UDF could have
    distinguished the two.

    This pins the token's presence, so a jar that stops reporting it fails here
    rather than silently disarming the containerized-cluster lane, whose
    not-the-driver gate reads exactly this value. The lane asserts the VALUE;
    this asserts it exists to be asserted on.
    """
    _impl, _diagnostics, runtime = implementation(spark)
    tokens = dict(t.split("=", 1) for t in runtime.split() if "=" in t)
    assert "exec" in tokens, (
        f"no exec= in the runtime string {runtime!r}. The cluster lane's "
        f"'executor is not the driver' gate reads this token; without it that "
        f"gate cannot distinguish a real executor from local mode."
    )
    print(f"\n  ran on executor {tokens['exec']!r}")
# ── identity, pure Spark SQL (no kernel, no worker) ──────────────────

def test_entity_id_expr_matches_the_python_helper(spark, registered):
    """`entity_id_for_members` re-expressed as a column expression.

    Needed no kernel at all: it is sha256 over sorted, newline-joined ids, and
    every piece has a Spark equivalent. The orderings agree because Python sorts
    `str` by code point and Spark sorts by UTF-8 bytes, which for UTF-8 is the
    same order -- but "agree in principle" is what this test exists to replace.

    An entity_id that differs is not a wrong number; it is a DIFFERENT ENTITY,
    so exact equality is the only meaningful bar.
    """
    from goldenmatch.spark.identity import entity_id_expr, entity_id_for_members
    from pyspark.sql import functions as F

    clusters = {
        1: ["src:b", "src:a"],            # unsorted input
        2: ["src:a"],                     # singleton
        3: ["src:z", "src:a", "src:m"],   # 3 members
        4: ["src:é", "src:a"],            # multi-byte
        5: ["src:a", "src:a"],            # duplicate member ids
    }
    rows = [(cid, rid) for cid, rids in clusters.items() for rid in rids]
    df = spark.createDataFrame(rows, "cluster_id long, record_id string")
    got = {
        r["cluster_id"]: r["eid"]
        for r in df.groupBy("cluster_id")
        .agg(F.collect_list("record_id").alias("rids"))
        .select("cluster_id", entity_id_expr(F.col("rids")).alias("eid"))
        .collect()
    }
    want = {cid: entity_id_for_members(rids) for cid, rids in clusters.items()}
    assert got == want, (
        f"entity_id differs between Spark SQL and Python -- that is a different "
        f"ENTITY, not a rounding difference: "
        f"{ {k: (want[k], got.get(k)) for k in want if got.get(k) != want[k]} }"
    )


def test_golden_json_expr_keeps_null_fields(spark, registered):
    """`to_json` OMITS null fields by default; `json.dumps` emits them.

    Without `ignoreNullFields=false` a golden record silently loses every null
    column -- the row still looks like a golden record, just a smaller one. This
    pins the option rather than the whole encoding.
    """
    import json

    from goldenmatch.spark.identity import golden_json_expr

    df = spark.createDataFrame(
        [("a", None, 1), (None, "b", 2)], "x string, y string, n long"
    )
    got = [r[0] for r in df.select(golden_json_expr(["x", "y", "n"])).collect()]
    want = [
        json.dumps({"x": "a", "y": None, "n": 1}),
        json.dumps({"x": None, "y": "b", "n": 2}),
    ]
    assert [json.loads(g) for g in got] == [json.loads(w) for w in want], (
        f"null fields were dropped or reordered: {got}"
    )
    for g in got:
        assert "null" in g, f"a null field vanished from {g}"


def test_mint_entity_ids_pure_sql_matches_the_udf_path(spark, registered):
    """The whole function, both paths, on one frame."""
    from goldenmatch.spark.identity import mint_entity_ids

    df = spark.createDataFrame(
        [(1, "src:b"), (1, "src:a"), (2, "src:c"), (3, "src:z"), (3, "src:y")],
        "cluster_id long, record_id string",
    )
    udf_path = {r["cluster_id"]: r["entity_id"] for r in mint_entity_ids(df).collect()}
    sql_path = {
        r["cluster_id"]: r["entity_id"]
        for r in mint_entity_ids(df, pure_sql=True).collect()
    }
    assert sql_path == udf_path, f"{udf_path} vs {sql_path}"
    assert all(v.startswith("ent:h1:") for v in sql_path.values()), sql_path


def test_uuid7_is_refused_on_the_pure_sql_path(spark, registered, monkeypatch):
    """uuid7 mints a per-cluster UUIDv7 -- deliberately non-deterministic, and
    Spark's uuid() is v4. Refusing beats silently minting different ids."""
    from goldenmatch.spark.identity import mint_entity_ids

    monkeypatch.setenv("GOLDENMATCH_SAIL_IDENTITY_ID_SCHEME", "uuid7")
    df = spark.createDataFrame([(1, "src:a")], "cluster_id long, record_id string")
    with pytest.raises(ValueError, match="uuid7"):
        mint_entity_ids(df, pure_sql=True)


# ── the row-shaped scorer, in the config pipeline ────────────────────

def test_rowwise_config_scoring_matches_the_python_path_exactly(spark, registered):
    """The default JVM shape must equal the Python path, pair for pair.

    `scorer_shape="row"` is the default because it measured 1.95x faster than
    the batched shape (run 31714236735: J1's reshape +1.997s, the JNI downcall
    it avoids +0.747s). A faster wrong answer is worthless, and the failure mode
    here is PLAUSIBLE -- a slot read from the wrong column still yields a score
    in [0, 1] for every pair -- so equality is exact, with no tolerance.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates, score_candidates
    from goldenmatch.spark.jvm import TRANSFORM_UDF_NAME, UDF_NAME

    src = _parity_source(spark)
    cfg = _parity_config()
    cands = generate_candidates(src, cfg, id_col="__row_id__")

    def collect(**kw):
        out = score_candidates(cands, src, cfg, id_col="__row_id__", **kw)
        return {(r["a"], r["b"]): r["score"] for r in out.collect()}

    want = collect()
    got = collect(scorer_udf=UDF_NAME, transform_udf=TRANSFORM_UDF_NAME)

    assert set(got) == set(want), (
        f"the row-shaped JVM path scored a different SET of pairs: "
        f"only-jvm={sorted(set(got) - set(want))} "
        f"only-python={sorted(set(want) - set(got))}"
    )
    mismatches = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not mismatches, (
        f"row-shaped JVM and Python scored the same pair differently. Both call "
        f"the same score_one over the same bytes, so this is a slot read from "
        f"the wrong column -- (jvm, python): {mismatches}"
    )
    assert want, "no pairs scored; the fixture proves nothing"


def test_the_two_jvm_shapes_agree(spark, registered):
    """Row and batch must be interchangeable, or `scorer_shape` is a correctness
    switch rather than a performance one -- and nobody would know which value
    was the right one.

    Stated directly rather than left to transitivity through the Python path: a
    change that moved BOTH shapes together would pass the two parity tests above
    and be caught here only if they are compared to each other.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates, score_candidates
    from goldenmatch.spark.jvm import TRANSFORM_UDF_NAME, UDF_NAME

    src = _parity_source(spark)
    cfg = _parity_config()
    cands = generate_candidates(src, cfg, id_col="__row_id__")

    def collect(shape):
        out = score_candidates(
            cands, src, cfg, id_col="__row_id__", scorer_udf=UDF_NAME,
            transform_udf=TRANSFORM_UDF_NAME, scorer_shape=shape,
        )
        return {(r["a"], r["b"]): r["score"] for r in out.collect()}

    row, batch = collect("row"), collect("batch")
    assert set(row) == set(batch), (
        f"only-row={sorted(set(row) - set(batch))} "
        f"only-batch={sorted(set(batch) - set(row))}"
    )
    mismatches = {k: (row[k], batch[k]) for k in batch if row[k] != batch[k]}
    assert not mismatches, f"the two JVM shapes disagree: {mismatches}"


def test_an_unknown_scorer_shape_is_refused(spark):
    """Not silently treated as the default: a typo'd shape would otherwise run
    the row path while the caller believed they had pinned the batch one."""
    from goldenmatch.spark.config_pipeline import generate_candidates, score_candidates
    from goldenmatch.spark.jvm import UDF_NAME

    src = _parity_source(spark)
    cfg = _parity_config()
    cands = generate_candidates(src, cfg, id_col="__row_id__")

    with pytest.raises(ValueError, match="scorer_shape"):
        score_candidates(
            cands, src, cfg, id_col="__row_id__", scorer_udf=UDF_NAME,
            scorer_shape="batched",
        )
