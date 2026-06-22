"""S2a (spec 2026-06-22-autoconfig-smarter-faster-s1-s3): adaptive identifier
cardinality floor in `_classify_by_data`.

The fixed 0.95 floor for promoting a near-unique numeric-shaped column to
`identifier` becomes `max(0.95, 1 - 1/sqrt(n))`: it RISES above 0.95 at scale
(a 10k-row 0.95-cardinality column is a high-entropy name, not an ID) and never
drops below 0.95, so small-n behavior is unchanged (a looser small-n floor
reclassified moderately-unique phone/numeric columns and broke established
matchkey behavior). The kernel lives in the shared core (classify.rs); this pins
the pure-Python oracle, which the golden vectors prove byte-identical to Rust.
"""
from __future__ import annotations

import math


def _floor(n: int) -> float:
    return max(0.95, 1.0 - 1.0 / math.sqrt(n))


from goldenmatch.core.autoconfig import _classify_by_data  # noqa: E402


def test_small_n_unchanged_from_old_floor():
    # n=10, 8 unique -> card 0.80. floor(10)=max(0.95, 0.684)=0.95, so 0.80 is
    # NOT promoted -- identical to the old fixed 0.95 (no small-n reclassification).
    vals = ["1000", "1001", "1002", "1003", "1004", "1005", "1006", "1007", "1007", "1007"]
    assert len(set(vals)) / len(vals) == 0.8
    assert _floor(10) == 0.95
    assert _classify_by_data(vals)[0] != "identifier"


def test_stricter_at_scale_rejects_below_adaptive_floor():
    # n=900, 860 unique -> card ~0.9556 < floor(900)=max(0.95, 0.9667)=0.9667
    # -> NOT identifier, even though it CLEARS the old fixed 0.95. This is S2a's
    # one behavioral change: stricter at scale.
    big = [str(100_000 + i) for i in range(860)] + [str(100_000 + i) for i in range(40)]
    assert len(big) == 900
    card = len(set(big)) / len(big)
    assert card >= 0.95  # old fixed floor would have PROMOTED it
    assert card < _floor(900)  # new floor (0.9667) rejects it
    assert _classify_by_data(big)[0] != "identifier"


def test_floor_never_below_0_95():
    # The cap guarantees small/medium n keep the historical 0.95 floor.
    for n in (10, 30, 100, 400):
        assert _floor(n) == 0.95
    # Only above ~400 does it rise.
    assert _floor(900) > 0.95


def test_high_cardinality_still_identifier():
    # card 1.0 clears any floor.
    vals = [str(1000 + i) for i in range(15)]
    assert _classify_by_data(vals)[0] == "identifier"
