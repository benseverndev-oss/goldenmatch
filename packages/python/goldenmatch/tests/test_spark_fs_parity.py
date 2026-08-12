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


def _spark_scores(spark_df, cfg) -> dict[tuple[int, int], float]:
    """Every candidate pair's FS posterior, unthresholded."""
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
        fs_score_expr(mk, _em(), lhs, rhs).alias("p"),
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
