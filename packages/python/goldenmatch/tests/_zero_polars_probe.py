"""Subprocess probe for the D6 zero-polars gate (run via test_zero_polars_gate).

Runs an eligible exact dedupe on the Frame lane and exits nonzero if polars
landed in sys.modules.
"""
import os
import pathlib
import sys
import tempfile


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
os.environ["GOLDENMATCH_NATIVE"] = "0"
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"

d = pathlib.Path(tempfile.mkdtemp())
csv = d / "people.csv"
csv.write_text(
    "first,last,city\n"
    "ann,smith,nyc\n"
    "ann,smith,nyc\n"
    "bob,jones,la\n"
    "bobby,jones,la\n"
    "cara,lee,sf\n",
    encoding="utf-8",
)

import goldenmatch.core.pipeline as P  # noqa: E402
from goldenmatch.config.schemas import (  # noqa: E402
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    QualityConfig,
    TransformConfig,
)

cfg = GoldenMatchConfig(
    matchkeys=[
        MatchkeyConfig(
            name="k",
            type="exact",
            fields=[MatchkeyField(field="first"), MatchkeyField(field="last")],
        )
    ],
    quality=QualityConfig(mode="disabled"),
    transform=TransformConfig(mode="disabled"),
)
res = P.run_dedupe([(str(csv), "people")], cfg)
assert res["golden"] is not None and res["golden"].num_rows >= 1

assert "polars" not in sys.modules
print("ZERO-POLARS OK")
