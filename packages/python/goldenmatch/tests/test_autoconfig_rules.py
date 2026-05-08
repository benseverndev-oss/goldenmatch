"""Tests for autoconfig_rules.py — rule helper functions and indicator-aware rules.

v1.10 Phase 4 (Tasks 4.0–4.3).
"""
import pytest

# ============================================================
# v1.10 indicator-aware rule test helpers (added 2026-05-08)
# ============================================================
import polars as _v110_pl
from goldenmatch.config.schemas import (
    GoldenMatchConfig as _V110GMC,
    MatchkeyConfig as _V110MK,
    MatchkeyField as _V110MKF,
    BlockingConfig as _V110BC,
    BlockingKeyConfig as _V110BKC,
    StandardizationConfig as _V110SC,
)
from goldenmatch.core.complexity_profile import (
    ComplexityProfile as _V110CP,
    DataProfile as _V110DP,
    BlockingProfile as _V110BP,
    ScoringProfile as _V110SP,
    ClusterProfile as _V110ClP,
    MatchkeyProfile as _V110MP,
    FieldStats as _V110FS,
    ColumnPrior as _V110ColP,
    SparsityVerdict as _V110SV,
)
from goldenmatch.core.autoconfig_history import (
    RunHistory as _V110RH,
    HistoryEntry as _V110HE,
    PolicyDecision as _V110PD,
)


def _build_test_config(blocking_field="email", threshold=0.85):
    """Minimal valid GoldenMatchConfig with one weighted matchkey + blocking."""
    return _V110GMC(
        matchkeys=[_V110MK(
            name="primary",
            type="weighted",
            threshold=threshold,
            fields=[_V110MKF(
                field="email", transforms=["lowercase"],
                scorer="ensemble", weight=1.0,
            )],
        )],
        blocking=_V110BC(
            strategy="static",
            keys=[_V110BKC(fields=[blocking_field], transforms=["lowercase"])],
            max_block_size=1000,
            skip_oversized=True,
        ),
    )


def _build_test_config_with_email_standardization():
    cfg = _build_test_config(blocking_field="email")
    return cfg.model_copy(update={
        "standardization": _V110SC(rules={"email": ["email", "strip"]}),
    })


def _get_threshold(cfg):
    return cfg.get_matchkeys()[0].threshold


def _get_blocking_field(cfg):
    return cfg.blocking.keys[0].fields[0]


def _profile_with_mass_above(mass_above, blocking_col="email"):
    return _V110CP(
        data=_V110DP(
            n_rows=1000, n_cols=4,
            column_types={blocking_col: "id-like", "name": "text",
                          "city": "geo", "dob": "date"},
        ),
        blocking=_V110BP(
            keys_used=[[blocking_col]], n_blocks=100,
            total_comparisons=500, reduction_ratio=0.95, block_sizes_p99=20,
        ),
        scoring=_V110SP(
            n_pairs_scored=500, candidates_compared=500,
            mass_above_threshold=mass_above,
            mass_in_borderline=0.1, dip_statistic=0.05,
        ),
        cluster=_V110ClP(transitivity_rate=0.95),
        matchkey=_V110MP(per_field={
            blocking_col: _V110FS(0.5, 0.0, 10),
        }),
    )


def _profile_with_health_yellow():
    return _profile_with_mass_above(mass_above=0.3)


def _empty_history():
    return _V110RH()


def _history_with_prior_decision():
    h = _V110RH()
    h.entries.append(_V110HE(
        iteration=0, config=_build_test_config(),
        profile=_profile_with_mass_above(0.0),
        decision=_V110PD(
            rule_name="rule_blocking_field_null_heavy",
            rationale="prior", config_diff={},
        ),
        error=None, wall_clock_ms=10,
    ))
    return h


def _ctx_with_priors(priors):
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=_v110_pl.DataFrame(),
        column_priors=priors,
        sparsity_verdict=_V110SV(is_sparse=False, estimated_n_true_pairs=100),
    )


def _ctx_with_sparsity(sv):
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    return IndicatorContext(
        df=_v110_pl.DataFrame(),
        column_priors={},
        sparsity_verdict=sv,
    )


def _ctx_with_priors_and_hits(priors, full_pop_hits):
    ctx = _ctx_with_priors(priors)
    for col, hits in full_pop_hits.items():
        ctx._memo[("full_pop_matchkey_hits", col)] = hits
    return ctx


# ============================================================
# Task 4.1: Rule helper tests
# ============================================================

def test_with_lower_threshold_returns_new_config():
    from goldenmatch.core.autoconfig_rules import _with_lower_threshold
    cfg = _build_test_config(threshold=0.85)
    new_cfg, rationale = _with_lower_threshold(cfg, delta=0.05)
    assert _get_threshold(new_cfg) == 0.80


def test_with_lower_threshold_at_floor_returns_unchanged():
    from goldenmatch.core.autoconfig_rules import _with_lower_threshold
    cfg = _build_test_config(threshold=0.5)
    new_cfg, _rationale = _with_lower_threshold(cfg, delta=0.05)
    assert new_cfg == cfg


def test_with_normalize_standardization_adds_rule():
    from goldenmatch.core.autoconfig_rules import _with_normalize_standardization
    cfg = _build_test_config()
    new_cfg, _rationale = _with_normalize_standardization(cfg, "email")
    assert new_cfg.standardization is not None
    assert "email" in new_cfg.standardization.rules


def test_with_normalize_standardization_idempotent():
    from goldenmatch.core.autoconfig_rules import _with_normalize_standardization
    cfg = _build_test_config_with_email_standardization()
    new_cfg, _rationale = _with_normalize_standardization(cfg, "email")
    assert new_cfg == cfg


def test_with_multi_pass_adds_orthogonal_key():
    from goldenmatch.core.autoconfig_rules import _with_multi_pass
    from goldenmatch.config.schemas import BlockingKeyConfig
    cfg = _build_test_config(blocking_field="email")
    ortho = BlockingKeyConfig(fields=["last_name"], transforms=["lowercase"])
    new_cfg, _rationale = _with_multi_pass(cfg, ortho)
    assert new_cfg.blocking.strategy == "multi_pass"
    assert any(k.fields == ["last_name"] for k in new_cfg.blocking.keys)


def test_orthogonal_key_picks_unused_column():
    from goldenmatch.core.autoconfig_rules import _orthogonal_key
    cfg = _build_test_config(blocking_field="email")
    df_cols = ["email", "name", "city", "dob"]
    ortho = _orthogonal_key(cfg, df_cols)
    assert ortho is not None
    assert ortho.fields[0] not in {"email"}


def test_orthogonal_key_returns_none_when_no_candidates():
    from goldenmatch.core.autoconfig_rules import _orthogonal_key
    cfg = _build_test_config(blocking_field="email")
    df_cols = ["email"]    # only the used column
    ortho = _orthogonal_key(cfg, df_cols)
    assert ortho is None


# ============================================================
# Task 4.2: rule_no_matches indicator-aware tests
# ============================================================

def test_rule_no_matches_high_identity_prior_tries_lower_threshold_first():
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="email")
    cfg = _build_test_config(blocking_field="email", threshold=0.85)
    ctx = _ctx_with_priors({"email": _V110ColP(identity_score=0.9, corruption_score=0.0)})
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    new_cfg, decision = outcome
    assert new_cfg != cfg
    # First alternative is lower_threshold
    assert _get_threshold(new_cfg) == 0.80


def test_rule_no_matches_sparse_proposes_lower_threshold_aggressive():
    """Sparse path: lower threshold by 0.10 (proxy for ExpandSample)."""
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="text_col")
    cfg = _build_test_config(blocking_field="text_col", threshold=0.85)
    ctx = _ctx_with_sparsity(_V110SV(is_sparse=True, estimated_n_true_pairs=10))
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    new_cfg, _ = outcome
    assert _get_threshold(new_cfg) == 0.75


def test_rule_no_matches_baseline_no_ctx_today_behavior():
    """ctx=None → today's behavior (lower threshold by 0.05)."""
    from goldenmatch.core.autoconfig_rules import rule_no_matches
    profile = _profile_with_mass_above(0.0, blocking_col="text_col")
    cfg = _build_test_config(blocking_field="text_col", threshold=0.85)
    history = _empty_history()
    outcome = rule_no_matches(profile, cfg, history, ctx=None)
    assert outcome is not None
    new_cfg, _ = outcome
    assert _get_threshold(new_cfg) == 0.80
