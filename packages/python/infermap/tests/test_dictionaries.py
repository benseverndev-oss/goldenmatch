"""Tests for infermap.dictionaries and the domain-aware AliasScorer path."""
from __future__ import annotations

import pytest

from infermap import MapEngine
from infermap.dictionaries import (
    UnknownDomainError,
    available_domains,
    load_domain,
    merge_domains,
)
from infermap.scorers.alias import AliasScorer, build_lookup
from infermap.types import FieldInfo, SchemaInfo


# --- module-level loader ----------------------------------------------------

def test_available_domains_includes_shipped():
    domains = available_domains()
    assert "generic" in domains
    assert "healthcare" in domains
    assert "finance" in domains
    assert "ecommerce" in domains


def test_load_domain_generic_nonempty():
    d = load_domain("generic")
    assert isinstance(d, dict)
    assert "email" in d
    assert "e_mail" in d["email"]


def test_load_domain_healthcare_shape():
    d = load_domain("healthcare")
    assert "patient_id" in d
    assert "mrn" in d["patient_id"]
    # Every value must be a list of strings
    for canonical, aliases in d.items():
        assert isinstance(canonical, str)
        assert isinstance(aliases, list)
        assert all(isinstance(a, str) for a in aliases)


def test_load_unknown_domain_raises():
    with pytest.raises(UnknownDomainError):
        load_domain("not_a_real_domain")


def test_merge_domains_unions_alias_lists():
    merged = merge_domains(["generic", "ecommerce"])
    # `email` appears only in generic, must survive merge
    assert "email" in merged
    # `product_id` appears only in ecommerce, must survive merge
    assert "product_id" in merged


def test_merge_domains_deduplicates_aliases():
    # Construct two domains that both list the same alias for the same canonical.
    # We can't mutate the shipped files, so exercise via generic alone twice —
    # merging should not duplicate entries.
    once = load_domain("generic")
    twice = merge_domains(["generic", "generic"])
    # Every alias list in `twice` should have the same length as `once`.
    for k in once:
        assert len(twice[k]) == len(once[k])


# --- build_lookup -----------------------------------------------------------

def test_build_lookup_maps_aliases_to_canonical():
    lookup = build_lookup({"email": ["e_mail", "contact_email"]})
    assert lookup["email"] == "email"
    assert lookup["e_mail"] == "email"
    assert lookup["contact_email"] == "email"


def test_build_lookup_lowercases_aliases():
    lookup = build_lookup({"email": ["CONTACT_EMAIL"]})
    assert lookup["contact_email"] == "email"


# --- AliasScorer with per-instance dict -------------------------------------

def test_alias_scorer_per_instance_dict_isolates_state():
    """A per-instance AliasScorer does not consult the module-level ALIASES."""
    # Construct a scorer that ONLY knows about `foo`.
    scorer = AliasScorer(aliases={"foo_canonical": ["foo", "foo_alias"]})
    src = FieldInfo(name="foo")
    tgt = FieldInfo(name="foo_alias")
    result = scorer.score(src, tgt)
    assert result is not None and result.score == 0.95
    # Now ask about `email` which the per-instance dict does NOT know about
    # (but the module-level one does). Must not match.
    src_e = FieldInfo(name="email")
    tgt_e = FieldInfo(name="e_mail")
    result_e = scorer.score(src_e, tgt_e)
    # Per-instance dict doesn't know email; both canonicals are None → abstain.
    assert result_e is None


def test_default_alias_scorer_still_uses_module_dict():
    """Without per-instance aliases, the scorer reads module ALIASES."""
    scorer = AliasScorer()
    src = FieldInfo(name="email")
    tgt = FieldInfo(name="e_mail")
    result = scorer.score(src, tgt)
    assert result is not None and result.score == 0.95


# --- End-to-end via MapEngine -----------------------------------------------

def test_engine_with_healthcare_domain_matches_mrn():
    """`mrn` and `patient_id` should match via the healthcare dictionary."""
    src = SchemaInfo(fields=[
        FieldInfo(name="mrn", dtype="string", sample_values=["A", "B", "C"], value_count=3),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="patient_id", dtype="string", sample_values=["1", "2", "3"], value_count=3),
    ])

    # Without the domain, the match is weak (fuzzy only).
    result_base = MapEngine().map_schemas(src, tgt)
    base_conf = result_base.mappings[0].confidence if result_base.mappings else 0.0

    result_dom = MapEngine(domains=["healthcare"]).map_schemas(src, tgt)
    assert result_dom.mappings, "expected at least one mapping with healthcare domain"
    dom_conf = result_dom.mappings[0].confidence
    assert dom_conf > base_conf, f"healthcare domain should boost confidence: {base_conf} -> {dom_conf}"


def test_engine_with_finance_domain_matches_txn_abbreviations():
    src = SchemaInfo(fields=[
        FieldInfo(name="txn_id", dtype="string", sample_values=["1", "2"], value_count=2),
        FieldInfo(name="amt", dtype="float", sample_values=["1.0", "2.0"], value_count=2),
        FieldInfo(name="ccy", dtype="string", sample_values=["USD", "EUR"], value_count=2),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="transaction_id", dtype="string", sample_values=["x", "y"], value_count=2),
        FieldInfo(name="amount", dtype="float", sample_values=["10.0", "20.0"], value_count=2),
        FieldInfo(name="currency", dtype="string", sample_values=["GBP", "JPY"], value_count=2),
    ])
    result = MapEngine(domains=["finance"]).map_schemas(src, tgt)
    pairs = {(m.source, m.target) for m in result.mappings}
    assert ("txn_id", "transaction_id") in pairs
    assert ("amt", "amount") in pairs
    assert ("ccy", "currency") in pairs


def test_engine_default_domain_is_generic_only():
    """MapEngine() without domains does NOT pick up healthcare-specific aliases."""
    src = SchemaInfo(fields=[
        FieldInfo(name="mrn", dtype="string", sample_values=["A"], value_count=1),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="patient_id", dtype="string", sample_values=["1"], value_count=1),
    ])
    # With generic only, mrn and patient_id are unrelated canonicals → no alias bonus
    result = MapEngine().map_schemas(src, tgt)
    if result.mappings:
        # The mapping may still exist via fuzzy/profile, but confidence should
        # be well below what healthcare gives us (we verified elsewhere).
        assert result.mappings[0].confidence < 0.4


def test_engine_ecommerce_domain_available():
    """Ecommerce domain loads and knows about sku↔product_id."""
    src = SchemaInfo(fields=[
        FieldInfo(name="sku", dtype="string", sample_values=["X1", "X2"], value_count=2),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="product_id", dtype="string", sample_values=["P1", "P2"], value_count=2),
    ])
    result = MapEngine(domains=["ecommerce"]).map_schemas(src, tgt)
    assert result.mappings
    assert result.mappings[0].source == "sku"
    assert result.mappings[0].target == "product_id"


def test_engine_explicit_generic_in_domains_list_is_not_duplicated():
    """Passing domains=['generic', 'healthcare'] should be equivalent to ['healthcare']."""
    src = SchemaInfo(fields=[
        FieldInfo(name="email", dtype="string", sample_values=["a@b"], value_count=1),
    ])
    tgt = SchemaInfo(fields=[
        FieldInfo(name="e_mail", dtype="string", sample_values=["c@d"], value_count=1),
    ])
    r1 = MapEngine(domains=["healthcare"]).map_schemas(src, tgt)
    r2 = MapEngine(domains=["generic", "healthcare"]).map_schemas(src, tgt)
    assert len(r1.mappings) == len(r2.mappings) == 1
    assert r1.mappings[0].confidence == r2.mappings[0].confidence


def test_engine_unknown_domain_raises():
    with pytest.raises(UnknownDomainError):
        MapEngine(domains=["not_a_real_domain"])
