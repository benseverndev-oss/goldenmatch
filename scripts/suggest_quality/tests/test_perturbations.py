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
