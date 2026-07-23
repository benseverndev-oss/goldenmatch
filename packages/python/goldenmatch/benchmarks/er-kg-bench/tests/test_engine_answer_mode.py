"""GOLDENGRAPH_QA_ANSWER_MODE is an ANSWER-time override of the retrieval/synth mode, so
the generic env-A/B (run_engine_ab_env) can flip local-vs-hybrid synthesis over ONE shared
(hybrid) build. Precedence: `mode` kwarg > env > engine's configured mode. Patches
goldengraph.answer.ask -- no store, no LLM."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.engines.goldengraph import GoldenGraphQAEngine  # noqa: E402


class _Stub:
    input_tokens = 0
    output_tokens = 0

    def complete(self, prompt):  # pragma: no cover - not called (ask is patched)
        return ""


def _engine(retrieval_mode):
    return GoldenGraphQAEngine(llm=_Stub(), embedder=_Stub(), retrieval_mode=retrieval_mode)


_HANDLE = {"store": object(), "valid_t": 0, "tx_t": 0, "passages": None, "query_schema": None}


def _capture_mode(monkeypatch):
    seen = {}

    def _fake_ask(question, store, **kw):
        seen["mode"] = kw.get("mode")
        return "Answer: X"

    monkeypatch.setattr("goldengraph.answer.ask", _fake_ask)
    return seen


def test_env_overrides_configured_mode(monkeypatch):
    seen = _capture_mode(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_QA_ANSWER_MODE", "hybrid")
    _engine("local").answer(_HANDLE, "q?")
    assert seen["mode"] == "hybrid"  # env beats the engine's configured 'local'


def test_kwarg_beats_env(monkeypatch):
    seen = _capture_mode(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_QA_ANSWER_MODE", "hybrid")
    _engine("local").answer(_HANDLE, "q?", mode="local")
    assert seen["mode"] == "local"  # explicit kwarg wins over env


def test_unset_uses_configured_mode(monkeypatch):
    seen = _capture_mode(monkeypatch)
    monkeypatch.delenv("GOLDENGRAPH_QA_ANSWER_MODE", raising=False)
    _engine("hybrid").answer(_HANDLE, "q?")
    assert seen["mode"] == "hybrid"  # byte-identical default path


def test_empty_env_ignored(monkeypatch):
    seen = _capture_mode(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_QA_ANSWER_MODE", "")
    _engine("local").answer(_HANDLE, "q?")
    assert seen["mode"] == "local"  # empty string is not a mode


def _capture_passage_k(monkeypatch):
    seen = {}

    def _fake_ask(question, store, **kw):
        seen["passage_k"] = kw.get("passage_k")
        return "Answer: X"

    monkeypatch.setattr("goldengraph.answer.ask", _fake_ask)
    return seen


def _engine_pk(passage_k):
    return GoldenGraphQAEngine(llm=_Stub(), embedder=_Stub(), passage_k=passage_k)


def test_passage_k_env_override_beats_configured(monkeypatch):
    # GOLDENGRAPH_QA_PASSAGE_K is an ANSWER-time override so run_engine_ab_env can sweep it over
    # ONE shared build. The engine was constructed with passage_k=10; the env flips it per call.
    seen = _capture_passage_k(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_QA_PASSAGE_K", "3")
    _engine_pk(10).answer(_HANDLE, "q?")
    assert seen["passage_k"] == 3


def test_passage_k_unset_uses_configured(monkeypatch):
    seen = _capture_passage_k(monkeypatch)
    monkeypatch.delenv("GOLDENGRAPH_QA_PASSAGE_K", raising=False)
    _engine_pk(7).answer(_HANDLE, "q?")
    assert seen["passage_k"] == 7  # byte-identical default path


def test_passage_k_invalid_env_falls_back(monkeypatch):
    seen = _capture_passage_k(monkeypatch)
    monkeypatch.setenv("GOLDENGRAPH_QA_PASSAGE_K", "notanint")
    _engine_pk(10).answer(_HANDLE, "q?")
    assert seen["passage_k"] == 10  # non-int env is ignored, not a crash


def test_engine_default_retrieval_mode_is_hybrid(monkeypatch):
    # Ship 2026-07-22: the bench engine now defaults to hybrid (GOLDENGRAPH_QA_MODE
    # unset), so build_kg indexes passages and answer() uses hybrid synthesis by
    # default -- the measured +169%/+143% config. =local restores the old default.
    monkeypatch.delenv("GOLDENGRAPH_QA_MODE", raising=False)
    assert GoldenGraphQAEngine(llm=_Stub(), embedder=_Stub())._retrieval_mode == "hybrid"
    monkeypatch.setenv("GOLDENGRAPH_QA_MODE", "local")
    assert GoldenGraphQAEngine(llm=_Stub(), embedder=_Stub())._retrieval_mode == "local"
