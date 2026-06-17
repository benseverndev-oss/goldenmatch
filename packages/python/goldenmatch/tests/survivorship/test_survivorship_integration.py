"""End-to-end integration tests for the correlated survivorship feature.

Exercises the FULL survivorship path through the real entry point
`build_golden_records_batch`. Each test targets one headline guarantee:

1. Lock-step group prevents Frankenstein records across 3 sources.
2. Combined group + conditional + validate in a single config.
3. Circular `when:` dependency raises ResolutionError end-to-end.
4. Malicious `when:` predicate raises PredicateError, never executes.
5. Non-survivorship configs bypass the survivorship branch (byte-parity).
"""
from __future__ import annotations

import unittest.mock as mock

import polars as pl
import pytest

from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import _survivorship_active, build_golden_records_batch
from goldenmatch.core.survivorship.conditions import PredicateError, ResolutionError


# ---------------------------------------------------------------------------
# Test 1: 3-source Frankenstein prevention via lock-step group
# ---------------------------------------------------------------------------

def test_three_source_group_lock_step_no_frankenstein():
    """A field_group over [street, city, state, zip] pins ALL four columns to ONE
    winning record, even though independent per-field merge would mix sources.

    Setup:
      Row 10 (src_a): long street, no zip   -> 3 of 4 group cols populated
      Row 11 (src_b): short street, zip OK  -> 4 of 4 group cols populated  <- group winner
      Row 12 (src_c): no street, no zip     -> 2 of 4 group cols populated

    Without the group, most_complete would pick row 10's street (longest) and
    row 11's zip (only populated) and row 12 or 11's city -- a Frankenstein.
    With the group, row 11 wins on populated-count and ALL four come from row 11.
    """
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "__source__": ["src_a", "src_b", "src_c"],
        "street": [
            "123 Wonderland Boulevard Apt 4B",  # longest, but row 10 only has 3/4
            "123 Wonder Blvd",
            None,
        ],
        "city": ["Los Angeles", "LA", "Los Angeles"],
        "state": ["CA", "CA", "CA"],
        "zip": [None, "90001", None],
    })

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(
                name="addr",
                columns=["street", "city", "state", "zip"],
                strategy="most_complete",
            )
        ],
    )

    recs = build_golden_records_batch(df, rules, provenance=True)
    assert len(recs) == 1
    rec = recs[0]

    # All four group fields must share the SAME source_row_id (the group winner, row 11).
    group_cols = ["street", "city", "state", "zip"]
    source_row_ids = [rec[c]["source_row_id"] for c in group_cols]
    assert len(set(source_row_ids)) == 1, (
        f"Frankenstein detected: group columns came from multiple rows: {source_row_ids}"
    )
    winner_row_id = source_row_ids[0]
    # Row 11 wins (4 populated) -- confirm values are internally consistent.
    assert winner_row_id == 11
    assert rec["street"]["value"] == "123 Wonder Blvd"
    assert rec["zip"]["value"] == "90001"
    assert rec["city"]["value"] == "LA"
    assert rec["state"]["value"] == "CA"


# ---------------------------------------------------------------------------
# Test 2: Combined group + conditional + validate in ONE config
# ---------------------------------------------------------------------------

def test_combined_group_conditional_validate():
    """A single GoldenRulesConfig with field_group + conditional field_rules + validate.

    Setup (3 rows, cluster 1):
      Row 10 (crm):     street long, state=CA, phone=valid,  dt=2024-01-01, score=valid_short
      Row 11 (billing): street short, state=CA, phone=valid2, dt=2020-01-01, score=NOT_VALID_VERY_LONG
      Row 12 (web):     street mid, state=CA, phone=valid3,  dt=2022-01-01, score=valid_medium

    field_groups: addr=[street,city,state,zip] -- lock-step, row 10 wins (4 populated vs 3/2)
    field_rules:
      phone: [when state in [CA] -> most_recent, else source_priority crm]
      score: most_complete + validate (fake validator: must start with 'valid')

    Expected:
      - All addr fields from row 10 (4/4 populated wins over 3/4 and 2/4).
      - phone: state resolved as CA -> most_recent -> dt=2024 row -> row 10 -> crm phone.
      - score: NOT_VALID_VERY_LONG is the longest but fails validate -> dropped to None ->
               next longest valid candidate wins (valid_medium or valid_short depending on length).
               The key assertion is that NOT_VALID_VERY_LONG is NOT in the result.
    """
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 1],
        "__row_id__": [10, 11, 12],
        "__source__": ["crm", "billing", "web"],
        "street": [
            "123 Main Street Apt 4B",
            "123 Main St",
            "123 Main Street",
        ],
        "city": ["Los Angeles", "LA", "Los Angeles"],
        "state": ["CA", "CA", "CA"],
        "zip": ["90001", "90001", None],
        "phone": ["2125550100", "9995550100", "2125550200"],
        "dt": ["2024-01-01", "2020-01-01", "2022-01-01"],
        "score": ["valid_short", "NOT_VALID_VERY_LONG_INDEED", "valid_medium_ok"],
    })

    # Patch the validate engine: anything NOT starting with 'valid' is dropped.
    import goldenmatch.core.survivorship.validate as V

    def _fake_resolve(name):
        return lambda values: [
            v is not None and str(v).startswith("valid") for v in values
        ]

    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[
            GoldenGroupRule(
                name="addr",
                columns=["street", "city", "state", "zip"],
                strategy="most_complete",
            )
        ],
        field_rules={
            "phone": [
                GoldenFieldRule(
                    strategy="most_recent",
                    date_column="dt",
                    when="state in ['CA', 'NY']",
                ),
                GoldenFieldRule(
                    strategy="source_priority",
                    source_priority=["crm", "billing", "web"],
                ),
            ],
            "score": GoldenFieldRule(
                strategy="most_complete",
                validate="test_validator",
            ),
        },
    )

    with mock.patch.object(V, "_resolve_validator", _fake_resolve):
        recs = build_golden_records_batch(df, rules, provenance=True)

    assert len(recs) == 1
    rec = recs[0]

    # Assertion 1: address group is lock-stepped (all from row 10, 4/4 populated).
    addr_sids = [rec[c]["source_row_id"] for c in ["street", "city", "state", "zip"]]
    assert len(set(addr_sids)) == 1, f"addr group not lock-stepped: {addr_sids}"
    assert addr_sids[0] == 10

    # Assertion 2: phone picks the conditional branch (state=CA -> most_recent -> row 10).
    assert rec["phone"]["value"] == "2125550100"
    assert rec["phone"]["source_row_id"] == 10

    # Assertion 3: validate fired -- NOT_VALID_VERY_LONG_INDEED (longest) is not the winner.
    assert rec["score"]["value"] != "NOT_VALID_VERY_LONG_INDEED"
    assert rec["score"]["value"] is not None
    assert str(rec["score"]["value"]).startswith("valid")


# ---------------------------------------------------------------------------
# Test 3: Circular when: raises ResolutionError end-to-end
# ---------------------------------------------------------------------------

def test_circular_when_raises_resolution_error_end_to_end():
    """A config whose field_rules form a when: cycle must raise ResolutionError
    when passed through build_golden_records_batch, not hang or silently compute.

    The Pydantic validator does NOT do cycle detection -- that fires in
    build_resolution_order at build time (when the full column graph is known).
    """
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "a": ["x", "y"],
        "b": ["p", "q"],
    })

    # a.when references b, b.when references a -> cycle.
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "a": [
                GoldenFieldRule(strategy="most_complete", when="b == 'p'"),
                GoldenFieldRule(strategy="most_complete"),
            ],
            "b": [
                GoldenFieldRule(strategy="most_complete", when="a == 'x'"),
                GoldenFieldRule(strategy="most_complete"),
            ],
        },
    )

    with pytest.raises(ResolutionError):
        build_golden_records_batch(df, rules)


# ---------------------------------------------------------------------------
# Test 4: Malicious when: is rejected, not executed
# ---------------------------------------------------------------------------

def test_malicious_when_predicate_raises_predicate_error():
    """A field_rules entry whose when: is a forbidden AST node (function call,
    attribute access, etc.) must raise PredicateError -- the safe evaluator gates
    the live path and the expression is NEVER executed.

    "__import__('os')" would be a Call node, which the allowlist rejects.
    """
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "safe_col": ["x", "y"],
        "target": ["a", "b"],
    })

    # Build the config directly; the schema validator accepts arbitrary when: strings
    # (it cannot do AST analysis of all possible predicates at config time --
    # cycle detection and predicate safety are enforced at evaluation time).
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "target": [
                # This uses a Call node (__import__) which the safe evaluator disallows.
                GoldenFieldRule(strategy="most_complete", when="__import__('os') == 1"),
                GoldenFieldRule(strategy="most_complete"),
            ],
        },
    )

    with pytest.raises(PredicateError):
        build_golden_records_batch(df, rules)


# ---------------------------------------------------------------------------
# Test 5: Byte-parity -- non-survivorship configs bypass the survivorship branch
# ---------------------------------------------------------------------------

def test_non_survivorship_configs_bypass_survivorship_branch():
    """Plain GoldenRulesConfig (default_strategy only) and a static field_rules
    config (single non-conditional, non-validated rule) must:
      (a) Report _survivorship_active() == False.
      (b) Produce the expected golden values (locking existing behavior).

    This proves the new survivorship branch is NOT taken for these configs,
    guaranteeing byte-identical behavior on the existing fast/slow path.
    """
    df = pl.DataFrame({
        "__cluster_id__": [1, 1, 2, 2],
        "__row_id__": [10, 11, 20, 21],
        "__source__": ["crm", "billing", "crm", "billing"],
        "name": ["Alice Smith", "Alice S", "Bob Jones", "Robert Jones"],
        "city": ["New York", "NY", "Chicago", "Chicago"],
    })

    # Config (a): plain default_strategy.
    rules_plain = GoldenRulesConfig(default_strategy="most_complete")
    assert _survivorship_active(rules_plain) is False

    recs_plain = build_golden_records_batch(df, rules_plain)
    assert len(recs_plain) == 2
    # most_complete picks the longest non-null value per field.
    plain_by_cid = {r["__cluster_id__"]: r for r in recs_plain}
    assert plain_by_cid[1]["name"]["value"] == "Alice Smith"  # longer
    assert plain_by_cid[1]["city"]["value"] == "New York"     # longer
    assert plain_by_cid[2]["name"]["value"] == "Robert Jones" # longer
    assert plain_by_cid[2]["city"]["value"] == "Chicago"      # same length, stable pick

    # Config (b): static field_rules (source_priority, no when, no validate, not a list).
    rules_static = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={
            "name": GoldenFieldRule(
                strategy="source_priority",
                source_priority=["crm", "billing"],
            )
        },
    )
    assert _survivorship_active(rules_static) is False

    recs_static = build_golden_records_batch(df, rules_static)
    assert len(recs_static) == 2
    static_by_cid = {r["__cluster_id__"]: r for r in recs_static}
    # source_priority crm > billing: crm names should win.
    assert static_by_cid[1]["name"]["value"] == "Alice Smith"   # crm row 10
    assert static_by_cid[2]["name"]["value"] == "Bob Jones"     # crm row 20
    # city still governed by default most_complete.
    assert static_by_cid[1]["city"]["value"] == "New York"
