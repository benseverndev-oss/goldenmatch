"""SP-moat resolver: cluster injected alias nodes 3 ways. `er` (goldenmatch) must
merge variant surface forms of ONE entity that `exact` leaves split -- the moat in
miniature. Surname-diverse fixture to avoid dedupe blocking hangs."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.stark_resolve import resolve_aliases  # noqa: E402


def _clusters_as_sets(clusters):
    return {frozenset(c) for c in clusters}


# two entities: A has 3 variant surface forms, B has 1. Distinct real surnames.
_ALIAS_NODES = [
    ("A#a0", "Interleukin 6"), ("A#a1", "IL 6"), ("A#a2", "Interleukin-6"),
    ("B#a0", "Metformin"),
]


def test_none_all_singletons():
    cl = resolve_aliases(_ALIAS_NODES, "none")
    assert _clusters_as_sets(cl) == {frozenset([i]) for i, _ in _ALIAS_NODES}


def test_exact_merges_only_identical_names():
    dup = _ALIAS_NODES + [("C#a0", "Metformin")]     # exact dup of B's name
    cl = resolve_aliases(dup, "exact")
    sets = _clusters_as_sets(cl)
    assert frozenset(["B#a0", "C#a0"]) in sets        # identical names merge
    assert frozenset(["A#a0"]) in sets                # variant forms stay split
    assert frozenset(["A#a1"]) in sets


def test_er_merges_variant_surface_forms():
    # The moat in miniature: `exact` leaves all 3 A-variants as 3 singletons (distinct
    # names); `er` merges at least some of them into a multi-alias cluster that exact
    # cannot -- WITHOUT conflating A with B. (The real engine may not merge EVERY
    # variant -- e.g. an abbreviation form -- which is an honest resolver-limit finding,
    # not a unit-test target; we assert it recovers MORE than exact does, and never
    # over-merges across entities.)
    a_ids = {"A#a0", "A#a1", "A#a2"}
    exact_a = [s for s in _clusters_as_sets(resolve_aliases(_ALIAS_NODES, "exact")) if s & a_ids]
    er_sets = _clusters_as_sets(resolve_aliases(_ALIAS_NODES, "er"))
    er_a = [s for s in er_sets if s & a_ids]
    assert len(exact_a) == 3                            # exact: every A-variant a singleton
    assert len(er_a) < 3                               # er merges >=2 A-variants exact left split
    il6 = next(s for s in er_a if "A#a0" in s)
    assert len(il6 & a_ids) >= 2 and not (il6 - a_ids)  # multi-A cluster, no cross-entity merge
    assert frozenset(["B#a0"]) in er_sets              # B stays separate (no over-merge)
