"""``flow`` adapter — GoldenFlow ``TransformResult`` → ``AnalyzerInput.artifacts``.

Duck-typed: reads ``.df`` and ``.manifest`` off the result; imports nothing from
``goldenflow``.
"""

from __future__ import annotations

from typing import Any

from goldenanalysis.models import AnalyzerInput


class FlowArtifactAdapter:
    """Normalizes a GoldenFlow ``TransformResult`` into an ``AnalyzerInput``."""

    def load(self, result: Any, *, dataset: str | None = None) -> AnalyzerInput:
        return AnalyzerInput(
            dataset=dataset or "flow",
            frame=getattr(result, "df", None),
            artifacts={
                "__producer__": "goldenflow",
                "manifest": getattr(result, "manifest", None),
            },
        )
