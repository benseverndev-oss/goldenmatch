"""Cross-surface lock for the frame kernels' EQUALITY SEMANTICS on adversarial values.

The frame kernels (`distinct_count`, `null_ratio_per_column`, `duplicate_row_ratio`)
decide *when two values are the same*. Python/Rust intern via Arrow with an explicit
canon (``-0.0``/``+0.0`` fold, all ``NaN`` -> one id, null is its own id); TS keys off
``JSON.stringify`` + a ``" null"`` sentinel. This test pins that the two surfaces agree
on the tricky cases (``-0.0`` vs ``0.0``, ``NaN`` vs ``NaN`` vs null, empty-string vs
null, int vs float). The TS side asserts the byte-identical expectations against the
SAME fixture (``frameKernels.parity.test.ts``); ``frame_kernels_adversarial.json`` is a
byte-identical copy shared with ``packages/typescript/goldenanalysis/tests/fixtures/``.

JSON cannot represent ``NaN``/``-0.0`` inputs, so the frames are built in CODE on both
sides (mirrored) and the fixture holds only the finite expected outputs. This replaces
the deferred Wave-1b WASM surface (see
``docs/superpowers/specs/2026-07-06-goldenanalysis-wave1b-deferred.md``): it proves the
equality semantics match cross-surface without shipping arrow-in-wasm.
"""

import json
from pathlib import Path

import polars as pl
from goldenanalysis.core import aggregate as agg

FIXTURE = Path(__file__).parent / "fixtures" / "frame_kernels_adversarial.json"

_NAN = float("nan")

# Mirror of the TS `SCENARIOS` in frameKernels.parity.test.ts. Keep in lockstep.
SCENARIOS: dict[str, dict[str, list]] = {
    "float_nan_null": {"f": [-0.0, 0.0, _NAN, _NAN, None, 1.0, 1.0]},
    "typed_numeric": {"i": [5, 5, 3, None, 5], "g": [5.0, 5.0, 3.0, None, 5.0]},
    "string_empty_null": {"s": ["a", "a", "", None, "a", "b", None]},
    "mixed": {
        "f": [-0.0, 0.0, _NAN, _NAN, None, 1.0, 1.0],
        "i": [5, 5, 3, 3, None, 5, 5],
        "s": ["a", "a", "", None, "a", "b", None],
    },
}


def _kernels(cols: dict[str, list]) -> dict:
    df = pl.DataFrame(cols)
    return {
        "distinct": {c: agg.distinct_count(df[c]) for c in df.columns},
        "null_ratio": agg.null_ratio_per_column(df),
        "dup_ratio": agg.duplicate_row_ratio(df),
    }


def test_frame_kernels_match_fixture() -> None:
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    got = {name: _kernels(cols) for name, cols in SCENARIOS.items()}
    assert got == expected
