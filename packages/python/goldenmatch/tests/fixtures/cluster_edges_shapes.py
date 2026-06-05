"""Hand-built parity fixture for the scale-mode DataFusion cluster edge view
(``goldenmatch.core.cluster_edges_df``). Every ``expected`` value is computed BY
HAND below and cross-checked in the docstring so a reviewer can eyeball parity
without running anything.

The fixture deliberately exercises every clause of the parity contract:
  - cid 10: fully-connected TRIANGLE (size 3, 3 edges) + a DUPLICATE canonical
    pair whose LATER score is LOWER -> MAX keeps the higher (pins MAX, not
    last-wins).
  - cid 20: SPARSE cluster (size 4, only 3 edges -> connectivity < 1) that is
    also WEAK (avg - min > 0.3).
  - cid 30: REVERSED pair (31, 30) [a > b] + a non-canonical-only pair
    (32, 30) [a > b, no (30, 32) twin] sharing the MIN score -> exercises
    AS-GIVEN keys and the LEXICOGRAPHIC bottleneck tie-break.
  - cid 40: SINGLETON (size 1, 0 edges) -> confidence 1.0, survives the rollup.
  - a CROSS-CUT edge (3, 4): endpoints in cid 10 and cid 20 -> DROPPED.
"""
from __future__ import annotations

import polars as pl


def build_cluster_edges_fixture():
    """Return ``(pairs, assignments, expected)``.

    ``pairs``       : list[(a, b, score)] RAW input, AS-GIVEN (never canonicalized).
    ``assignments`` : pl.DataFrame{member_id, cluster_id}, one row per member.
    ``expected``    : dict[cid -> {members, edges, size, min_edge, avg_edge,
                      connectivity, confidence, quality, bottleneck}].
    """
    # ------------------------------------------------------------------ pairs
    # Order matters only for the (1, 2) duplicate (proves MAX != last-wins:
    # the LATER 0.4 must lose to the earlier 0.9).
    pairs: list[tuple[int, int, float]] = [
        # cid 10 -- triangle {1,2,3}
        (1, 2, 0.9),
        (1, 3, 0.8),
        (2, 3, 0.7),
        (1, 2, 0.4),  # DUPLICATE of (1,2) with a LOWER later score -> MAX keeps 0.9
        # cross-cut: 3 in cid 10, 4 in cid 20 -> DROPPED
        (3, 4, 0.99),
        # cid 20 -- sparse {4,5,6,9}; 9 has no edges (size 4, only 3 edges)
        (4, 5, 0.9),
        (5, 6, 0.8),
        (4, 6, 0.2),
        # cid 30 -- {30,31,32}; reversed + non-canonical-only sharing min score
        (31, 30, 0.5),   # REVERSED (a > b)
        (32, 30, 0.5),   # non-canonical-only (a > b, no (30,32)); ties min with (31,30)
        (31, 32, 0.9),
        # cid 40 singleton {99} has NO pairs
    ]

    # ------------------------------------------------------------ assignments
    assignments = pl.DataFrame(
        {
            "member_id": [1, 2, 3, 4, 5, 6, 9, 30, 31, 32, 99],
            "cluster_id": [10, 10, 10, 20, 20, 20, 20, 30, 30, 30, 40],
        }
    )
    assert assignments["member_id"].is_unique().all(), "member_id must be unique"

    # ---------------------------------------------------------------- expected
    # cid 10 (triangle): edges {(1,2):0.9, (1,3):0.8, (2,3):0.7}
    #   size=3, edge_count=3, min=0.7, avg=(0.9+0.8+0.7)/3=0.8
    #   max_possible=3*2/2=3, conn=3/3=1.0
    #   confidence=0.4*0.7+0.3*0.8+0.3*1.0 = 0.28+0.24+0.30 = 0.82
    #   bottleneck = min-score edge = (2,3); avg-min=0.1 (<=0.3) -> strong
    cid10 = {
        "members": {1, 2, 3},
        "edges": {(1, 2): 0.9, (1, 3): 0.8, (2, 3): 0.7},
        "size": 3,
        "min_edge": 0.7,
        "avg_edge": 0.8,
        "connectivity": 1.0,
        "confidence": 0.4 * 0.7 + 0.3 * 0.8 + 0.3 * 1.0,  # 0.82
        "quality": "strong",
        "bottleneck": (2, 3),
    }

    # cid 20 (sparse + weak): edges {(4,5):0.9, (5,6):0.8, (4,6):0.2}
    #   size=4 (member 9 edgeless), edge_count=3, min=0.2, avg=(0.9+0.8+0.2)/3=1.9/3
    #   max_possible=4*3/2=6, conn=3/6=0.5
    #   avg-min = 1.9/3 - 0.2 = 0.4333.. (>0.3) -> WEAK
    #   confidence(raw)=0.4*0.2+0.3*(1.9/3)+0.3*0.5 = 0.08+0.19+0.15 = 0.42
    #   bottleneck = (4,6) (min score 0.2)
    cid20 = {
        "members": {4, 5, 6, 9},
        "edges": {(4, 5): 0.9, (5, 6): 0.8, (4, 6): 0.2},
        "size": 4,
        "min_edge": 0.2,
        "avg_edge": (0.9 + 0.8 + 0.2) / 3,
        "connectivity": 3 / 6,
        "confidence": 0.4 * 0.2 + 0.3 * ((0.9 + 0.8 + 0.2) / 3) + 0.3 * 0.5,
        "quality": "weak",
        "bottleneck": (4, 6),
    }

    # cid 30 (reversed / non-canonical / lexicographic tie):
    #   edges {(31,30):0.5, (32,30):0.5, (31,32):0.9} -- keys AS-GIVEN
    #   size=3, edge_count=3, min=0.5, avg=(0.5+0.5+0.9)/3=1.9/3
    #   max_possible=3, conn=3/3=1.0
    #   bottleneck: min score 0.5 ties (31,30) & (32,30); lexicographic (a,b)
    #     ascending -> (31,30) wins (a 31 < 32)
    #   avg-min = 1.9/3 - 0.5 = 0.1333.. (<=0.3) -> strong
    #   confidence=0.4*0.5+0.3*(1.9/3)+0.3*1.0 = 0.2+0.19+0.3 = 0.69
    cid30 = {
        "members": {30, 31, 32},
        "edges": {(31, 30): 0.5, (32, 30): 0.5, (31, 32): 0.9},
        "size": 3,
        "min_edge": 0.5,
        "avg_edge": (0.5 + 0.5 + 0.9) / 3,
        "connectivity": 1.0,
        "confidence": 0.4 * 0.5 + 0.3 * ((0.5 + 0.5 + 0.9) / 3) + 0.3 * 1.0,
        "quality": "strong",
        "bottleneck": (31, 30),
    }

    # cid 40 (singleton): no edges; size=1 -> confidence 1.0, connectivity 1.0,
    #   bottleneck None, strong (weak rule needs size > 1).
    cid40 = {
        "members": {99},
        "edges": {},
        "size": 1,
        "min_edge": 0.0,   # coalesced (no edges)
        "avg_edge": 0.0,   # coalesced (no edges)
        "connectivity": 1.0,
        "confidence": 1.0,
        "quality": "strong",
        "bottleneck": None,
    }

    expected = {10: cid10, 20: cid20, 30: cid30, 40: cid40}
    return pairs, assignments, expected
