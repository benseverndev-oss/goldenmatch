"""#2663: rule_no_matches must fire on a bucket-routed profile.

`candidates_compared == 0` means two different things depending on
`candidates_counted` (#2639/#2644): a MEASURED zero (blocking truly produced
no candidates -- rule_blocking_singleton_trap's territory) or an ABSENT
count (bucket never accumulates one). `rule_no_matches` read only the raw
value, so it could never fire on the bucket path -- not "rarely," never.
Bucket is the default scorer at nearly every scale (#526).
"""
from __future__ import annotations

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import RunHistory
from goldenmatch.core.autoconfig_rules import rule_no_matches
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ScoringProfile,
)


def _weighted_cfg(threshold: float = 0.8) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=threshold,
            fields=[MatchkeyField(field="org_name", scorer="jaro_winkler", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["postcode"], transforms=["strip"])],
        ),
    )


def _profile(*, candidates_compared: int, candidates_counted: bool, n_blocks: int) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=845),
        blocking=BlockingProfile(n_blocks=n_blocks, reduction_ratio=0.9 if n_blocks else 0.0),
        scoring=ScoringProfile(
            mass_above_threshold=0.0,
            n_pairs_scored=0,
            candidates_compared=candidates_compared,
            candidates_counted=candidates_counted,
        ),
    )


def test_declines_on_a_measured_zero_candidate_count():
    """Unchanged behavior: a REAL zero defers to the singleton-trap rule."""
    profile = _profile(candidates_compared=0, candidates_counted=True, n_blocks=12)
    result = rule_no_matches(profile, _weighted_cfg(), RunHistory())
    assert result is None


def test_fires_on_an_absent_candidate_count_bucket_route():
    """The regression this pins: bucket's honest candidates_counted=False
    must not be read as 'no candidates exist' -- it means 'not counted'.
    mass_above_threshold == 0 with n_blocks > 0 (blocking clearly ran) is
    real signal even without a candidate count."""
    profile = _profile(candidates_compared=0, candidates_counted=False, n_blocks=12)
    result = rule_no_matches(profile, _weighted_cfg(), RunHistory())
    assert result is not None, "rule_no_matches must fire on an absent-but-plausible bucket profile"
    new_cfg, decision = result
    assert new_cfg.get_matchkeys()[0].threshold < 0.8
