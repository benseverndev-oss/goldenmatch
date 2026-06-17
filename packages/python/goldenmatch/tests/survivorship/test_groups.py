import polars as pl
from goldenmatch.config.schemas import GoldenGroupRule
from goldenmatch.core.survivorship.groups import build_field_groups, detect_groups_heuristic


def test_heuristic_detects_address():
    df = pl.DataFrame({"street": ["a"], "city": ["b"], "state": ["c"], "zip": ["d"], "age": [1]})
    groups = detect_groups_heuristic(df)
    addr = [g for g in groups if g.category == "address"]
    assert addr and set(addr[0].columns) >= {"street", "city", "state", "zip"}


def test_heuristic_needs_two_members():
    df = pl.DataFrame({"street": ["a"], "age": [1]})
    assert not any(g.category == "address" for g in detect_groups_heuristic(df))


def test_explicit_beats_detected_on_overlap():
    df = pl.DataFrame({"street": ["a"], "city": ["b"], "state": ["c"], "zip": ["d"]})
    explicit = [GoldenGroupRule(name="my_addr", columns=["street", "city"])]
    out = build_field_groups(df, pack=None, explicit=explicit, enabled=True)
    assert any(g.name == "my_addr" for g in out)
    seen = set()
    for g in out:
        for c in g.columns:
            assert c not in seen   # disjointness across the final set
            seen.add(c)


def test_detection_disabled_returns_only_explicit():
    df = pl.DataFrame({"street": ["a"], "city": ["b"], "state": ["c"], "zip": ["d"]})
    out = build_field_groups(df, pack=None, explicit=[], enabled=False)
    assert out == []


def test_infermap_fed_maps_pack_groups_to_real_columns(monkeypatch):
    import goldenmatch.core.survivorship.groups as G
    fake_map = {"st": "street", "ct": "city"}
    fake_pack_groups = [("address", ["street", "city", "state", "zip"])]
    monkeypatch.setattr(G, "_infermap_canonical_map", lambda df, pack: fake_map)
    monkeypatch.setattr(G, "_pack_groups", lambda pack: fake_pack_groups)
    df = pl.DataFrame({"st": ["a"], "ct": ["b"]})
    groups = G._infermap_fed_groups(df, pack="sentinel")
    assert groups and set(groups[0].columns) == {"st", "ct"}


def test_infermap_import_error_is_failopen(monkeypatch):
    import goldenmatch.core.survivorship.groups as G
    def boom(df, pack):
        raise ImportError("infermap not installed")
    monkeypatch.setattr(G, "_infermap_canonical_map", boom)
    df = pl.DataFrame({"st": ["a"], "ct": ["b"]})
    assert G._infermap_fed_groups(df, pack="sentinel") == []


def test_infermap_smoke_real(monkeypatch):
    """Smoke test: infermap_fed_groups does not crash on a real DomainPack.
    Skipped if infermap or goldencheck_types are not importable in this env.
    """
    pytest = __import__("pytest")
    pytest.importorskip("goldencheck_types")
    pytest.importorskip("infermap")
    import goldenmatch.core.survivorship.groups as G
    from goldencheck_types import DomainPack, FieldGroupSpec
    from goldencheck_types.types import FieldSpec
    address_pack = DomainPack(
        name="address_test",
        description="smoke test pack",
        types={
            "street": FieldSpec(
                name="street",
                description="street address",
                name_hints=frozenset(["street", "addr"]),
                value_signals={},
                confidence_threshold=0.5,
                suppress=frozenset(),
            ),
            "city": FieldSpec(
                name="city",
                description="city name",
                name_hints=frozenset(["city", "town"]),
                value_signals={},
                confidence_threshold=0.5,
                suppress=frozenset(),
            ),
        },
        groups=[FieldGroupSpec(name="address", members=["street", "city"])],
    )
    df = pl.DataFrame({"street": ["123 Main St"], "city": ["Springfield"]})
    result = G._infermap_fed_groups(df, pack=address_pack)
    # Fail-open: must return a list (may be empty if infermap doesn't map columns)
    assert isinstance(result, list)


def test_heuristic_does_not_absorb_email_address_into_address():
    df = pl.DataFrame({"email_address": ["a@b.com"], "city": ["NY"], "state": ["NY"]})
    groups = detect_groups_heuristic(df)
    for g in groups:
        if g.category == "address":
            assert "email_address" not in g.columns


def test_heuristic_does_not_match_real_estate_as_state():
    df = pl.DataFrame({"real_estate": ["x"], "city": ["NY"], "zip": ["10001"]})
    groups = detect_groups_heuristic(df)
    for g in groups:
        if g.category == "address":
            assert "real_estate" not in g.columns


def test_email_address_assigned_to_contact_not_address():
    df = pl.DataFrame({"email_address": ["a@b.com"], "phone": ["555-0001"],
                       "city": ["NY"], "state": ["NY"]})
    groups = detect_groups_heuristic(df)
    contact = [g for g in groups if g.category == "contact"]
    assert contact and "email_address" in contact[0].columns
    for g in groups:
        if g.category == "address":
            assert "email_address" not in g.columns
