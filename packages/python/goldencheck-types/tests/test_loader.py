import pytest
from goldencheck_types import list_domains, load_domain


def test_list_domains_includes_finance():
    domains = list_domains()
    assert "finance" in domains
    assert "healthcare" in domains
    assert "ecommerce" in domains
    assert "generic" in domains


def test_load_finance_pack():
    pack = load_domain("finance")
    assert pack.name == "finance"
    assert "account_number" in pack.types
    hints = pack.types["account_number"].name_hints
    assert any("account" in h for h in hints)


def test_load_unknown_raises():
    with pytest.raises(KeyError):
        load_domain("does_not_exist")


def test_generic_pack_is_empty():
    pack = load_domain("generic")
    assert pack.types == {}


def test_confidence_threshold_parses():
    pack = load_domain("_test_threshold")
    assert pack.types["ssn"].confidence_threshold == 0.85


def test_load_domain_is_memoized():
    """Repeated load_domain calls should return the same object instance —
    memoizing keeps detect_domain (which iterates every pack on each
    call) from re-parsing YAML on every invocation."""
    a = load_domain("finance")
    b = load_domain("finance")
    assert a is b


def test_clear_cache_drops_memoized_pack(monkeypatch, tmp_path):
    """clear_cache() forces a re-read after on-disk YAML changes.

    Tests that mutate fixtures (or flip GOLDENCHECK_TYPES_TEST_DIR
    between cases) MUST call clear_cache() — otherwise the cached pack
    from the previous load wins.
    """
    from goldencheck_types import clear_cache

    # Drop into an isolated test dir.
    monkeypatch.setenv("GOLDENCHECK_TYPES_TEST_DIR", str(tmp_path))
    clear_cache()
    (tmp_path / "fakedomain.yaml").write_text(
        "description: v1\ntypes:\n  email:\n    name_hints: [a]\n",
        encoding="utf-8",
    )
    p1 = load_domain("fakedomain")
    assert p1.description == "v1"

    # Mutate, then prove the cached value still wins WITHOUT clearing.
    (tmp_path / "fakedomain.yaml").write_text(
        "description: v2\ntypes:\n  email:\n    name_hints: [b]\n",
        encoding="utf-8",
    )
    p2 = load_domain("fakedomain")
    assert p2.description == "v1", "cache must hold the pre-mutation pack"

    # Now clear and re-read.
    clear_cache()
    p3 = load_domain("fakedomain")
    assert p3.description == "v2"
