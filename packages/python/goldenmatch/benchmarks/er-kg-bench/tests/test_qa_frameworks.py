"""Real-framework QA baseline is opt-in + best-effort (never fatal, never gated)."""

from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench import qa_eval, qa_frameworks  # noqa: E402
from erkgbench.qa_loader import QAItem  # noqa: E402

_MEN = {0: "A", 1: "A-form2", 2: "B"}
_TYP = {0: "org", 1: "org", 2: "org"}
_CTX = {0: "", 1: "", 2: ""}
_FACTS = {0: ["f0"], 1: ["f1"]}
_ITEM = QAItem(qa_id="x", entity_id="E", seed_surface="A", question="?",
               facts={0: "f0", 1: "f1"}, gold_answer="f0 and f1")


def test_framework_adapters_returns_list_never_raises():
    # On a plain venv the framework libs are absent -> empty; must never raise.
    adapters = qa_frameworks.framework_adapters()
    assert isinstance(adapters, list)


def test_run_qa_eval_skips_a_raising_framework_adapter():
    class _BoomFramework:
        name = "neo4j-graphrag"

        def resolve(self, records):
            raise ImportError("neo4j_graphrag not installed")

    rows = qa_eval.run_qa_eval(
        [_BoomFramework()], records=[], items=[_ITEM], facts_by_record=_FACTS,
        mentions=_MEN, types=_TYP, contexts=_CTX, failure_class={0: "abbr", 1: "abbr"},
    )
    assert rows[0]["status"] == "skipped"
    assert "neo4j_graphrag" in rows[0]["error"]
