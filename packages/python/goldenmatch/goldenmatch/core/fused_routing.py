"""Fused-routing helpers (controller auto-routing to the fused path).

Pure, self-contained routing logic with NO pipeline/controller imports, so it is
independently testable. Stage A ships only the est-peak-RSS model; later stages
add ``config_needs_artifacts`` and ``maybe_route_fused_match`` here.

The est-peak-RSS model estimates the *classic* (non-fused) match path's peak RSS
from signals the controller already holds, so the match-routing post-step can
decide whether the run is under enough memory pressure to route to the fused
kernel (which halves peak RSS but drops artifacts). See the design at
``docs/superpowers/specs/2026-07-09-controller-fused-auto-routing-design.md`` §4.1.

All four coefficients are ``GOLDENMATCH_FUSED_*`` env-overridable so the trigger
tunes the model without a code change; ``_RSS_SCALE`` is the calibration knob
(its default is pinned to the memcap bench's measured classic peak — see
``tests/test_fused_routing.py::test_est_rss_calibrated_to_bench``).
"""

from __future__ import annotations

import os

# Physical-size coefficients (bytes). A scored pair costs ~64 B (two int64 ids +
# an f64 score + list/store overhead); a materialized matchkey cell ~40 B; the
# per-block cdist matrices are float64 (8 B) and BLOCK_CONCURRENCY score in
# parallel at once.
_BYTES_PER_PAIR = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_PAIR", "64"))
_BYTES_PER_CELL = float(os.environ.get("GOLDENMATCH_FUSED_BYTES_PER_CELL", "40"))
_BLOCK_CONCURRENCY = float(os.environ.get("GOLDENMATCH_FUSED_BLOCK_CONCURRENCY", "4"))

# Calibration knob. Default 0.763 pins the model to the ONE committed measured
# classic peak (10M => 5.19 GB, from core/fused_match.py's module docstring /
# the bench-match-fused run). See the calibration test for the derivation of the
# CALIB inputs. Physical-size coefficients over-read the real peak (allocator
# slack, transient frees), so a single sub-1.0 scale absorbs the residual.
_RSS_SCALE = float(os.environ.get("GOLDENMATCH_FUSED_RSS_SCALE", "0.763"))


def estimate_classic_match_peak_rss_gb(
    n_rows: int,
    est_pairs: int,
    block_max: int,
    n_score_cols: int,
) -> float:
    """Estimate the classic match path's peak RSS in GB.

    Args:
        n_rows: full-data row count (n_rows_full).
        est_pairs: extrapolated full-data candidate-pair count
            (``BlockingProfile.estimated_pair_count``).
        block_max: full-data max block size (peak concurrent cdist matrix side).
        n_score_cols: number of matchkey comparison fields materialized.

    The three terms are the materialized matchkey columns, the scored-pairs
    store, and the peak concurrent float64 cdist matrices; a single ``_RSS_SCALE``
    coefficient (calibrated against the memcap bench) absorbs the residual.
    """
    frame_b = n_rows * max(1, n_score_cols) * _BYTES_PER_CELL
    pairs_b = est_pairs * _BYTES_PER_PAIR
    block_b = (block_max**2) * 8 * _BLOCK_CONCURRENCY
    return _RSS_SCALE * (frame_b + pairs_b + block_b) / 1e9


def _golden_uses_confidence_majority(golden_rules) -> bool:
    """True if the golden rules select ``confidence_majority`` anywhere.

    Scans all four strategy locations on ``GoldenRulesConfig``:
    ``default_strategy`` (which the validator also resolves from ``default``),
    ``field_rules`` (each value is a single ``GoldenFieldRule`` OR a list of
    when-guarded clauses), ``field_groups`` (``GoldenGroupRule.strategy`` --
    scanned defensively, though its validator currently forbids CM there), and
    ``cluster_overrides`` (``dict[int, dict[field, GoldenFieldRule]]``).

    ``confidence_majority`` survivorship needs per-pair scores, which bare
    connected-component match_fused can't supply -> divergence.
    """
    if golden_rules is None:
        return False
    if golden_rules.default_strategy == "confidence_majority":
        return True
    for rule in golden_rules.field_rules.values():
        clauses = rule if isinstance(rule, list) else [rule]
        if any(clause.strategy == "confidence_majority" for clause in clauses):
            return True
    if any(g.strategy == "confidence_majority" for g in golden_rules.field_groups):
        return True
    if golden_rules.cluster_overrides:
        for field_map in golden_rules.cluster_overrides.values():
            if any(r.strategy == "confidence_majority" for r in field_map.values()):
                return True
    return False


def config_needs_artifacts(config) -> bool:
    """Config-driven divergence gate for match routing (spec §4.3).

    Returns True when the CONFIG alone forces the classic (block->score->cluster)
    path -- i.e. bare-connected-component ``match_fused`` would either DIVERGE
    from classic output or drop artifacts a consumer needs. OR of:

    - ``golden_rules.auto_split`` (DEFAULT True) -- classic MST-splits oversized
      clusters + downgrades weak clusters; bare CCs don't.
    - ``config.identity.enabled`` -- builds evidence edges from pair scores
      match_fused can't produce.
    - golden uses ``confidence_majority`` anywhere -- needs pair scores.
    - ``config.output.lineage_provenance`` -- full ``__survivorship_prov__``
      the fused golden path can't reproduce.

    This is the CONFIG-only half of ``needs_artifacts`` (both the controller and
    the pipeline read it authoritatively -- single source of truth). Caller-intent
    flags (lineage/review/explain/anomaly requested) are threaded separately via
    the ``_api.py`` ``fused_match_allowed`` hint, NOT here.

    NONE-guards: ``golden_rules`` None resolves to the pipeline default where
    ``auto_split=True`` (``pipeline.py`` ~2126), so a None golden_rules is
    default-DENY (returns True). ``config.identity`` / ``config.output`` None are
    treated as the feature being off.

    NARROWNESS (spec §4.3, important for a future reader): because
    ``auto_split`` DEFAULTS True, this returns True for almost every default
    config -- so match routing rarely fires. It fires ONLY when the user
    explicitly set ``golden_rules.auto_split=False`` (and the other three
    conditions are clear). This narrowness is intended: match routing is a
    capacity-survival escape hatch under memory pressure, not a broad default.
    """
    golden_rules = config.golden_rules
    # None golden_rules -> pipeline default auto_split=True -> default-DENY.
    auto_split = True if golden_rules is None else golden_rules.auto_split
    if auto_split:
        return True
    identity = config.identity
    if identity is not None and identity.enabled:
        return True
    if _golden_uses_confidence_majority(golden_rules):
        return True
    output = config.output
    if output is not None and output.lineage_provenance:
        return True
    return False


# --- Stage D: the match-routing post-step -------------------------------------

# Fraction of available RAM the estimated classic peak must exceed to route match
# to the fused kernel. 0.65 leaves headroom for the OS + allocator slack; env
# ``GOLDENMATCH_FUSED_PRESSURE_FRACTION`` tunes it without a code change.
_DEFAULT_PRESSURE_FRACTION = 0.65


def _count_score_cols(config) -> int:
    """Number of matchkey comparison fields in the covered weighted matchkey.

    Reads the SINGLE covered ``weighted`` matchkey via
    ``fused_match._covered_weighted_matchkey`` (the same gate the single-key and
    multi-pass fused entries use) and counts its comparison fields -- the columns
    the classic scorer materializes. Floors to 1 (a matchkey always materializes
    >=1 score column); callers only reach this after confirming coverage, so the
    None branch is defensive.
    """
    from goldenmatch.core.fused_match import _covered_weighted_matchkey

    mk = _covered_weighted_matchkey(config)
    fields = getattr(mk, "fields", None) if mk is not None else None
    return len(fields) if fields else 1


def maybe_route_fused_match(
    *,
    config,
    profile,
    runtime,
    n_rows: int,
    needs_artifacts: bool,
) -> bool:
    """Decide whether to route the match stage to the fused kernel.

    Returns True only when ALL hold:

    - ``GOLDENMATCH_MATCH_FUSED`` is not the kill-switch (``0``/``false``/``off``);
    - ``needs_artifacts`` is False (no caller-intent / config-driven divergence --
      the controller folds the caller hint + ``config_needs_artifacts`` into it);
    - the config is COVERED by the fused single-key OR multi-pass entry
      (``match_fused_ready`` / ``match_fused_multipass_ready``). The
      Fellegi-Sunter branch is deliberately OUT of v1 -- no trained ``EMResult``
      exists at decision time -- so a probabilistic config is not covered here;
    - the estimated CLASSIC peak RSS exceeds ``available_ram_gb * frac`` (memory
      pressure). Match routing is a capacity-survival escape hatch, not a broad
      default; below the pressure line the classic path runs unchanged.

    ``n_rows`` is passed explicitly: the controller holds the authoritative
    full-data row count as its local ``n_rows`` at the insertion point.
    ``profile.blocking.estimated_pair_count`` (property) + ``block_sizes_max``
    (field) carry the full-scale blocking signals (extrapolated or measured).
    """
    import os

    if os.environ.get("GOLDENMATCH_MATCH_FUSED", "").lower() in {"0", "false", "off"}:
        return False
    if needs_artifacts:
        return False

    from goldenmatch.core.fused_match import match_fused_multipass_ready, match_fused_ready

    if not (match_fused_ready(config) or match_fused_multipass_ready(config)):
        return False  # FS branch out of v1 (no EMResult at decision time).

    frac = float(
        os.environ.get(
            "GOLDENMATCH_FUSED_PRESSURE_FRACTION", str(_DEFAULT_PRESSURE_FRACTION)
        )
    )
    est = estimate_classic_match_peak_rss_gb(
        n_rows=n_rows,
        est_pairs=profile.blocking.estimated_pair_count,
        block_max=profile.blocking.block_sizes_max,
        n_score_cols=_count_score_cols(config),
    )
    return est > runtime.available_ram_gb * frac
