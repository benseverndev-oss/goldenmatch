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
        "standardization": _V110SC(rules={"email": ["lowercase", "strip"]}),
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
