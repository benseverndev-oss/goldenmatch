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

# ── Kind inference from values (#2758) ────────────────────────────────────────
#
# A segment's ``kind`` -- person / organization / asset / place -- is the axis
# downstream matching behaviour keys off, and it arrives populated ONLY when the
# detected role appears in some domain pack's vocabulary. Every layer named by
# its own qualifier (`film`, `rental`, `assays`) therefore carries
# ``kind="unknown"``, which #2574's Part B measured at 331 of 608 columns across
# all three layer corpora. That is the one genuinely value-shaped gap that work
# found: no name yields ``film -> asset`` without enumerating every noun in
# every vertical, which is the losing game the closed ``IDENTITY_KINDS`` set
# exists to avoid.
#
# MEASURED, on the real frames this repo has (per-token rate over the columns a
# value-only classifier calls name-like):
#
#     column                    kind     tokens/value   surname rate
#     NCVR.last_name            person            1.0          0.696
#     NCVR.middle_name          person            1.0          0.587
#     NCVR.first_name           person            1.0          0.423
#     DBLP.authors              person            6.2          0.328
#     NCVR.res_street_address   place             2.2          0.266
#     Buy.name (product title)  asset             5.4          0.122
#     Google.name               asset             6.7          0.094
#     Amazon.title              asset             4.5          0.065
#     DBLP.title                asset             6.7          0.050
#
# Two things that measurement settles. Person name columns are SHORT and dense
# in surnames; product and paper titles are long and sparse, and the gap is
# ~3.5x. And a per-VALUE metric ("does any token hit the list") is useless here:
# it scores product descriptions at 0.83-0.86, because a 10k-surname list
# contains ordinary English words (Brown, Long, Case, Price) and long text hits
# one almost surely. The token gate is not tuning, it is what makes the metric
# mean anything.
#
# ABSTENTION IS THE DESIGN, not a limitation of it. The corpus available here is
# ONE real person frame, six asset frames, and no organization or place frame at
# all -- Febrl3 needs `recordlinkage`, which is not installed. A classifier
# fitted to that would be exactly the self-agreement failure the layer corpora
# were built to escape. So this labels only where the evidence is unambiguous
# and leaves ``unknown`` otherwise: abstaining costs nothing (it is the status
# quo), and the only risk it can carry is a WRONG label. Only ``person`` is
# inferred, because ``person`` is the only kind with two-sided evidence here.
# Organization needs a real org frame; the legal-form signal peaks at 0.080 on
# `manufacturer` columns, which are mostly bare brand names with no suffix.

#: Per-token surname rate above which a short column reads as person names.
#: Person columns measured 0.423-0.696 and asset columns 0.050-0.122; this sits
#: between them with margin on both sides (0.12 below the lowest person, 0.18
#: above the highest asset) rather than tight against either.
_PERSON_SURNAME_FLOOR = 0.30

#: Mean tokens per value above which a column is prose, not a name. Person name
#: columns measured 1.0; the nearest asset column is 4.5. Set at 2.0 so a
#: two-part name passes and a street address (2.2, and 0.266 surname because
#: street names ARE surnames) does not.
_PERSON_MAX_TOKENS_PER_VALUE = 2.0

#: Values sampled per column. Kind is a property of a column's whole population,
#: and a few hundred values settle a rate this coarse.
_KIND_SAMPLE = 200

#: Fraction of a column's tokens that must be USPS street types before the
#: column is read as addresses rather than names. One in five is enough: a
#: street address carries exactly one street type among a handful of tokens,
#: and a genuine person column carries none.
_ADDRESS_TOKEN_FLOOR = 0.2


def _looks_like_addresses(tokens: list[str]) -> bool:
    """True when a street-type vocabulary explains the column.

    Street names ARE surnames -- NCVR's address column scores 0.266 -- so the
    surname rate alone reads addresses as people. The tokens-per-value gate
    catches the measured case (2.2 against a 2.0 floor) but only just: a bare
    ``JACKSON ST`` is exactly 2 tokens and sails through on a 0.5 rate. Excluding
    on the shipped USPS street-type vocabulary is the principled guard, and it is
    what that reference data is for.
    """
    from goldenmatch.refdata import addresses

    if not addresses.is_available() or not tokens:
        return False
    street = addresses.known_tokens()
    hits = sum(1 for t in tokens if t.lower() in street)
    return hits / len(tokens) >= _ADDRESS_TOKEN_FLOOR


def _column_person_rate(values: list[str]) -> tuple[float, float] | None:
    """``(surname rate per token, mean tokens per value)`` or None if unusable.

    Returns None when a street-type vocabulary explains the column, which is an
    abstention rather than a measurement.
    """
    from goldenmatch.refdata import surnames

    if not surnames.is_available():
        return None
    cleaned = [v.strip() for v in values if v and v.strip()]
    if not cleaned:
        return None
    tokens: list[str] = []
    for value in cleaned:
        tokens += [
            t for t in value.replace(",", " ").replace(".", " ").split() if t.isalpha()
        ]
    if not tokens or _looks_like_addresses(tokens):
        return None
    hits = sum(1 for t in tokens if surnames.surname_count(t))
    return hits / len(tokens), len(tokens) / len(cleaned)


def infer_kinds(df: Any, segments: list[Segment]) -> list[Segment]:
    """Fill ``kind`` from VALUES for segments a pack could not name.

    Returns a new list; segments whose ``kind`` is already known are passed
    through untouched -- a pack's declaration is an explicit statement and
    values do not get to overrule it. Fail-open on everything, including
    missing reference data: on any failure the segments are returned unchanged,
    which is the same "no information" posture the rest of this module takes.
    """
    if df is None or not segments:
        return segments
    try:
        from goldenmatch.core.frame import to_frame

        frame = to_frame(df)
        available = set(frame.columns)
        out: list[Segment] = []
        for seg in segments:
            if seg.kind and seg.kind != "unknown":
                out.append(seg)
                continue
            person = False
            for col in seg.columns:
                if col not in available:
                    continue
                values = [
                    str(v)
                    for v in frame.head(_KIND_SAMPLE).column(col).to_list()
                    if v is not None
                ]
                rate = _column_person_rate(values)
                if rate is None:
                    continue
                surname_rate, tokens_per_value = rate
                if (
                    surname_rate >= _PERSON_SURNAME_FLOOR
                    and tokens_per_value <= _PERSON_MAX_TOKENS_PER_VALUE
                ):
                    person = True
                    break
            out.append(
                Segment(
                    label=seg.label, kind="person" if person else seg.kind,
                    columns=list(seg.columns), score=seg.score, reason=seg.reason,
                )
                if person
                else seg
            )
        return out
    except Exception as exc:  # fail-open
        logger.debug("kind inference unavailable (%s); kinds unchanged", exc)
        return segments
