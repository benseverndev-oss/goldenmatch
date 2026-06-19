"""Lock the committed golden-vector fixture against the Python reference.

The fixture (``tests/fixtures/sketch_golden.json``) is generated from
``sketch.py`` by ``scripts/gen_sketch_golden.py``. This test asserts the
reference still reproduces it bit-for-bit — a tripwire against accidental
algorithm drift. The Rust and TS suites assert against the same fixture, so the
three implementations are anchored to one source of truth.
"""
from __future__ import annotations

import json
from pathlib import Path

from goldenmatch.core import sketch

_FIXTURE = Path(__file__).parent / "fixtures" / "sketch_golden.json"


def _load() -> list[dict]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _ints(xs: list[str]) -> list[int]:
    return [int(x) for x in xs]


def test_golden_fixture_present_and_nonempty():
    cases = _load()
    assert len(cases) >= 10  # edge coverage


def test_python_reference_reproduces_golden_fixture():
    for case in _load():
        text, mode, k = case["text"], case["mode"], case["k"]
        num_perms, num_bands, seed = case["num_perms"], case["num_bands"], case["seed"]

        sh = sketch.shingle(text, mode, k)
        assert sh == _ints(case["shingles"]), f"shingles mismatch for {case!r}"

        sig = sketch.signature(sh, num_perms, seed)
        assert sig == _ints(case["signature"]), f"signature mismatch for {case!r}"

        bands = sketch.band_hashes(sig, num_bands)
        assert bands == _ints(case["band_hashes"]), f"band_hashes mismatch for {case!r}"
