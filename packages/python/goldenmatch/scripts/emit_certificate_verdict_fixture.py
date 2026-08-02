#!/usr/bin/env python3
"""Emit the cross-language parity fixture for the certificate trust-verdict block
(`goldenmatch.core.key_integrity_certificate.certificate_verdict`).

The verdict block is the `key_integrity` metadata a semantic catalog carries — the
single-sourced projection the Cube / OSI / MetricFlow emitters write back. It is a
pure projection of a `KeyIntegrityCertificate`'s fields, computed identically on
Python and the TS port (`certificateVerdict`); this fixture (Python-generated)
locks them field-for-field. Read directly by both
`tests/test_certificate_verdict.py` and
`tests/parity/certificate-verdict.parity.test.ts` — no copy.

Each case carries the certificate's constructor `init` fields + optional
`resolution` mutations so both languages reconstruct the SAME certificate, then
assert `certificate_verdict(cert)` == the emitted `expected` block.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core.key_integrity_certificate import (
    KeyIntegrityCertificate,
    certificate_verdict,
)
from goldenmatch.semantic.key_integrity import _wilson_interval

# name -> (init kwargs, resolution kwargs | None). The resolution kwargs mutate
# the certificate in place (as the ER tier does). undercount CI is derived from
# _wilson_interval so the fixture stays consistent with the D2 helper.
_CASES: list[tuple[str, dict, dict | None]] = [
    (
        "clean_unique_with_measure",
        dict(
            key_columns=["customer_id"], grain=None, n_rows=4, n_key_groups=4,
            is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
            measure_fan_out={"revenue": 1.0},
        ),
        None,
    ),
    (
        "structural_fanout_no_resolution",
        dict(
            key_columns=["customer_id"], grain=None, n_rows=5, n_key_groups=4,
            is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
            measure_fan_out={"revenue": 1.4},
        ),
        None,
    ),
    (
        "resolved_with_fragmentation_ci",
        dict(
            key_columns=["customer_id"], grain=None, n_rows=5, n_key_groups=4,
            is_unique_at_grain=False, duplicate_key_groups=1, max_fan_out=2.0,
            measure_fan_out={"revenue": 1.4},
        ),
        dict(resolved_entities=2, fragmented_entities=1),
    ),
    (
        "clean_no_measures",
        dict(
            key_columns=["order_id"], grain=None, n_rows=3, n_key_groups=3,
            is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
            measure_fan_out={},
        ),
        None,
    ),
    (
        "resolved_no_fragments",
        dict(
            key_columns=["customer_id"], grain=["day"], n_rows=6, n_key_groups=6,
            is_unique_at_grain=True, duplicate_key_groups=0, max_fan_out=1.0,
            measure_fan_out={"revenue": 1.0, "units": 1.0},
        ),
        dict(resolved_entities=3, fragmented_entities=0),
    ),
]

_OUT = (
    Path(__file__).resolve().parent.parent
    / ".."
    / ".."
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "key-integrity"
    / "certificate_verdict_cases.json"
)


def _build() -> dict:
    cases = []
    for name, init, resolution in _CASES:
        cert = KeyIntegrityCertificate(**init)
        res_out: dict | None = None
        if resolution is not None:
            resolved = resolution["resolved_entities"]
            fragmented = resolution["fragmented_entities"]
            cert.resolved_entities = resolved
            cert.fragmented_entities = fragmented
            cert.undercount_estimate = (fragmented / resolved) if resolved else 0.0
            ci = _wilson_interval(fragmented, resolved)
            if ci is not None:
                cert.undercount_ci_low, cert.undercount_ci_high = ci
            res_out = {
                "resolved_entities": resolved,
                "fragmented_entities": fragmented,
                "undercount_estimate": cert.undercount_estimate,
                "undercount_ci_low": cert.undercount_ci_low,
                "undercount_ci_high": cert.undercount_ci_high,
            }
        case: dict = {
            "name": name,
            "init": {
                "keyColumns": init["key_columns"],
                "grain": init["grain"],
                "nRows": init["n_rows"],
                "nKeyGroups": init["n_key_groups"],
                "isUniqueAtGrain": init["is_unique_at_grain"],
                "duplicateKeyGroups": init["duplicate_key_groups"],
                "maxFanOut": init["max_fan_out"],
                "measureFanOut": init["measure_fan_out"],
            },
            "resolution": res_out,
            "expected": certificate_verdict(cert),
        }
        cases.append(case)
    return {
        "_comment": (
            "Cross-language parity oracle for the certificate trust-verdict block "
            "(the `key_integrity` catalog tag the Cube/OSI/MetricFlow emitters write "
            "back). Generated from the Python reference "
            "goldenmatch.core.key_integrity_certificate.certificate_verdict by "
            "scripts/emit_certificate_verdict_fixture.py. Read DIRECTLY by both "
            "tests/test_certificate_verdict.py and "
            "tests/parity/certificate-verdict.parity.test.ts -- no copy. Each case's "
            "`init` (+ optional `resolution` mutation) reconstructs the same "
            "certificate on both surfaces; `expected` is certificate_verdict(cert)."
        ),
        "cases": cases,
    }


def main() -> None:
    _OUT.write_text(json.dumps(_build(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    main()
