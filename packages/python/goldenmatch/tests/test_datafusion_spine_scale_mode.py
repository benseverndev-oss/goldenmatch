"""Stage D: scale-mode feature-gate. ``run_spine`` must raise an explicit
error (never silently ignore) when ``mode="scale"`` is paired with an
unsupported surface, and must refuse a non-scale config."""
from __future__ import annotations

import pytest
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)


def _base_config(**overrides) -> GoldenMatchConfig:
    """Valid single-field weighted scale-mode config (the supported shape).
    ``overrides`` patch top-level fields; matchkey-level surfaces are set by
    mutating the returned config's matchkey in the test."""
    cfg = GoldenMatchConfig(
        mode="scale",
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_last",
                fields=[MatchkeyField(
                    column="last_name", scorer="jaro_winkler", weight=1.0,
                )],
                comparison="weighted",
                threshold=0.85,
            ),
        ],
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _run(cfg):
    from goldenmatch.backends.datafusion_spine import run_spine
    return run_spine([], cfg)


def test_gate_rejects_non_scale_mode():
    cfg = _base_config()
    cfg.mode = "standard"
    with pytest.raises(ValueError, match="mode='scale'"):
        _run(cfg)


def test_gate_rejects_llm_boost():
    with pytest.raises(NotImplementedError, match="boost"):
        _run(_base_config(llm_boost=True))


def test_gate_rejects_llm_auto():
    with pytest.raises(NotImplementedError, match="LLM"):
        _run(_base_config(llm_auto=True))


def test_gate_rejects_llm_scorer_enabled():
    from goldenmatch.config.schemas import LLMScorerConfig
    with pytest.raises(NotImplementedError, match="LLM"):
        _run(_base_config(llm_scorer=LLMScorerConfig(enabled=True)))


def test_gate_allows_llm_scorer_present_but_disabled():
    # A disabled LLM scorer is inert -> must NOT trip the gate. Assert the
    # gate itself (its precise unit boundary) returns without raising; we
    # don't drive the full empty-blocks pipeline here (that path hits an
    # unrelated pre-existing empty-input SchemaError in the frames-out tail).
    from goldenmatch.backends.datafusion_spine import (
        _validate_scale_mode_supported,
    )
    from goldenmatch.config.schemas import LLMScorerConfig

    cfg = _base_config(llm_scorer=LLMScorerConfig(enabled=False))
    assert _validate_scale_mode_supported(cfg) is None


def test_gate_rejects_domain_enabled():
    from goldenmatch.config.schemas import DomainConfig
    with pytest.raises(NotImplementedError, match="domain"):
        _run(_base_config(domain=DomainConfig(enabled=True)))


def test_gate_rejects_rerank():
    cfg = _base_config()
    cfg.get_matchkeys()[0].rerank = True
    with pytest.raises(NotImplementedError, match="rerank"):
        _run(cfg)


def test_gate_rejects_negative_evidence():
    cfg = _base_config()
    cfg.get_matchkeys()[0].negative_evidence = [
        NegativeEvidenceField(
            field="phone", scorer="exact", threshold=0.9, penalty=0.5,
        )
    ]
    with pytest.raises(NotImplementedError, match="negative.evidence"):
        _run(cfg)


def test_gate_rejects_probabilistic_matchkey():
    # A weighted matchkey plus a stray probabilistic one: the gate iterates
    # ALL matchkeys (never silently ignores the extra), so it errors.
    cfg = GoldenMatchConfig(
        mode="scale",
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_last",
                fields=[MatchkeyField(
                    column="last_name", scorer="jaro_winkler", weight=1.0,
                )],
                comparison="weighted",
                threshold=0.85,
            ),
            MatchkeyConfig(
                name="prob_mk",
                fields=[MatchkeyField(
                    column="last_name", scorer="jaro_winkler",
                )],
                comparison="probabilistic",
            ),
        ],
    )
    with pytest.raises(NotImplementedError, match="weighted"):
        _run(cfg)
