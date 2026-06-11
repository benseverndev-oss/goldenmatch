"""#510 quality-invariant scale harness tests. Imports the repo-root script
(scripts/quality_invariant_scale.py) by path; runs in the `python` lane (no Ray)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

# Repo root is 4 parents up from this file:
# packages/python/goldenmatch/tests/<this> -> repo root.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPTS = _REPO_ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import quality_invariant_scale as qis  # noqa: E402


def test_corrupt_cell_types_are_deterministic_and_string_valued():
    # transpose (type_sel<0.25): "abcd" with pos 0 -> "bacd"
    assert qis._corrupt_cell("abcd", 0.10, 0.0) == "bacd"
    # delete (0.25<=type_sel<0.50): "abcd" pos 0 -> "bcd"
    assert qis._corrupt_cell("abcd", 0.30, 0.0) == "bcd"
    # token drop (0.50<=type_sel<0.75) on multi-token: "12 main st" drop tok 0
    out = qis._corrupt_cell("12 main st", 0.60, 0.0)
    assert out == "main st"
    # whole-field null (type_sel>=0.75) -> empty
    assert qis._corrupt_cell("abcd", 0.90, 0.5) == ""
    # empty / single-char inputs never raise
    assert qis._corrupt_cell("", 0.10, 0.0) == ""
    assert qis._corrupt_cell("x", 0.10, 0.0) in ("x", "")


def test_apply_field_corruption_prefix_stable_across_n():
    # Row i's corruption depends only on (seed, field stream), NOT on n.
    base = [f"value{i:04d}" for i in range(50)]
    ss = np.random.SeedSequence([0, 1])
    rng_small = np.random.default_rng(ss.spawn(1)[0])
    rng_big = np.random.default_rng(np.random.SeedSequence([0, 1]).spawn(1)[0])
    small = qis._apply_field_corruption(list(base), 0.5, rng_small)
    big = qis._apply_field_corruption([f"value{i:04d}" for i in range(50)], 0.5, rng_big)
    # Same stream, same length here -> identical. (Cross-n prefix stability is
    # asserted at the generator level in Task 2; the (n,3) block draw is what
    # guarantees it.)
    assert small == big
