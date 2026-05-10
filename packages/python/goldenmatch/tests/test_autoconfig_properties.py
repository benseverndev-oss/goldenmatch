"""Hypothesis property tests for the auto-config controller.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md §Testing tier 5.
Property #3 corrected per spec revision-history (drift-aware).

Settings: 20 examples per property, deadline disabled (full pipeline runs are slow).
Each property uses small synthetic dataframes (<=30 rows) to keep wall clock <10s/test.
"""
from __future__ import annotations
import polars as pl
import pytest

# Hypothesis is in [dev] extras; CI only installs [web] so it may be absent.
# Skip the whole module cleanly when hypothesis isn't available — local runs
# (and any CI step that installs [dev]) get full property coverage.
hypothesis = pytest.importorskip("hypothesis")
from hypothesis import given, settings, strategies as st, HealthCheck  # noqa: E402

import goldenmatch  # noqa: E402
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
from goldenmatch.core.autoconfig_controller import ConfigValidationError
from goldenmatch.core.complexity_profile import HealthVerdict


# ---- Strategies ---------------------------------------------------------

# Small ASCII-ish strings to keep cardinality bounded and avoid encoding issues
_safe_text = st.text(
    alphabet=st.characters(min_codepoint=ord("a"), max_codepoint=ord("z")),
    min_size=2, max_size=12,
)


def _small_df_strategy() -> st.SearchStrategy[pl.DataFrame]:
    """Generate small DataFrames with 2-3 columns and 5-30 rows.

    Bounded shape keeps the controller from chasing tail cases that aren't
    related to the property under test.
    """
    @st.composite
    def _build(draw):
        n_rows = draw(st.integers(min_value=5, max_value=30))
        n_cols = draw(st.integers(min_value=2, max_value=3))
        data: dict[str, list[str]] = {}
        for c in range(n_cols):
            col = f"col_{c}"
            data[col] = draw(st.lists(_safe_text, min_size=n_rows, max_size=n_rows))
        return pl.DataFrame(data)
    return _build()


# ---- Properties ----------------------------------------------------------

@settings(
    max_examples=20, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(df=_small_df_strategy())
def test_determinism(df: pl.DataFrame):
    """auto_configure_df(df) is deterministic -- same df -> same config."""
    cfg1 = goldenmatch.auto_configure_df(df)
    cfg2 = goldenmatch.auto_configure_df(df)
    # Pydantic models compare by value
    assert cfg1 == cfg2, "auto_configure_df is not deterministic for the same input"


@settings(
    max_examples=20, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(df=_small_df_strategy())
def test_sample_stability(df: pl.DataFrame):
    """When n_rows < sample_skip_below (5000), controller uses full data --
    no sample is taken, sample_size in meta == n_rows (or 0 for short-circuit)."""
    goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    if state is None:
        return  # facade returned without running controller (rare)
    profile, history = state
    if history.iteration == 0:
        return  # pathological short-circuit (single-col, etc.)
    e0 = history.entries[0]
    # Below sample_skip_below, sample_size should equal full df height (no down-sampling)
    if df.height < 5000:
        # The meta.sample_size reflects what was actually run on
        assert e0.profile.meta.sample_size in (0, df.height), (
            f"sample_size={e0.profile.meta.sample_size} but n_rows={df.height} "
            f"(below skip threshold; should be full)"
        )


@settings(
    max_examples=20, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(df=_small_df_strategy())
def test_profile_non_collapse(df: pl.DataFrame):
    """_finalize does not produce RED if any history entry was non-RED
    AND drift < DRIFT_THRESHOLD (0.30). Drift-induced YELLOW is allowed."""
    goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    if state is None:
        return
    profile, history = state
    if not history.entries:
        return  # no iterations ran
    drift = history.full_vs_sample_drift
    if drift is None:
        return  # _finalize was skipped (e.g. _api shortcut path) -- N/A
    if drift >= 0.30:
        return  # high-drift case -- RED is acceptable per spec
    has_non_red_entry = any(
        e.profile.health() != HealthVerdict.RED for e in history.entries
    )
    if has_non_red_entry:
        # _finalize's profile must not be RED if drift was low and prior healthy
        assert profile.health() != HealthVerdict.RED, (
            f"profile collapsed to RED despite drift={drift:.4f} < 0.30 "
            f"and {sum(1 for e in history.entries if e.profile.health() != HealthVerdict.RED)} "
            f"non-RED history entries"
        )


@settings(
    max_examples=30, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(df=_small_df_strategy())
def test_no_silent_crashes(df: pl.DataFrame):
    """Random small dataframes must return either a GoldenMatchConfig or
    a typed exception. Never AttributeError/KeyError/IndexError from inside."""
    try:
        cfg = goldenmatch.auto_configure_df(df)
        assert isinstance(cfg, GoldenMatchConfig)
    except (ConfigValidationError, TypeError, ValueError):
        # Typed exceptions are acceptable -- they indicate the input was
        # invalid in a way the system explicitly handles
        pass
    # Note: AttributeError, KeyError, IndexError, RuntimeError, etc. are
    # NOT caught -- they would surface as test failures, indicating a
    # silent-failure mode somewhere in the pipeline.


@settings(
    max_examples=20, deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture],
)
@given(df=_small_df_strategy())
def test_history_audit_invariant(df: pl.DataFrame):
    """Every decision in history.decisions corresponds to a rule in
    HeuristicRefitPolicy's rule table -- no 'phantom' rules."""
    from goldenmatch.core.autoconfig_rules import DEFAULT_RULES

    goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    if state is None or not state[1].decisions:
        return  # no decisions recorded -- trivially satisfies the invariant

    history = state[1]
    rule_names = {r.__name__.replace("rule_", "") for r in DEFAULT_RULES}
    for d in history.decisions:
        assert d.rule_name in rule_names, (
            f"decision rule_name={d.rule_name!r} not in known rules {rule_names}"
        )
        assert d.rationale, "rationale must not be empty"


def test_apply_negative_evidence_monotonic_in_penalty():
    """Higher penalty → ≤ final score (never increases)."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("123", "999")}
    base_mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    p_low = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[],
                              scorer="exact", threshold=0.5, penalty=0.1),
    ]})
    p_high = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[],
                              scorer="exact", threshold=0.5, penalty=0.5),
    ]})
    assert _apply_negative_evidence(p_low, pair) <= _apply_negative_evidence(p_high, pair)


def test_promote_negative_evidence_idempotent_property():
    """Applying twice yields the same result."""
    import polars as pl
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    df = pl.DataFrame({
        "name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="t", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["name"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(cfg, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    assert once.matchkeys[0].negative_evidence == twice.matchkeys[0].negative_evidence


def test_ne_on_exact_monotonic_in_penalty():
    """Increasing penalty for NE on exact matchkey -> total penalty is monotonically non-decreasing."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig,
        MatchkeyField,
        NegativeEvidenceField,
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("a", "b")}
    base_mk = MatchkeyConfig(
        name="exact_email",
        type="exact",
        threshold=0.5,
        fields=[
            MatchkeyField(field="email", transforms=[], scorer="exact", weight=1.0)
        ],
    )
    p_low = base_mk.model_copy(
        update={
            "negative_evidence": [
                NegativeEvidenceField(
                    field="phone",
                    transforms=[],
                    scorer="exact",
                    threshold=0.5,
                    penalty=0.1,
                ),
            ]
        }
    )
    p_high = base_mk.model_copy(
        update={
            "negative_evidence": [
                NegativeEvidenceField(
                    field="phone",
                    transforms=[],
                    scorer="exact",
                    threshold=0.5,
                    penalty=0.5,
                ),
            ]
        }
    )
    assert _apply_negative_evidence(p_low, pair) <= _apply_negative_evidence(p_high, pair)


def test_promote_ne_extension_idempotent_property():
    """promote_negative_evidence on a config with both weighted+exact matchkeys
    is idempotent: calling twice produces identical output."""
    import polars as pl
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.config.schemas import (
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
        BlockingConfig,
        BlockingKeyConfig,
    )
    df = pl.DataFrame(
        {
            "email": [f"u{i}@x.com" for i in range(10)],
            "phone": [f"5551{i:03d}" for i in range(10)],
        }
    )
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_email",
                type="exact",
                threshold=None,
                fields=[
                    MatchkeyField(
                        field="email", transforms=[], scorer="exact", weight=1.0
                    )
                ],
            ),
            MatchkeyConfig(
                name="fuzzy_match",
                type="weighted",
                threshold=0.85,
                fields=[
                    MatchkeyField(
                        field="email", transforms=[], scorer="ensemble", weight=1.0
                    )
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=[])],
            max_block_size=1000,
            skip_oversized=False,
        ),
    )
    priors = {
        "email": ColumnPrior(0.95, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(cfg, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    for mk_a, mk_b in zip(once.matchkeys, twice.matchkeys):
        assert (mk_a.negative_evidence or []) == (mk_b.negative_evidence or [])
        assert mk_a.threshold == mk_b.threshold
