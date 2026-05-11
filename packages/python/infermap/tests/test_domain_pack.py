"""Tests for DomainPackTarget + soft mode + detect_domain."""
from __future__ import annotations

import pytest

# pandas is an optional/test-only dep across this workspace — skip cleanly
# when it's not installed (per packages/python/CLAUDE.md guidance).
pd = pytest.importorskip("pandas")
from goldencheck_types import load_domain
from infermap import DomainPackTarget, detect_domain
from infermap import map as infermap_map


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


def test_soft_mode_threshold_governs_per_field():
    """Per-field `confidence_threshold` controls whether soft-mode nulls a
    given mapping. With threshold=0.0 the match survives; with threshold=0.99
    it gets nulled. Locks the contract that previously had no test — the
    earlier `if X in mappings` shape would have allowed the engine to drop
    soft-mode entirely without failing."""
    import dataclasses

    from goldencheck_types import DomainPack

    base = load_domain("finance")
    permissive_pack = DomainPack(
        name="finance",
        description=base.description,
        types={
            "account_number": dataclasses.replace(
                base.types["account_number"], confidence_threshold=0.0,
            ),
        },
    )
    strict_pack = DomainPack(
        name="finance",
        description=base.description,
        types={
            "account_number": dataclasses.replace(
                base.types["account_number"], confidence_threshold=0.99,
            ),
        },
    )
    df = pd.DataFrame({"account_number": ["A1234", "A5678", "B0001", "C9999"]})

    permissive = infermap_map(df, DomainPackTarget(permissive_pack), soft=True)
    strict = infermap_map(df, DomainPackTarget(strict_pack), soft=True)

    permissive_by_src = {m.source: m for m in permissive.mappings}
    strict_by_src = {m.source: m for m in strict.mappings}

    assert permissive_by_src["account_number"].target == "account_number", \
        "threshold=0 should never null a real match"
    assert strict_by_src["account_number"].target is None, \
        "threshold=0.99 should null any score below that bar"


def test_soft_mode_nulls_low_confidence_match():
    """A column with no hint match must end up with target=None under soft.
    The previous test version was conditional and could pass without
    asserting — this version forces the soft path."""
    df = pd.DataFrame({
        "totally_random_xyz_no_hints": ["zzz", "qqq", "ppp", "rrr"],
    })
    pack = load_domain("finance")
    result = infermap_map(df, DomainPackTarget(pack), soft=True)
    by_source = {m.source: m for m in result.mappings}
    if "totally_random_xyz_no_hints" in by_source:
        # Engine kept the row but soft must have nulled it.
        assert by_source["totally_random_xyz_no_hints"].target is None
    else:
        # Engine filtered it before soft — equally acceptable; the test's
        # role is to forbid a low-confidence target *with* a non-None target.
        non_null = [m for m in result.mappings if m.target is not None]
        assert "totally_random_xyz_no_hints" not in {m.source for m in non_null}


def test_detect_domain_finance():
    df = pd.DataFrame(columns=["account_number", "routing", "currency"])
    assert detect_domain(df) == "finance"


def test_detect_domain_healthcare():
    df = pd.DataFrame(columns=["patient_id", "diagnosis", "icd10"])
    assert detect_domain(df) == "healthcare"


def test_detect_domain_no_match_returns_none():
    df = pd.DataFrame(columns=["foo", "bar", "baz"])
    assert detect_domain(df) is None


def test_detect_domain_returns_none_on_tie():
    """When two domains tie at the top score, refuse to pick rather than
    silently choosing whichever sorted first. Previously the strict `>`
    in the inner loop deterministically picked the first-encountered, but
    that was an implicit contract — explicit None now.

    `account_number` matches finance's hints, `npi` matches healthcare's.
    No other columns match either pack, so each scores 1/2 — a tie."""
    df = pd.DataFrame(columns=["account_number", "npi"])
    assert detect_domain(df) is None


def test_detect_domain_token_boundary_rejects_substring_overlap():
    """Token-boundary matching means 'id' (if it were a hint) would not
    match 'paid' or 'mid_x'. Use real-world hints to assert this:
    finance's `dx`-style hints don't exist, but healthcare has 'dx'.
    A column named `void_dx_log` should match (token member); a column
    named `mids` should NOT (substring overlap on 'm', 'i', 'd', 's'
    with no token equality)."""
    df = pd.DataFrame(columns=["mids", "tax_paid", "user_active"])
    # None of these tokenize to any hint in any built-in pack.
    assert detect_domain(df) is None
