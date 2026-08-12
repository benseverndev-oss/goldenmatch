"""P5 unit tests: the Fellegi-Sunter math, pinned against the one-box.

No Spark needed, so these run on every PR. The pure statements
(`level_thresholds_for`, `fs_pair_weight`, `fs_posterior`) are compared to
`core.probabilistic`'s own functions rather than to hand-written numbers -- the
Spark expressions mirror these, so binding these to the one-box is what makes the
distributed score the same score.
"""
from __future__ import annotations

import math

import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import (
    EMResult,
    comparison_vector,
    fs_regular_weight_sum,
    posterior_from_weight,
    prior_weight,
)
from goldenmatch.spark.probabilistic import (
    FSSparkUnsupported,
    _validate_fs_spark_supported,
    fs_pair_weight,
    fs_posterior,
    level_thresholds_for,
)


def _mk(fields, **kw) -> MatchkeyConfig:
    return MatchkeyConfig(name="fs", type="probabilistic", fields=fields, **kw)


def _em(match_weights, *, proportion_matched=0.002) -> EMResult:
    return EMResult(
        m_probs={k: [] for k in match_weights},
        u_probs={k: [] for k in match_weights},
        match_weights=match_weights,
        converged=True,
        iterations=5,
        proportion_matched=proportion_matched,
    )


# ── level assignment ─────────────────────────────────────────────────

@pytest.mark.parametrize(
    "field_kwargs,sim,expected_level",
    [
        # 2 levels: one cut at partial_threshold
        ({"levels": 2, "partial_threshold": 0.8}, 0.79, 0),
        ({"levels": 2, "partial_threshold": 0.8}, 0.80, 1),
        # 3 levels: partial_threshold then a HARD-CODED 0.95 (not even spacing)
        ({"levels": 3, "partial_threshold": 0.7}, 0.69, 0),
        ({"levels": 3, "partial_threshold": 0.7}, 0.70, 1),
        ({"levels": 3, "partial_threshold": 0.7}, 0.94, 1),
        ({"levels": 3, "partial_threshold": 0.7}, 0.95, 2),
        # N > 3: evenly spaced k/N
        ({"levels": 4, "partial_threshold": 0.5}, 0.24, 0),
        ({"levels": 4, "partial_threshold": 0.5}, 0.25, 1),
        ({"levels": 4, "partial_threshold": 0.5}, 0.75, 3),
        # custom thresholds -- the schema stores them DESCENDING, and the
        # satisfied-count is order-independent, which is why
        # `level_thresholds_for` sorts before returning.
        ({"levels": 3, "level_thresholds": [0.9, 0.6]}, 0.59, 0),
        ({"levels": 3, "level_thresholds": [0.9, 0.6]}, 0.60, 1),
        ({"levels": 3, "level_thresholds": [0.9, 0.6]}, 0.90, 2),
    ],
)
def test_level_thresholds_reproduce_comparison_vector(
    field_kwargs, sim, expected_level
):
    """`level_thresholds_for` collapses comparison_vector's four spellings into
    one ascending list. Count-of-satisfied must equal what it assigns."""
    f = MatchkeyField(field="x", scorer="exact", **field_kwargs)
    got = sum(1 for t in level_thresholds_for(f) if sim >= t)
    assert got == expected_level


def test_level_assignment_matches_comparison_vector_end_to_end():
    """The same claim through the REAL function, so the collapse cannot drift
    from the authority it is collapsing."""
    f = MatchkeyField(field="name", scorer="exact", levels=3, partial_threshold=0.7)
    mk = _mk([f])
    for a, b, why in [
        ("smith", "smith", "identical -> top level"),
        ("smith", "jones", "different -> level 0"),
    ]:
        want = comparison_vector({"name": a}, {"name": b}, mk)[0]
        sim = 1.0 if a == b else 0.0
        got = sum(1 for t in level_thresholds_for(f) if sim >= t)
        assert got == want, why


# ── weight summation ─────────────────────────────────────────────────

def test_weight_sum_matches_fs_regular_weight_sum():
    weights = {"first": [-2.0, 4.0], "last": [-3.0, 6.0], "city": [-1.0, 2.0]}
    fields = ["first", "last", "city"]
    for vec in ([1, 1, 1], [0, 1, 0], [1, 0, 1], [0, 0, 0]):
        indexed = list(enumerate(fields))
        want = fs_regular_weight_sum(weights, vec, indexed)
        assert fs_pair_weight(vec, weights, fields) == pytest.approx(want)


def test_unobserved_field_contributes_nothing_not_the_last_weight():
    """The trap `fs_regular_weight_sum` exists to prevent.

    `weights[-1]` in Python is the LAST element -- the highest-agreement weight
    -- so an unobserved field would supply maximal evidence FOR a match. It must
    contribute exactly zero instead.
    """
    weights = {"first": [-2.0, 4.0], "last": [-3.0, 6.0]}
    fields = ["first", "last"]

    got = fs_pair_weight([1, -1], weights, fields)
    assert got == 4.0, "unobserved `last` must add nothing"
    # what the bug would have produced: weights['last'][-1] == 6.0
    assert got != 4.0 + 6.0

    indexed = list(enumerate(fields))
    assert got == pytest.approx(fs_regular_weight_sum(weights, [1, -1], indexed))


# ── posterior ────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "total_weight", [-100.0, -20.0, -8.0, -1.0, 0.0, 1.0, 8.0, 20.0, 100.0]
)
def test_posterior_matches_the_one_box(total_weight):
    prior_w = prior_weight(0.002)
    assert fs_posterior(total_weight, prior_w) == pytest.approx(
        posterior_from_weight(total_weight, prior_w), abs=1e-12
    )


def test_posterior_is_clamped_rather_than_overflowing():
    """Without the clamp `2^-logodds` overflows to inf and the row becomes NaN
    -- and NaN compares false against every threshold, so a strongly-rejected
    pair would VANISH rather than be rejected."""
    prior_w = prior_weight(0.5)
    assert fs_posterior(1e6, prior_w) == 1.0
    assert fs_posterior(-1e6, prior_w) == 0.0
    assert not math.isnan(fs_posterior(-1e6, prior_w))


def test_no_evidence_leaves_the_posterior_at_the_prior():
    """The cleanest statement of what `unobserved` means.

    A pair where every field is unobserved contributes zero bits, so the
    posterior must be exactly the prior match rate -- absence of evidence moves
    nothing. It is also a sharp check on the whole chain: prior_weight and
    posterior_from_weight are inverses, so any sign or base error shows up here.
    """
    for rate in (0.002, 0.05, 0.5):
        assert fs_posterior(0.0, prior_weight(rate)) == pytest.approx(rate, abs=1e-12)


def test_threshold_is_the_link_not_the_review():
    """`resolve_thresholds` returns (link, review) -- link FIRST.

    Destructuring it the other way cuts at the review threshold, which is
    clamped <= link by construction, so every pair the one-box would have queued
    for a human gets auto-linked instead. It looks like nothing more than a
    slightly generous run. (This bug was written and caught here.)
    """
    from goldenmatch.core.probabilistic import resolve_thresholds
    from goldenmatch.spark.config_pipeline import _matchkey_threshold

    mk = _mk([MatchkeyField(field="first", scorer="jaro_winkler")])
    em = _em({"first": [-2.0, 4.0]})
    link, review = resolve_thresholds(mk, em)

    assert review <= link, "review must never exceed link"
    assert _matchkey_threshold(mk, em) == pytest.approx(link)
    if review < link:
        assert _matchkey_threshold(mk, em) != pytest.approx(review)


def test_prior_weight_is_negative_for_a_rare_match_rate():
    """Sanity on the direction: a 0.2% within-block match rate is evidence a
    pair must overcome, so the prior is strongly negative."""
    assert prior_weight(0.002) < -8.0


# ── the feature gate ─────────────────────────────────────────────────

def test_term_frequency_adjustment_is_refused():
    f = MatchkeyField(field="last", scorer="jaro_winkler", tf_adjustment=True)
    with pytest.raises(FSSparkUnsupported, match="term-frequency"):
        _validate_fs_spark_supported(_mk([f]), _em({"last": [-1.0, 1.0]}))


def test_negative_evidence_is_refused():
    from goldenmatch.config.schemas import NegativeEvidenceField

    mk = _mk(
        [MatchkeyField(field="last", scorer="jaro_winkler")],
        negative_evidence=[
            NegativeEvidenceField(
                field="dob", scorer="exact", threshold=0.9, penalty_bits=2.0
            )
        ],
    )
    with pytest.raises(FSSparkUnsupported, match="negative evidence"):
        _validate_fs_spark_supported(mk, _em({"last": [-1.0, 1.0]}))


@pytest.mark.parametrize(
    "scorer,extra",
    [("embedding", {}), ("record_embedding", {"columns": ["bio", "notes"]})],
)
def test_model_backed_scorers_are_refused(scorer, extra):
    f = MatchkeyField(field="bio", scorer=scorer, **extra)
    with pytest.raises(FSSparkUnsupported, match=scorer):
        _validate_fs_spark_supported(_mk([f]), _em({"bio": [-1.0, 1.0]}))


def test_a_model_trained_on_other_fields_is_refused():
    """A model whose weights do not cover the matchkey's fields would score
    every pair on a subset and look plausible doing it."""
    mk = _mk([
        MatchkeyField(field="first", scorer="jaro_winkler"),
        MatchkeyField(field="last", scorer="jaro_winkler"),
    ])
    with pytest.raises(ValueError, match="no match weights for"):
        _validate_fs_spark_supported(mk, _em({"first": [-1.0, 1.0]}))


def test_a_supported_fs_config_passes():
    """A gate that refuses everything would pass every refusal test above."""
    mk = _mk([
        MatchkeyField(field="first", scorer="jaro_winkler"),
        MatchkeyField(field="last", scorer="levenshtein"),
    ])
    _validate_fs_spark_supported(
        mk, _em({"first": [-2.0, 4.0], "last": [-3.0, 6.0]})
    )


# ── model resolution ─────────────────────────────────────────────────

def test_probabilistic_without_a_model_says_what_to_do():
    """The tier does not train on demand; the message must say why and how."""
    from goldenmatch.spark.probabilistic import resolve_fs_model

    mk = _mk([MatchkeyField(field="first", scorer="jaro_winkler")])
    with pytest.raises(ValueError, match="model_path"):
        resolve_fs_model(mk)


def test_a_missing_model_file_is_reported_as_missing(tmp_path):
    from goldenmatch.spark.probabilistic import resolve_fs_model

    mk = _mk([MatchkeyField(field="first", scorer="jaro_winkler")])
    with pytest.raises(FileNotFoundError):
        resolve_fs_model(mk, model_path=str(tmp_path / "nope.json"))


def test_a_saved_model_round_trips(tmp_path):
    import json

    from goldenmatch.spark.probabilistic import resolve_fs_model

    em = _em({"first": [-2.0, 4.0]})
    p = tmp_path / "model.json"
    p.write_text(json.dumps(em.to_dict()), encoding="utf-8")

    mk = _mk([MatchkeyField(field="first", scorer="jaro_winkler")])
    loaded = resolve_fs_model(mk, model_path=str(p))
    assert loaded.match_weights == em.match_weights
    assert loaded.proportion_matched == pytest.approx(em.proportion_matched)
