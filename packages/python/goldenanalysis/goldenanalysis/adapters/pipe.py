"""``pipe`` adapter — GoldenPipe ``PipeResult`` → ``AnalyzerInput.artifacts``.

Near-passthrough: ``PipeResult.artifacts`` already carries the per-stage outputs
(findings / manifest / clusters / scored_pairs / match_stats / recall_certificate
/ ...) under the same keys the analyzers read. Duck-typed; no ``goldenpipe`` import.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from goldenanalysis.adapters.match import _normalize_cert
from goldenanalysis.models import AnalyzerInput


def _dataset_from_source(source: Any) -> str:
    if not source or not isinstance(source, str) or source.startswith("<"):
        return "frame"
    return Path(source).stem or "frame"


class PipeArtifactAdapter:
    """Normalizes a GoldenPipe ``PipeResult`` into an ``AnalyzerInput``."""

    def load(self, result: Any, *, dataset: str | None = None) -> AnalyzerInput:
        artifacts: dict[str, Any] = dict(getattr(result, "artifacts", {}) or {})
        artifacts["__producer__"] = "goldenpipe"
        if "recall_certificate" in artifacts:
            normalized = _normalize_cert(artifacts["recall_certificate"])
            if normalized is None:
                artifacts.pop("recall_certificate", None)
            else:
                artifacts["recall_certificate"] = normalized
        ds = dataset or _dataset_from_source(getattr(result, "source", None))
        return AnalyzerInput(dataset=ds, artifacts=artifacts)
