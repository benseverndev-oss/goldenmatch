"""Tests for the ``carceral`` domain pack.

Targets the three carceral-specific normalization wins documented in the
module docstring: operator-org prefix stripping, BOP / state-complex
abbreviation expansion, and the lat/lng pack helper. Plus a smoke test
on the composite ``carceral_name_normalize`` that mirrors what a real
HIFLD↔ECHO matchkey would see.
"""

from __future__ import annotations

import polars as pl
from goldenflow.domains.carceral import (
    CARCERAL_BOP_ABBREVIATIONS,
    CARCERAL_OPERATOR_ORGS,
    CARCERAL_STATE_COMPLEX_ALIASES,
    PACK,
    carceral_abbreviate,
    carceral_name_normalize,
    carceral_org_strip,
    latlng_pack,
)

# ── Metadata ────────────────────────────────────────────────────────────


def test_pack_metadata():
    assert PACK.name == "carceral"
    assert "carceral_org_strip" in PACK.transforms
    assert "carceral_abbreviate" in PACK.transforms
    assert "carceral_name_normalize" in PACK.transforms
    assert "latlng_pack" in PACK.transforms
    # Composes with existing transforms
    assert "address_standardize" in PACK.transforms
    assert "zip_normalize" in PACK.transforms


def test_constants_are_frozen():
    assert isinstance(CARCERAL_OPERATOR_ORGS, frozenset)
    assert "TDCJ" in CARCERAL_OPERATOR_ORGS
    assert "MDOC" in CARCERAL_OPERATOR_ORGS
    assert "GEO" in CARCERAL_OPERATOR_ORGS
    assert "USP" in CARCERAL_BOP_ABBREVIATIONS
    assert "ASPC" in CARCERAL_STATE_COMPLEX_ALIASES


# ── carceral_org_strip ─────────────────────────────────────────────────


def test_org_strip_comma_separator():
    s = pl.Series(
        "name",
        [
            "MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
            "TDCJ, ALLRED UNIT",
        ],
    )
    out = carceral_org_strip(s)
    assert out[0] == "SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION"
    assert out[1] == "ALLRED UNIT"


def test_org_strip_hyphen_separator():
    s = pl.Series("name", ["TDCJ - DARRINGTON WWTP", "GEO - LAKEVIEW"])
    out = carceral_org_strip(s)
    assert out[0] == "DARRINGTON WWTP"
    assert out[1] == "LAKEVIEW"


def test_org_strip_phrase_form():
    s = pl.Series(
        "name",
        [
            "TX DEPT OF CRIM JUST- MCCONNELL UNIT",
            "PA DEPT OF CORR/CHESTER SCI",
            "TX DEPT OF CRIMINAL JUSTICE - ALLRED UNIT",
        ],
    )
    out = carceral_org_strip(s)
    assert out[0] == "MCCONNELL UNIT"
    assert out[1] == "CHESTER SCI"
    assert out[2] == "ALLRED UNIT"


def test_org_strip_preserves_when_no_prefix():
    s = pl.Series("name", ["ALLEGHENY COUNTY JAIL", "CALHOUN COUNTY JAIL"])
    out = carceral_org_strip(s)
    assert out[0] == "ALLEGHENY COUNTY JAIL"
    assert out[1] == "CALHOUN COUNTY JAIL"


def test_org_strip_preserves_midstring_acronym():
    # "DOC" in the middle of a name is not stripped — it's only treated as
    # a prefix when followed by a separator at the start.
    s = pl.Series("name", ["NORTH END TRANSITIONAL HOUSING UNIT / WORK CENTER"])
    out = carceral_org_strip(s)
    assert out[0] == "NORTH END TRANSITIONAL HOUSING UNIT / WORK CENTER"


def test_org_strip_none_passes_through():
    s = pl.Series("name", [None, ""])
    out = carceral_org_strip(s)
    assert out[0] is None
    assert out[1] == ""


# ── carceral_abbreviate ────────────────────────────────────────────────


def test_abbreviate_bop_facility_types():
    s = pl.Series(
        "name",
        ["USP HAZELTON", "FCI DUBLIN", "FCC YAZOO CITY", "FPC PENSACOLA", "FMC SPRINGFIELD"],
    )
    out = carceral_abbreviate(s)
    assert out[0] == "UNITED STATES PENITENTIARY HAZELTON"
    assert out[1] == "FEDERAL CORRECTIONAL INSTITUTION DUBLIN"
    assert out[2] == "FEDERAL CORRECTIONAL COMPLEX YAZOO CITY"
    assert out[3] == "FEDERAL PRISON CAMP PENSACOLA"
    assert out[4] == "FEDERAL MEDICAL CENTER SPRINGFIELD"


def test_abbreviate_state_complex_aliases():
    s = pl.Series("name", ["ASPC-LEWIS", "ASP - YUMA COMPLEX", "APS-PERRYVILLE"])
    # punctuation is preserved by the simple expander; downstream
    # name_normalize handles punctuation stripping
    out = carceral_abbreviate(s)
    assert "ARIZONA STATE PRISON COMPLEX" in out[0]
    assert "ARIZONA STATE PRISON" in out[1]
    assert "ARIZONA STATE PRISON" in out[2]


def test_abbreviate_word_bounded():
    # USP appears mid-word in some non-carceral facility names — must not
    # expand inside other tokens.
    s = pl.Series("name", ["NUSPACE", "CALLUSP"])
    out = carceral_abbreviate(s)
    assert out[0] == "NUSPACE"
    assert out[1] == "CALLUSP"


# ── carceral_name_normalize (composite) ────────────────────────────────


def test_name_normalize_collapses_two_sides_to_same():
    """The whole point — HIFLD-form and ECHO-form for the same facility
    should normalize to identical (or near-identical) strings."""
    s = pl.Series(
        "name",
        [
            "USP HAZELTON",  # HIFLD-style
            "UNITED STATES PENITENTIARY HAZELTON",  # ECHO-style
            "FDC HOUSTON",
            "FEDERAL DETENTION CENTER HOUSTON",
        ],
    )
    out = carceral_name_normalize(s)
    assert out[0] == out[1] == "UNITED STATES PENITENTIARY HAZELTON"
    assert out[2] == out[3] == "FEDERAL DETENTION CENTER HOUSTON"


def test_name_normalize_strips_punctuation():
    s = pl.Series("name", ["LT. SHERMAN WALKER CORRECTIONAL FACILITY"])
    out = carceral_name_normalize(s)
    assert out[0] == "LT SHERMAN WALKER CORRECTIONAL FACILITY"


def test_name_normalize_handles_operator_prefix_plus_abbreviation():
    s = pl.Series(
        "name",
        [
            "MDOC, SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
            "SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION",
        ],
    )
    out = carceral_name_normalize(s)
    assert out[0] == out[1]
    assert out[0] == "SOUTH MISSISSIPPI CORRECTIONAL INSTITUTION"


def test_name_normalize_arizona_aspc_pattern():
    """The motivating example: HIFLD `ASPC-LEWIS` vs ECHO `APS-LEWIS COMPLEX`
    score below 0.65 on raw Jaro-Winkler. After this normalizer both
    collapse to a common ARIZONA STATE PRISON prefix and clear 0.97."""
    s = pl.Series("name", ["ASPC-LEWIS", "APS-LEWIS COMPLEX"])
    out = carceral_name_normalize(s)
    assert "ARIZONA STATE PRISON" in out[0]
    assert "ARIZONA STATE PRISON" in out[1]
    assert "LEWIS" in out[0]
    assert "LEWIS" in out[1]


# ── latlng_pack ────────────────────────────────────────────────────────


def test_latlng_pack_packs_when_both_present():
    df = pl.DataFrame({"id": ["a", "b"], "lat": [39.0, 32.5], "lng": [-90.0, -116.5]})
    out = latlng_pack(df)
    assert "latlng" in out.columns
    assert out["latlng"].to_list() == ["39.0|-90.0", "32.5|-116.5"]


def test_latlng_pack_empty_on_either_null():
    df = pl.DataFrame(
        {"id": ["a", "b", "c"], "lat": [39.0, None, 32.5], "lng": [None, -90.0, -116.5]}
    )
    out = latlng_pack(df)
    assert out["latlng"].to_list() == ["", "", "32.5|-116.5"]


def test_latlng_pack_no_op_when_columns_missing():
    df = pl.DataFrame({"id": ["a", "b"], "name": ["x", "y"]})
    out = latlng_pack(df)
    assert "latlng" not in out.columns
    assert out.shape == df.shape
