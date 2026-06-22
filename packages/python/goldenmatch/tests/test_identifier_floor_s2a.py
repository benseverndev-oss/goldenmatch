"""S2a (spec 2026-06-22-autoconfig-smarter-faster-s1-s3): adaptive identifier
cardinality floor in `_classify_by_data`.

The fixed 0.95 floor for promoting a near-unique numeric-shaped column to
`identifier` becomes `1 - 1/sqrt(n)`: stricter at scale (a 10k-row 0.95-card
column is a high-entropy name, not an ID), looser on tiny samples. The kernel
lives in the shared core (classify.rs); this pins the pure-Python oracle, which
the classifier golden vectors prove byte-identical to Rust.
"""
from __future__ import annotations

import math

from goldenmatch.core.autoconfig import _classify_by_data


def test_small_n_looser_floor_promotes_to_identifier():
    # n=10, 8 unique -> card 0.80 >= floor(10)=0.684 -> identifier
    vals = ["1000", "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1007", "1007"]
    assert len(vals) == 10
    assert len(set(vals)) / len(vals) == 0.8
    col_type, conf = _classify_by_data(vals)
    assert col_type == "identifier"
    assert conf == 0.9


def test_old_fixed_floor_would_have_rejected_small_n_case():
    # The same column under the OLD fixed 0.95 floor: 0.80 < 0.95 -> NOT
    # identifier. This documents the behavior change S2a introduces.
    vals = ["1000", "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1007", "1007"]
    card = len(set(vals)) / len(vals)
    assert card < 0.95  # old floor would reject
    assert card >= 1.0 - 1.0 / math.sqrt(len(vals))  # new floor accepts


def test_stricter_at_scale_rejects_below_adaptive_floor():
    # n=900, 860 unique -> card ~0.9556 < floor(900)=0.9667 -> NOT identifier,
    # even though it clears the old fixed 0.95.
    big = [str(100_000 + i) for i in range(860)] + [str(100_000 + i) for i in range(40)]
    assert len(big) == 900
    card = len(set(big)) / len(big)
    floor = 1.0 - 1.0 / math.sqrt(len(big))
    assert card >= 0.95  # old fixed floor would have PROMOTED it
    assert card < floor  # new adaptive floor rejects
    col_type, _ = _classify_by_data(big)
    assert col_type != "identifier"


def test_high_cardinality_still_identifier():
    # card 1.0 clears any floor.
    vals = [str(1000 + i) for i in range(15)]
    assert _classify_by_data(vals)[0] == "identifier"
