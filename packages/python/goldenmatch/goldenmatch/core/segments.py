"""Segment labels — which *party* each column describes.

InferMap's identity-layer detector answers "who is in this table": a loan tape
refers to a lender, a borrower and a servicer, each its own entity population.
This module is GoldenMatch's **read-only consumer** of that answer, turning
layers into the segment labels that per-block configuration (#2575) needs as
its input.

Deliberately inert with respect to matching. It labels; it does not vary
scorers, thresholds, weights or blocking, and nothing here reaches the
vectorized fast paths, the global EM, or the single-score-space clustering
assumption. Those are #2575's subject, behind its measured-quality-win gate.
Landing the labels first is what makes that work testable.

**Two entry points, one shape.** When GoldenPipe already ran InferMap, the
layers ride on ``InferredSchema.layers`` and cost nothing to read
(:func:`segments_from_schema`). Standalone callers detect on demand
(:func:`detect_segments`), which is name-only and so costs what
``detect_domain`` costs.

**Optional dependency, fail-open.** InferMap is not a GoldenMatch runtime
dependency — the import is lazy and every failure degrades to ``[]``, the same
posture ``core/survivorship/groups.py`` uses for its InferMap-fed detection.
An empty list means "no segment information", never "one anonymous
population": callers must not read absence as a claim about the data.

Layer objects are consumed **structurally** (``role`` / ``kind`` / ``columns``
/ ``score`` / ``reason``) rather than by importing ``goldencheck_types``, which
keeps GoldenMatch free of a hard dependency on the wire package.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Label carried by a segment whose party is clearly present but unrecognised.
#: Mirrors ``goldencheck_types.UNKNOWN_ROLE`` structurally (see module docstring
#: on why the constant is not imported).
UNKNOWN_SEGMENT: str = "unknown"


@dataclass(frozen=True)
class Segment:
    """One party present in a frame, and the columns describing it.

    A *segment* is GoldenMatch's name for what InferMap calls an identity
    layer. Same grouping, consumer-side vocabulary: #2575 partitions work by
    segment, so the label is the thing that travels.

    ``score`` and ``reason`` are carried through from detection unchanged so a
    consumer can gate on detection confidence rather than trusting every label
    equally — the never-black-box commitment applied to this surface.
    """

    label: str
    kind: str
    columns: list[str] = field(default_factory=list)
    score: float = 0.0
    reason: str = ""

    @property
    def is_unknown(self) -> bool:
        return self.label == UNKNOWN_SEGMENT


def _to_segment(layer: Any) -> Segment | None:
    """Structurally adapt one identity layer. Returns None if it is not one."""
    role = getattr(layer, "role", None)
    columns = getattr(layer, "columns", None)
    if not isinstance(role, str) or columns is None:
        return None
    return Segment(
        label=role,
        kind=str(getattr(layer, "kind", "unknown")),
        columns=[str(c) for c in columns],
        score=float(getattr(layer, "score", 0.0) or 0.0),
        reason=str(getattr(layer, "reason", "")),
    )


def segments_from_layers(layers: Any) -> list[Segment]:
    """Adapt InferMap identity layers into segments. Fail-open: bad input -> []."""
    if not layers:
        return []
    try:
        return [s for s in (_to_segment(lyr) for lyr in layers) if s is not None]
    except Exception as exc:  # fail-open
        logger.warning("segment adaptation failed (%s); no segments", exc)
        return []


def segments_from_schema(schema: Any) -> list[Segment]:
    """Read segments off an ``InferredSchema`` GoldenPipe already produced.

    The free path: InferMap ran in the pipeline's ``infer_schema`` stage, so
    this is a field read, not a detection. Schemas predating layers carry no
    ``layers`` attribute and yield ``[]``.
    """
    if schema is None:
        return []
    return segments_from_layers(getattr(schema, "layers", None))


def detect_segments(df: Any, *, domain: str | None = None) -> list[Segment]:
    """Detect segments directly, for callers with no ``InferredSchema`` in hand.

    Name-only detection via InferMap. Fail-open on everything, ``ImportError``
    included: InferMap is optional here.
    """
    if df is None:
        return []
    try:
        import infermap

        result = infermap.detect_identity_layers(df, domain=domain)
    except Exception as exc:  # fail-open (incl. ImportError)
        logger.debug("segment detection unavailable (%s); no segments", exc)
        return []
    return segments_from_layers(getattr(result, "layers", None))


def column_segments(segments: list[Segment]) -> dict[str, str]:
    """Column name -> segment label, for the columns that belong to one.

    First segment wins a contested column, matching detection order (segments
    arrive best-scored-first). Columns in no segment are simply absent — the
    caller decides what unlabelled means rather than being handed a guess.
    """
    out: dict[str, str] = {}
    for seg in segments:
        for col in seg.columns:
            out.setdefault(col, seg.label)
    return out


def is_heterogeneous(segments: list[Segment]) -> bool:
    """True when a frame describes more than one party.

    The precondition for per-block configuration being worth anything: a
    single-segment frame is exactly the uniform case one global config already
    serves well. No segments detected is **not** heterogeneous — absence of
    evidence is not evidence of uniformity, but it is also not a licence to
    partition.
    """
    return len(segments) > 1
