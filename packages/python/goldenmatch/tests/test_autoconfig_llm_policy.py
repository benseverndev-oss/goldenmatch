"""LLMRefitPolicy unit tests. The policy is tested with a mocked LLM call
to avoid real API spend. End-to-end LLM behavior (with real API key) is
covered by an opt-in benchmark rerun, not in this file."""
from unittest.mock import MagicMock, patch

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
from goldenmatch.core.autoconfig_policy import (
    LLMRefitPolicy,
    RefitPolicy,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ClusterProfile,
    ComplexityProfile,
    DataProfile,
    FieldStats,
    MatchkeyProfile,
    ScoringProfile,
)


def _green_profile() -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=4,
                          column_types={"a": "text", "b": "id-like",
                                        "c": "text", "d": "date"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p99=20,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=200, candidates_compared=500,
            mass_above_threshold=0.4, dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
    )


def _yellow_profile() -> ComplexityProfile:
    """YELLOW config: oversized borderline mass + everything else green."""
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=4,
                          column_types={"a": "text", "b": "text",
                                        "c": "text", "d": "text"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p99=20,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=200, candidates_compared=500,
            mass_above_threshold=0.4, dip_statistic=0.05,
            mass_in_borderline=0.4,  # YELLOW: > 0.3
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
    )


def _config():
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )


def test_llm_policy_short_circuits_when_base_proposes():
    """When the base policy returns a config, LLM is never called."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = "BASE_PROPOSAL"
    policy = LLMRefitPolicy(base=base)
    with patch.object(policy, "_call_llm") as mock_llm:
        out = policy.propose(_yellow_profile(), _config(), RunHistory())
    assert out == "BASE_PROPOSAL"
    mock_llm.assert_not_called()


def test_llm_policy_short_circuits_on_green_profile():
    """When base returns None and profile is GREEN, LLM is not called."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    policy = LLMRefitPolicy(base=base)
    with patch.object(policy, "_call_llm") as mock_llm:
        out = policy.propose(_green_profile(), _config(), RunHistory())
    assert out is None
    mock_llm.assert_not_called()


def test_llm_policy_calls_llm_when_base_none_and_profile_yellow():
    """When base returns None and profile is YELLOW or RED, LLM is consulted."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    new_cfg = _config().model_copy(update={
        "matchkeys": [_config().matchkeys[0].model_copy(update={"threshold": 0.5})],
    })
    policy = LLMRefitPolicy(base=base)
    with patch.object(policy, "_call_llm", return_value=new_cfg) as mock_llm:
        out = policy.propose(_yellow_profile(), _config(), RunHistory())
    mock_llm.assert_called_once()
    assert out == new_cfg


def test_llm_policy_returns_none_on_llm_exception():
    """Any LLM error falls back silently to None — never crashes auto-config."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    policy = LLMRefitPolicy(base=base)
    with patch.object(policy, "_call_llm", side_effect=RuntimeError("network down")):
        out = policy.propose(_yellow_profile(), _config(), RunHistory())
    assert out is None


def test_llm_policy_respects_max_calls_per_run():
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    policy = LLMRefitPolicy(base=base, max_calls_per_run=2)
    with patch.object(policy, "_call_llm", return_value=_config()) as mock_llm:
        for _ in range(5):
            policy.propose(_yellow_profile(), _config(), RunHistory())
    assert mock_llm.call_count == 2


def test_apply_diff_threshold_change():
    policy = LLMRefitPolicy()
    cfg = _config()
    diff = {"matchkeys": [{"name": "m", "threshold": 0.5}]}
    new_cfg = policy._apply_diff(cfg, diff)
    assert new_cfg is not None
    assert new_cfg.matchkeys[0].threshold == 0.5


def test_apply_diff_blocking_swap():
    policy = LLMRefitPolicy()
    cfg = _config()
    diff = {"blocking": {"keys": [
        {"fields": ["surname"], "transforms": ["soundex"]},
    ]}}
    new_cfg = policy._apply_diff(cfg, diff)
    assert new_cfg is not None
    assert new_cfg.blocking.keys[0].fields == ["surname"]
    assert "soundex" in new_cfg.blocking.keys[0].transforms


def test_apply_diff_drop_matchkey():
    policy = LLMRefitPolicy()
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(name="keep", type="weighted", threshold=0.7,
                           fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                                  weight=1.0, transforms=["lowercase"])]),
            MatchkeyConfig(name="drop_me", type="exact",
                           fields=[MatchkeyField(field="__title_key__",
                                                  transforms=["lowercase"])]),
        ],
        blocking=BlockingConfig(strategy="static",
                                 keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
                                 max_block_size=5000, skip_oversized=False),
    )
    diff = {"drop_matchkeys": ["drop_me"]}
    new_cfg = policy._apply_diff(cfg, diff)
    assert new_cfg is not None
    assert len(new_cfg.matchkeys) == 1
    assert new_cfg.matchkeys[0].name == "keep"


def test_apply_diff_returns_none_when_no_change():
    policy = LLMRefitPolicy()
    cfg = _config()
    # Diff that doesn't change anything (matching the existing threshold)
    diff = {"matchkeys": [{"name": "m", "threshold": 0.7}]}
    new_cfg = policy._apply_diff(cfg, diff)
    assert new_cfg is None


def test_llm_policy_attaches_decision_to_history_on_success():
    """A successful LLM proposal attaches a PolicyDecision to the history entry."""

    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    new_cfg = _config().model_copy(update={
        "matchkeys": [_config().matchkeys[0].model_copy(update={"threshold": 0.5})],
    })
    policy = LLMRefitPolicy(base=base)

    history = RunHistory()
    # Add a stub history entry so the decision can be attached
    history.entries.append(HistoryEntry(
        iteration=1,
        config=_config(),
        profile=_yellow_profile(),
        decision=None,
        error=None,
        wall_clock_ms=10,
    ))

    with patch.object(policy, "_call_llm", return_value=new_cfg):
        out = policy.propose(_yellow_profile(), _config(), history)

    assert out == new_cfg
    assert history.entries[-1].decision is not None
    assert history.entries[-1].decision.rule_name == "llm_proposal"


def test_llm_policy_no_decision_attached_when_no_history_entries():
    """When history is empty, LLM proposal still returns the new config (no crash)."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    new_cfg = _config().model_copy(update={
        "matchkeys": [_config().matchkeys[0].model_copy(update={"threshold": 0.5})],
    })
    policy = LLMRefitPolicy(base=base)

    with patch.object(policy, "_call_llm", return_value=new_cfg):
        out = policy.propose(_yellow_profile(), _config(), RunHistory())

    assert out == new_cfg


def test_llm_policy_returns_none_when_llm_returns_same_config():
    """If LLM proposes the same config as current, treat as no-op."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    cfg = _config()
    policy = LLMRefitPolicy(base=base)

    with patch.object(policy, "_call_llm", return_value=cfg):
        out = policy.propose(_yellow_profile(), cfg, RunHistory())

    assert out is None


def test_llm_policy_call_count_increments_on_success():
    """Counter tracks LLM calls so the budget gate fires correctly."""
    base = MagicMock(spec=RefitPolicy)
    base.propose.return_value = None
    new_cfg = _config().model_copy(update={
        "matchkeys": [_config().matchkeys[0].model_copy(update={"threshold": 0.5})],
    })
    policy = LLMRefitPolicy(base=base, max_calls_per_run=10)

    assert policy._calls_this_run == 0
    with patch.object(policy, "_call_llm", return_value=new_cfg):
        policy.propose(_yellow_profile(), _config(), RunHistory())
    assert policy._calls_this_run == 1


def test_call_llm_returns_none_without_api_key(monkeypatch):
    """_call_llm returns None immediately when OPENAI_API_KEY is unset."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    policy = LLMRefitPolicy()
    result = policy._call_llm(_yellow_profile(), _config(), RunHistory())
    assert result is None


def test_apply_diff_returns_none_for_non_goldenmatconfig():
    """_apply_diff is a no-op for non-GoldenMatchConfig inputs."""
    policy = LLMRefitPolicy()
    result = policy._apply_diff({"some": "dict"}, {"matchkeys": [{"name": "m", "threshold": 0.5}]})
    assert result is None


# --- shared ConfigEdit vocabulary (consolidation with the optimizer) ---

def test_config_from_edits_folds_shared_vocabulary():
    """The LLM repair now speaks the shared closed ConfigEdit vocabulary:
    a list of edits folds onto the current config."""
    policy = LLMRefitPolicy()
    cfg = _config()  # threshold 0.7, blocking strategy static
    out = policy._config_from_edits(cfg, {"edits": [
        {"op": "threshold_shift", "delta": -0.2},
        {"op": "blocking_strategy", "strategy": "multi_pass"},
    ]})
    assert out is not None
    assert abs(out.matchkeys[0].threshold - 0.5) < 1e-9
    assert out.blocking.strategy == "multi_pass"


def test_config_from_edits_satisfied_and_empty_return_none():
    policy = LLMRefitPolicy()
    cfg = _config()
    assert policy._config_from_edits(cfg, {"action": "satisfied"}) is None
    assert policy._config_from_edits(cfg, {"edits": []}) is None
    assert policy._config_from_edits(cfg, {"edits": [{"op": "junk"}]}) is None
    # non-config input is a no-op
    assert policy._config_from_edits({"x": 1}, {"edits": [{"op": "threshold_shift", "delta": -0.1}]}) is None
