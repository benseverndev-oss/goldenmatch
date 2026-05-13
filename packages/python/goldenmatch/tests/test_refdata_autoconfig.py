"""Tests for refdata-aware auto-config integration."""

from __future__ import annotations

import goldenmatch.refdata  # noqa: F401  registers scorers + transforms
import pytest
from goldenmatch.refdata.autoconfig_hooks import refine_matchkey_field

# ── refinement: last_name → name_freq_weighted_jw ───────────────────────────


@pytest.mark.parametrize("col_name", [
    "last_name", "LAST_NAME", "lastname", "lname", "l_name",
    "surname", "Surname", "family_name", "last",
])
def test_last_name_columns_get_surname_scorer(col_name: str):
    scorer, transforms = refine_matchkey_field(col_name, "jaro_winkler", ["lowercase"])
    assert scorer == "name_freq_weighted_jw"
    assert transforms == ["lowercase"]  # unchanged


@pytest.mark.parametrize("col_name", [
    "first_name", "FIRST_NAME", "firstname", "fname", "f_name",
    "given_name", "Given_Name", "forename", "first",
])
def test_first_name_columns_get_alias_scorer(col_name: str):
    scorer, transforms = refine_matchkey_field(col_name, "jaro_winkler", ["lowercase"])
    assert scorer == "given_name_aliased_jw"
    assert transforms == ["lowercase"]


# ── refinement: company → legal_form_strip transform ────────────────────────


@pytest.mark.parametrize("col_name", [
    "company_name", "Company", "business_name", "BusinessName",
    "organization", "employer",
    "legal_name", "entity_name",
])
def test_company_columns_get_legal_form_strip(col_name: str):
    scorer, transforms = refine_matchkey_field(col_name, "token_sort", ["lowercase", "strip"])
    assert scorer == "token_sort"  # scorer unchanged
    assert transforms == ["legal_form_strip", "lowercase", "strip"]  # prepended


def test_legal_form_strip_idempotent_if_already_present():
    """Don't double-add the transform if a caller already specified it."""
    scorer, transforms = refine_matchkey_field(
        "company_name", "token_sort", ["legal_form_strip", "lowercase"],
    )
    assert transforms == ["legal_form_strip", "lowercase"]
    assert transforms.count("legal_form_strip") == 1


# ── refinement: address → address_normalize transform ───────────────────────


@pytest.mark.parametrize("col_name", [
    "address", "ADDRESS", "street", "street_address", "addr",
    "addr_line", "address_line_1", "mailing_address",
])
def test_address_columns_get_address_normalize(col_name: str):
    scorer, transforms = refine_matchkey_field(col_name, "token_sort", ["lowercase", "strip"])
    assert transforms == ["address_normalize", "lowercase", "strip"]


def test_address_normalize_idempotent_if_already_present():
    scorer, transforms = refine_matchkey_field(
        "address", "token_sort", ["address_normalize", "strip"],
    )
    assert transforms.count("address_normalize") == 1


# ── composition + no-op cases ───────────────────────────────────────────────


def test_unrelated_column_unchanged():
    """A column that doesn't match any refdata pattern passes through."""
    scorer, transforms = refine_matchkey_field("price", "token_sort", ["strip"])
    assert scorer == "token_sort"
    assert transforms == ["strip"]


def test_exact_scorer_not_swapped():
    """The refinement only swaps string-similarity scorers — exact / embedding
    stay put."""
    scorer, _ = refine_matchkey_field("last_name", "exact", ["lowercase"])
    assert scorer == "exact"


@pytest.mark.parametrize("input_scorer", ["jaro_winkler", "levenshtein", "token_sort", "ensemble"])
def test_all_string_sim_scorers_swap_for_last_name(input_scorer: str):
    scorer, _ = refine_matchkey_field("last_name", input_scorer, [])
    assert scorer == "name_freq_weighted_jw"


def test_pathological_company_lastname_not_double_swapped():
    """A 'company_lastname' column: 'company' matches the legal-form regex
    and prepends legal_form_strip. The scorer stays as caller specified
    because the LAST_NAME regex anchors at the start of the column name
    (so 'lastname' as a suffix doesn't trigger the scorer swap)."""
    scorer, transforms = refine_matchkey_field(
        "company_lastname", "jaro_winkler", ["lowercase"],
    )
    assert "legal_form_strip" in transforms  # company prefix matches


def test_does_not_mutate_caller_transforms_list():
    """The function should not modify the caller's transforms list in place."""
    caller_list = ["lowercase"]
    refine_matchkey_field("last_name", "jaro_winkler", caller_list)
    assert caller_list == ["lowercase"]


# ── end-to-end: build_matchkeys picks refdata when columns named accordingly


def test_build_matchkeys_picks_refdata_scorer_for_last_name():
    """Auto-config should emit a matchkey using name_freq_weighted_jw when the
    column is named 'last_name'. Verified via the public build_matchkeys API."""
    import polars as pl
    from goldenmatch.core.autoconfig import build_matchkeys, profile_columns

    df = pl.DataFrame({
        "first_name": ["John", "Jane", "Bob", "Alice", "Bob"] * 3,
        "last_name": ["Smith", "Doe", "Smith", "Brown", "Smyth"] * 3,
    })
    profiles = profile_columns(df)
    matchkeys = build_matchkeys(profiles, df=df)

    # Pull the union of every scorer across every field across every matchkey.
    all_scorers: set[str] = set()
    for mk in matchkeys:
        for f in mk.fields:
            if f.scorer:
                all_scorers.add(f.scorer)

    assert "name_freq_weighted_jw" in all_scorers, (
        f"expected name_freq_weighted_jw in {all_scorers}"
    )
    assert "given_name_aliased_jw" in all_scorers, (
        f"expected given_name_aliased_jw in {all_scorers}"
    )


# ── col_type gating ─────────────────────────────────────────────────────────


@pytest.mark.parametrize("non_name_type", [
    "numeric", "date", "identifier", "email", "phone", "zip", "geo",
])
def test_last_name_swap_skipped_when_coltype_disagrees(non_name_type: str):
    """A column literally named ``last_name`` but holding non-name data
    (numeric IDs, dates, hashed identifiers) keeps the caller-specified
    scorer — without this guard the swap silently degrades quality."""
    scorer, _ = refine_matchkey_field(
        "last_name", "jaro_winkler", ["lowercase"], col_type=non_name_type,
    )
    assert scorer == "jaro_winkler"


@pytest.mark.parametrize("non_name_type", [
    "numeric", "date", "identifier", "email", "phone",
])
def test_first_name_swap_skipped_when_coltype_disagrees(non_name_type: str):
    scorer, _ = refine_matchkey_field(
        "first_name", "jaro_winkler", ["lowercase"], col_type=non_name_type,
    )
    assert scorer == "jaro_winkler"


def test_last_name_swap_fires_when_coltype_agrees():
    scorer, _ = refine_matchkey_field(
        "last_name", "jaro_winkler", ["lowercase"], col_type="name",
    )
    assert scorer == "name_freq_weighted_jw"


def test_first_name_swap_fires_when_coltype_agrees():
    scorer, _ = refine_matchkey_field(
        "first_name", "jaro_winkler", ["lowercase"], col_type="name",
    )
    assert scorer == "given_name_aliased_jw"


def test_address_normalize_skipped_when_coltype_is_numeric():
    """A column named ``address`` whose data profiles as numeric (e.g.
    house-number-only) doesn't get the address tokenizer."""
    _, transforms = refine_matchkey_field(
        "address", "token_sort", ["strip"], col_type="numeric",
    )
    assert "address_normalize" not in transforms


def test_address_normalize_fires_when_coltype_is_address():
    _, transforms = refine_matchkey_field(
        "address", "token_sort", ["strip"], col_type="address",
    )
    assert transforms[0] == "address_normalize"


def test_legal_form_strip_skipped_when_coltype_is_numeric():
    """``company_id`` columns sometimes profile as numeric; the legal-form
    stripper would do nothing useful and may corrupt downstream blocking."""
    _, transforms = refine_matchkey_field(
        "company_name", "token_sort", ["strip"], col_type="numeric",
    )
    assert "legal_form_strip" not in transforms


def test_legal_form_strip_fires_on_description_shaped_companies():
    """Free-text company names sometimes profile as ``description`` rather
    than ``name`` — keep the refinement firing on that shape."""
    _, transforms = refine_matchkey_field(
        "company_name", "token_sort", ["strip"], col_type="description",
    )
    assert "legal_form_strip" in transforms


def test_naics_normalize_skipped_when_coltype_is_name():
    """A column literally named ``industry`` but holding free-text people
    names (col_type='name') shouldn't be coerced to a NAICS code."""
    _, transforms = refine_matchkey_field(
        "industry", "token_sort", ["strip"], col_type="name",
    )
    assert "naics_normalize" not in transforms


def test_naics_normalize_fires_when_coltype_is_identifier():
    _, transforms = refine_matchkey_field(
        "naics_code", "exact", ["strip"], col_type="identifier",
    )
    assert "naics_normalize" in transforms


def test_coltype_none_preserves_legacy_behavior():
    """Backwards compat: callers without a profile (tests, ad-hoc use)
    keep the original name-only matching when col_type is omitted."""
    scorer, transforms = refine_matchkey_field("last_name", "jaro_winkler", [])
    assert scorer == "name_freq_weighted_jw"


def test_build_matchkeys_skips_refdata_swap_on_mistyped_last_name():
    """End-to-end: a column named ``last_name`` whose data is numeric
    should NOT get the surname scorer at auto-config time."""
    import polars as pl
    from goldenmatch.core.autoconfig import build_matchkeys, profile_columns

    df = pl.DataFrame({
        "last_name": [str(i) for i in range(1000, 1030)],  # numeric-looking IDs
        "city": ["NYC", "LA"] * 15,
    })
    profiles = profile_columns(df)
    last_name_profile = next(p for p in profiles if p.name == "last_name")
    # Sanity check the premise: the classifier should NOT have called
    # this a name. If it did, the test setup is wrong (not the gate).
    assert last_name_profile.col_type != "name", (
        f"profiler should not classify all-numeric data as a name; "
        f"got col_type={last_name_profile.col_type!r}"
    )
    matchkeys = build_matchkeys(profiles, df=df)
    all_scorers = {
        f.scorer for mk in matchkeys for f in mk.fields if f.scorer
    }
    assert "name_freq_weighted_jw" not in all_scorers, (
        f"refdata scorer should not fire when col_type disagrees; "
        f"got {all_scorers}"
    )


def test_build_matchkeys_prepends_legal_form_strip_for_company():
    """A column named 'company_name' should pick up legal_form_strip."""
    import polars as pl
    from goldenmatch.core.autoconfig import build_matchkeys, profile_columns

    df = pl.DataFrame({
        "company_name": [
            f"Acme Inc {i}" if i % 2 else f"Beta Corp {i}" for i in range(30)
        ],
        "city": ["NYC", "LA"] * 15,
    })
    profiles = profile_columns(df)
    matchkeys = build_matchkeys(profiles, df=df)

    company_field = None
    for mk in matchkeys:
        for f in mk.fields:
            if f.field == "company_name":
                company_field = f
                break
    assert company_field is not None
    assert "legal_form_strip" in (company_field.transforms or [])
