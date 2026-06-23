"""Retrieval-budget knobs on the goldengraph QA engine.

The 2026-06-23 N=50 trace's dominant failure shifted to RETRIEVAL-BUDGET: answers
that were connected to the seeds (same_component=True) but fell OUTSIDE the
budget-capped ball (the adapter called `ask` with node_budget defaulting to 64).
These pure tests pin that the engine now reads a raised, env-tunable hops +
node_budget at construction (so a sweep can set them per run) and that explicit
args win. No native store / LLM needed -- the engine imports goldengraph lazily.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_e2e.engines.goldengraph import GoldenGraphQAEngine  # noqa: E402


def _engine(**kw):
    # __init__ only wraps llm/embedder (no calls), so stubs are fine.
    return GoldenGraphQAEngine(llm=object(), embedder=object(), **kw)


def test_retrieval_budget_defaults_are_raised(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_QA_RETRIEVAL_HOPS", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_QA_NODE_BUDGET", raising=False)
    eng = _engine()
    assert eng._retrieval_hops == 6  # was 4
    assert eng._node_budget == 256  # was the ask() default of 64


def test_retrieval_budget_reads_env_at_construction(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_QA_RETRIEVAL_HOPS", "8")
    monkeypatch.setenv("GOLDENGRAPH_QA_NODE_BUDGET", "512")
    eng = _engine()
    assert eng._retrieval_hops == 8
    assert eng._node_budget == 512


def test_explicit_args_beat_env(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_QA_RETRIEVAL_HOPS", "8")
    monkeypatch.setenv("GOLDENGRAPH_QA_NODE_BUDGET", "512")
    eng = _engine(retrieval_hops=3, node_budget=99)
    assert eng._retrieval_hops == 3
    assert eng._node_budget == 99
