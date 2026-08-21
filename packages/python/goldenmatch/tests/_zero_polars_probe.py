"""Subprocess probe for the D6 zero-polars gate (run via test_zero_polars_gate).

Runs an eligible exact dedupe on the Frame lane and exits nonzero if polars
landed in sys.modules.
"""
import os
import pathlib
import sys


# Simulate the D6 end-state: polars is NOT INSTALLED. Any import attempt
# raises ImportError, so polars-present optimizations must fail open/soft
# and the seam-native routes must carry the run.
class _PolarsBlocker:
    def find_module(self, name, path=None):  # noqa: D102 (py<3.12 protocol)
        if name == "polars" or name.startswith("polars."):
            return self
        return None

    def find_spec(self, name, path=None, target=None):  # noqa: D102
        if name == "polars" or name.startswith("polars."):
            raise ImportError("polars blocked (D6 zero-polars gate)")
        return None

    def load_module(self, name):  # noqa: D102
        raise ImportError("polars blocked (D6 zero-polars gate)")


sys.meta_path.insert(0, _PolarsBlocker())

os.environ["GOLDENMATCH_FRAME"] = "arrow"
os.environ["GOLDENMATCH_NATIVE"] = os.environ.get("GOLDENMATCH_NATIVE_GATE", "0")
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"

# The case module must be importable from this file's own directory -- the gate
# puts the tests dir on PYTHONPATH, but a direct `python tests/_zero_polars_probe.py`
# run should work too.
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from _zero_polars_cases import CASES  # noqa: E402

case_name = sys.argv[1] if len(sys.argv) > 1 else "exact"
if case_name not in CASES:
    raise SystemExit(f"unknown case {case_name!r}; known: {sorted(CASES)}")

res = CASES[case_name]()
assert res is not None, f"case {case_name!r} returned no result"

_leaked = sorted(m for m in sys.modules if m == "polars" or m.startswith("polars."))
assert not _leaked, f"case {case_name!r} imported polars: {_leaked}"
print("ZERO-POLARS OK")
