"""Emit the column-signals cross-surface parity fixture.

Runs the Python healer adapter's ``_build_column_signals_batch`` on a small,
hardcoded ``(rows, clusters, config)`` and writes the result (plus the matching
inputs) to the TS parity fixture so
``packages/typescript/goldenmatch/tests/parity/suggest-column-signals.parity.test.ts``
can assert ``buildColumnSignals(sameInput) == expected``.

Run from the repo's Python .venv, e.g.::

    .venv/Scripts/python.exe packages/python/goldenmatch/tests/fixtures/emit_column_signals_fixture.py
    # or
    .venv/bin/python packages/python/goldenmatch/tests/fixtures/emit_column_signals_fixture.py

No CLI args. Idempotent: overwrites the fixture in place.

Notes:
- ``variant_rate`` is FORCED to 0.0 by stubbing ``blocking_risk`` -> {} so the
  fixture matches the TS port's contract (the TS package has no GoldenCheck, so
  ``buildColumnSignals`` always emits variant_rate 0.0 — the "goldencheck-absent
  path" the Python adapter falls back to).
- The config here is held in LOCKSTEP with ``fixtureConfig()`` in the TS parity
  test. If you change one, change the other.
"""
from __future__ import annotations

import json
from pathlib import Path

import polars as pl

# Force the goldencheck-absent variant_rate path (TS has no goldencheck), so the
# emitted variant_rate is 0.0 for every column — matching the TS builder.
import goldenmatch.core.quality as _quality

_quality.blocking_risk = lambda *args, **kwargs: {}  # type: ignore[assignment]

from goldenmatch.config.schemas import GoldenMatchConfig  # noqa: E402
from goldenmatch.core.suggest.adapter import (  # noqa: E402
    _build_column_signals_batch,
)

# --- Hardcoded inputs (kept in lockstep with the TS parity test) -----------

ROWS: list[dict] = [
    {"email": "alice@example.com", "name": "Alice Smith", "phone": "555-100-2000", "zip": "10001"},
    {"email": "alice@example.com", "name": "Alice Smyth", "phone": "555-100-2000", "zip": "10001"},
    {"email": "bob@example.com", "name": "Bob Jones", "phone": "555-300-4000", "zip": "20002"},
    {"email": "carol@example.com", "name": "Carol White", "phone": None, "zip": "30003"},
]

# Cluster list (for the TS fixture) — members are POSITIONAL indices because the
# rows carry no __row_id__ (the adapter falls back to positional slicing).
CLUSTERS_LIST = [{"members": [0, 1], "size": 2, "oversized": False}]

# Cluster dict shape the adapter consumes.
CLUSTERS_DICT = {
    0: {
        "members": [0, 1],
        "size": 2,
        "oversized": False,
        "quality": "strong",
        "confidence": 0.9,
    }
}

CONFIG = GoldenMatchConfig.model_validate(
    {
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["zip"], "transforms": []}],
            "max_block_size": 5000,
            "skip_oversized": False,
        },
        "matchkeys": [
            {
                "name": "person",
                "type": "weighted",
                "fields": [{"field": "name", "scorer": "jaro_winkler", "weight": 1.0}],
                "threshold": 0.85,
                "negative_evidence": [
                    {"field": "email", "scorer": "exact", "threshold": 0.5, "penalty": 0.5}
                ],
            }
        ],
    }
)

OUT = (
    Path(__file__).resolve().parents[5]
    / "packages"
    / "typescript"
    / "goldenmatch"
    / "tests"
    / "parity"
    / "fixtures"
    / "suggest"
    / "column_signals_basic.json"
)


def main() -> None:
    df = pl.DataFrame(ROWS)
    batch = _build_column_signals_batch(df, CONFIG, CLUSTERS_DICT)
    expected = batch.to_pylist()

    payload = {
        "rows": ROWS,
        "clusters": CLUSTERS_LIST,
        "expected": expected,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(expected)} column signals)")


if __name__ == "__main__":
    main()
