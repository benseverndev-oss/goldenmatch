"""rule_matchkey_demote_high_cardinality_field tests.

v23 QIS telemetry (2026-05-29) showed the `matchkey` sub-profile stayed
YELLOW across iterations 0-4 without any rule addressing it. The verdict
fires when any field has `post_transform_cardinality_ratio > 0.95`
(`MatchkeyProfile.health()` in core/complexity_profile.py:153-160).

This rule removes that field from the matchkey's fields list. Auto-config
handles NE retention separately; the rule's job is to stop the matchkey
from carrying a uniquely-identifying field that contributes near-zero
discriminative power.
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
from goldenmatch.core.autoconfig_rules import (
    DEFAULT_RULES,
    rule_matchkey_demote_high_cardinality_field,
)
from goldenmatch.core.complexity_profile import (
    ComplexityProfile,
    DataProfile,
    FieldStats,
    MatchkeyProfile,
)


def _mk(*field_names: str, type_: str = "weighted") -> MatchkeyConfig:
    return MatchkeyConfig(
        name="test_mk",
        type=type_,
        threshold=0.5 if type_ == "weighted" else None,
        fields=[
            MatchkeyField(field=n, scorer="jaro_winkler", weight=1.0)
            for n in field_names
        ],
    )


def _cfg(matchkeys: list[MatchkeyConfig]) -> GoldenMatchConfig:
    """Minimal valid config. Pydantic requires `blocking` to be present
    whenever a weighted/probabilistic matchkey is configured, so the
    fixture pins a trivial static-blocking key. The rule under test
    doesn't read blocking, but the constructor validates it."""
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["__test_block__"])],
        ),
    )


def _profile(field_cards: dict[str, float]) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=10_000_000, n_cols=8),
        matchkey=MatchkeyProfile(per_field={
            name: FieldStats(
                post_transform_cardinality_ratio=card,
                post_transform_null_rate=0.0,
                post_transform_value_length_p50=10,
            )
            for name, card in field_cards.items()
        }),
    )


class TestRuleFires:
    def test_demotes_email_field_with_cardinality_one(self):
        """The QIS realistic case: email is in the matchkey with cardinality ~1.0."""
        cfg = _cfg([_mk("first_name", "last_name", "email")])
        profile = _profile({"first_name": 0.20, "last_name": 0.20, "email": 1.0})
        result = rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory())
        assert result is not None
        new_cfg, decision = result
        assert decision.rule_name == "matchkey_demote_high_cardinality_field"
        new_mk = new_cfg.matchkeys[0]
        new_field_names = [f.field for f in new_mk.fields]
        assert "email" not in new_field_names, (
            f"expected email demoted, fields={new_field_names}"
        )
        assert "first_name" in new_field_names
        assert "last_name" in new_field_names

    def test_picks_highest_cardinality_first(self):
        """When multiple fields qualify, the highest-cardinality one is demoted
        first (deterministic, ties broken by name)."""
        cfg = _cfg([_mk("email", "id", "first_name")])
        profile = _profile({"email": 0.99, "id": 1.0, "first_name": 0.20})
        result = rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory())
        assert result is not None
        new_cfg, _ = result
        # id has cardinality 1.0, higher than email's 0.99
        new_field_names = [f.field for f in new_cfg.matchkeys[0].fields]
        assert "id" not in new_field_names
        # email still in (next iteration would catch it)
        assert "email" in new_field_names

    def test_includes_cardinality_in_rationale(self):
        cfg = _cfg([_mk("name", "email")])
        profile = _profile({"name": 0.20, "email": 0.97})
        result = rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory())
        assert result is not None
        _, decision = result
        assert "email" in decision.rationale
        assert "0.97" in decision.rationale


class TestRuleDoesNotFire:
    def test_no_high_cardinality_field(self):
        cfg = _cfg([_mk("first_name", "last_name")])
        profile = _profile({"first_name": 0.20, "last_name": 0.20})
        assert rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory()) is None

    def test_cardinality_exactly_095_does_not_fire(self):
        """Boundary: > 0.95, not >= 0.95. Match what health() uses."""
        cfg = _cfg([_mk("name")])
        profile = _profile({"name": 0.95})
        assert rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory()) is None

    def test_skips_when_would_empty_the_matchkey(self):
        """If the only field is high-cardinality, removing it would leave the
        matchkey empty. Skip and let other rules handle it."""
        cfg = _cfg([_mk("email")])
        profile = _profile({"email": 1.0})
        assert rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory()) is None

    def test_skips_exact_matchkey(self):
        """Exact matchkeys have different cardinality semantics; > 0.95 there
        means "fingerprint-quality" which is desired."""
        cfg = _cfg([_mk("email", "first_name", type_="exact")])
        profile = _profile({"email": 1.0, "first_name": 0.20})
        assert rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory()) is None

    def test_skips_when_field_not_in_profile(self):
        """If profile doesn't have stats for the matchkey's field, skip."""
        cfg = _cfg([_mk("first_name", "last_name")])
        profile = _profile({"unrelated": 1.0})
        assert rule_matchkey_demote_high_cardinality_field(profile, cfg, RunHistory()) is None


class TestRuleIntegration:
    def test_listed_in_default_rules(self):
        """The rule must be in DEFAULT_RULES to fire in production."""
        assert rule_matchkey_demote_high_cardinality_field in DEFAULT_RULES

    def test_listed_after_existing_scoring_rules(self):
        """Position matters: scoring rules (unimodal_scoring, etc) should fire
        first since they're more specific. This rule is a structural cleanup."""
        rule_names = [r.__name__ for r in DEFAULT_RULES]
        scoring_idx = rule_names.index("rule_unimodal_scoring")
        demote_idx = rule_names.index("rule_matchkey_demote_high_cardinality_field")
        assert demote_idx > scoring_idx
