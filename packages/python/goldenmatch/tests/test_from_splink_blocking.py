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
from goldenmatch.config.from_splink import ConversionReport, convert_blocking


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


def test_surname_and_dob_substring_conjunction():
    rule = 'l."surname" = r."surname" AND SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)'
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is not None
    assert config.strategy == "static"
    assert len(config.keys) == 1
    key = config.keys[0]
    assert key.fields == ["surname", "dob"]
    # BlockingKeyConfig.transforms is a single chain applied to every field in
    # the key (no per-field slot) -- the dob-only SUBSTR is carried as the
    # key's one transform. SUBSTR(x, 1, 4) -> substring:0:4 per the verified
    # convention above.
    assert key.transforms == ["substring:0:4"]
    # This is LOSSY: surname was plain equality in Splink but gets the
    # substring transform here (key-level chain). It must be a WARNING so
    # strict=True gates on it, not a silent info.
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert report.has_warnings
    msg = warnings[0].message
    assert "widened" in msg or "approximate" in msg
    assert "surname" in msg
    assert "skip_oversized" in msg


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


def test_conflicting_substr_offsets_rule_dropped():
    rule = "SUBSTR(l.a, 1, 4) = SUBSTR(r.a, 1, 4) AND SUBSTR(l.b, 1, 2) = SUBSTR(r.b, 1, 2)"
    report = ConversionReport()
    config = convert_blocking([rule], report)

    assert config is None
    warnings = [f for f in report.findings if f.severity == "warning"]
    assert len(warnings) == 1
    assert "conflicting SUBSTR offsets" in warnings[0].message


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
    assert config.keys[0].transforms == ["substring:0:4"]


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
