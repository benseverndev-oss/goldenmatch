"""Tests for Splink blocking_rules -> GoldenMatch BlockingConfig.

Verified conventions (see goldenmatch/config/from_splink.py comments):
  - `goldenmatch/utils/transforms.py:35-39` implements `substring:<start>:<end>`
    as a Python slice `value[start:end]`. SQL `SUBSTR(x, 1, 4)` is 1-based and
    grabs 4 characters (chars 1-4), so it maps to `substring:0:4`
    (python_start = sql_start - 1, python_end = python_start + sql_len).
  - `BlockingConfig` for `strategy="multi_pass"` sets BOTH `keys` and `passes`
    to the same list, per `goldenmatch/core/autoconfig_rules.py:_with_multi_pass`
    (lines ~102-116), not `passes` alone.
"""
import pytest
from goldenmatch.config.from_splink import (
    ConversionReport,
    SplinkConversionError,
    convert_blocking,
    from_splink,
)


def test_single_equality_rule_is_static():
    report = ConversionReport()
    config = convert_blocking(['l."postcode" = r."postcode"'], report)

    assert config is not None
    assert config.strategy == "static"
    assert len(config.keys) == 1
    assert config.keys[0].fields == ["postcode"]
    assert config.keys[0].transforms == []
    assert not report.has_warnings
    assert not report.has_errors


def test_bare_unquoted_columns_work():
    report = ConversionReport()
    config = convert_blocking(["l.postcode = r.postcode"], report)

    assert config is not None
    assert config.strategy == "static"
    assert config.keys[0].fields == ["postcode"]
    assert not report.has_warnings


def test_surname_and_dob_substring_conjunction_exact():
    # #1826: mixed plain-equality + SUBSTR rules map EXACTLY via per-field
    # transforms -- surname stays plain equality, dob gets its substring.
    # (Pre-#1826 this widened the whole key to first-initials and produced
    # the 388K mega-block.)
    rule = 'l."surname" = r."surname" AND SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.strategy == "static"
    assert len(config.keys) == 1
    key = config.keys[0]
    assert key.fields == ["surname", "dob"]
    assert key.transforms == []
    assert key.field_transforms == {"dob": ["substring:0:4"]}
    # Exact mapping: no lossy-widening warning anymore.
    assert not report.has_warnings


def test_pure_substr_rule_is_info_only():
    rule = 'SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["dob"]
    assert config.keys[0].transforms == ["substring:0:4"]
    # No plain-equality field got a transform it didn't have: clean, not lossy.
    assert not report.has_warnings
    infos = [f for f in report.findings if f.severity == "info"]
    assert len(infos) == 1


def test_pure_equality_conjunction_is_info_only():
    rule = 'l."surname" = r."surname" AND l."city" = r."city"'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["surname", "city"]
    assert config.keys[0].transforms == []
    assert not report.has_warnings
    infos = [f for f in report.findings if f.severity == "info"]
    assert len(infos) == 1


def test_two_rules_produce_multi_pass():
    rules = [
        'l."postcode" = r."postcode"',
        'l."surname" = r."surname"',
    ]
    report = ConversionReport()
    config = convert_blocking(rules, report)

    assert config is not None
    assert config.strategy == "multi_pass"
    assert len(config.keys) == 2
    assert config.keys[0].fields == ["postcode"]
    assert config.keys[1].fields == ["surname"]
    # multi_pass sets both keys and passes to the same list.
    assert config.passes == config.keys


def test_one_rule_is_static_not_multi_pass():
    report = ConversionReport()
    config = convert_blocking(['l."postcode" = r."postcode"'], report)

    assert config.strategy == "static"
    assert config.passes is None


def test_splink4_dict_form_handled_same_as_string():
    rule = {"blocking_rule": 'l."postcode" = r."postcode"', "sql_dialect": "duckdb"}
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.strategy == "static"
    assert config.keys[0].fields == ["postcode"]
    assert not report.has_warnings


def test_arithmetic_rule_dropped_with_warning():
    report = ConversionReport()
    config = convert_blocking(["l.amount / r.amount > 0.7"], report)

    assert config is None
    assert report.has_errors
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "amount" in warnings[0].message


def test_or_rule_dropped_with_warning():
    rule = 'l."postcode" = r."postcode" OR l."dob" = r."dob"'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1


def test_parenthesized_rule_converts():
    # Splink 4 serialization paren-wraps conjuncts; balanced outer parens
    # are stripped rather than dropping the rule.
    rule = '(l."postcode" = r."postcode")'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["postcode"]
    assert not report.has_warnings


def test_all_rules_dropped_returns_none_and_errors():
    rules = ["l.amount / r.amount > 0.7", '(l."x" = r."x") OR (l."y" = r."y")']
    report = ConversionReport()
    config = convert_blocking(rules, report)

    assert config is None
    assert report.has_errors
    errors = [f for f in report.findings if f.severity == "error"]
    assert len(errors) == 1
    assert errors[0].splink_path == "blocking_rules"


def test_dict_rule_missing_blocking_rule_key_dropped():
    rule = {"sql_dialect": "duckdb"}  # no "blocking_rule" key
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "not a SQL string" in warnings[0].message


def test_none_rule_dropped():
    report = ConversionReport()
    config = convert_blocking([None], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "not a SQL string" in warnings[0].message


def test_per_field_substr_offsets_convert_exactly():
    # Pre-#1826 different offsets across fields dropped the whole rule (one
    # key-level chain could not represent both). Per-field transforms can.
    rule = "SUBSTR(l.a, 1, 4) = SUBSTR(r.a, 1, 4) AND SUBSTR(l.b, 1, 2) = SUBSTR(r.b, 1, 2)"
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    key = config.keys[0]
    assert key.fields == ["a", "b"]
    assert key.transforms == []
    assert key.field_transforms == {
        "a": ["substring:0:4"],
        "b": ["substring:0:2"],
    }
    assert not report.has_warnings


def test_substr_start_zero_dropped():
    # SQL SUBSTR is 1-based; start=0 has no clean Python-slice equivalent
    # (py_start=-1 would wrap). Treated as unrecognized -> rule dropped.
    report = ConversionReport()
    config = convert_blocking(["SUBSTR(l.x, 0, 3) = SUBSTR(r.x, 0, 3)"], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "unrecognized" in warnings[0].message


def test_substr_zero_length_dropped():
    # Length 0 would derive an empty key (one mega-block) -> rule dropped.
    report = ConversionReport()
    config = convert_blocking(["SUBSTR(l.x, 1, 0) = SUBSTR(r.x, 1, 0)"], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "unrecognized" in warnings[0].message


def test_repeated_field_deduped_order_preserving():
    rule = "l.a = r.a AND l.a = r.a"
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["a"]
    assert not report.has_warnings


def test_cross_column_equality_dropped_with_warning():
    rule = 'l."first_name" = r."surname"'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "first_name" in warnings[0].message or "surname" in warnings[0].message


def test_splink4_serialized_parenthesized_conjuncts():
    # Splink 4's SettingsCreator.create_settings_dict() serializes block_on()
    # rules with EVERY conjunct paren-wrapped -- exactly this shape (observed
    # from splink 4.0.16). The recognizer must strip balanced outer parens.
    rule = (
        '(l."surname" = r."surname") AND '
        "(SUBSTRING(l.dob, 1, 4) = SUBSTRING(r.dob, 1, 4))"
    )
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["surname", "dob"]
    # Exact per-field mapping (#1826): dob carries its own chain, surname
    # keeps plain equality.
    assert config.keys[0].transforms == []
    assert config.keys[0].field_transforms == {"dob": ["substring:0:4"]}


def test_whole_rule_paren_wrapped():
    report = ConversionReport()
    config = convert_blocking(['((l."a" = r."a") AND (l."b" = r."b"))'], report)

    assert config is not None
    assert config.keys[0].fields == ["a", "b"]


def test_unbalanced_parens_still_dropped():
    report = ConversionReport()
    config = convert_blocking(['(l."a" = r."a"'], report)

    assert config is None
    assert report.has_errors


# ── IS NOT NULL guard conjuncts (#1783) ──────────────────────────────────────
#
# Splink CustomRule blocking rules routinely carry trailing IS NOT NULL guards
# on the equality columns. GM's blocker already implements those semantics
# (null key components form no block), so guards on key columns are
# recognized-and-ignored EXACTLY (info, not warn).

# The two verbatim rules from issue #1783.
_ISSUE_1783_RULE_1 = (
    "l.first_name = r.first_name AND l.last_name = r.last_name "
    "AND l.first_name IS NOT NULL AND l.last_name IS NOT NULL"
)
_ISSUE_1783_RULE_2 = (
    "l.last_name = r.last_name AND l.phone_number = r.phone_number "
    "AND l.last_name IS NOT NULL AND l.phone_number IS NOT NULL"
)


def test_issue_1783_rule_1_converts_with_no_warning():
    report = ConversionReport()
    config = convert_blocking([_ISSUE_1783_RULE_1], report)

    assert config is not None
    assert config.strategy == "static"
    assert config.keys[0].fields == ["first_name", "last_name"]
    assert config.keys[0].transforms == []
    # Guards on key columns are implicit in GM blocking: info, never a warning
    # (a warning would trip strict=True on a perfectly faithful conversion).
    assert not report.has_warnings
    assert not report.has_errors
    infos = [f for f in report.findings if f.severity == "info"]
    assert any("null guard" in f.message for f in infos)


def test_issue_1783_rule_2_converts_with_no_warning():
    report = ConversionReport()
    config = convert_blocking([_ISSUE_1783_RULE_2], report)

    assert config is not None
    assert config.keys[0].fields == ["last_name", "phone_number"]
    assert config.keys[0].transforms == []
    assert not report.has_warnings


def test_splink4_paren_wrapped_quoted_guards_convert():
    rule = (
        '(l."first_name" = r."first_name") AND (l."last_name" = r."last_name") '
        'AND (l."first_name" IS NOT NULL) AND (l."last_name" IS NOT NULL)'
    )
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["first_name", "last_name"]
    assert config.keys[0].transforms == []
    assert not report.has_warnings


def test_guard_on_non_key_column_converts_with_warning():
    # GM cannot express a null-constraint on a column that isn't part of the
    # blocking key: the guard is dropped, candidates are a superset of
    # Splink's. That's lossy -> warning (strict=True gates on it), but the
    # rule still converts.
    rule = "l.a = r.a AND l.b IS NOT NULL"
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.keys[0].fields == ["a"]
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    msg = warnings[0].message
    assert "approximate" in msg or "superset" in msg
    assert "b" in msg


def test_guards_only_rule_dropped_as_unrecognized():
    # No equality/SUBSTR conjunct at all: nothing to block on -> the existing
    # unrecognized-drop path.
    rule = "l.a IS NOT NULL AND l.b IS NOT NULL"
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "unrecognized" in warnings[0].message


def _settings_with_blocking(rules):
    return {
        "comparisons": [
            {
                "output_column_name": "first_name",
                "comparison_levels": [
                    {
                        "sql_condition": (
                            '"first_name_l" IS NULL OR "first_name_r" IS NULL'
                        ),
                        "is_null_level": True,
                    },
                    {"sql_condition": '"first_name_l" = "first_name_r"'},
                    {"sql_condition": "ELSE"},
                ],
            }
        ],
        "blocking_rules_to_generate_predictions": rules,
    }


def test_settings_level_simple_plus_guarded_rule_emits_both_keys():
    # The issue's setup: block_on("npi")-style simple rule mixed with the
    # compound guarded rule. Both keys must survive conversion.
    settings = _settings_with_blocking(['l."npi" = r."npi"', _ISSUE_1783_RULE_1])
    conversion = from_splink(settings)

    blocking = conversion.config.blocking
    assert blocking is not None
    assert blocking.strategy == "multi_pass"
    assert len(blocking.keys) == 2
    assert blocking.keys[0].fields == ["npi"]
    assert blocking.keys[1].fields == ["first_name", "last_name"]
    assert not conversion.report.has_warnings


def test_strict_true_raises_on_non_key_guard():
    settings = _settings_with_blocking(["l.a = r.a AND l.b IS NOT NULL"])

    with pytest.raises(SplinkConversionError) as exc_info:
        from_splink(settings, strict=True)
    assert "warning(s)" in str(exc_info.value)


def test_strict_true_does_not_raise_on_key_column_guards():
    # Faithful conversion: guards on key columns must NOT trip strict mode.
    settings = _settings_with_blocking([_ISSUE_1783_RULE_1])
    conversion = from_splink(settings, strict=True)

    assert conversion.config.blocking.keys[0].fields == ["first_name", "last_name"]


def test_paren_wrapped_unrecognizable_conjunct_dropped():
    # A paren-wrapped OR rule: the whole-rule _strip_outer_parens peels the
    # outer parens, no top-level AND exists, and the lone OR conjunct must
    # land in the unrecognized-rule path with a warning (config dropped).
    report = ConversionReport()
    config = convert_blocking(['(l."a" = r."a" OR l."b" = r."b")'], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "unrecognized" in warnings[0].message
