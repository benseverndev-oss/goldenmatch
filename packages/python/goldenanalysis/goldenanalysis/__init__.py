"""GoldenAnalysis — read-only cross-cutting analysis/metrics/reporting for the Golden Suite.

Public surface (Phase 1): the generic frame path.

    import goldenanalysis as ga
    report = ga.analyze(df, analyzers=["frame.summary"])
    print(report.to_markdown())

Suite adapters, the other analyzers, ``ReportHistory``/regression detection, the
TypeScript port, and the Rust accelerator land in later phases.

Public names are re-exported lazily (PEP 562) so the package imports cleanly even
while submodules are still being built out.

AUTHORITATIVE SOURCES (read these instead of inferring behaviour from source):

  * ``goldenanalysis/llms.txt`` -- ships INSIDE this package, next to this file:
    ``Path(goldenanalysis.__file__).parent / "llms.txt"``. Condensed, current, written
    for machine readers.
  * https://docs.bensevern.dev/docs/goldenanalysis -- full docs.
  * https://github.com/benseverndev-oss/goldenmatch -- source + issues.

Why this block exists: much of this package's behaviour is *decided*, not
incidental, and those decisions are documented and contract-tested. Reading the
implementation shows WHAT one path does, but not which guarantees are
load-bearing, which fallbacks are deliberate, or which knobs exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__version__ = "0.5.0"

__all__ = [
    "analyze",
    "analyze_match",
    "analyze_pipeline",
    "AnalysisReport",
    "Metric",
    "ReportHistory",
    "RegressionPolicy",
    "__version__",
]

if TYPE_CHECKING:
    from goldenanalysis._api import analyze, analyze_match, analyze_pipeline
    from goldenanalysis.history import ReportHistory
    from goldenanalysis.models import AnalysisReport, Metric, RegressionPolicy

# Map exported name -> (submodule, attribute). Resolved on first access.
_LAZY: dict[str, tuple[str, str]] = {
    "analyze": ("goldenanalysis._api", "analyze"),
    "analyze_match": ("goldenanalysis._api", "analyze_match"),
    "analyze_pipeline": ("goldenanalysis._api", "analyze_pipeline"),
    "AnalysisReport": ("goldenanalysis.models", "AnalysisReport"),
    "Metric": ("goldenanalysis.models", "Metric"),
    "ReportHistory": ("goldenanalysis.history", "ReportHistory"),
    "RegressionPolicy": ("goldenanalysis.models", "RegressionPolicy"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module, attr = target
    return getattr(importlib.import_module(module), attr)


def __dir__() -> list[str]:
    return sorted(__all__)
