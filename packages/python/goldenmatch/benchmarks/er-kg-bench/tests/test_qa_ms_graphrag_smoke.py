"""Protocol conformance + that graphrag imports + that the scaffolded config is
valid for the installed graphrag version. Does NOT run build_index / local_search
(expensive real LLM) -- those are validated in the opt-in bench-graphrag-qa lane.
Runs in the graphrag isolated venv."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

pytest.importorskip("graphrag")

from erkgbench.qa_e2e.engines.ms_graphrag import MSGraphRAGQAEngine  # noqa: E402
from erkgbench.qa_e2e.harness import QAEngine  # noqa: E402


def test_ms_graphrag_engine_conforms_to_protocol():
    eng = MSGraphRAGQAEngine(model="gpt-4o-mini")
    assert isinstance(eng, QAEngine)
    assert eng.name == "ms_graphrag"
    assert eng.fidelity == "real-e2e"


def test_ms_graphrag_config_is_valid_for_installed_version(tmp_path):
    """Scaffold + load the config and assert the chat/embedding models are actually
    populated. No LLM, no network. Guards the version-schema drift that silently
    blanked the models when a hand-written 2.x settings.yaml met graphrag 3.x."""
    eng = MSGraphRAGQAEngine(model="gpt-4o-mini", embedding_model="text-embedding-3-large")
    # _build_config os.chdir()s into the project dir (graphrag's load_config does);
    # restore CWD so the chdir doesn't leak into the rest of the test session.
    import os

    cwd = os.getcwd()
    try:
        cfg = eng._build_config(str(tmp_path))
    finally:
        os.chdir(cwd)

    assert cfg.completion_models, "no completion model configured (schema drift?)"
    assert cfg.embedding_models, "no embedding model configured (schema drift?)"
    chat = next(iter(cfg.completion_models.values()))
    assert chat.model == "gpt-4o-mini"
    embed = next(iter(cfg.embedding_models.values()))
    assert embed.model == "text-embedding-3-large"
    # The embedding model dim must match the vector store column dim, or local_search
    # fails on a LanceDB dim mismatch (1536 vs 3072). 3-large == default 3072.
    assert cfg.vector_store.vector_size == 3072
