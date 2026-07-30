"""Closed-loop refit — turn adjudication verdicts into supervised FS labels (D2).

The borderline-band adjudicator (the optional LLM boost, or the human review
queue) produces per-pair verdicts. Today those verdicts adjudicate the current
run's borderline pairs and are then discarded. This module *closes the loop*:
confident verdicts become **labels**, the labels feed the EXISTING supervised
primitive :func:`goldenmatch.core.probabilistic.estimate_m_from_labels`, and the
refined :class:`EMResult` is returned as the **suggested** config. The caller
decides whether to apply it (:meth:`RefitResult.persist`).

Design notes (see ``docs/superpowers/specs/2026-07-29-local-model-integration-design.md``):

- **One authoritative owner.** This adds no second refit path — it feeds the
  existing supervised-m primitive. It is NOT the auto-config controller's
  ``RefitPolicy`` family (signal-driven config re-proposal); this is
  label-driven m re-estimation over the Fellegi-Sunter comparison model.
- **Suggest by default.** :func:`refit_from_labels` never mutates the config or
  touches disk — it returns a :class:`RefitResult` carrying the refined model.
  :meth:`RefitResult.persist` is the explicit apply step (used by ``auto_refit``).
- **Labels are confident MATCHES only.** ``estimate_m_from_labels`` estimates m
  from known true-match pairs (u stays from random pairs), so confident
  non-match verdicts have nowhere to feed and are dropped. A wrong label poisons
  m, so the confidence gate is precision-first.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from goldenmatch.config.schemas import GoldenMatchConfig, MatchkeyConfig
    from goldenmatch.core.probabilistic import EMResult

# A verdict from the adjudicator: (id_a, id_b, is_match, confidence).
Verdict = tuple[int, int, bool, float]


class RefitNotApplicableError(Exception):
    """Raised when a refit cannot be performed.

    Two causes: the config carries no ``probabilistic`` matchkey to refine, or
    no verdict cleared the confidence gate (nothing to learn from).
    """


@dataclass
class RefitResult:
    """The outcome of a closed-loop refit (a suggestion until applied).

    Attributes:
        em_result: the refined Fellegi-Sunter model (supervised m; ``iterations=0``).
        link_threshold: recalibrated match cutoff (:func:`compute_thresholds`).
        review_threshold: recalibrated review-band lower bound.
        n_labels: number of confident-match labels the estimate used.
        matchkey_name: the probabilistic matchkey that was refined.
    """

    em_result: EMResult
    link_threshold: float
    review_threshold: float
    n_labels: int
    matchkey_name: str

    def persist(self, config: GoldenMatchConfig, path: str) -> GoldenMatchConfig:
        """Apply the refit: save the model to ``path`` and return a config copy
        whose refined matchkey reuses it via ``model_path`` (skips EM next run).

        The original ``config`` is left untouched — this returns a deep copy, so
        applying a refit is an explicit, inspectable step, never a hidden
        mutation of the caller's config.
        """
        self.em_result.save_json(path)
        suggested = config.model_copy(deep=True)
        for mk in suggested.get_matchkeys():
            if mk.name == self.matchkey_name:
                mk.model_path = path
                break
        return suggested


def labels_from_verdicts(
    verdicts: Iterable[Verdict],
    confidence_threshold: float = 0.95,
) -> list[tuple[int, int]]:
    """Extract confident-match labels from adjudicator verdicts.

    A verdict becomes a label only when it is a match AND its confidence is at or
    above ``confidence_threshold`` (mirroring the LLM-scorer tier's
    ``auto_threshold`` semantics). Pairs are canonicalized ``(min, max)`` and
    deduplicated, matching the project-wide pair invariant.
    """
    seen: set[tuple[int, int]] = set()
    labels: list[tuple[int, int]] = []
    for id_a, id_b, is_match, confidence in verdicts:
        if not is_match or confidence < confidence_threshold or id_a == id_b:
            continue
        pair = (min(id_a, id_b), max(id_a, id_b))
        if pair not in seen:
            seen.add(pair)
            labels.append(pair)
    return labels


def _find_probabilistic_matchkey(config: GoldenMatchConfig) -> MatchkeyConfig:
    for mk in config.get_matchkeys():
        if getattr(mk, "type", None) == "probabilistic":
            return mk
    raise RefitNotApplicableError(
        "closed-loop refit needs a probabilistic (Fellegi-Sunter) matchkey to "
        "refine; this config has none. The refit recalibrates FS m-probabilities "
        "from confident labels — a weighted/exact config has no EM model to refine."
    )


def refit_from_labels(
    df,
    config: GoldenMatchConfig,
    labels: list[tuple[int, int]],
    *,
    matchkey: MatchkeyConfig | None = None,
) -> RefitResult:
    """Re-estimate the FS model from labeled match pairs (suggest mode).

    Reuses :func:`estimate_m_from_labels` verbatim. Does NOT mutate ``config`` or
    write to disk — returns a :class:`RefitResult`. Use :meth:`RefitResult.persist`
    to apply it.

    Args:
        df: the prepared frame; MUST carry a ``__row_id__`` column (the pipeline's
            internal id that ``labels`` reference). Arrow or polars.
        config: the run config; its ``probabilistic`` matchkey is refined.
        labels: confident ``(id_a, id_b)`` true-match pairs.
        matchkey: refine this matchkey instead of auto-selecting the first
            probabilistic one (must be ``type="probabilistic"``).
    """
    from goldenmatch.core.blocker import collect_blocking_fields
    from goldenmatch.core.probabilistic import compute_thresholds, estimate_m_from_labels

    if not labels:
        raise RefitNotApplicableError(
            "no confident-match labels to refit from (raise the adjudicator "
            "confidence or gather more verdicts)."
        )

    mk = matchkey if matchkey is not None else _find_probabilistic_matchkey(config)
    if getattr(mk, "type", None) != "probabilistic":
        raise RefitNotApplicableError(
            f"matchkey {mk.name!r} is {mk.type!r}, not 'probabilistic'; only an FS "
            "matchkey can be refit from labels."
        )

    blocking_fields: list[str] = []
    if getattr(config, "blocking", None) is not None:
        try:
            blocking_fields = collect_blocking_fields(config.blocking)
        except Exception:  # pragma: no cover - defensive; refit stays best-effort
            blocking_fields = []

    em = estimate_m_from_labels(df, mk, labels, blocking_fields=blocking_fields)
    link, review = compute_thresholds(em)
    return RefitResult(
        em_result=em,
        link_threshold=link,
        review_threshold=review,
        n_labels=len(labels),
        matchkey_name=mk.name,
    )


def refit_from_verdicts(
    df,
    config: GoldenMatchConfig,
    verdicts: Iterable[Verdict],
    *,
    confidence_threshold: float = 0.95,
    matchkey: MatchkeyConfig | None = None,
) -> RefitResult:
    """Convenience: extract confident-match labels from ``verdicts`` then refit.

    Equivalent to :func:`labels_from_verdicts` followed by :func:`refit_from_labels`.
    """
    labels = labels_from_verdicts(verdicts, confidence_threshold=confidence_threshold)
    return refit_from_labels(df, config, labels, matchkey=matchkey)
