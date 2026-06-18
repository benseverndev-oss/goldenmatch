from goldenmatch.plugins.registry import PluginRegistry
from goldenmatch.refdata.business_aliases import canonical_company_form


def test_business_canonical_collapses_seed_aliases():
    assert canonical_company_form("Acme Inc") == canonical_company_form("Acme Incorporated")


def test_business_canonical_unknown_passthrough_is_stable():
    a = canonical_company_form("Globex Pharma")
    assert a == canonical_company_form("Globex Pharma")   # idempotent


def test_alias_transforms_registered():
    from goldenmatch.refdata.given_names import are_equivalent
    reg = PluginRegistry.instance()
    assert reg.has_transform("refdata_business_canonical")
    assert reg.has_transform("refdata_given_name_canonical")
    gn = reg.get_transform("refdata_given_name_canonical")
    assert are_equivalent("Bob", "Robert")                 # precondition from the data
    assert gn.transform("Bob") == gn.transform("Robert")   # same set -> same canonical
