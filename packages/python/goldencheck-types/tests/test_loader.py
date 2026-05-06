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
