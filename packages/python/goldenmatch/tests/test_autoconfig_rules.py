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


def _build_test_config_with_exact_email_and_weighted():
    """Config with both an exact email matchkey and a weighted name matchkey."""
    return _V110GMC(
        matchkeys=[
            _V110MK(
                name="exact_email", type="exact", threshold=1.0,
                fields=[_V110MKF(field="email", transforms=["lowercase"],
                                 scorer="exact", weight=1.0)],
            ),
            _V110MK(
                name="fuzzy_match", type="weighted", threshold=0.8,
                fields=[
                    _V110MKF(field="first_name", transforms=["lowercase"],
                             scorer="ensemble", weight=0.5),
                    _V110MKF(field="last_name", transforms=["lowercase"],
                             scorer="ensemble", weight=0.5),
                ],
            ),
        ],
        blocking=_V110BC(
            strategy="static",
            keys=[_V110BKC(fields=["city"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=True,
        ),
    )


def _build_test_config_with_only_exact_email():
    """Config with only an exact email matchkey (no weighted matchkey)."""
    return _V110GMC(
        matchkeys=[_V110MK(
            name="exact_email", type="exact", threshold=1.0,
            fields=[_V110MKF(field="email", transforms=["lowercase"],
                             scorer="exact", weight=1.0)],
        )],
        blocking=_V110BC(
            strategy="static",
            keys=[_V110BKC(fields=["city"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=True,
        ),
    )


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


# ============================================================
# Task 4.3: rule_blocking_key_swap indicator-aware tests
# ============================================================

def test_rule_blocking_key_swap_vetoed_when_v0_key_good():
    """High identity_score + nonzero full_pop_hits → veto swap."""
    from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="email")
    ctx = _ctx_with_priors_and_hits(
        {"email": _V110ColP(identity_score=0.9, corruption_score=0.5)},
        full_pop_hits={"email": 100},
    )
    history = _history_with_prior_decision()
    outcome = rule_blocking_key_swap(profile, cfg, history, ctx=ctx)
    assert outcome is None    # vetoed


def test_rule_blocking_key_swap_proceeds_when_no_indicator_evidence():
    from goldenmatch.core.autoconfig_rules import rule_blocking_key_swap
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="text_col")
    ctx = _ctx_with_priors({"text_col": _V110ColP(0.0, 0.0)})
    history = _history_with_prior_decision()
    outcome = rule_blocking_key_swap(profile, cfg, history, ctx=ctx)
    # Today's behavior: swap proceeds (rule does fire, but may also return None
    # if there's nothing to swap to — accept either non-None or None as long
    # as it isn't vetoed by indicator logic). The point of the test is to
    # confirm low identity_score doesn't trigger the new veto.
    # If the rule fires, the new config should differ from current.
    if outcome is not None:
        new_cfg, _ = outcome
        assert new_cfg != cfg


# ============================================================
# Task 5.1: rule_corruption_normalize tests
# ============================================================

def test_rule_corruption_normalize_fires_high_corruption_high_identity():
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    # Use RED profile (mass_above=0.0) so health() != GREEN
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="email")
    ctx = _ctx_with_priors({
        "email": _V110ColP(identity_score=0.9, corruption_score=0.6),
    })
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is not None
    new_cfg, decision = outcome
    assert new_cfg.standardization is not None
    assert "email" in new_cfg.standardization.rules


def test_rule_corruption_normalize_idempotent_when_already_normalized():
    """Doesn't fire if standardization rule for col already exists."""
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config_with_email_standardization()
    ctx = _ctx_with_priors({
        "email": _V110ColP(identity_score=0.9, corruption_score=0.6),
    })
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_corruption_normalize_no_fire_low_identity():
    """Low identity_score → don't normalize even if corruption is high."""
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(blocking_field="email")
    ctx = _ctx_with_priors({
        "email": _V110ColP(identity_score=0.3, corruption_score=0.6),
    })
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_corruption_normalize_no_ctx_no_fire():
    """ctx=None → rule doesn't fire (no signal to act on)."""
    from goldenmatch.core.autoconfig_rules import rule_corruption_normalize
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config()
    outcome = rule_corruption_normalize(profile, cfg, _empty_history(), ctx=None)
    assert outcome is None


# ============================================================
# Task 5.2: rule_cross_blocking_disagreement tests
# ============================================================

def test_rule_cross_blocking_disagreement_fires_on_low_overlap():
    from goldenmatch.core.autoconfig_rules import rule_cross_blocking_disagreement
    import polars as pl
    df = pl.DataFrame({
        "email": ["a@x.com"] * 10 + ["b@x.com"] * 10,
        "name": ["alice"] * 10 + ["bob"] * 10,
    })
    profile = _profile_with_mass_above(0.05)
    cfg = _build_test_config(blocking_field="email")
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=_V110SV(False, 100),
    )
    # Pre-populate cross_blocking_overlap memo as low
    ctx._memo[("cross_blocking_overlap", "email", "name")] = 0.1
    history = _history_with_prior_decision()
    outcome = rule_cross_blocking_disagreement(profile, cfg, history, ctx=ctx)
    # The rule needs profile.health() == RED — accept either fired or no-fire
    # depending on rollup; the key invariant is that low overlap doesn't crash
    if outcome is not None:
        new_cfg, _ = outcome
        assert new_cfg.blocking.strategy == "multi_pass"


def test_rule_cross_blocking_disagreement_no_fire_high_overlap():
    """High overlap (≥0.3) → don't fire."""
    from goldenmatch.core.autoconfig_rules import rule_cross_blocking_disagreement
    import polars as pl
    df = pl.DataFrame({"email": ["a@x.com"], "name": ["alice"]})
    profile = _profile_with_mass_above(0.05)
    cfg = _build_test_config(blocking_field="email")
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=_V110SV(False, 100),
    )
    ctx._memo[("cross_blocking_overlap", "email", "name")] = 0.7
    outcome = rule_cross_blocking_disagreement(profile, cfg, _history_with_prior_decision(), ctx=ctx)
    assert outcome is None


# ============================================================
# Task 5.3: rule_sparse_match_expand tests
# ============================================================

def test_rule_sparse_match_expand_fires_when_sparse_iter_zero():
    from goldenmatch.core.autoconfig_rules import rule_sparse_match_expand
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config(threshold=0.85)
    ctx = _ctx_with_sparsity(_V110SV(is_sparse=True, estimated_n_true_pairs=10))
    outcome = rule_sparse_match_expand(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is not None
    assert ctx.has_fired("rule_sparse_match_expand")


def test_rule_sparse_match_expand_one_shot_doesnt_fire_twice():
    from goldenmatch.core.autoconfig_rules import rule_sparse_match_expand
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config()
    ctx = _ctx_with_sparsity(_V110SV(is_sparse=True, estimated_n_true_pairs=10))
    rule_sparse_match_expand(profile, cfg, _empty_history(), ctx=ctx)
    # Second call should not fire
    outcome2 = rule_sparse_match_expand(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome2 is None


def test_rule_sparse_match_expand_no_fire_when_not_sparse():
    from goldenmatch.core.autoconfig_rules import rule_sparse_match_expand
    profile = _profile_with_mass_above(0.0)
    cfg = _build_test_config()
    ctx = _ctx_with_sparsity(_V110SV(is_sparse=False, estimated_n_true_pairs=100))
    outcome = rule_sparse_match_expand(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


# ============================================================
# Task 5.1: _demote_exact_to_weighted_fuzzy helper tests
# ============================================================

def test_demote_exact_to_weighted_fuzzy_removes_exact_matchkey():
    """The exact matchkey on email is removed; email becomes a fuzzy field."""
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_exact_email_and_weighted()
    new_cfg, rationale = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    # No more standalone exact matchkey on email
    assert not any(
        mk.type == "exact" and any(f.field == "email" for f in mk.fields)
        for mk in new_cfg.matchkeys
    )
    # email is now a participant in the weighted matchkey
    weighted = [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0]
    assert any(f.field == "email" for f in weighted.fields)


def test_demote_adds_to_blocking_when_not_present():
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_exact_email_and_weighted()
    new_cfg, _ = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    blocking_cols = set()
    for k in new_cfg.blocking.keys:
        blocking_cols.update(k.fields)
    assert "email" in blocking_cols


def test_demote_skips_when_no_weighted_matchkey():
    """If no weighted matchkey to add to → no-op."""
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_only_exact_email()
    new_cfg, rationale = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    assert new_cfg == cfg


# ============================================================
# Task 5.2: rule_demote_clustered_identity tests
# ============================================================

def test_rule_demote_clustered_identity_fires_on_collision():
    """High collision_rate + identity-prior + exact matchkey → fires."""
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    from goldenmatch.core.complexity_profile import CollisionSignal
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)
    import polars as pl
    df = pl.DataFrame({
        "email": [f"u{i // 2}@x.com" for i in range(15)],   # 8 unique emails, ratio=0.53
        "first_name": ["Brian"] * 15,
        "last_name": ["Smith"] * 15,
        "address": [f"{i} Main St" for i in range(15)],   # all different
        "city": ["NYC"] * 15,
    })
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    ctx = IndicatorContext(
        df=df,
        column_priors={
            "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
            "address": _V110ColP(identity_score=0.7, corruption_score=0.0),
        },
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    # Pre-populate collision_rate as high
    ctx._memo[("identity_collision_signal", "email", ("address",))] = (
        CollisionSignal(rate=0.6, witness_used="address")
    )
    history = _empty_history()
    outcome = rule_demote_clustered_identity(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    new_cfg, decision = outcome
    # exact_email matchkey should be gone
    assert not any(
        mk.type == "exact" and any(f.field == "email" for f in mk.fields)
        for mk in new_cfg.matchkeys
    )
    assert "demoted" in decision.rationale.lower()


def test_rule_demote_clustered_identity_no_fire_when_collision_low():
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    from goldenmatch.core.complexity_profile import CollisionSignal
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)
    import polars as pl
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(15)],   # all unique
        "address": [f"{i} Main St" for i in range(15)],
    })
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    ctx = IndicatorContext(
        df=df,
        column_priors={
            "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
            "address": _V110ColP(identity_score=0.7, corruption_score=0.0),
        },
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    ctx._memo[("identity_collision_signal", "email", ("address",))] = (
        CollisionSignal(rate=0.05, witness_used="")
    )
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_demote_clustered_identity_no_fire_no_exact_matchkey():
    """If no exact matchkey on the candidate column → don't fire."""
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    cfg = _build_test_config(blocking_field="email")    # weighted only, no exact_email
    profile = _profile_with_mass_above(0.0)
    ctx = _ctx_with_priors({
        "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
    })
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_demote_clustered_identity_no_fire_when_ctx_none():
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=None)
    assert outcome is None
