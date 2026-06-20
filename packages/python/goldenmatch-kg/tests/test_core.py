import polars as pl  # noqa: F401  (ensures polars present)
import pytest  # noqa: F401  (imported for skipif gating in future shim tests)
from goldenmatch_kg.core import Entity, EntityResolution, resolve_entities


def _ents():
    # Two real duplicate sets + one singleton (string ids, arbitrary order).
    return [
        Entity(id="a", name="International Business Machines", type="org"),
        Entity(id="b", name="IBM", type="org"),
        Entity(id="c", name="Apple Inc", type="org"),
        Entity(id="d", name="Apple", type="org"),
        Entity(id="e", name="Microsoft", type="org"),
    ]


def test_resolve_returns_full_partition_over_input_ids():
    res = resolve_entities(_ents())
    assert isinstance(res, EntityResolution)
    flat = sorted(i for g in res.groups for i in g)
    assert flat == ["a", "b", "c", "d", "e"]          # every id present exactly once
    assert set(res.canonical_id) == {"a", "b", "c", "d", "e"}


def test_members_of_a_group_share_one_canonical():
    res = resolve_entities(_ents())
    for group in res.groups:
        cids = {res.canonical_id[i] for i in group}
        assert len(cids) == 1                          # one canonical per group
        cid = cids.pop()
        assert cid in group                            # canonical is a member
        # canonical name is the longest member name (most complete surface)
        names = {res.canonical_name[i] for i in group}
        assert len(names) == 1


def test_parity_with_dedupe_df():
    import goldenmatch as gm
    ents = _ents()
    df = pl.DataFrame({"name": [e.name for e in ents], "entity_type": [e.type for e in ents]})
    direct = {
        frozenset(int(m) for m in info["members"])
        for info in gm.dedupe_df(df).clusters.values()
        if info.get("size", len(info["members"])) > 1
    }
    res = resolve_entities(ents)
    idx = {e.id: n for n, e in enumerate(ents)}
    core_multi = {frozenset(idx[i] for i in g) for g in res.groups if len(g) > 1}
    assert core_multi == direct                         # faithful wrapper


def test_empty_input():
    res = resolve_entities([])
    assert res.groups == () and res.canonical_id == {} and res.canonical_name == {}
