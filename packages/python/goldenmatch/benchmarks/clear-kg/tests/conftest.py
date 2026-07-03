"""Make the CLEAR-KG harness modules and `goldenmatch` importable from tests."""
import os
import sys
from pathlib import Path

_HARNESS = Path(__file__).parent.parent            # benchmarks/clear-kg
_PKG_ROOT = _HARNESS.parent.parent                 # packages/python/goldenmatch
for p in (str(_HARNESS), str(_PKG_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("GOLDENMATCH_NATIVE", "0")
os.environ.setdefault("POLARS_SKIP_CPU_CHECK", "1")
