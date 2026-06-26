"""Unit tests for the suggest_quality perturbation catalog.

Fast: pure config -> config mutation, no native kernel, no pipeline.
"""
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)

from scripts.suggest_quality import perturbations
from scripts.suggest_quality.perturbations import CATALOG, get


def _config_with_threshold(threshold: float) -> GoldenMatchConfig:
    """A minimal config with one weighted matchkey at the given threshold."""
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_match",
                type="weighted",
                threshold=threshold,
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
            )
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["name"])],
        ),
    )


def test_far_too_high_in_catalog():
    p = get("threshold_far_too_high")
    assert p.expected_rule == "lower_threshold"
    assert p.builds_on_existing_rule is True
    assert p in CATALOG


def test_far_too_high_overshoots_valley():
    cfg = _config_with_threshold(0.80)
    out = perturbations._apply_threshold_far_too_high(cfg)
    # 0.80 + 0.18 = 0.98 -- well beyond the ~0.875 valley (>= 0.95).
    assert out.get_matchkeys()[0].threshold == 0.98
    assert out.get_matchkeys()[0].threshold >= 0.95


def test_far_too_high_capped_at_ceiling():
    cfg = _config_with_threshold(0.90)
    out = perturbations._apply_threshold_far_too_high(cfg)
    # 0.90 + 0.18 = 1.08 -> capped at 0.99.
    assert out.get_matchkeys()[0].threshold == 0.99


def test_far_too_high_does_not_mutate_input():
    cfg = _config_with_threshold(0.80)
    perturbations._apply_threshold_far_too_high(cfg)
    assert cfg.get_matchkeys()[0].threshold == 0.80  # original untouched


def test_far_too_high_no_primary_mk_returns_unchanged():
    # A config with no weighted/fuzzy matchkey -> guard returns config unchanged.
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_only",
                type="exact",
                fields=[MatchkeyField(field="email")],
            )
        ],
    )
    out = perturbations._apply_threshold_far_too_high(cfg)
    assert out is not None
    assert perturbations._applies_threshold_too_high(cfg) is False


def test_near_valley_threshold_nudges_just_below():
    cfg = _config_with_threshold(0.80)
    out = perturbations._apply_near_valley_threshold(cfg)
    assert out.get_matchkeys()[0].threshold == 0.75   # 0.80 - 0.05
    assert cfg.get_matchkeys()[0].threshold == 0.80   # input untouched


def test_over_merge_bait_lowers_hard_floor_050():
    cfg = _config_with_threshold(0.70)
    out = perturbations._apply_over_merge_bait(cfg)
    assert out.get_matchkeys()[0].threshold == 0.50   # max(0.50, 0.70 - 0.30)
    assert cfg.get_matchkeys()[0].threshold == 0.70


def test_adversarial_perturbations_in_catalog():
    names = {p.name for p in CATALOG}
    assert {"near_valley_threshold", "over_merge_bait"} <= names
