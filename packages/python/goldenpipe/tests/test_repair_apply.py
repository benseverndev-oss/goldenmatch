from goldenpipe.repair_host import FIXERS, merge_transforms, repair_transform_specs


def _plan(*items):
    return {"repairs": list(items)}


def test_fixer_only_grouped_and_deduped():
    plan = _plan(
        {"column": "email", "check": "format_detection", "suggested_transforms": ["email_normalize"], "reason": "x"},
        {"column": "email", "check": "pattern_consistency", "suggested_transforms": ["email_canonical", "email_normalize"], "reason": "y"},
    )
    specs, skipped = repair_transform_specs(plan)
    assert specs == [{"column": "email", "ops": ["email_normalize", "email_canonical"]}]  # dedup, order preserved
    assert skipped == []


def test_assertion_ops_are_skipped_not_applied():
    plan = _plan(
        {"column": "iban", "check": "pattern_consistency", "suggested_transforms": ["iban_validate"], "reason": "bad"},
        {"column": "signup", "check": "future_dated", "suggested_transforms": ["date_validate"], "reason": "future"},
    )
    specs, skipped = repair_transform_specs(plan)
    assert specs == []
    assert {"column": "iban", "op": "iban_validate"} in skipped
    assert {"column": "signup", "op": "date_validate"} in skipped


def test_mixed_item_keeps_fixer_skips_assertion():
    # a column flagged for both a fixer and an assertion
    plan = _plan({"column": "dob", "check": "format_detection", "suggested_transforms": ["date_parse", "date_validate"], "reason": "z"})
    specs, skipped = repair_transform_specs(plan)
    assert specs == [{"column": "dob", "ops": ["date_parse"]}]
    assert skipped == [{"column": "dob", "op": "date_validate"}]


def test_fixers_membership():
    assert "email_normalize" in FIXERS and "iban_validate" not in FIXERS


def test_merge_user_first_then_repair_deduped():
    user = [{"column": "email", "ops": ["email_lowercase"]}, {"column": "name", "ops": ["strip"]}]
    repair = [{"column": "email", "ops": ["email_normalize", "email_lowercase"]}, {"column": "zip", "ops": ["zip_normalize"]}]
    merged = merge_transforms(user, repair)
    assert merged == [
        {"column": "email", "ops": ["email_lowercase", "email_normalize"]},  # user-first, dup dropped
        {"column": "name", "ops": ["strip"]},
        {"column": "zip", "ops": ["zip_normalize"]},
    ]
