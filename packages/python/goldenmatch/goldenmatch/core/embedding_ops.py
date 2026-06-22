"""Embedding ops -- drift detection, per-field model selection, canonicalization eval (#1093).

The operations layer for running embeddings + canonicalization in production, the
last phase of the RAG entity-canonicalization epic (#1087). Three capabilities,
all dependency-light (numpy + the existing primitives), deterministic, and
offline-testable:

1. **Drift detection** (`embedding_drift`) -- has the embedding model's output
   distribution shifted between a reference run and now? Compares two embedding
   sets via centroid cosine drift + a population-stability index (PSI) over the
   projection onto the reference centroid, and raises an alarm past a threshold.

2. **Per-field model selection** (`select_field_models`) -- names and descriptions
   want different embedders. Routes short identifier-like text to the zero-config
   in-house model and long free text to a stronger sentence model, honoring any
   explicit per-field ``MatchkeyField.model`` override. Returns model ids that
   ``get_embedder`` accepts.

3. **Canonicalization quality eval** (`evaluate_canonicalization`) -- measure the
   output of ``canonicalize_cluster``: field completeness, provenance coverage,
   synthesized-value rate, LLM-vs-deterministic mix, and (against an optional gold)
   per-field precision/recall/F1 in the ``EvalResult`` shape.

Together these meet the #1093 done-bar: drift alarms + measured canonicalization
quality.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

# ── 1. Drift detection ───────────────────────────────────────────────────────


@dataclass
class EmbeddingDriftReport:
    """Drift between a reference embedding set and a current one."""

    n_reference: int
    n_current: int
    centroid_cosine_drift: float  # 1 - cos(ref_centroid, cur_centroid), in [0, 2]
    mean_shift: float  # L2 distance between the two centroids
    norm_ratio: float  # mean ||current|| / mean ||reference|| (≈1 for normalized)
    psi: float  # population stability index over projection onto ref centroid
    drifted: bool  # alarm: psi >= alarm_psi OR centroid drift >= alarm_centroid_cosine

    def as_dict(self) -> dict[str, Any]:
        return {
            "n_reference": self.n_reference,
            "n_current": self.n_current,
            "centroid_cosine_drift": round(self.centroid_cosine_drift, 6),
            "mean_shift": round(self.mean_shift, 6),
            "norm_ratio": round(self.norm_ratio, 6),
            "psi": round(self.psi, 6),
            "drifted": self.drifted,
        }

    # alias for parity with the EvalResult.summary() convention elsewhere
    summary = as_dict


def _psi(ref: np.ndarray, cur: np.ndarray, *, bins: int) -> float:
    """Population stability index of ``cur`` vs ``ref`` over quantile bins of ref.

    PSI = Σ (cur% - ref%) * ln(cur% / ref%). 0 = identical; the usual ops bands
    are <0.1 stable, 0.1-0.25 moderate shift, >=0.25 significant shift.
    """
    if ref.size == 0 or cur.size == 0:
        return 0.0
    # Quantile edges from the reference so each ref bin is ~equally populated.
    qs = np.linspace(0.0, 1.0, bins + 1)
    edges = np.quantile(ref, qs)
    edges[0], edges[-1] = -np.inf, np.inf
    # Degenerate (all-equal) reference: no meaningful bins -> no measurable drift.
    if not np.all(np.diff(edges[1:-1]) > 0) and bins > 1 and np.ptp(ref) == 0:
        return 0.0
    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)
    eps = 1e-6
    ref_pct = ref_counts / max(ref_counts.sum(), 1) + eps
    cur_pct = cur_counts / max(cur_counts.sum(), 1) + eps
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def embedding_drift(
    reference: np.ndarray,
    current: np.ndarray,
    *,
    bins: int = 10,
    alarm_psi: float = 0.25,
    alarm_centroid_cosine: float = 0.1,
) -> EmbeddingDriftReport:
    """Measure embedding-space drift between a reference set and a current set.

    Args:
        reference: ``(n_ref, dim)`` reference embeddings (e.g. a stored baseline).
        current: ``(n_cur, dim)`` current embeddings to check for shift.
        bins: number of quantile bins for the PSI projection histogram.
        alarm_psi: PSI at/above which drift alarms (0.25 = the standard
            "significant shift" band).
        alarm_centroid_cosine: centroid cosine drift at/above which drift alarms.

    Returns:
        An ``EmbeddingDriftReport``. ``drifted`` is the alarm. Never raises on
        non-empty same-dim inputs.

    Raises:
        ValueError: if the two sets have different embedding dimensionality.
    """
    ref = np.asarray(reference, dtype=np.float64)
    cur = np.asarray(current, dtype=np.float64)
    if ref.ndim != 2 or cur.ndim != 2:
        raise ValueError("embedding_drift: inputs must be 2-D (n, dim) arrays")
    if ref.size == 0 or cur.size == 0:
        return EmbeddingDriftReport(
            n_reference=len(ref), n_current=len(cur),
            centroid_cosine_drift=0.0, mean_shift=0.0, norm_ratio=1.0,
            psi=0.0, drifted=False,
        )
    if ref.shape[1] != cur.shape[1]:
        raise ValueError(
            f"embedding_drift: dim mismatch ref={ref.shape[1]} cur={cur.shape[1]}"
        )

    ref_centroid = ref.mean(axis=0)
    cur_centroid = cur.mean(axis=0)
    rc_norm = float(np.linalg.norm(ref_centroid))
    cc_norm = float(np.linalg.norm(cur_centroid))
    cos = float(ref_centroid @ cur_centroid) / (rc_norm * cc_norm + 1e-12)
    centroid_cosine_drift = 1.0 - cos
    mean_shift = float(np.linalg.norm(cur_centroid - ref_centroid))
    ref_norms = np.linalg.norm(ref, axis=1)
    cur_norms = np.linalg.norm(cur, axis=1)
    ref_radius = float(ref_norms.mean())
    norm_ratio = float(cur_norms.mean()) / (ref_radius + 1e-12)

    # PSI over the scalar projection onto the unit reference-centroid direction --
    # captures a shift in how the population spreads along the reference axis.
    direction = ref_centroid / (rc_norm + 1e-12)
    psi = _psi(ref @ direction, cur @ direction, bins=bins)

    # PSI is the primary alarm (scale-free, standard). The centroid-cosine signal
    # is reliable ONLY when the reference centroid has a real direction -- for
    # embeddings diffuse around the origin (centroid ≈ 0) its direction is noise,
    # so it joins the alarm only when the centroid is "directional" (a cone).
    centroid_is_directional = rc_norm >= 0.3 * ref_radius
    drifted = bool(
        psi >= alarm_psi
        or (centroid_is_directional and centroid_cosine_drift >= alarm_centroid_cosine)
    )
    return EmbeddingDriftReport(
        n_reference=len(ref), n_current=len(cur),
        centroid_cosine_drift=centroid_cosine_drift, mean_shift=mean_shift,
        norm_ratio=norm_ratio, psi=psi, drifted=drifted,
    )


# ── 2. Per-field model selection ─────────────────────────────────────────────

# Sensible defaults: short identifier-like text → the zero-config in-house model
# (fast, no cloud/torch, char-n-gram-ish); long free text → a stronger sentence
# model that captures semantics. Overridable per call.
DEFAULT_SHORT_MODEL = "inhouse"
DEFAULT_LONG_MODEL = "all-MiniLM-L6-v2"
# Mean character length at/above which a text column is treated as "long".
DEFAULT_LONG_CHAR_THRESHOLD = 40


@dataclass
class FieldModelChoice:
    """The embedding model chosen for one field + why."""

    column: str
    model: str
    reason: str  # "override" | "long-text" | "short-text"
    mean_chars: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "model": self.model,
            "reason": self.reason,
            "mean_chars": None if self.mean_chars is None else round(self.mean_chars, 2),
        }


def select_field_model(
    column: str,
    values: list[Any] | None = None,
    *,
    override: str | None = None,
    short_model: str = DEFAULT_SHORT_MODEL,
    long_model: str = DEFAULT_LONG_MODEL,
    long_char_threshold: int = DEFAULT_LONG_CHAR_THRESHOLD,
) -> FieldModelChoice:
    """Choose an embedding model for one field.

    An explicit ``override`` (e.g. from ``MatchkeyField.model``) always wins.
    Otherwise the mean character length of ``values`` routes the column: long
    free text (descriptions) → ``long_model``, short text (names/ids) →
    ``short_model``. With no values, defaults to ``short_model``.
    """
    if override:
        return FieldModelChoice(column=column, model=override, reason="override")
    if not values:
        return FieldModelChoice(column=column, model=short_model, reason="short-text")
    lengths = [len(str(v)) for v in values if v is not None and str(v) != ""]
    mean_chars = float(np.mean(lengths)) if lengths else 0.0
    if mean_chars >= long_char_threshold:
        return FieldModelChoice(
            column=column, model=long_model, reason="long-text", mean_chars=mean_chars
        )
    return FieldModelChoice(
        column=column, model=short_model, reason="short-text", mean_chars=mean_chars
    )


def select_field_models(
    df: Any,
    columns: list[str] | None = None,
    *,
    overrides: dict[str, str] | None = None,
    short_model: str = DEFAULT_SHORT_MODEL,
    long_model: str = DEFAULT_LONG_MODEL,
    long_char_threshold: int = DEFAULT_LONG_CHAR_THRESHOLD,
) -> dict[str, FieldModelChoice]:
    """Choose a per-field embedding model for each text column of ``df``.

    Args:
        df: a Polars DataFrame (or anything with ``.columns`` + ``df[col].to_list()``).
        columns: restrict to these columns (default: all string/object columns).
        overrides: ``{column: model}`` explicit choices that always win
            (e.g. collected from ``MatchkeyField.model``).
        short_model / long_model / long_char_threshold: routing knobs.

    Returns:
        ``{column: FieldModelChoice}``. Non-text columns are skipped (unless an
        override names them).
    """
    overrides = overrides or {}
    cols = columns if columns is not None else list(getattr(df, "columns", []))
    out: dict[str, FieldModelChoice] = {}
    for col in cols:
        if col in overrides:
            out[col] = FieldModelChoice(column=col, model=overrides[col], reason="override")
            continue
        try:
            values = df[col].to_list()
        except Exception:
            continue
        # Treat as text only if some value is a non-numeric string.
        if not any(isinstance(v, str) for v in values):
            continue
        out[col] = select_field_model(
            col, values, short_model=short_model, long_model=long_model,
            long_char_threshold=long_char_threshold,
        )
    return out


# ── 3. Canonicalization quality eval ─────────────────────────────────────────


@dataclass
class CanonicalizationEval:
    """Measured quality of a batch of ``canonicalize_cluster`` outputs.

    The first block is always available (no labels needed); ``tp``/``fp``/``fn``
    (and the precision/recall/f1 properties) are populated only when a ``gold`` is
    supplied, mirroring ``EvalResult``.
    """

    n_records: int = 0
    n_fields: int = 0  # total canonical cells across all records
    field_completeness: float = 0.0  # non-null canonical cells / n_fields
    provenance_coverage: float = 0.0  # cells traceable to a source / n_fields
    synthesized_rate: float = 0.0  # cells with no source record / n_fields
    llm_rate: float = 0.0  # records canonicalized by an LLM / n_records
    # Field-accuracy vs gold (zero unless a gold is supplied).
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "n_records": self.n_records,
            "n_fields": self.n_fields,
            "field_completeness": round(self.field_completeness, 4),
            "provenance_coverage": round(self.provenance_coverage, 4),
            "synthesized_rate": round(self.synthesized_rate, 4),
            "llm_rate": round(self.llm_rate, 4),
        }
        if self.tp or self.fp or self.fn:
            out["field_accuracy"] = {
                "tp": self.tp, "fp": self.fp, "fn": self.fn,
                "precision": round(self.precision, 4),
                "recall": round(self.recall, 4),
                "f1": round(self.f1, 4),
            }
        return out

    as_dict = summary


def evaluate_canonicalization(
    records: list[Any],
    *,
    gold: list[dict[str, Any]] | None = None,
) -> CanonicalizationEval:
    """Measure the quality of a batch of ``CanonicalRecord`` outputs.

    Args:
        records: the ``CanonicalRecord`` objects from ``canonicalize_cluster``
            (anything exposing ``.record``, ``.provenance``, ``.method``).
        gold: optional list of expected canonical dicts, aligned by index to
            ``records``. When given, per-field precision/recall/F1 are computed
            over the gold fields (a gold field matches when the canonical value
            equals it).

    Returns:
        A ``CanonicalizationEval``. Never raises on valid input.
    """
    ev = CanonicalizationEval(n_records=len(records))
    if not records:
        return ev

    total_cells = 0
    non_null = 0
    with_source = 0
    synthesized = 0
    llm = 0
    for rec in records:
        record = getattr(rec, "record", {}) or {}
        provenance = getattr(rec, "provenance", {}) or {}
        if getattr(rec, "method", "") == "llm":
            llm += 1
        for fname, value in record.items():
            total_cells += 1
            if value is not None and str(value) != "":
                non_null += 1
            prov = provenance.get(fname)
            src = getattr(prov, "source_index", None) if prov is not None else None
            if src is not None:
                with_source += 1
            else:
                synthesized += 1

    ev.n_fields = total_cells
    if total_cells:
        ev.field_completeness = non_null / total_cells
        ev.provenance_coverage = with_source / total_cells
        ev.synthesized_rate = synthesized / total_cells
    ev.llm_rate = llm / len(records)

    if gold is not None:
        for i, rec in enumerate(records):
            expected = gold[i] if i < len(gold) else {}
            record = getattr(rec, "record", {}) or {}
            for fname, exp_val in (expected or {}).items():
                if exp_val is None or str(exp_val) == "":
                    continue
                got = record.get(fname)
                if got is not None and str(got) == str(exp_val):
                    ev.tp += 1
                else:
                    ev.fn += 1
                    if got is not None and str(got) != "":
                        ev.fp += 1  # produced a wrong non-null value
    return ev
