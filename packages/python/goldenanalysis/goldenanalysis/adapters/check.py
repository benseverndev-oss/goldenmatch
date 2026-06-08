"""``check`` adapter — GoldenCheck scan output → ``AnalyzerInput.artifacts``.

Two entry points:
- ``from_scan(findings, profile, ...)`` — pure, no ``goldencheck`` import (the seam
  the unit tests and the pipe adapter use).
- ``load(df, ...)`` — lazy-imports ``goldencheck`` and runs ``scan_dataframe``.
"""

from __future__ import annotations

from typing import Any

from goldenanalysis.models import AnalyzerInput


class CheckArtifactAdapter:
    """Normalizes GoldenCheck scan output into an ``AnalyzerInput``."""

    def from_scan(self, findings: Any, profile: Any = None, *, dataset: str | None = None) -> AnalyzerInput:
        return AnalyzerInput(
            dataset=dataset or "check",
            artifacts={"__producer__": "goldencheck", "findings": findings, "profile": profile},
        )

    def load(self, df: Any, *, dataset: str | None = None, **scan_kwargs: Any) -> AnalyzerInput:
        try:
            import goldencheck  # pyright: ignore[reportMissingImports]  # optional [check] extra
        except ImportError as exc:  # pragma: no cover - exercised in CI with the extra
            raise RuntimeError(
                "goldenanalysis[check] requires goldencheck: pip install goldenanalysis[check]"
            ) from exc
        findings, profile = goldencheck.scan_dataframe(df, **scan_kwargs)
        return self.from_scan(findings, profile, dataset=dataset)
