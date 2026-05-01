"""Tests for common-affix canonicalization and the InitialismScorer."""
from __future__ import annotations

from infermap import MapEngine
from infermap.engine import _common_affix_tokens, _populate_canonical_names
from infermap.scorers.initialism import (
    InitialismScorer,
    _is_prefix_concat,
    _score_pair,
    _tokenize,
)
from infermap.types import FieldInfo, SchemaInfo


# --- _common_affix_tokens ---------------------------------------------------

def test_common_prefix_with_delimiter_boundary():
    assert _common_affix_tokens(
        ["prospect_City", "prospect_Employer", "prospect_Phone"], at_start=True
    ) == "prospect_"


def test_common_prefix_rejects_mid_token_overlap():
    # "email" and "employee" share "em" but not at a delimiter boundary.
    assert _common_affix_tokens(["email", "employee", "employer"], at_start=True) == ""


def test_common_suffix():
    assert _common_affix_tokens(
        ["email_id", "customer_id", "order_id"], at_start=False
    ) == "_id"


def test_common_affix_single_field_returns_empty():
    assert _common_affix_tokens(["alone"], at_start=True) == ""


def test_populate_canonical_names():
    schema = SchemaInfo(fields=[
        FieldInfo(name="prospect_City"),
        FieldInfo(name="prospect_Employer"),
        FieldInfo(name="prospect_Phone"),
    ])
    _populate_canonical_names(schema)
    assert [f.canonical_name for f in schema.fields] == ["City", "Employer", "Phone"]


def test_populate_canonical_names_no_affix():
    schema = SchemaInfo(fields=[FieldInfo(name="foo"), FieldInfo(name="bar")])
    _populate_canonical_names(schema)
    assert [f.canonical_name for f in schema.fields] == ["foo", "bar"]


def test_map_schemas_does_not_mutate_input():
    """The engine populates canonical_name internally but must not leak it
    to the caller's schemas (defensive deep-copy contract)."""
    src = SchemaInfo(fields=[
        FieldInfo(name="prospect_City", dtype="string"),
        FieldInfo(name="prospect_Employer", dtype="string"),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="City", dtype="string"),
        FieldInfo(name="Employer", dtype="string"),
    ])
    MapEngine().map_schemas(src, tgt)
    # Caller's schemas should be untouched.
    assert all(f.canonical_name is None for f in src.fields)
    assert all(f.canonical_name is None for f in tgt.fields)


# --- InitialismScorer tokenizer --------------------------------------------

def test_tokenize_snake_case():
    assert _tokenize("assay_id") == ["assay", "id"]


def test_tokenize_camel_case():
    assert _tokenize("CustomerId") == ["customer", "id"]


def test_tokenize_mixed():
    assert _tokenize("relationship_Type") == ["relationship", "type"]


# --- InitialismScorer prefix-concat matcher --------------------------------

def test_prefix_concat_positive():
    assert _is_prefix_concat("assi", ["assay", "id"])
    assert _is_prefix_concat("consc", ["confidence", "score"])
    assert _is_prefix_concat("relatit", ["relationship", "type"])


def test_prefix_concat_negative():
    assert not _is_prefix_concat("xyz", ["assay", "id"])
    assert not _is_prefix_concat("celid", ["assay", "id"])  # "cel" not prefix of assay


def test_score_pair_positive_cases():
    for a, b in [
        ("assay_id", "ASSI"),
        ("confidence_score", "CONSC"),
        ("relationship_type", "RELATIT"),
        ("curated_by", "CURAB"),
    ]:
        s = _score_pair(a, b)
        assert s is not None and s > 0.6, f"{a} <-> {b} scored {s}"


def test_score_pair_abstains_on_unrelated():
    assert _score_pair("city", "employer") is None
    assert _score_pair("email", "phone") is None


def test_score_pair_abstains_on_identical():
    # Identical after tokenization — other scorers handle these.
    assert _score_pair("assay_id", "assay_id") is None


def test_initialism_scorer_uses_canonical_name():
    """When canonical_name is set, the scorer uses it over raw name."""
    src = FieldInfo(name="prefix_assay_id")
    src.canonical_name = "assay_id"
    tgt = FieldInfo(name="ASSI")
    result = InitialismScorer().score(src, tgt)
    assert result is not None
    assert result.score > 0.6


# --- End-to-end via MapEngine ----------------------------------------------

def test_prefix_strip_fixes_near_tie():
    """Regression guard for the `prospect_City` → `City` near-tie problem.

    Before prefix normalization, `City` vs `prospectcity` lost in fuzzy-name
    similarity to unrelated distractors. After, it should be the top pick.
    """
    src = SchemaInfo(fields=[
        FieldInfo(name="City", dtype="string", sample_values=["NYC", "LA", "SF"], value_count=3),
        FieldInfo(name="Employer", dtype="string", sample_values=["Acme", "Widgets", "Co"], value_count=3),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="prospect_City", dtype="string", sample_values=["Boston", "Austin", "Seattle"], value_count=3),
        FieldInfo(name="prospect_Employer", dtype="string", sample_values=["Foo", "Bar", "Baz"], value_count=3),
        FieldInfo(name="prospect_Phone", dtype="string", sample_values=["555", "444", "333"], value_count=3),
    ])
    result = MapEngine().map_schemas(src, tgt)
    pairs = {(m.source, m.target) for m in result.mappings}
    assert ("City", "prospect_City") in pairs
    assert ("Employer", "prospect_Employer") in pairs


def test_initialism_scorer_fixes_chembl_pattern():
    """Regression guard for ChEMBL initialism cases."""
    src = SchemaInfo(fields=[
        FieldInfo(name="assay_id", dtype="integer", sample_values=["1", "2", "3"], value_count=3),
        FieldInfo(name="relationship_type", dtype="string", sample_values=["a", "b", "c"], value_count=3),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="ASSI", dtype="integer", sample_values=["10", "20", "30"], value_count=3),
        FieldInfo(name="RELATIT", dtype="string", sample_values=["x", "y", "z"], value_count=3),
    ])
    result = MapEngine().map_schemas(src, tgt)
    pairs = {(m.source, m.target) for m in result.mappings}
    assert ("assay_id", "ASSI") in pairs
    assert ("relationship_type", "RELATIT") in pairs
