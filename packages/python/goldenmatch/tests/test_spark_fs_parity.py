"""P5 parity gate: distributed FS scoring must equal the one-box's FS scoring.

Runs in the Spark lanes; skips where no Spark Connect client is installed.

The reference is `core.probabilistic`'s own functions -- `comparison_vector`,
`fs_regular_weight_sum`, `posterior_from_weight` -- called directly on the same
rows. That is the point: the claim is not "the Spark path produces sensible
probabilities", it is "the Spark path produces THE MODEL'S probabilities". Only
comparing against the implementation that defines them can establish that.

The model here is hand-built rather than trained, so the weights are known
constants and a divergence points at the expression rather than at EM.
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
from goldenmatch.core.probabilistic import (  # noqa: E402
    EMResult,
    comparison_vector,
    fs_regular_weight_sum,
    posterior_from_weight,
    prior_weight,
)

_ID = "__row_id__"
_COLS = [_ID, "first", "last", "city"]

# Nulls again deliberate: row 4 is missing `last`, so its comparison with row 3
# is UNOBSERVED and must contribute zero -- not `weights[-1]`, the highest
# agreement weight, which is the trap `fs_regular_weight_sum` guards.
_ROWS = [
    (0, "jon", "smith", "york"),
    (1, "john", "smith", "york"),
    (2, "jonathan", "smyth", "york"),
    (3, "amy", "wong", "leeds"),
    (4, "amy", None, "leeds"),
    (5, None, None, "leeds"),
]

# Deliberately asymmetric: `last` carries three times the agreement evidence of
# `first`, so a bug that swaps or drops a field moves the score visibly.
_WEIGHTS = {"first": [-2.0, 4.0], "last": [-3.0, 9.0]}
_PROPORTION_MATCHED = 0.05


def _mk(missing: str = "unobserved") -> MatchkeyConfig:
    return MatchkeyConfig(
        name="fs",
        type="probabilistic",
        missing=missing,
        fields=[
            MatchkeyField(
                field="first", scorer="jaro_winkler", levels=2, partial_threshold=0.8
            ),
            MatchkeyField(
                field="last", scorer="jaro_winkler", levels=2, partial_threshold=0.8
            ),
        ],
    )


def _em() -> EMResult:
    return EMResult(
        m_probs={k: [] for k in _WEIGHTS},
        u_probs={k: [] for k in _WEIGHTS},
        match_weights=dict(_WEIGHTS),
        converged=True,
        iterations=7,
        proportion_matched=_PROPORTION_MATCHED,
    )


def _config(missing: str = "unobserved") -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[_mk(missing)],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
    )


def _reference_posterior(row_a: dict, row_b: dict, mk) -> float:
    """The one-box's answer for one pair, via its own functions."""
    vec = comparison_vector(row_a, row_b, mk)
    indexed = [(i, f.resolved_field) for i, f in enumerate(mk.fields)]
    total = fs_regular_weight_sum(_WEIGHTS, vec, indexed)
    return posterior_from_weight(total, prior_weight(_PROPORTION_MATCHED))


@pytest.fixture()
def source(spark):
    return spark.createDataFrame(_ROWS, _COLS)


def _spark_scores(spark_df, cfg, *, scorer_udf=None, transform_udf=None) -> dict[tuple[int, int], float]:
    """Every candidate pair's FS posterior, unthresholded.

    ``scorer_udf`` routes the per-field similarity to the jar's row-shaped
    kernel instead of the arrow_udf, which is the ONLY part of Spark FS that
    ever needed a Python worker.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates
    from goldenmatch.spark.probabilistic import fs_score_expr
    from pyspark.sql import functions as F

    mk = cfg.get_matchkeys()[0]
    cands = generate_candidates(spark_df, cfg, id_col=_ID)
    lhs, rhs = "__lhs__", "__rhs__"
    joined = (
        cands.alias("__cand__")
        .join(spark_df.alias(lhs), F.col(f"{lhs}.{_ID}") == F.col("__cand__.a"))
        .join(spark_df.alias(rhs), F.col(f"{rhs}.{_ID}") == F.col("__cand__.b"))
    )
    out = joined.select(
        F.col("__cand__.a").alias("a"),
        F.col("__cand__.b").alias("b"),
        fs_score_expr(
            mk, _em(), lhs, rhs,
            scorer_udf=scorer_udf, transform_udf=transform_udf,
        ).alias("p"),
    )
    return {(int(r["a"]), int(r["b"])): float(r["p"]) for r in out.collect()}


@pytest.mark.parametrize("missing", ["unobserved", "disagree"])
def test_fs_posterior_matches_the_one_box(source, missing):
    """The load-bearing assertion, under BOTH missing-value semantics.

    `missing` is a config choice in the one-box because whether missingness is
    informative depends on the data, so a distributed path that honoured only
    one of them would be right for half its users.
    """
    cfg = _config(missing)
    mk = cfg.get_matchkeys()[0]
    rows = {r[0]: dict(zip(_COLS, r)) for r in _ROWS}

    got = _spark_scores(source, cfg)
    assert got, "no candidate pairs were scored"

    for (a, b), p in got.items():
        want = _reference_posterior(rows[a], rows[b], mk)
        assert p == pytest.approx(want, abs=1e-9), (
            f"pair ({a},{b}) under missing={missing!r}: Spark {p} vs one-box {want}"
        )


def test_an_unobserved_field_does_not_add_the_top_weight(source):
    """Rows 3 and 4 agree on `first` and row 4 has no `last`.

    Under `unobserved`, `last` contributes nothing, so the total is `first`'s
    agreement weight alone. The bug this guards would index `weights[-1]` and
    add 9.0 -- the strongest possible evidence FOR a match -- on the basis that
    a value is missing.
    """
    cfg = _config("unobserved")
    got = _spark_scores(source, cfg)
    p = got[(3, 4)]

    want_total = _WEIGHTS["first"][1]  # agreement on `first` only
    want = posterior_from_weight(want_total, prior_weight(_PROPORTION_MATCHED))
    assert p == pytest.approx(want, abs=1e-9)

    wrong = posterior_from_weight(
        want_total + _WEIGHTS["last"][1], prior_weight(_PROPORTION_MATCHED)
    )
    assert p != pytest.approx(wrong, abs=1e-9), (
        "an unobserved field supplied the top agreement weight"
    )


def test_missing_disagree_mode_differs_from_unobserved(source):
    """The two modes must actually DIFFER on a pair with a missing field --
    otherwise honouring both proves nothing."""
    unobs = _spark_scores(source, _config("unobserved"))
    disag = _spark_scores(source, _config("disagree"))
    assert unobs[(3, 4)] != pytest.approx(disag[(3, 4)], abs=1e-9), (
        "missing='disagree' scored identically to 'unobserved'; the mode is "
        "not reaching the expression"
    )
    # disagree treats the absence as evidence AGAINST, so it must score lower.
    assert disag[(3, 4)] < unobs[(3, 4)]


def test_end_to_end_probabilistic_pipeline_runs(source, tmp_path):
    """A probabilistic config runs through `run_config_pipeline` with a model
    on disk -- the shape a Splink import produces."""
    import json

    from goldenmatch.spark.config_pipeline import run_config_pipeline

    model = tmp_path / "fs_model.json"
    model.write_text(json.dumps(_em().to_dict()), encoding="utf-8")

    golden = run_config_pipeline(
        source,
        _config(),
        id_col=_ID,
        golden_cols=["first", "last", "city"],
        fs_model_path=str(model),
    )
    rows = golden.collect()
    assert set(golden.columns) == {"cluster_id", "first", "last", "city"}
    # rows 0/1/2 share a city block and agree strongly on `last`; they must
    # cluster rather than the run merely completing.
    assert rows, "no golden records produced from the probabilistic run"


def test_probabilistic_without_a_model_fails_before_any_spark_work(source):
    """The message must arrive as a config error, not as a failed distributed
    job -- so it is raised before the blocking self-join is submitted."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    with pytest.raises(ValueError, match="model_path"):
        run_config_pipeline(source, _config(), id_col=_ID, golden_cols=["first"])


# ── Fellegi-Sunter, in the executor JVM ──────────────────────────────
#
# The Spark tier ran FS with the per-field similarity on an arrow_udf, which
# means a Python worker on every executor. Everything AROUND that call -- the
# level ladder, the weight lookup, the bit sum, the posterior -- was already
# Spark SQL. So routing one call to the jar is the whole of what it takes to run
# the thing Splink does with nothing installed on the cluster.
#
# EM TRAINING still does not distribute (driver-side sample of blocked pairs);
# these tests supply a trained model, which is the shipped contract.

def _jar():
    from goldenmatch.spark.jvm import JvmScorerUnavailable, find_jar

    try:
        return find_jar()
    except JvmScorerUnavailable as exc:
        pytest.skip(f"no JVM scorer jar built: {exc}")


@pytest.fixture()
def jvm_registered(spark):
    from goldenmatch.spark.jvm import JvmScorerUnavailable, install

    try:
        return install(spark, jar=_jar())
    except JvmScorerUnavailable as exc:
        pytest.skip(f"cannot register the JVM scorer: {exc}")


@pytest.mark.parametrize("missing", ["unobserved", "disagree"])
def test_fs_in_the_jvm_matches_the_python_path_exactly(source, jvm_registered, missing):
    """THE gate. Same posterior, pair for pair, with no Python on the executor.

    Exact equality, no tolerance: both sides run the same Rust `score_one` over
    the same bytes and everything downstream of it is the same SQL. A tolerance
    would hide the class of bug this exists to catch, because those bugs produce
    PLAUSIBLE probabilities -- a level ladder read one threshold off still
    yields a number in [0, 1] for every pair.
    """
    from goldenmatch.spark.jvm import ROW_UDF_NAME, TRANSFORM_UDF_NAME

    cfg = _config(missing)
    want = _spark_scores(source, cfg)
    got = _spark_scores(
        source, cfg, scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME
    )

    assert set(got) == set(want), (
        f"different pair sets: only-jvm={sorted(set(got) - set(want))} "
        f"only-python={sorted(set(want) - set(got))}"
    )
    mismatched = {k: (got[k], want[k]) for k in want if got[k] != want[k]}
    assert not mismatched, (
        f"missing={missing}: JVM and Python FS disagree. Both reach the same "
        f"score_one and the level/weight/posterior arithmetic is the same SQL, "
        f"so a difference is a level read at the wrong threshold or a weight "
        f"from the wrong index -- (jvm, python): {mismatched}"
    )
    assert want, "no pairs scored; the fixture proves nothing"


@pytest.mark.parametrize("missing", ["unobserved", "disagree"])
def test_fs_in_the_jvm_matches_the_ONE_BOX(source, jvm_registered, missing):
    """And against the one-box directly, not only against the Spark path.

    Transitivity would give this, but stating it means a change that moved the
    Spark FS expression and the JVM route together -- they share every line
    except the similarity call -- cannot pass while both have drifted off the
    reference implementation.
    """
    from goldenmatch.spark.jvm import ROW_UDF_NAME, TRANSFORM_UDF_NAME

    cfg = _config(missing)
    mk = cfg.get_matchkeys()[0]
    got = _spark_scores(
        source, cfg, scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME
    )

    by_id = {r[0]: dict(zip(_COLS, r)) for r in _ROWS}
    for (a, b), p in got.items():
        want = _reference_posterior(by_id[a], by_id[b], mk)
        assert p == pytest.approx(want, abs=1e-12), (
            f"pair ({a}, {b}): JVM FS returned {p!r}, the one-box {want!r}"
        )


def test_the_end_to_end_probabilistic_pipeline_runs_jar_only(source, jvm_registered, tmp_path):
    """A whole FS dedupe with the scorer, transforms and survivorship in the jar.

    This is the claim in one test: Fellegi-Sunter, distributed, with nothing
    goldenmatch-shaped installed on an executor.
    """
    import json

    from goldenmatch.spark.config_pipeline import run_config_pipeline
    from goldenmatch.spark.jvm import SURVIVORSHIP_UDF_NAME, TRANSFORM_UDF_NAME, UDF_NAME

    model = tmp_path / "fs_model.json"
    model.write_text(json.dumps(_em().to_dict()), encoding="utf-8")

    out = run_config_pipeline(
        source, _config(), id_col=_ID,
        golden_cols=["first", "last", "city"],
        fs_model_path=str(model),
        scorer_udf=UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
        survivorship_udf=SURVIVORSHIP_UDF_NAME,
    )
    rows = out.collect()
    assert rows, "the FS pipeline produced no golden records"


def test_the_batched_shape_still_refuses_fs(source, jvm_registered):
    """The batch shape has no FS combine and must say so rather than guess.

    Its scores are per-SLOT in flat arrays; a level is a threshold ladder over
    ONE field's similarity and the weights sum across fields. Expressing that
    over the reshaped arrays would be a second implementation of the FS combine.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates, score_candidates
    from goldenmatch.spark.jvm import UDF_NAME

    cfg = _config()
    cands = generate_candidates(source, cfg, id_col=_ID)
    with pytest.raises(NotImplementedError, match="Fellegi-Sunter"):
        score_candidates(
            cands, source, cfg, id_col=_ID, scorer_udf=UDF_NAME,
            scorer_shape="batch",
        )


# ── agreement-pattern counts, computed on the cluster ────────────────

def _driver_pattern_counts(spark_df, cfg):
    """The same counts, derived on the driver with the ONE-BOX's functions.

    `comparison_vector` is what `_build_comparison_matrix` calls, so this is the
    reference the trained model would have been built from -- not a
    reimplementation of the level ladder, which would only prove the two
    reimplementations agree.
    """
    from collections import Counter

    from goldenmatch.core.probabilistic import comparison_vector
    from goldenmatch.spark.config_pipeline import generate_candidates

    mk = cfg.get_matchkeys()[0]
    by_id = {r[0]: dict(zip(_COLS, r)) for r in _ROWS}
    counts = Counter()
    for r in generate_candidates(spark_df, cfg, id_col=_ID).collect():
        vec = comparison_vector(by_id[int(r["a"])], by_id[int(r["b"])], mk)
        counts[tuple(int(v) for v in vec)] += 1
    return sorted(counts.items())


def _spark_pattern_counts(spark_df, cfg, **kw):
    from goldenmatch.spark.config_pipeline import generate_candidates
    from goldenmatch.spark.em import agreement_pattern_counts
    from pyspark.sql import functions as F

    lhs, rhs = "__lhs__", "__rhs__"
    joined = (
        generate_candidates(spark_df, cfg, id_col=_ID).alias("__cand__")
        .join(spark_df.alias(lhs), F.col(f"{lhs}.{_ID}") == F.col("__cand__.a"))
        .join(spark_df.alias(rhs), F.col(f"{rhs}.{_ID}") == F.col("__cand__.b"))
    )
    return agreement_pattern_counts(
        joined, cfg.get_matchkeys()[0], lhs=lhs, rhs=rhs, **kw
    )


@pytest.mark.parametrize("missing", ["unobserved", "disagree"])
def test_pattern_counts_match_the_one_box_comparison_vectors(source, missing):
    """THE gate for distributed training input.

    If these counts are wrong, `train_em(pair_weights=counts)` trains on a
    population that does not exist -- and it would converge happily and produce
    a plausible model, because a wrong count is still a positive integer.
    """
    cfg = _config(missing)
    assert _spark_pattern_counts(source, cfg) == _driver_pattern_counts(source, cfg)


def test_the_counts_sum_to_the_candidate_pair_count(source):
    """No pair may be dropped or double-counted by the GROUP BY.

    Stated separately from the vector comparison above because the two fail
    differently: a wrong LEVEL shows up there, a lost PAIR shows up here, and a
    pair lost to a null-handling bug in the ladder could leave every surviving
    pattern correct.
    """
    from goldenmatch.spark.config_pipeline import generate_candidates

    cfg = _config()
    want = generate_candidates(source, cfg, id_col=_ID).count()
    got = sum(c for _, c in _spark_pattern_counts(source, cfg))
    assert got == want


def test_pattern_counts_are_identical_jar_only(source, jvm_registered):
    """And with no Python on the executors, which is the point of computing
    them out there at all: a training run that needed an executor virtualenv
    would leave the jar-only claim only half true."""
    from goldenmatch.spark.jvm import ROW_UDF_NAME, TRANSFORM_UDF_NAME

    cfg = _config()
    assert _spark_pattern_counts(
        source, cfg, scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME
    ) == _driver_pattern_counts(source, cfg)


def test_an_oversized_pattern_space_is_refused_not_collected(source):
    """`collect()` of an unexpectedly large frame is a driver OOM, which is the
    failure this whole path exists to avoid -- so the bound is checked against
    the real row count before anything is materialised."""
    cfg = _config()
    with pytest.raises(ValueError, match="max_patterns"):
        _spark_pattern_counts(source, cfg, max_patterns=1)
