"""Tests for DomainPackTarget + soft mode + detect_domain."""
from __future__ import annotations

import pytest

# pandas is an optional/test-only dep across this workspace — skip cleanly
# when it's not installed (per packages/python/CLAUDE.md guidance).
pd = pytest.importorskip("pandas")
from goldencheck_types import load_domain

from infermap import DomainPackTarget, detect_domain, map as infermap_map


def test_domain_pack_target_to_schema_info():
    pack = load_domain("finance")
    tgt = DomainPackTarget(pack)
    schema = tgt.to_schema_info()
    assert schema.source_name == "domain:finance"
    field_names = {f.name for f in schema.fields}
    # Some canonical types should appear
    assert "account_number" in field_names
    # name_hints flow into sample_values
    acct = next(f for f in schema.fields if f.name == "account_number")
    assert acct.sample_values
    assert any("account" in s for s in acct.sample_values)


def test_map_with_domain_pack_target_returns_mapresult():
    df = pd.DataFrame({
        "account_number": ["A1234", "A5678", "B0001", "C9999"],
        "currency": ["USD", "EUR", "GBP", "USD"],
        "totally_random_xyz": ["zzz", "qqq", "ppp", "rrr"],
    })
    pack = load_domain("finance")
    result = infermap_map(df, DomainPackTarget(pack))
    # MapResult shape preserved (mappings, not fields)
    assert hasattr(result, "mappings")


def test_soft_mode_marks_low_confidence_unknown():
    df = pd.DataFrame({
        "account_number": ["A1234", "A5678", "B0001", "C9999"],
        "totally_random_xyz_no_hints": ["zzz", "qqq", "ppp", "rrr"],
    })
    pack = load_domain("finance")
    result = infermap_map(df, DomainPackTarget(pack), soft=True)
    # totally_random column either doesn't appear in mappings (filtered out
    # by the assignment step) or appears with target=None after soft.
    by_source = {m.source: m for m in result.mappings}
    if "totally_random_xyz_no_hints" in by_source:
        assert by_source["totally_random_xyz_no_hints"].target is None


def test_detect_domain_finance():
    df = pd.DataFrame(columns=["account_number", "routing", "currency"])
    assert detect_domain(df) == "finance"


def test_detect_domain_healthcare():
    df = pd.DataFrame(columns=["patient_id", "diagnosis", "icd10"])
    assert detect_domain(df) == "healthcare"


def test_detect_domain_no_match_returns_none():
    df = pd.DataFrame(columns=["foo", "bar", "baz"])
    assert detect_domain(df) is None
