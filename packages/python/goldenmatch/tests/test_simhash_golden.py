"""Lock the committed SimHash golden-vector fixture against the Python reference.

The fixture (``tests/fixtures/sketch_simhash_golden.json``) is generated from
``sketch.py`` by ``scripts/gen_simhash_golden.py``. This test asserts the
reference still reproduces it bit-for-bit — a tripwire against accidental
algorithm drift. The Rust and TS suites assert against the same fixture, so the
three implementations are anchored to one source of truth (#1082).
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core import sketch

_FIXTURE = Path(__file__).parent / "fixtures" / "sketch_simhash_golden.json"


def _load() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _ints(xs: list[str]) -> list[int]:
    return [int(x) for x in xs]


def test_golden_fixture_present_and_nonempty():
    cases = _load()
    assert len(cases) >= 10  # edge coverage


def test_python_reference_reproduces_simhash_golden_fixture():
    for case in _load():
        vector = case["vector"]
        num_planes, num_bands, seed = case["num_planes"], case["num_bands"], case["seed"]

        sig = sketch.simhash_signature(vector, num_planes, seed)
        assert sig == case["signature"], f"signature mismatch for {case['label']!r}"

        bands = sketch.simhash_band_hashes(sig, num_bands)
        assert bands == _ints(case["band_hashes"]), f"band_hashes mismatch for {case['label']!r}"
