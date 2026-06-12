"""``match`` adapter — GoldenMatch ``DedupeResult`` → ``AnalyzerInput.artifacts``.

Duck-typed: reads ``.clusters`` / ``.scored_pairs`` / ``.stats`` off the result,
so it imports nothing from ``goldenmatch``. The recall certificate is optional —
passed in by the caller, or read off ``result.recall_certificate`` when the
producer attached one (``dedupe_df(..., certify=True)``).
"""

from __future__ import annotations

from typing import Any

from goldenanalysis.models import AnalyzerInput


def _normalize_cert(cert: Any) -> dict[str, Any] | None:
    """Normalize a recall certificate to ``{estimate, safe_bound}`` (or None)."""
    if cert is None:
        return None
    if isinstance(cert, dict):
        return {
            "estimate": cert.get("estimate", cert.get("recall")),
            "safe_bound": cert.get("safe_bound", cert.get("recall_lower")),
        }
    return {
        "estimate": getattr(cert, "recall", None),
        "safe_bound": getattr(cert, "recall_lower", None),
    }


def _primary_threshold(config: Any) -> float | None:
    """Best-effort: the first matchkey's threshold from the result's config."""
    try:
        matchkeys = config.get_matchkeys() if hasattr(config, "get_matchkeys") else getattr(config, "matchkeys", None)
        for mk in matchkeys or []:
            thr = getattr(mk, "threshold", None)
            if thr is not None:
                return float(thr)
    except Exception:
        return None
    return None


class MatchArtifactAdapter:
    """Normalizes a GoldenMatch ``DedupeResult`` into an ``AnalyzerInput``."""

    def load(self, result: Any, *, dataset: str | None = None, certificate: Any = None) -> AnalyzerInput:
        cert = certificate if certificate is not None else getattr(result, "recall_certificate", None)
        artifacts: dict[str, Any] = {
            "__producer__": "goldenmatch",
            "clusters": getattr(result, "clusters", {}) or {},
            "scored_pairs": getattr(result, "scored_pairs", []) or [],
            "match_stats": getattr(result, "stats", {}) or {},
            "match_threshold": _primary_threshold(getattr(result, "config", None)),
        }
        normalized = _normalize_cert(cert)
        if normalized is not None:
            artifacts["recall_certificate"] = normalized
        return AnalyzerInput(dataset=dataset or "match", artifacts=artifacts)
