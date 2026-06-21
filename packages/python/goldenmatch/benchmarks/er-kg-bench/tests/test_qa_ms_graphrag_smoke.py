"""Protocol conformance + that graphrag imports. Does NOT run build_index /
local_search (version-sensitive config + expensive real LLM) -- those are validated
in the opt-in bench-graphrag-qa lane. Runs in the graphrag isolated venv."""
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
