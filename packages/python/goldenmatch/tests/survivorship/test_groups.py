import polars as pl
from goldenmatch.core.survivorship.groups import detect_groups_heuristic, build_field_groups
from goldenmatch.config.schemas import GoldenGroupRule


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
