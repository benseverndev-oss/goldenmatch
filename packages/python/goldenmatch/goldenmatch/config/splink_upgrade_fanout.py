"""The ``fan_out`` upgrade lever: negative-evidence suggestion + cluster-guard
tuning for imported Splink models.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md

Runs between ``distance_thresholds`` and ``calibration`` in the upgrade pass
(see ``goldenmatch/config/splink_upgrade.py``'s lever registry, which lazily
imports this module). Task F1 landed the scaffold (bare-settings skip, no-op
stub bodies); Task F2 the NE candidate eligibility (``_ne_candidates``). The
NE-suggestion body arrives in Task F3 and the cluster-guard tuning body in
Task F4.
"""
from __future__ import annotations

from dataclasses import dataclass

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig
from goldenmatch.config.splink_upgrade import _LeverContext
from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
from goldenmatch.core.blocker import collect_blocking_fields

# ── Tuning constants ─────────────────────────────────────────────────────────

_FANOUT_POSTERIOR_CONFIDENT = 0.9   # "confident merge" posterior floor for the risk gate
_FANOUT_MIN_FIRE_RATE = 0.02        # min NE firing rate among confident-merge pairs
_FANOUT_MIN_FIRING_PAIRS = 10       # min absolute firing confident pairs (estimation support)
_FANOUT_MIN_NONNULL = 0.5           # candidate columns sparser than this are not suggested
_FANOUT_MIN_CARDINALITY = 0.5       # mirrors autoconfig NE's _CARDINALITY_THRESHOLD
# A perfect surrogate key has zero shared-identity signal (mirrors #721's
# uniform exact-scorer gate); it would also fire on true dups and be dropped by
# the w>=0 check, but excluding it up front keeps findings clean.
_FANOUT_MAX_CARDINALITY = 0.999
_FANOUT_RANDOM_PAIRS = 10_000       # u_fire sample size (train_em's random-pair u route scale)
_PROB_CLAMP = 1e-4                  # m/u clamp away from 0/1 before log2
_GUARD_MIN_CAP = 10                 # floor of max(10, 2 * reference_max)
_NE_NAME_PATTERNS = (
    "phone", "mobile", "email", "e-mail", "e_mail", "address", "addr",
    "ssn", "npi", "license", "licence", "passport",
)


# ── Lever entrypoint ─────────────────────────────────────────────────────────

_BARE_SETTINGS_SKIP_MSG_FANOUT = (
    "skipped: no imported model; run-time EM training and auto-config own "
    "negative-evidence and guard decisions natively"
)


def run_fan_out_lever(ctx: _LeverContext) -> None:
    if ctx.conversion.em_model is None:
        ctx.report.info("upgrade:fan_out", _BARE_SETTINGS_SKIP_MSG_FANOUT, mapped_to=None)
        return
    _suggest_negative_evidence(ctx)   # Task F3
    _tune_cluster_guard(ctx)          # Task F4


# ── NE candidate eligibility ─────────────────────────────────────────────────


@dataclass
class _NECandidate:
    """A column eligible for a negative-evidence suggestion, with the
    transforms + scorer ``_pick_scorer_for_column`` assigned it."""

    column: str
    transforms: list[str]
    scorer: str


def _ne_candidates(
    df: pl.DataFrame, mk: MatchkeyConfig, blocking: BlockingConfig
) -> list[_NECandidate]:
    """Select NE candidate columns from the (already sampled) frame.

    A column is a candidate when ALL of (spec: "NE candidate columns"):

    - it is not a comparison field of ``mk``, not ``__record__``, and not a
      blocking-key field (via ``collect_blocking_fields``);
    - its name is identity-grade (any ``_NE_NAME_PATTERNS`` substring,
      case-insensitive);
    - its non-null rate is >= ``_FANOUT_MIN_NONNULL`` (NE needs both sides
      present, so a sparse column would rarely fire);
    - its cardinality ratio (n_unique / n_rows) lies within
      [``_FANOUT_MIN_CARDINALITY``, ``_FANOUT_MAX_CARDINALITY``].

    Returns candidates in df column order (deterministic). The risk gate that
    consumes them is Task F3.
    """
    n_rows = max(1, len(df))
    excluded = {f.field for f in mk.fields}
    excluded.add("__record__")
    excluded.update(collect_blocking_fields(blocking))

    candidates: list[_NECandidate] = []
    for col in df.columns:
        if col in excluded:
            continue
        col_lower = col.lower()
        if not any(pattern in col_lower for pattern in _NE_NAME_PATTERNS):
            continue
        series = df[col]
        non_null_rate = 1 - series.null_count() / n_rows
        if non_null_rate < _FANOUT_MIN_NONNULL:
            continue
        cardinality = series.n_unique() / n_rows
        if not (_FANOUT_MIN_CARDINALITY <= cardinality <= _FANOUT_MAX_CARDINALITY):
            continue
        transforms, scorer = _pick_scorer_for_column(col, "")
        candidates.append(_NECandidate(column=col, transforms=transforms, scorer=scorer))
    return candidates


def _suggest_negative_evidence(ctx: _LeverContext) -> None:
    pass  # Task F3


def _tune_cluster_guard(ctx: _LeverContext) -> None:
    pass  # Task F4
