"""rule_precision_anchor_threshold_raise trigger-matrix tests (#1319).

The #1207 over-merge shape: the controller commits a config whose weighted
matchkey scores NAMES ONLY, and identical common full names over-merge
distinct people (crafted 2600-row fixture: P 0.009 at the committed 0.8
threshold). The rule raises the name-only weighted threshold to 0.9 in a
single shot when the config shape is pathological AND a strong-identifier
exact anchor keeps recall free AND the #1318 tf_freqs downweight is live.

Each of the five trigger conditions is independently falsified here; the
through-the-controller verification lives in the P2 task.
"""
from __future__ import annotations

import polars as pl
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
    rule_precision_anchor_threshold_raise,
    rule_sparse_match_expand,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ColumnPrior,
    ComplexityProfile,
    DataProfile,
    FieldStats,
    MatchkeyProfile,
    ScoringProfile,
    SparsityVerdict,
)

_TF_FREQS = {"smith": 0.21, "jones": 0.15, "zelinski": 0.001}


def _name_field(field: str, scorer: str, tf_freqs: dict[str, float] | None) -> MatchkeyField:
    return MatchkeyField(field=field, scorer=scorer, weight=1.0, tf_freqs=tf_freqs)


def _weighted_mk(
    fields: list[MatchkeyField] | None = None,
    threshold: float = 0.8,
) -> MatchkeyConfig:
    if fields is None:
        fields = [
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
        ]
    return MatchkeyConfig(
        name="fuzzy_name", type="weighted", threshold=threshold, fields=fields,
    )


def _exact_mk(field: str = "email") -> MatchkeyConfig:
    return MatchkeyConfig(
        name=f"exact_{field}", type="exact",
        fields=[MatchkeyField(field=field)],
    )


def _cfg(matchkeys: list[MatchkeyConfig]) -> GoldenMatchConfig:
    """Minimal valid config. Pydantic requires `blocking` whenever a
    weighted matchkey is configured; the rule under test doesn't read it."""
    return GoldenMatchConfig(
        matchkeys=matchkeys,
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["last_name"])],
        ),
    )


def _profile_with_mass_above(mass_above: float) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(
            n_rows=2600, n_cols=4,
            column_types={"email": "id-like", "first_name": "text",
                          "last_name": "text", "city": "geo"},
        ),
        blocking=BlockingProfile(
            keys_used=[["last_name"]], n_blocks=100,
            total_comparisons=15058, reduction_ratio=0.95, block_sizes_p99=40,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=15058, candidates_compared=15058,
            mass_above_threshold=mass_above,
            mass_in_borderline=0.02, dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={
            "last_name": FieldStats(0.1, 0.0, 8),
        }),
    )


def _ctx_with_priors(priors: dict[str, ColumnPrior]):
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=pl.DataFrame(),
        column_priors=priors,
        sparsity_verdict=SparsityVerdict(is_sparse=False, estimated_n_true_pairs=100),
    )


def _strong_email_ctx():
    return _ctx_with_priors(
        {"email": ColumnPrior(identity_score=0.9, corruption_score=0.0)}
    )


def _call(profile, cfg, ctx):
    return rule_precision_anchor_threshold_raise(profile, cfg, RunHistory(), ctx=ctx)


class TestFires:
    def test_all_five_conditions_satisfied(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk(threshold=0.8)])
        result = _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx())
        assert result is not None
        new_cfg, decision = result
        raised = [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0]
        assert raised.threshold == 0.9
        # copy-on-write: input config unmutated
        original = [mk for mk in cfg.matchkeys if mk.type == "weighted"][0]
        assert original.threshold == 0.8
        assert decision.rule_name == "precision_anchor_threshold_raise"
        assert "name-only" in decision.rationale
        assert "#1319" in decision.rationale
        assert "email" in decision.rationale
        assert decision.config_diff  # names the threshold change
        assert any("threshold" in k for k in decision.config_diff)

    def test_single_name_field_carrying_tf_freqs_fires(self):
        """The measured fixture shape: only last_name carries the table.
        The ANY-field formulation is load-bearing."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
        ])])
        result = _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx())
        assert result is not None
        new_cfg, _ = result
        assert [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0].threshold == 0.9


class TestConditionFalsification:
    def test_mass_below_gate_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        assert _call(_profile_with_mass_above(0.94), cfg, _strong_email_ctx()) is None

    def test_non_name_scorer_in_weighted_mk_returns_none(self):
        """The NCVR shape: weighted mk carries an address field too."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", _TF_FREQS),
            _name_field("address", "jaro_winkler", None),
        ])])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_no_exact_matchkey_returns_none(self):
        cfg = _cfg([_weighted_mk()])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_exact_anchor_identity_score_too_low_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        ctx = _ctx_with_priors(
            {"email": ColumnPrior(identity_score=0.5, corruption_score=0.0)}
        )
        assert _call(_profile_with_mass_above(1.0), cfg, ctx) is None

    def test_exact_anchor_field_missing_from_priors_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        ctx = _ctx_with_priors(
            {"phone": ColumnPrior(identity_score=0.9, corruption_score=0.0)}
        )
        assert _call(_profile_with_mass_above(1.0), cfg, ctx) is None

    def test_no_tf_freqs_on_any_name_field_returns_none(self):
        """Identical names without the table score 1.0 and clear any
        threshold < 1 -- the raise would be pure recall risk."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(fields=[
            _name_field("first_name", "given_name_aliased_jw", None),
            _name_field("last_name", "name_freq_weighted_jw", None),
        ])])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_threshold_already_raised_returns_none(self):
        """Convergence: single-fire by construction."""
        cfg = _cfg([_exact_mk("email"), _weighted_mk(threshold=0.9)])
        assert _call(_profile_with_mass_above(1.0), cfg, _strong_email_ctx()) is None

    def test_ctx_none_returns_none(self):
        cfg = _cfg([_exact_mk("email"), _weighted_mk()])
        assert rule_precision_anchor_threshold_raise(
            _profile_with_mass_above(1.0), cfg, RunHistory()
        ) is None


class TestRegistration:
    def test_registered_directly_before_sparse_match_expand(self):
        assert (
            DEFAULT_RULES.index(rule_precision_anchor_threshold_raise)
            == DEFAULT_RULES.index(rule_sparse_match_expand) - 1
        )
