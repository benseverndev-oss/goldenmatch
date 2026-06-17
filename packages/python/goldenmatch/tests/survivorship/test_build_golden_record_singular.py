"""Regression tests: build_golden_record (singular) must route survivorship configs
through the staged resolve_cluster pass, not the plain per-column merge loop.

Three cases:
1. field_groups lock-step: all columns in the group come from the same winning row.
2. list-form (conditional) field_rules: must not pass a list to merge_field.
3. Plain config (no survivorship levers): existing loop unchanged, byte-identical.
"""
import polars as pl
from goldenmatch.config.schemas import GoldenFieldRule, GoldenGroupRule, GoldenRulesConfig
from goldenmatch.core.golden import build_golden_record


def _addr_frame():
    return pl.DataFrame({
        "__row_id__": [10, 11],
        "__source__": ["billing", "crm"],
        "street": ["1 Main Street Apt 4B", "1 Main St"],
        "city": ["LA", "LA"],
        "zip": [None, "90001"],
        "state": ["CA", "CA"],
    })


def test_singular_builder_lock_steps_field_group():
    # row 11 wins the group (3/3 populated); street + zip must come from the SAME row.
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"], strategy="most_complete")],
    )
    rec = build_golden_record(_addr_frame(), rules)
    # Without lock-step, most_complete would pick the LONGER street ('1 Main Street Apt 4B', row 10)
    # while zip would come from row 11 -> Frankenstein. Lock-step pins both to row 11.
    assert rec["street"]["value"] == "1 Main St"
    assert rec["zip"]["value"] == "90001"


def test_singular_builder_handles_list_form_field_rules():
    # A list-form (conditional) rule must not be passed raw to merge_field.
    df = pl.DataFrame({
        "__row_id__": [10, 11],
        "__source__": ["billing", "crm"],
        "state": ["CA", "CA"],
        "phone": ["5551112222", "5553334444"],
        "dt": ["2020-01-01", "2024-01-01"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": [
            GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'"),
            GoldenFieldRule(strategy="source_priority", source_priority=["crm"]),
        ]},
    )
    rec = build_golden_record(df, rules)
    assert rec["phone"]["value"] == "5553334444"  # most_recent branch -> 2024 row


def test_singular_builder_plain_config_unchanged():
    # Non-survivorship config must take the existing loop (byte-identical).
    df = pl.DataFrame({"__row_id__": [10, 11], "name": ["Alice Smith", "Alice S"]})
    rules = GoldenRulesConfig(default_strategy="most_complete")
    rec = build_golden_record(df, rules)
    assert rec["name"]["value"] == "Alice Smith"  # longest -> most_complete
    assert "__golden_confidence__" in rec
