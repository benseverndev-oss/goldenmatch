"""Aggregator unit tests: the engine x ambiguity table, the pooled hop-decay curve,
and the summary, on synthetic result dicts (no LLM, no network)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from erkgbench.qa_e2e.aggregate_qa_e2e import (  # noqa: E402
    load_results,
    render_markdown,
)


def _run(engine, ambiguity, answer_match, per_question, cost=0.01, token_f1=0.02):
    return {
        "engine": engine,
        "corpus": "engineered",
        "model": "gpt-4o-mini",
        "ambiguity": ambiguity,
        "n_questions": len(per_question),
        "n_answered": len(per_question),
        "answer_match": answer_match,
        "exact_match": 0.0,
        "token_f1": token_f1,
        "support_recall": 0.0,
        "decay_curve": {},
        "cost_usd": cost,
        "budget_exhausted": False,
        "per_question": per_question,
    }


def _pq(hop, am):
    return {"id": f"q{hop}", "hop_count": hop, "answer_match": am}


def test_render_has_ambiguity_and_hop_tables():
    results = [
        _run("goldengraph", 0.0, 0.8, [_pq(1, 1.0), _pq(2, 1.0), _pq(3, 0.0)]),
        _run("goldengraph", 1.0, 0.4, [_pq(1, 1.0), _pq(2, 0.0), _pq(3, 0.0)]),
        _run("lightrag", 0.0, 0.6, [_pq(1, 1.0), _pq(2, 0.0), _pq(3, 0.0)]),
        _run("lightrag", 1.0, 0.2, [_pq(1, 0.0), _pq(2, 0.0), _pq(3, 0.0)]),
    ]
    md = render_markdown(results)
    # ambiguity columns present
    assert "amb=0.0" in md and "amb=1.0" in md
    # goldengraph row carries its two ambiguity answer-match cells
    assert "| goldengraph | 0.800 | 0.400 |" in md
    assert "| lightrag | 0.600 | 0.200 |" in md
    # pooled 1-hop decay: goldengraph saw 1-hop am 1.0 and 1.0 -> mean 1.000
    assert "by hop count" in md
    # 2-hop pooled for goldengraph: 1.0 and 0.0 -> 0.500
    hop_section = md.split("by hop count")[1]
    assert "| goldengraph | 1.000 | 0.500 | 0.000 |" in hop_section


def test_summary_totals_cost_and_means():
    results = [
        _run("goldengraph", 0.0, 0.8, [_pq(1, 1.0)], cost=0.10),
        _run("goldengraph", 1.0, 0.4, [_pq(1, 0.0)], cost=0.30),
    ]
    md = render_markdown(results)
    # mean answer-match (0.8+0.4)/2 = 0.600; total cost 0.40
    assert "| goldengraph | 0.600 |" in md
    assert "0.4000 | 2 |" in md


def test_load_results_reads_and_flattens(tmp_path):
    (tmp_path / "results_qa_e2e_goldengraph_amb0.0.json").write_text(
        json.dumps([_run("goldengraph", 0.0, 0.5, [_pq(1, 1.0)])]), encoding="utf-8"
    )
    (tmp_path / "results_qa_e2e_lightrag_amb1.0.json").write_text(
        json.dumps([_run("lightrag", 1.0, 0.1, [_pq(1, 0.0)])]), encoding="utf-8"
    )
    # an unrelated file must be ignored
    (tmp_path / "notes.json").write_text("[]", encoding="utf-8")
    loaded = load_results(tmp_path)
    assert {r["engine"] for r in loaded} == {"goldengraph", "lightrag"}
    assert len(loaded) == 2


def test_missing_cells_render_dash():
    # goldengraph only ran amb=0.0; lightrag only amb=1.0 -> each missing one column
    results = [
        _run("goldengraph", 0.0, 0.8, [_pq(1, 1.0)]),
        _run("lightrag", 1.0, 0.2, [_pq(1, 0.0)]),
    ]
    md = render_markdown(results)
    assert "| goldengraph | 0.800 | - |" in md
    assert "| lightrag | - | 0.200 |" in md
