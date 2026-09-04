"""``_union_find_remap`` is a pure function -- no Snowflake connection needed.

Covers the "Port of ``IdentityStore.merge_by_shared_field`` (store.py)" claim
in its docstring: the union-find + survivor rule (lexicographically smallest
entity id in the component) were lifted from the store.py inline algorithm.
The chaining case it explicitly calls out (an entity sharing two different
values, linking two otherwise-disjoint groups into one component) had no test
anywhere in the suite.
"""
from __future__ import annotations

from goldenmatch.identity.snowflake_backend import _union_find_remap


def test_single_group_survivor_is_lexicographically_smallest():
    remap, groups = _union_find_remap({"123": ["e2", "e1"]})
    assert groups == 1
    assert remap == [("e2", "e1")]


def test_no_shared_values_is_a_noop():
    remap, groups = _union_find_remap({})
    assert (remap, groups) == ([], 0)


def test_chains_across_two_shared_values_into_one_component():
    """e1/e2 share value v1; e2/e3 share value v2. e2 is the bridge, so all
    three must collapse into ONE component with the lexicographically
    smallest id as the sole survivor -- not two separate two-entity merges."""
    by_val = {"v1": ["e2", "e1"], "v2": ["e2", "e3"]}
    remap, groups = _union_find_remap(by_val)
    assert groups == 1
    survivors = {new for _old, new in remap}
    absorbed = {old for old, _new in remap}
    assert survivors == {"e1"}
    assert absorbed == {"e2", "e3"}


def test_chain_survivor_is_lexicographically_smallest_regardless_of_order():
    """Same chain, but the smallest id (``e1``) is reached only via the
    bridge -- the union-find must still find it as the global root."""
    by_val = {"va": ["e3", "e2"], "vb": ["e2", "e1"]}
    remap, groups = _union_find_remap(by_val)
    assert groups == 1
    assert {new for _old, new in remap} == {"e1"}


def test_disjoint_groups_stay_separate():
    by_val = {"v1": ["b", "a"], "v2": ["d", "c"]}
    remap, groups = _union_find_remap(by_val)
    assert groups == 2
    assert dict(remap) == {"b": "a", "d": "c"}
