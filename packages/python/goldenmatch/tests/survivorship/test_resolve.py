import polars as pl
from goldenmatch.config.schemas import GoldenRulesConfig, GoldenGroupRule, GoldenFieldRule
from goldenmatch.core.golden import build_golden_records_batch


def _frame():
    return pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "__source__": ["billing", "crm"],
        "street": ["1 Main Street Apt 4B", "1 Main St"],
        "city": ["LA", "LA"],
        "zip": [None, "90001"],
        "state": ["CA", "CA"],
        "phone": ["5551112222", "5553334444"],
        "dt": ["2020-01-01", "2024-01-01"],
    })


def test_group_lockstep_no_frankenstein():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_groups=[GoldenGroupRule(name="addr", columns=["street", "city", "zip"], strategy="most_complete")],
    )
    rec = build_golden_records_batch(_frame(), rules, provenance=True)[0]
    # record 1 (row 11) wins the group (3/3 populated); all three address fields come from row 11
    assert rec["street"]["value"] == "1 Main St"
    assert rec["zip"]["value"] == "90001"
    assert rec["street"]["source_row_id"] == rec["zip"]["source_row_id"] == 11


def test_conditional_strategy_picks_most_recent_when_ca():
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": [
            GoldenFieldRule(strategy="most_recent", date_column="dt", when="state == 'CA'"),
            GoldenFieldRule(strategy="source_priority", source_priority=["crm"]),
        ]},
    )
    rec = build_golden_records_batch(_frame(), rules, provenance=True)[0]
    assert rec["phone"]["value"] == "5553334444"   # most_recent -> 2024 row


def test_validate_drops_invalid_candidate():
    # phone validated via nanp; an invalid candidate is dropped before merge.
    df = pl.DataFrame({
        "__cluster_id__": [1, 1],
        "__row_id__": [10, 11],
        "phone": ["212-555-0100", "not-a-phone"],
    })
    rules = GoldenRulesConfig(
        default_strategy="most_complete",
        field_rules={"phone": GoldenFieldRule(strategy="most_complete", validate="nanp")},
    )
    rec = build_golden_records_batch(df, rules, provenance=True)[0]
    assert rec["phone"]["value"] == "212-555-0100"  # invalid 'not-a-phone' dropped


def test_plain_config_unchanged_shape():
    rules = GoldenRulesConfig(default_strategy="most_complete")
    rec = build_golden_records_batch(_frame(), rules, provenance=True)[0]
    assert rec["__cluster_id__"] == 1
    assert "value" in rec["street"] and "confidence" in rec["street"]
