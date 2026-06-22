"""LLM entity canonicalization (#1091).

The LLM path is exercised with a stub ``llm_call`` (no network); the
deterministic fallback + graceful degradation are the always-on core.
"""
from __future__ import annotations

import json

import pytest
from goldenmatch.core.llm_canonicalize import (
    CanonicalRecord,
    canonicalize_cluster,
)

RECORDS = [
    {"name": "Bob", "email": "bob@x.com", "phone": None, "__row_id__": 0},
    {"name": "Robert Smith", "email": "bob@x.com", "phone": "555-1234", "__row_id__": 1},
]


def _stub_llm(payload: dict, in_tok: int = 100, out_tok: int = 30):
    return lambda prompt: (json.dumps(payload), in_tok, out_tok)


class _Budget:
    def __init__(self, *, exhausted=False, can=True, cost=0.0123):
        self.budget_exhausted = exhausted
        self._can = can
        self.total_cost_usd = cost
        self.usage: list = []

    def can_send(self, n):  # noqa: ARG002
        return self._can

    def record_usage(self, i, o, m):
        self.usage.append((i, o, m))


# ── deterministic fallback ───────────────────────────────────────────────────


def test_deterministic_when_no_provider(monkeypatch):
    monkeypatch.setattr(
        "goldenmatch.core.llm_scorer._detect_provider", lambda: (None, None),
    )
    out = canonicalize_cluster(RECORDS)
    assert isinstance(out, CanonicalRecord)
    assert out.method == "deterministic"
    # most-complete value per field; internal keys ignored.
    assert out.record["name"] == "Robert Smith"
    assert out.provenance["name"].source_index == 1
    assert out.record["phone"] == "555-1234"
    assert out.provenance["phone"].source_index == 1
    assert "__row_id__" not in out.record


def test_empty_cluster():
    out = canonicalize_cluster([])
    assert out.method == "deterministic"
    assert out.record == {}


# ── LLM path (stubbed) ───────────────────────────────────────────────────────


def test_llm_choice_with_provenance():
    payload = {
        "fields": {
            "name": {"value": "Robert Smith", "source": 1, "reason": "full name"},
            "email": {"value": "bob@x.com", "source": 0, "reason": "agree"},
            "phone": {"value": "555-1234", "source": 1, "reason": "present"},
        },
        "rationale": "merged duplicates",
    }
    out = canonicalize_cluster(RECORDS, llm_call=_stub_llm(payload))
    assert out.method == "llm"
    assert out.rationale == "merged duplicates"
    assert out.record == {"name": "Robert Smith", "email": "bob@x.com", "phone": "555-1234"}
    assert out.provenance["name"].source_index == 1
    assert out.provenance["name"].rationale == "full name"


def test_llm_source_corrected_when_index_wrong():
    # The model claims source=0 for the phone, but record 0's phone is None;
    # provenance is corrected to the record that actually holds the value.
    payload = {
        "fields": {"phone": {"value": "555-1234", "source": 0, "reason": "x"}},
        "rationale": "r",
    }
    out = canonicalize_cluster(RECORDS, fields=["phone"], llm_call=_stub_llm(payload))
    assert out.record["phone"] == "555-1234"
    assert out.provenance["phone"].source_index == 1


def test_llm_synthesized_value_has_no_source():
    payload = {
        "fields": {"name": {"value": "Bobby Smith", "source": 9, "reason": "blend"}},
        "rationale": "r",
    }
    out = canonicalize_cluster(RECORDS, fields=["name"], llm_call=_stub_llm(payload))
    assert out.record["name"] == "Bobby Smith"
    assert out.provenance["name"].source_index is None  # not in any record


def test_llm_skipped_field_falls_back_per_cell():
    # The model only answers 'name'; 'phone' falls back to deterministic.
    payload = {"fields": {"name": {"value": "Bob", "source": 0}}, "rationale": "r"}
    out = canonicalize_cluster(RECORDS, llm_call=_stub_llm(payload))
    assert out.record["name"] == "Bob"
    assert out.record["phone"] == "555-1234"  # deterministic
    assert out.provenance["phone"].rationale == "most complete value"


def test_malformed_llm_reply_degrades():
    out = canonicalize_cluster(RECORDS, llm_call=lambda p: ("not json at all", 1, 1))
    assert out.method == "deterministic"
    assert out.record["name"] == "Robert Smith"


def test_llm_call_raising_degrades():
    def boom(prompt):
        raise RuntimeError("network down")

    out = canonicalize_cluster(RECORDS, llm_call=boom)
    assert out.method == "deterministic"


# ── budget ───────────────────────────────────────────────────────────────────


def test_budget_exhausted_degrades():
    b = _Budget(exhausted=True)
    out = canonicalize_cluster(
        RECORDS, llm_call=_stub_llm({"fields": {}, "rationale": ""}), budget=b,
    )
    assert out.method == "deterministic"
    assert b.usage == []  # no call charged


def test_budget_refuses_degrades():
    b = _Budget(can=False)
    out = canonicalize_cluster(
        RECORDS, llm_call=_stub_llm({"fields": {}, "rationale": ""}), budget=b,
    )
    assert out.method == "deterministic"


def test_budget_records_usage_and_cost_on_success():
    b = _Budget(cost=0.05)
    payload = {"fields": {"name": {"value": "Bob", "source": 0}}, "rationale": "r"}
    out = canonicalize_cluster(RECORDS, llm_call=_stub_llm(payload, 120, 40), budget=b)
    assert out.method == "llm"
    assert b.usage == [(120, 40, "gpt-4o-mini")]
    assert out.llm_cost_usd == pytest.approx(0.05)


def test_as_dict_serializable():
    payload = {"fields": {"name": {"value": "Bob", "source": 0, "reason": "r"}}, "rationale": "ok"}
    out = canonicalize_cluster(RECORDS, llm_call=_stub_llm(payload))
    blob = json.dumps(out.as_dict())
    assert "provenance" in blob and "method" in blob
