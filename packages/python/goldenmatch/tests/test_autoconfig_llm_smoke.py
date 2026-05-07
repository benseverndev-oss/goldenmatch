"""Smoke test that hits the real OpenAI API -- gated on env var."""
import os
import pytest


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="OPENAI_API_KEY not set",
)
@pytest.mark.skipif(
    os.environ.get("GOLDENMATCH_LLM_SMOKE_TEST") != "1",
    reason="set GOLDENMATCH_LLM_SMOKE_TEST=1 to run live API smoke",
)
def test_llm_policy_real_api_round_trip():
    """One real API call to verify the prompt + parsing actually work."""
    from goldenmatch.core.autoconfig_policy import LLMRefitPolicy, HeuristicRefitPolicy
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import (
        ComplexityProfile, DataProfile, BlockingProfile, ScoringProfile,
        ClusterProfile, MatchkeyProfile, FieldStats,
    )

    profile = ComplexityProfile(
        data=DataProfile(
            n_rows=100, n_cols=4,
            column_types={"name": "text", "city": "text",
                          "email": "text", "phone": "text"},
        ),
        blocking=BlockingProfile(
            keys_used=[["city"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p99=20,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=200, candidates_compared=500,
            mass_above_threshold=0.4, dip_statistic=0.05,
            mass_in_borderline=0.4,  # YELLOW
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={"name": FieldStats(0.5, 0.0, 10)}),
    )
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="name_match", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )

    policy = LLMRefitPolicy(HeuristicRefitPolicy())
    out = policy.propose(profile, config, RunHistory())
    # No assertion on what the LLM returns; just that it didn't crash
    assert out is None or isinstance(out, GoldenMatchConfig)
