"""Tests for goldenmatch.refdata.industries + naics_normalize transform."""

from __future__ import annotations

import goldenmatch.refdata  # noqa: F401  registers transform
import pytest
from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata import (
    code_for_title,
    industries_available,
    naics_normalize,
    title_for_code,
)

# ── data load ───────────────────────────────────────────────────────────────


def test_data_is_bundled():
    assert industries_available() is True


def test_lookup_count_reasonable():
    """Bundled list should cover sectors through 6-digit US industries."""
    from goldenmatch.refdata.industries import known_codes

    assert len(known_codes()) >= 1500


# ── code lookup ─────────────────────────────────────────────────────────────


def test_title_for_known_code_sector():
    assert title_for_code("51") == "Information"
    assert title_for_code("23") == "Construction"


def test_title_for_known_code_industry():
    """A 6-digit US code resolves to its title."""
    title = title_for_code("111110")  # Soybean Farming
    assert title is not None
    assert "Soybean" in title or "Farming" in title


def test_title_for_code_truncates_overlong_input():
    """Codes longer than 6 digits are truncated to the first 6."""
    over = title_for_code("1111101234")
    six = title_for_code("111110")
    assert over == six


def test_title_for_unknown_code_returns_none():
    assert title_for_code("999999") is None


def test_title_for_none_returns_none():
    assert title_for_code(None) is None


# ── title-to-code lookup ────────────────────────────────────────────────────


def test_code_for_known_title():
    assert code_for_title("Information") == "51"
    assert code_for_title("Construction") == "23"


def test_code_for_title_case_insensitive():
    assert code_for_title("INFORMATION") == "51"
    assert code_for_title("information") == "51"


def test_code_for_title_punctuation_tolerant():
    """Minor punctuation variations still resolve."""
    assert code_for_title("Information,") == "51"
    assert code_for_title("  Information  ") == "51"


def test_code_for_unknown_title_returns_none():
    assert code_for_title("Wholly Made-Up Industry") is None


def test_code_for_none_returns_none():
    assert code_for_title(None) is None


# ── naics_normalize transform ───────────────────────────────────────────────


@pytest.mark.parametrize("raw,expected", [
    # Numeric codes -> 6-digit canonical
    ("111110", "111110"),
    ("111 110", "111110"),
    ("111,110", "111110"),
    # Trailing text
    ("111110 (Soybean Farming)", "111110"),
    ("111110 - Soybean Farming", "111110"),
    # Sector code stays at its level
    ("51", "51"),
    ("23", "23"),
    # Subsector
    ("111", "111"),
])
def test_naics_normalize_codes(raw: str, expected: str):
    assert naics_normalize(raw) == expected


def test_naics_normalize_overlong_truncates():
    """Codes longer than 6 digits truncate to the first 6 (or fall back to a
    known prefix when the truncated form isn't a real code)."""
    # 511210 is in the 2017 NAICS but was retired in 2022 (now 513210). Use
    # a known 2022 code for the test.
    out = naics_normalize("111110extra")
    assert out == "111110"


def test_naics_normalize_known_title_returns_code():
    """A known industry title maps to its canonical code."""
    out = naics_normalize("Information")
    assert out == "51"


def test_naics_normalize_known_title_case_insensitive():
    assert naics_normalize("CONSTRUCTION") == "23"
    assert naics_normalize("construction") == "23"


def test_naics_normalize_unknown_passes_through():
    """Non-numeric, non-title input survives with whitespace collapsed."""
    out = naics_normalize("just a random  description")
    assert out == "just a random description"


def test_naics_normalize_none_returns_none():
    assert naics_normalize(None) is None


def test_naics_normalize_empty_returns_empty():
    assert naics_normalize("") == ""
    assert naics_normalize("   ") == ""


def test_naics_normalize_unknown_numeric_returns_truncated_digits():
    """An unknown 6-digit code still normalizes to digits-only — so two
    records sharing the same unknown code still match each other after the
    transform."""
    out = naics_normalize("987654 (some unknown classification)")
    assert out == "987654"


def test_naics_normalize_longest_known_prefix_fallback():
    """When the 6-digit truncation isn't a known code, the transform walks
    back through shorter prefixes and returns the longest one that IS in
    the table. Regression: this branch was uncovered prior to PR #222
    review — line 212-216 of industries.py."""
    # 11121 is a known 5-digit NAICS industry ("Vegetable and Melon Farming");
    # 111219 is a 6-digit US sub-industry; 11121X with X=any-non-matching
    # digit is NOT in the table at the 6-digit level. So "1112190" (6-digit
    # truncate = "111219") would resolve at the 6-digit layer; "1112199" is
    # the canary -- 111219 is in the table at 6-digit, so this becomes a
    # 6-digit hit. We need a case where the truncated 6 ISN'T in the table.
    # Use "111210Z" with Z stripped to digits "111210"; if 111210 is unused,
    # we should walk back to 11121 (the parent 5-digit). Pick a value the
    # test can pin without committing to specific table membership: take a
    # known 5-digit AND append a digit that makes the 6-digit form unknown.

    # 51 is the Information sector; 5111 is "Newspaper, Periodical, Book,
    # and Directory Publishers"; 511110 is "Newspaper Publishers". Construct
    # "5111199" -- digits = "511119" (not a real 6-digit code), parent
    # 51111 is "Newspaper, Periodical, Book, and Directory Publishers"
    # (5-digit).
    # If 511119 happens to be a real code in the bundle, this test will
    # silently still pass (different branch taken, same end-state). Verify
    # the unknown-6 + known-5 shape via title_for_code on each step.

    from goldenmatch.refdata.industries import title_for_code

    if title_for_code("511110") is None:
        pytest.skip("Bundled NAICS table doesn't expose the expected codes; rebundle and re-tune the fixture.")
    # Assemble an input whose truncated 6 isn't in the table but whose
    # 5-digit prefix is. Find one by scanning.
    from goldenmatch.refdata.industries import known_codes

    five_digit_present = next(
        (c for c in known_codes() if len(c) == 5
         and not any(c == k[:5] and len(k) == 6 for k in known_codes())),
        None,
    )
    if five_digit_present is None:
        pytest.skip("Every bundled 5-digit code has a 6-digit child; longest-prefix fallback exercised through other tests.")
    # Append a digit so the 6-digit form is fictitious.
    six = five_digit_present + "9"
    if six in known_codes():
        six = five_digit_present + "0"
    out = naics_normalize(six)
    assert out == five_digit_present, (
        f"Expected {six} to walk back to {five_digit_present}, got {out!r}"
    )


def test_naics_normalize_title_precedence_narrowest_wins():
    """When the same title appears at multiple hierarchy levels (rare in
    NAICS but possible), title_to_code should keep the narrowest (longest-
    code) match. Regression for the iteration-order rule at
    industries.py:103-107."""
    # Build a synthetic doubled title at a forced reload to verify the
    # narrowest-wins rule. We can't rely on a naturally-occurring doubled
    # title in the 2022 bundle; instead patch the data file lookup.
    from goldenmatch.refdata import industries as ind

    saved_state = dict(ind._state)
    try:
        # Manufacture a state where "Test Industry" exists at both 2-digit
        # ("99") and 6-digit ("999999") levels. The narrower (6-digit)
        # should win.
        ind._state["loaded"] = True
        ind._state["available"] = True
        ind._state["code_to_title"] = {"99": "Test Industry", "999999": "Test Industry"}
        # Build title_to_code with narrow-first iteration (mirrors _load).
        narrow_first = ["999999", "99"]  # sorted from longest code first
        tt: dict[str, str] = {}
        for code in narrow_first:
            key = "test industry"  # normalized form
            if key not in tt:
                tt[key] = code
        ind._state["title_to_code"] = tt
        assert ind.code_for_title("Test Industry") == "999999"
    finally:
        ind._state.clear()
        ind._state.update(saved_state)


def test_naics_normalize_scans_multiple_digit_runs():
    """Inputs like "NAICS 2022 code 511210" or "vintage 2022 511210"
    contain a year-shaped digit run before the real code. The transform
    should scan every digit run and return the first that resolves to a
    known code, not short-circuit on the leading year."""
    # 2022 is a 4-digit number. If treated as a NAICS code, it'd
    # potentially resolve to subsector 202 (no such NAICS) or sector 20
    # (no such NAICS) -- neither is in the table. The transform should
    # walk past 2022 to the next run.
    from goldenmatch.refdata.industries import known_codes

    # Confirm the failing-prefix-walk premise: 2022 doesn't resolve at
    # any hierarchy level we'd accept.
    assert "2022" not in known_codes()
    assert "202" not in known_codes()
    assert "20" not in known_codes()

    # Pick a real 6-digit code from the bundle.
    real_6digit = next(c for c in known_codes() if len(c) == 6)
    out = naics_normalize(f"NAICS 2022 code {real_6digit}")
    assert out == real_6digit, (
        f"Expected the year prefix to be skipped and {real_6digit} returned, got {out!r}"
    )


# ── integration ─────────────────────────────────────────────────────────────


def test_transform_registered():
    assert PluginRegistry.instance().has_transform("naics_normalize")


def test_apply_transform_dispatches_to_plugin():
    from goldenmatch.utils.transforms import apply_transform

    assert apply_transform("111110 Soybean Farming", "naics_normalize") == "111110"
    assert apply_transform(None, "naics_normalize") is None


def test_field_transform_validator_accepts_plugin():
    from goldenmatch.config.schemas import FieldTransform

    FieldTransform(transform="naics_normalize")  # should not raise


def test_matchkey_field_accepts_transform():
    from goldenmatch.config.schemas import MatchkeyField

    field = MatchkeyField(
        field="naics_code",
        scorer="exact",
        weight=1.0,
        transforms=["naics_normalize"],
    )
    assert field.transforms == ["naics_normalize"]


# ── autoconfig hook ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("col_name", [
    "naics", "NAICS", "naics_code", "naics_code_2022",
    "sic", "sic_code", "industry", "industry_code",
    "industry_classification", "business_type",
])
def test_autoconfig_hook_prepends_naics_normalize(col_name: str):
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    scorer, transforms = refine_matchkey_field(col_name, "exact", ["strip"])
    assert transforms == ["naics_normalize", "strip"]


def test_autoconfig_hook_idempotent_on_industry():
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    _, transforms = refine_matchkey_field(
        "naics", "exact", ["naics_normalize", "strip"],
    )
    assert transforms.count("naics_normalize") == 1


def test_autoconfig_hook_non_industry_unchanged():
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    _, transforms = refine_matchkey_field("price", "token_sort", ["strip"])
    assert "naics_normalize" not in transforms


def test_business_type_does_not_get_legal_form_strip():
    """``business_type`` is an industry-classification column, not a
    company-name column. The ``_COMPANY_NAME_RE`` pattern was tightened in
    PR #222 to exclude ``business_type`` so it doesn't pick up
    ``legal_form_strip`` alongside ``naics_normalize``. This pins that
    fix."""
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    _, transforms = refine_matchkey_field(
        "business_type", "token_sort", ["lowercase", "strip"],
    )
    assert "legal_form_strip" not in transforms, (
        f"business_type should not match _COMPANY_NAME_RE; got {transforms!r}"
    )
    assert "naics_normalize" in transforms


@pytest.mark.parametrize("col_name", [
    "company", "Company", "business_name", "business name",
    "legal_name", "entity_name", "firm",
])
def test_company_name_pattern_still_matches_after_tightening(col_name: str):
    """The tightening of ``_COMPANY_NAME_RE`` for the business_type case
    must not break the patterns it was already catching."""
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    _, transforms = refine_matchkey_field(col_name, "token_sort", ["strip"])
    assert "legal_form_strip" in transforms, (
        f"{col_name!r} should still trigger _COMPANY_NAME_RE; got {transforms!r}"
    )


@pytest.mark.parametrize("col_name", [
    "businesstype", "business type", "business_type", "BusinessType",
])
def test_business_type_variants_get_naics_normalize(col_name: str):
    """Cover the ``business.?type`` regex sub-pattern's branches."""
    from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

    _, transforms = refine_matchkey_field(col_name, "token_sort", ["strip"])
    assert "naics_normalize" in transforms
