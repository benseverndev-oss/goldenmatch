"""The ``fan_out`` upgrade lever: negative-evidence suggestion + cluster-guard
tuning for imported Splink models.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md

Runs between ``distance_thresholds`` and ``calibration`` in the upgrade pass
(see ``goldenmatch/config/splink_upgrade.py``'s lever registry, which lazily
imports this module). Task F1 lands the scaffold: the bare-settings skip and
no-op stub bodies. The NE-suggestion body arrives in Task F3 and the
cluster-guard tuning body in Task F4.
"""
from __future__ import annotations

# ── Tuning constants ─────────────────────────────────────────────────────────

_FANOUT_POSTERIOR_CONFIDENT = 0.9   # "confident merge" posterior floor for the risk gate
_FANOUT_MIN_FIRE_RATE = 0.02        # min NE firing rate among confident-merge pairs
_FANOUT_MIN_FIRING_PAIRS = 10       # min absolute firing confident pairs (estimation support)
_FANOUT_MIN_NONNULL = 0.5           # candidate columns sparser than this are not suggested
_FANOUT_MIN_CARDINALITY = 0.5       # mirrors autoconfig NE's _CARDINALITY_THRESHOLD
_FANOUT_MAX_CARDINALITY = 0.999     # a perfect surrogate key has zero shared-identity signal
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


def run_fan_out_lever(ctx) -> None:
    if ctx.conversion.em_model is None:
        ctx.report.info("upgrade:fan_out", _BARE_SETTINGS_SKIP_MSG_FANOUT, mapped_to=None)
        return
    _suggest_negative_evidence(ctx)   # Task F3
    _tune_cluster_guard(ctx)          # Task F4


def _suggest_negative_evidence(ctx) -> None:
    pass  # Task F3


def _tune_cluster_guard(ctx) -> None:
    pass  # Task F4
