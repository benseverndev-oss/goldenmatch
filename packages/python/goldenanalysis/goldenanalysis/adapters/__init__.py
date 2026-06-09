"""Artifact adapters normalize a producer's output into an ``AnalyzerInput``.

Phase 1 ships only the generic ``frame`` adapter (zero suite deps). Suite adapters
(match/check/flow/pipe) land in Phase 2.
"""

from __future__ import annotations

from goldenanalysis.adapters.frame import FrameArtifactAdapter

__all__ = ["FrameArtifactAdapter"]
