"""Namer quality eval harness (PR-19) — the ninth (last) frontier slice.

Slices 11-18 were deterministic, no-LLM. The namer (PR-7) is the opposite: it calls a real
LLM. This slice measures how good those names are against a labeled gold set — a
deterministic scorer (`score_naming`) unit-tested with hand-built suggestions and a
dict-driven fake backend, plus a thin `run_namer_eval` wrapper. The real provider is opt-in
behind `GOLDENMATCH_NAMER_EVAL_LIVE` (see the skipped live test), so CI never spends API
calls.
"""
from __future__ import annotations

import json
import os

import pyarrow as pa
import pytest
from goldenmatch.semantic import (
    NamerQuality,
    NameSuggestion,
    discover_semantic_model,
    run_namer_eval,
    score_naming,
)


def _tables() -> dict[str, pa.Table]:
    customers = pa.table({"customer_id": ["c1", "c2", "c3"], "region": ["w", "e", "w"]})
    orders = pa.table({"order_id": ["o1", "o2", "o3"], "customer_id": ["c1", "c1", "c2"],
                       "amount": [1.0, 2.0, 3.0]})
    return {"customers": customers, "orders": orders}


def _sug(target: str, name: str, *, verified: bool = True) -> NameSuggestion:
    return NameSuggestion(target=target, kind="measure", suggested_name=name,
                          confidence=0.9, verified=verified, evidence="x")


# --- pure scorer -----------------------------------------------------------------


def test_perfect_naming_scores_one():
    gold = {"measure:orders.amount": "Total Revenue", "dimension:customers.region": "Region"}
    suggestions = [_sug("measure:orders.amount", "Total Revenue"),
                   _sug("dimension:customers.region", "Region")]
    q = score_naming(suggestions, gold)
    assert isinstance(q, NamerQuality)
    assert q.n_targets == 2
    assert q.coverage == 1.0
    assert q.accuracy == 1.0
    assert q.precision == 1.0


def test_normalization_ignores_case_and_punctuation():
    gold = {"measure:orders.amount": "Total Revenue"}
    q = score_naming([_sug("measure:orders.amount", "total-revenue!")], gold)
    assert q.accuracy == 1.0


def test_wrong_name_lowers_accuracy_and_precision():
    gold = {"measure:orders.amount": "Total Revenue", "dimension:customers.region": "Region"}
    suggestions = [_sug("measure:orders.amount", "Total Revenue"),
                   _sug("dimension:customers.region", "Zip Code")]  # wrong
    q = score_naming(suggestions, gold)
    assert q.accuracy == 0.5
    assert q.precision == 0.5  # 1 correct of 2 suggested


def test_missing_suggestion_lowers_coverage_not_precision():
    gold = {"measure:orders.amount": "Total Revenue", "dimension:customers.region": "Region"}
    q = score_naming([_sug("measure:orders.amount", "Total Revenue")], gold)  # region absent
    assert q.coverage == 0.5
    assert q.accuracy == 0.5
    assert q.precision == 1.0  # everything it DID suggest was right


def test_gold_accepts_aliases():
    gold = {"measure:orders.amount": {"Total Revenue", "Revenue", "Sales"}}
    q = score_naming([_sug("measure:orders.amount", "Sales")], gold)
    assert q.accuracy == 1.0


def test_verified_accuracy_excludes_unverified_hits():
    gold = {"measure:orders.amount": "Total Revenue"}
    q = score_naming([_sug("measure:orders.amount", "Total Revenue", verified=False)], gold)
    assert q.accuracy == 1.0            # the name is right
    assert q.verified_accuracy == 0.0   # but it never passed self-critique


def test_to_dict_shape_and_per_target_results():
    gold = {"measure:orders.amount": "Total Revenue"}
    d = score_naming([_sug("measure:orders.amount", "Total Revenue")], gold).to_dict()
    assert set(d) >= {"n_targets", "coverage", "accuracy", "precision",
                      "verified_accuracy", "results"}
    r = d["results"][0]
    assert set(r) >= {"target", "gold", "suggested", "matched", "verified"}


# --- end-to-end runner with the deterministic fake backend -----------------------


class _FakeBackend:
    """Answers exactly the targets a prompt lists, per the namer's JSON contract."""

    _NAMES = {
        "measure:orders.amount": "Total Revenue",
        "dimension:customers.region": "Region",
    }

    def propose(self, prompt: str) -> str:
        from goldenmatch.semantic.discovery.namer import _targets_in_prompt

        targets = _targets_in_prompt(prompt)
        if "VERIFY" in prompt.upper():
            return json.dumps({"verdicts": [
                {"target": t, "supported": True, "confidence": 0.9} for t in targets
            ]})
        return json.dumps({"names": [
            {"target": t, "name": self._NAMES.get(t, t.split(":")[-1]), "evidence": "fix"}
            for t in targets
        ]})


def test_run_namer_eval_end_to_end_is_deterministic():
    tables = _tables()
    model = discover_semantic_model(tables)
    gold = {"measure:orders.amount": "Total Revenue",
            "dimension:customers.region": "Region"}
    q = run_namer_eval(model, tables, gold, backend=_FakeBackend())
    assert q.accuracy == 1.0
    assert q.coverage == 1.0


# --- live provider path: opt-in only, never spends API calls in CI ----------------


@pytest.mark.skipif(not os.getenv("GOLDENMATCH_NAMER_EVAL_LIVE"),
                    reason="live LLM eval is opt-in (set GOLDENMATCH_NAMER_EVAL_LIVE=1)")
def test_run_namer_eval_live_provider():
    from goldenmatch.semantic.discovery.namer import load_namer_backend

    backend = load_namer_backend()
    if backend is None:
        pytest.skip("no namer provider/key resolved")
    tables = _tables()
    model = discover_semantic_model(tables)
    gold = {"measure:orders.amount": {"Total Revenue", "Revenue", "Total Amount"}}
    q = run_namer_eval(model, tables, gold, backend=backend)
    assert q.coverage > 0.0
