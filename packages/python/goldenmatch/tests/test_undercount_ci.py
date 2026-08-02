"""Resolution-tier undercount confidence interval (95% Wilson score).

`undercount_estimate = fragmented / resolved` is a point estimate; the Wilson
interval bounds its SAMPLING uncertainty (few resolved entities → wide interval).
Pure arithmetic, single-sourced with the TS port by a shared fixture (read
directly from the TS parity tree — no copy).
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core.key_integrity_certificate import KeyIntegrityCertificate
from goldenmatch.semantic.key_integrity import _wilson_interval

FIXTURE = (
    Path(__file__).parent.parent
    / ".."
    / ".."
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "key-integrity"
    / "undercount_ci_cases.json"
)


def test_matches_fixture() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]
    assert cases
    for case in cases:
        ci = _wilson_interval(case["fragmented"], case["resolved"])
        exp = case["expected"]
        if exp["ci_low"] is None:
            assert ci is None, case["name"]
        else:
            assert ci is not None, case["name"]
            assert abs(ci[0] - exp["ci_low"]) < 1e-12, case["name"]
            assert abs(ci[1] - exp["ci_high"]) < 1e-12, case["name"]


def test_interval_properties() -> None:
    # Point estimate lies inside the interval; interval stays within [0, 1].
    for k, n in [(1, 2), (3, 10), (50, 100), (0, 5), (5, 5)]:
        ci = _wilson_interval(k, n)
        assert ci is not None
        low, high = ci
        assert 0.0 <= low <= high <= 1.0
        assert low <= k / n <= high
    # Fewer observations → wider interval at the same proportion.
    narrow = _wilson_interval(50, 100)
    wide = _wilson_interval(1, 2)
    assert (wide[1] - wide[0]) > (narrow[1] - narrow[0])


def test_certificate_conservative_bound() -> None:
    # safe_bound_conservative discounts the CI-upper undercount; falls back to
    # safe_bound when no interval was computed.
    cert = KeyIntegrityCertificate(
        key_columns=["k"], grain=None, n_rows=10, n_key_groups=10,
        is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
    )
    # No resolution run → falls back to safe_bound (== estimate == 1.0).
    assert cert.safe_bound_conservative == cert.safe_bound == 1.0

    # With a CI, the conservative bound uses 1 - ci_high and is <= safe_bound.
    cert.resolved_entities = 2
    cert.fragmented_entities = 1
    cert.undercount_estimate = 0.5
    lo, hi = _wilson_interval(1, 2)
    cert.undercount_ci_low, cert.undercount_ci_high = lo, hi
    assert cert.safe_bound == min(1.0, 1.0 - 0.5)  # 0.5, the point-estimate floor
    assert cert.safe_bound_conservative == min(1.0, 1.0 - hi)  # tighter (wider undercount)
    assert cert.safe_bound_conservative < cert.safe_bound
