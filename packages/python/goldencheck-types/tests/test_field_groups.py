from goldencheck_types.types import DomainPack, FieldGroupSpec, FieldSpec, SCHEMA_VERSION


def _spec(name):
    return FieldSpec(name=name, name_hints=[], value_signals={}, suppress=[])


def test_field_group_spec_defaults():
    g = FieldGroupSpec(name="address", members=["street", "city", "state", "zip"])
    assert g.category is None
    assert g.default_strategy == "most_complete"
    assert g.date_hint is None


def test_domain_pack_groups_default_empty_and_backcompat():
    # Existing 3-arg construction must still work (groups defaulted).
    pack = DomainPack(name="people", description="", types={"street": _spec("street")})
    assert pack.groups == []


def test_domain_pack_with_groups():
    pack = DomainPack(
        name="people", description="", types={"street": _spec("street")},
        groups=[FieldGroupSpec(name="address", members=["street", "city"])],
    )
    assert pack.groups[0].members == ["street", "city"]


def test_schema_version_bumped():
    assert SCHEMA_VERSION >= 3
