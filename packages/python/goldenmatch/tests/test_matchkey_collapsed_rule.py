"""A matchkey field with post-transform cardinality 0.0 is RED and nothing answered it.

Such a field has one distinct value, so every pair scores identically on it: it
carries weight and contributes no discrimination. Both rules that read
`profile.matchkey.per_field` before this one (`rule_unimodal_scoring`,
`rule_matchkey_demote_high_cardinality_field`) sort by HIGHEST cardinality --
neither handles this end, so the verdict was reported and never acted on.
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
from goldenmatch.core.autoconfig_rules import rule_matchkey_collapsed_field
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    FieldStats,
    HealthVerdict,
    MatchkeyProfile,
    ScoringProfile,
)


def _field(cardinality: float) -> FieldStats:
    return FieldStats(
        post_transform_cardinality_ratio=cardinality,
        post_transform_null_rate=0.0,
        post_transform_value_length_p50=8,
    )


def _cfg(fields: list[str]) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="mk", type="weighted", threshold=0.7,
                fields=[MatchkeyField(field=f, scorer="token_sort", weight=1.0)
                        for f in fields],
            )
        ],
        blocking=BlockingConfig(strategy="static",
                                keys=[BlockingKeyConfig(fields=["name"])]),
    )


def _profile(cardinalities: dict[str, float]) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=1000, n_cols=3),
        blocking=BlockingProfile(n_blocks=20, reduction_ratio=0.9),
        scoring=ScoringProfile(n_pairs_scored=500, candidates_compared=5000,
                               candidates_counted=True, mass_above_threshold=1.0,
                               dip_statistic=0.5),
        matchkey=MatchkeyProfile(per_field={n: _field(c)
                                            for n, c in cardinalities.items()}),
    )


def test_the_fixture_is_actually_the_red_this_rule_answers():
    """Guard the premise: without this, every assertion below could be testing
    a condition that never occurs."""
    profile = _profile({"name": 0.9, "country": 0.0})
    assert profile.matchkey.health() == HealthVerdict.RED
    assert profile.matchkey.red_reason() == "matchkey_collapsed_field"


def test_drops_the_collapsed_field():
    out = rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "country": 0.0}), _cfg(["name", "country"]), RunHistory()
    )
    assert out is not None
    new_cfg, decision = out
    assert [f.field for f in new_cfg.matchkeys[0].fields] == ["name"]
    assert "country" in decision.rationale


def test_drops_several_collapsed_fields_at_once():
    out = rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "country": 0.0, "region": 0.0}),
        _cfg(["name", "country", "region"]), RunHistory(),
    )
    assert out is not None
    assert [f.field for f in out[0].matchkeys[0].fields] == ["name"]


def test_does_not_fire_when_every_field_discriminates():
    assert rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "city": 0.3}), _cfg(["name", "city"]), RunHistory()
    ) is None


def test_ignores_a_collapsed_column_that_is_not_in_the_matchkey():
    """The profile covers every column; only matchkey fields carry weight."""
    assert rule_matchkey_collapsed_field(
        _profile({"name": 0.9, "unused": 0.0}), _cfg(["name"]), RunHistory()
    ) is None


def test_refuses_to_empty_the_matchkey():
    """A matchkey with no fields scores nothing, which is worse than a weak
    field. Returning None lets the policy advance to a rule that can help."""
    assert rule_matchkey_collapsed_field(
        _profile({"name": 0.0}), _cfg(["name"]), RunHistory()
    ) is None


def test_it_declares_the_condition_it_answers():
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES

    assert rule_matchkey_collapsed_field.targets == ("matchkey_collapsed_field",)
    assert rule_matchkey_collapsed_field in DEFAULT_RULES
