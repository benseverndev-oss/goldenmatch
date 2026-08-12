"""P4 unit tests: the parts of the config-driven Spark tier that need no Spark.

The weighted combine and the feature gate are pure Python, so they are pinned
here rather than only in the Spark lanes -- these run on every PR, not just the
ones that touch the tier.

The combine is tested AGAINST ``core.scorer.score_pair`` rather than against
hand-written expected numbers. Hand-written numbers would pass while drifting
from the one-box; comparing to the real implementation is what makes this a
parity test.
"""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.scorer import score_field, score_pair
from goldenmatch.spark.config_pipeline import (
    _validate_spark_config_supported,
    weighted_pair_score,
)


def _mk(fields, *, name="mk", type="weighted", threshold=0.85, **kw):
    return MatchkeyConfig(
        name=name, type=type, fields=fields, threshold=threshold, **kw
    )


def _cfg(matchkeys, *, blocking=None, **kw):
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=blocking
        or BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
        **kw,
    )


_FIELDS = [
    MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
    MatchkeyField(field="last", scorer="jaro_winkler", weight=2.0),
    MatchkeyField(field="city", scorer="levenshtein", weight=0.5),
]


# ── the weighted combine vs score_pair ───────────────────────────────

@pytest.mark.parametrize(
    "row_a,row_b",
    [
        # all present
        ({"first": "jon", "last": "smith", "city": "york"},
         {"first": "john", "last": "smyth", "city": "york"}),
        # one field missing on one side -> excluded from BOTH num and den
        ({"first": "jon", "last": "smith", "city": None},
         {"first": "john", "last": "smyth", "city": "york"}),
        # the HEAVY field missing: the denominator changes most here, which is
        # exactly the case a fixed total-weight denominator would get wrong.
        ({"first": "jon", "last": None, "city": "york"},
         {"first": "john", "last": "smyth", "city": "york"}),
        # missing on both sides
        ({"first": "jon", "last": None, "city": "york"},
         {"first": "john", "last": None, "city": "york"}),
        # nothing comparable at all
        ({"first": None, "last": None, "city": None},
         {"first": None, "last": None, "city": None}),
        # identical
        ({"first": "amy", "last": "wong", "city": "leeds"},
         {"first": "amy", "last": "wong", "city": "leeds"}),
    ],
)
def test_weighted_combine_matches_score_pair(row_a, row_b):
    """``weighted_pair_score`` must reproduce ``score_pair`` exactly."""
    per_field = [
        score_field(row_a[f.resolved_field], row_b[f.resolved_field], f.fuzzy_scorer)
        for f in _FIELDS
    ]
    got = weighted_pair_score(per_field, [f.fuzzy_weight for f in _FIELDS])
    want = score_pair(row_a, row_b, _FIELDS)
    assert got == pytest.approx(want, abs=1e-12), (
        f"combine drifted from score_pair: {got} vs {want}"
    )


def test_missing_field_is_excluded_from_the_denominator():
    """The property the parametrization above proves by comparison, stated
    directly so a reader sees WHY it matters.

    A pair agreeing perfectly on the one field it can compare scores 1.0, not
    1.0 * w / total_weight. Using the matchkey's total weight as a fixed
    denominator would score this 0.29 and drop it below every threshold.
    """
    scores = [None, 1.0, None]
    weights = [1.0, 2.0, 0.5]
    assert weighted_pair_score(scores, weights) == 1.0
    # the wrong answer a fixed denominator gives, pinned so the contrast is not
    # left to the reader's arithmetic
    assert 1.0 * 2.0 / sum(weights) == pytest.approx(0.5714, abs=1e-4)


def test_nothing_comparable_scores_zero_not_nan():
    assert weighted_pair_score([None, None], [1.0, 2.0]) == 0.0


# ── the null-vs-null trap ────────────────────────────────────────────

def test_two_records_missing_the_field_are_not_a_perfect_match():
    """The defect this pipeline had to route around.

    ``spark.scorers.score_batch`` maps a missing value to ``""`` and therefore
    scores null-vs-null as a PERFECT 1.0 -- so two records whose only shared
    evidence is that both are missing the field would merge at ANY threshold.
    The one-box excludes the field instead, and scores the pair 0.0.

    This asserts the ONE-BOX semantics, which the Spark expression reproduces by
    deciding comparability from the raw columns rather than from the kernel's
    output. If the kernel is ever fixed at source, this test still passes.
    """
    assert score_field(None, None, "jaro_winkler") is None
    assert score_pair({"first": None}, {"first": None},
                      [MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)]) == 0.0

    from goldenmatch.spark.scorers import score_batch

    raw = score_batch("jaro_winkler", [None], [None])[0]
    assert raw == 1.0, (
        "the raw kernel still scores null-vs-null as 1.0; the tier must keep "
        "deciding comparability from the inputs, not from this value"
    )


# ── the feature gate ─────────────────────────────────────────────────

def test_probabilistic_matchkey_is_accepted_since_p5():
    """P4a refused this; P5 executes it. The config gate now passes it, and the
    remaining requirement -- a TRAINED model -- is enforced separately, because
    only the model can tell you whether it matches the field set."""
    cfg = _cfg([
        _mk([MatchkeyField(field="first", scorer="jaro_winkler")],
            type="probabilistic", threshold=None)
    ])
    _validate_spark_config_supported(cfg)  # must not raise


def test_probabilistic_field_without_a_scorer_is_refused():
    """Defence in depth. MatchkeyConfig's own validator already requires a
    scorer on probabilistic fields, so this is only reachable when a caller has
    bypassed it -- `model_construct`, or mutation after construction. The
    one-box guards the same invariant the same way (its typed accessors assert
    rather than assume), and the payoff is identical: the crash names the
    matchkey and field instead of surfacing as a None deep inside a UDF on a
    worker.
    """
    mk = MatchkeyConfig.model_construct(
        name="mk",
        type="probabilistic",
        threshold=None,
        fields=[MatchkeyField.model_construct(field="first", scorer=None)],
    )
    cfg = GoldenMatchConfig.model_construct(
        matchkeys=[mk],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["city"])]),
    )
    with pytest.raises(ValueError, match="needs a scorer"):
        _validate_spark_config_supported(cfg)


@pytest.mark.parametrize(
    "kwargs,pattern",
    [
        ({"llm_boost": True}, "LLM"),
        ({"llm_auto": True}, "LLM"),
    ],
)
def test_unsupported_top_level_features_are_refused(kwargs, pattern):
    cfg = _cfg([_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])], **kwargs)
    with pytest.raises(NotImplementedError, match=pattern):
        _validate_spark_config_supported(cfg)


def test_negative_evidence_is_refused():
    from goldenmatch.config.schemas import NegativeEvidenceField

    cfg = _cfg([
        _mk(
            [MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)],
            negative_evidence=[
            NegativeEvidenceField(
                field="dob", scorer="exact", threshold=0.9, penalty=0.5
            )
        ],
        )
    ])
    with pytest.raises(NotImplementedError, match="negative evidence"):
        _validate_spark_config_supported(cfg)


def test_guarded_matchkey_is_refused():
    cfg = _cfg([
        _mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)],
            guard="a_state == b_state")
    ])
    with pytest.raises(NotImplementedError, match="guard"):
        _validate_spark_config_supported(cfg)


def test_non_static_blocking_is_refused_not_silently_run_as_static():
    cfg = _cfg(
        [_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["city"])],
            strategy="sorted_neighborhood",
        ),
    )
    with pytest.raises(NotImplementedError, match="sorted_neighborhood"):
        _validate_spark_config_supported(cfg)


def test_missing_blocking_is_refused_rather_than_cross_joined():
    """No blocking means an N^2 self-join. Refusing beats attempting.

    Reachable only via an EXACT-only config: GoldenMatchConfig's own validator
    already requires blocking once any matchkey is weighted or probabilistic,
    so this gate covers the gap that validator leaves open.
    """
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="e", type="exact", fields=[MatchkeyField(field="email")])
        ],
        blocking=None,
    )
    with pytest.raises(ValueError, match="blocking"):
        _validate_spark_config_supported(cfg)


def test_a_supported_config_passes_the_gate():
    """A gate that refuses everything would 'pass' every refusal test while
    disabling the feature entirely."""
    cfg = _cfg([
        _mk([
            MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="last", scorer="levenshtein", weight=2.0),
        ]),
        _mk([MatchkeyField(field="email")], name="exact_email", type="exact",
            threshold=None),
    ])
    _validate_spark_config_supported(cfg)  # must not raise


# ── golden rules ─────────────────────────────────────────────────────

def test_conditional_golden_rules_are_refused():
    from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig
    from goldenmatch.spark.config_pipeline import _field_strategy

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": [
                GoldenFieldRule(
                    strategy="most_recent",
                    date_column="updated_at",
                    when="source == 'crm'",
                ),
                GoldenFieldRule(strategy="most_complete"),
            ]
        },
    )
    with pytest.raises(NotImplementedError, match="conditional"):
        _field_strategy(rules, "name")


def test_per_field_strategy_resolves_and_falls_back_to_default():
    from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig
    from goldenmatch.spark.config_pipeline import _field_strategy

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"name": GoldenFieldRule(strategy="longest_value")},
    )
    assert _field_strategy(rules, "name") == "longest_value"
    assert _field_strategy(rules, "other") == "most_complete"


# ── multi_pass blocking ──────────────────────────────────────────────

def test_multi_pass_reads_passes_not_keys():
    """The recall-loss case, found by P6's lane test against a REAL auto-config
    output: `strategy=multi_pass` with ONE entry in `keys` and FIVE in `passes`.

    Reading `keys` would generate candidates from one pass out of five and
    silently drop the rest -- a large recall loss that looks like a clean run.
    """
    from goldenmatch.spark.config_pipeline import blocking_passes

    cfg = _cfg(
        [_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city"])],
            passes=[
                BlockingKeyConfig(fields=["city"]),
                BlockingKeyConfig(fields=["last"], transforms=["lowercase", "soundex"]),
                BlockingKeyConfig(fields=["first"], transforms=["lowercase"]),
            ],
        ),
    )
    got = blocking_passes(cfg)
    assert len(got) == 3, f"expected all 3 passes, got {len(got)}"
    assert [p.fields for p in got] == [["city"], ["last"], ["first"]]


def test_static_still_reads_keys():
    """A guard that always read `passes` would break every static config."""
    from goldenmatch.spark.config_pipeline import blocking_passes

    cfg = _cfg(
        [_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["city"]),
                  BlockingKeyConfig(fields=["email"])],
        ),
    )
    assert [p.fields for p in blocking_passes(cfg)] == [["city"], ["email"]]


def test_multi_pass_without_passes_falls_back_to_keys():
    """The schema permits a multi_pass config carrying only `keys`."""
    from goldenmatch.spark.config_pipeline import blocking_passes

    cfg = _cfg(
        [_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(
            strategy="multi_pass", keys=[BlockingKeyConfig(fields=["city"])]
        ),
    )
    assert [p.fields for p in blocking_passes(cfg)] == [["city"]]


def test_multi_pass_passes_the_gate():
    """It is what auto-config emits; refusing it made zero-config unusable on
    the tier (P6's lane test caught exactly that)."""
    cfg = _cfg(
        [_mk([MatchkeyField(field="first", scorer="jaro_winkler", weight=1.0)])],
        blocking=BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["city"])],
            passes=[BlockingKeyConfig(fields=["city"])],
        ),
    )
    _validate_spark_config_supported(cfg)  # must not raise


def test_nan_is_not_a_string_and_must_not_reach_the_scorer():
    """The crash an all-null batch caused, pinned at value level.

    A string column whose batch is entirely null arrives from Spark as float64,
    so iterating yields NaN rather than None -- and `x or ""` does not rescue it
    because NaN is TRUTHY. The float reached the scorer and raised
    `TypeError: 'float' object is not subscriptable`, failing the whole job.

    No pandas needed to state the invariant: NaN must never be handed to the
    scorer. `make_scorer_udf` normalizes it at the pandas boundary.
    """
    from goldenmatch.core import strsim

    nan = float("nan")
    assert bool(nan) is True, "NaN is truthy, which is why `x or ''` fails"
    with pytest.raises(TypeError):
        strsim.jaro_winkler_normalized_similarity(nan, nan)
    # None IS handled by the pure floor (mapped to ""), so normalizing to None
    # is sufficient -- no separate null branch is needed downstream.
    from goldenmatch.spark.scorers import score_batch

    assert score_batch("jaro_winkler", [None], [None])[0] == 1.0
