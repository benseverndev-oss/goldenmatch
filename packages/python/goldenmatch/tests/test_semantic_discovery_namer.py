"""Advisory LLM namer (PR-7) — annotates a discovered model with business names.

The namer is opt-in, self-verified, and NEVER authoritative: it only populates
`ProposedModel.naming`; the emitted YAML + certification are computed before naming
and never altered. These tests drive it with a `FakeNamerBackend` (no real LLM) so
they're deterministic, and prove the non-authoritative + graceful-abstain contracts.
"""
from __future__ import annotations

import json

import pyarrow as pa
import pytest
from goldenmatch.semantic import discover_semantic_model


def _fixture() -> dict[str, pa.Table]:
    """customers (customer_id key, region categorical) + orders (order_id key,
    customer_id FK, amount measure, status categorical incl. a 'C' value to gloss)."""
    customers = pa.table({
        "customer_id": ["c1", "c2", "c3"],
        "region": ["west", "east", "west"],
    })
    orders = pa.table({
        "order_id": ["o1", "o2", "o3", "o4"],
        "customer_id": ["c1", "c1", "c2", "c3"],
        "amount": [10.0, 20.0, 30.0, 40.0],
        "status": ["A", "C", "A", "C"],
    })
    return {"customers": customers, "orders": orders}


class FakeNamerBackend:
    """Deterministic stand-in for an LLM. On a `propose` prompt it returns a business
    name for every target named in the prompt; on a `verify` prompt it returns a
    support verdict per target (one target, `value:orders.status=C`, is deliberately
    marked unsupported to exercise the self-critique flagging)."""

    UNSUPPORTED = "value:orders.status=C"

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def propose(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # Import here so the test doesn't depend on internal helpers at collection time.
        from goldenmatch.semantic.discovery.namer import _targets_in_prompt

        targets = _targets_in_prompt(prompt)
        if "VERIFY" in prompt.upper():
            verdicts = [
                {
                    "target": t,
                    "supported": t != self.UNSUPPORTED,
                    "confidence": 0.2 if t == self.UNSUPPORTED else 0.9,
                }
                for t in targets
            ]
            return json.dumps({"verdicts": verdicts})
        names = [{"target": t, "name": _canned_name(t), "evidence": "fixture"} for t in targets]
        return json.dumps({"names": names})


def _canned_name(target: str) -> str:
    # Entity types get generic structural names from discovery ("entity"/"entity_2");
    # the namer's job is to turn those into business names. region classifies as `geo`
    # (no value glossary); status classifies as `categorical` (glossed A/C).
    return {
        "entity:entity": "Customers",
        "entity:entity_2": "Orders",
        "dimension:customers.region": "Region",
        "dimension:orders.status": "Order Status",
        "value:orders.status=A": "Active",
        "value:orders.status=C": "Churned",
        "measure:orders.amount": "Total Revenue",
    }.get(target, target.split(":")[-1])


# --- the module must exist ------------------------------------------------------


def test_name_suggestion_and_backend_protocol_exist():
    from goldenmatch.semantic.discovery.namer import (
        NamerBackend,
        NameSuggestion,
        name_semantic_model,
    )

    assert callable(name_semantic_model)
    assert isinstance(FakeNamerBackend(), NamerBackend)  # runtime_checkable Protocol
    s = NameSuggestion(
        target="entity:customers", kind="entity", suggested_name="Customers",
        confidence=0.9, verified=True, evidence="x",
    )
    assert s.suggested_name == "Customers"


# --- names every kind of target -------------------------------------------------


def test_names_entities_dimensions_values_and_measures():
    from goldenmatch.semantic.discovery.namer import name_semantic_model

    model = discover_semantic_model(_fixture())
    suggestions = name_semantic_model(model, _fixture(), backend=FakeNamerBackend())

    by_kind = {s.kind for s in suggestions}
    assert by_kind == {"entity", "dimension", "value", "measure"}

    named = {s.target: s.suggested_name for s in suggestions}
    assert named.get("entity:entity_2") == "Orders"
    assert named.get("dimension:orders.status") == "Order Status"
    assert named.get("measure:orders.amount") == "Total Revenue"


def test_value_glossary_samples_distinct_categorical_values():
    from goldenmatch.semantic.discovery.namer import name_semantic_model

    model = discover_semantic_model(_fixture())
    suggestions = name_semantic_model(model, _fixture(), backend=FakeNamerBackend())

    values = {s.target: s.suggested_name for s in suggestions if s.kind == "value"}
    # Both distinct status values are glossed from the sampled data.
    assert values.get("value:orders.status=A") == "Active"
    assert values.get("value:orders.status=C") == "Churned"


# --- two-pass self-critique -----------------------------------------------------


def test_self_critique_flags_unsupported_names_but_keeps_them():
    from goldenmatch.semantic.discovery.namer import name_semantic_model

    model = discover_semantic_model(_fixture())
    suggestions = name_semantic_model(model, _fixture(), backend=FakeNamerBackend())

    by_target = {s.target: s for s in suggestions}
    # The deliberately-unsupported (and low-confidence) value is present but flagged.
    assert by_target["value:orders.status=C"].verified is False
    # A supported, high-confidence name is verified.
    assert by_target["dimension:orders.status"].verified is True


# --- non-authoritative + integration -------------------------------------------


def test_naming_is_non_authoritative_yaml_and_certification_unchanged():

    base = discover_semantic_model(_fixture())
    named = discover_semantic_model(_fixture(), name=True, namer_backend=FakeNamerBackend())

    # Structural output byte-identical; only `naming` differs.
    assert named.yaml == base.yaml
    assert named.certification == base.certification
    assert base.naming == []
    assert len(named.naming) > 0
    assert "naming" in named.to_dict()


def test_default_is_off_and_backend_abstains_gracefully():
    from goldenmatch.semantic.discovery.namer import name_semantic_model

    model = discover_semantic_model(_fixture())
    # No backend resolvable -> abstain to an empty list, never raise.
    empty = name_semantic_model(model, _fixture(), backend=None)
    assert empty == []
    # And discover_semantic_model defaults name=False -> no naming.
    assert discover_semantic_model(_fixture()).naming == []


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
