"""The ``fan_out`` upgrade lever: negative-evidence suggestion + cluster-guard
tuning for imported Splink models.

Spec: docs/superpowers/specs/2026-07-14-fanout-ne-upgrade-lever-design.md

Runs between ``distance_thresholds`` and ``calibration`` in the upgrade pass
(see ``goldenmatch/config/splink_upgrade.py``'s lever registry, which lazily
imports this module). Task F1 landed the scaffold (bare-settings skip, no-op
stub bodies); Task F2 the NE candidate eligibility (``_ne_candidates``);
Task F3 the risk gate + posterior-weighted NE estimation
(``_suggest_negative_evidence``); Task F4 the reference-driven cluster-guard
tuning (``_tune_cluster_guard``).
"""
from __future__ import annotations

import math
import random
from collections import Counter
from dataclasses import dataclass

from goldenmatch._polars_lazy import pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    GoldenRulesConfig,
    MatchkeyConfig,
    NegativeEvidenceField,
)
from goldenmatch.config.splink_upgrade import (
    _CALIBRATION_MAX_PAIRS,
    _CALIBRATION_MIN_PAIRS,
    SplinkUpgradeError,
    _estimate_within_block_prior,
    _LeverContext,
)
from goldenmatch.core.autoconfig_negative_evidence import (
    _DEFAULT_NE_THRESHOLD,
    _pick_scorer_for_column,
)
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
        # col_type "" on purpose -- see NOTE in autoconfig_negative_evidence.py
        # (the dtype-vocabulary mismatch).
        transforms, scorer = _pick_scorer_for_column(col, "")
        candidates.append(_NECandidate(column=col, transforms=transforms, scorer=scorer))
    return candidates


_NE_MAPPED_TO = "matchkeys[0].negative_evidence"


def _suggest_negative_evidence(ctx: _LeverContext) -> None:
    """Risk-gated, posterior-weighted NE suggestion (spec: "The shared risk
    diagnosis" + "NE weight estimation (posterior-weighted)").

    Mirrors ``_lever_calibration``'s pair machinery (blocked-pair sampling,
    row lookup, regular-field FS weight sums), then per candidate measures
    the NE firing rate among CONFIDENT-merge pairs (posterior >=
    ``_FANOUT_POSTERIOR_CONFIDENT`` under the shared within-block prior
    re-estimate -- NOT the equal-odds posterior the re-estimation uses
    internally). Gated candidates get real ``__ne__<field>`` entries written
    into the upgraded model copy (FS-NE storage schema) and an EM-learned
    shape ``NegativeEvidenceField`` on the matchkey. Every skip is a finding;
    the lever never fails the pass. Guard tuning (Task F4) runs after us
    regardless of any skip here.
    """
    # Heavy module: import function-locally, matching _lever_calibration.
    from goldenmatch.core.probabilistic import (
        _ne_fired,
        _sample_blocked_pairs,
        comparison_vector,
        posterior_from_weight,
        prior_weight,
    )

    mk = ctx.upgraded_config.get_matchkeys()[0]
    em = ctx.em_model
    assert em is not None  # run_fan_out_lever gates on conversion.em_model

    blocking = ctx.upgraded_config.blocking
    if blocking is None:
        ctx.report.warn(
            "upgrade:fan_out",
            "skipped: config has no blocking configuration, cannot enumerate "
            "blocked candidate pairs -- no negative evidence suggested",
            mapped_to=_NE_MAPPED_TO,
        )
        return

    # Partial imported model (mixed bare/trained input): posteriors need
    # every comparison field covered by imported m/u -- mirrors
    # _lever_calibration's uncovered-fields guard. Guard tuning is
    # unaffected and still runs after us.
    uncovered = [
        f.field
        for f in mk.fields
        if f.field and f.field != "__record__" and f.field not in em.match_weights
    ]
    if uncovered:
        ctx.report.warn(
            "upgrade:fan_out",
            "skipped: the imported Splink model is partial (mixed bare/"
            "trained input) -- matchkey field(s) "
            f"{', '.join(uncovered)} carry no imported m/u, so blocked "
            "candidate pairs cannot be scored -- no negative evidence "
            "suggested",
            mapped_to=_NE_MAPPED_TO,
        )
        return

    candidates = _ne_candidates(ctx.df, mk, blocking)
    if not candidates:
        ctx.report.info(
            "upgrade:fan_out",
            "no eligible NE candidate columns on the sample (identity-grade "
            "name, non-null-rate and cardinality gates); no negative "
            "evidence suggested",
            mapped_to=_NE_MAPPED_TO,
        )
        return

    # Candidate pairs: the exact route _lever_calibration uses (build_blocks
    # on a __row_id__ LazyFrame + _sample_blocked_pairs), same cap and seed.
    from goldenmatch.core.blocker import build_blocks

    lf = ctx.df.lazy()
    if "__row_id__" not in ctx.df.columns:
        lf = lf.with_row_index("__row_id__")
    lf = lf.with_columns(pl.col("__row_id__").cast(pl.Int64))
    blocks = build_blocks(lf, blocking)

    # Block-size distribution is FINDINGS ONLY -- blocking.max_block_size
    # stays untouched in v1 (oversized blocks are processed, not dropped, so
    # tuning it has murky semantics; spec).
    sizes = sorted(b.materialize().height for b in blocks)
    if sizes:
        n_blocks = len(sizes)
        b50 = sizes[n_blocks // 2]
        b95 = sizes[min(n_blocks - 1, int(round(0.95 * (n_blocks - 1))))]
        ctx.report.info(
            "upgrade:fan_out",
            f"block size distribution on the sample: p50={b50}, p95={b95}, "
            f"max={sizes[-1]} over {n_blocks} block(s) "
            "(blocking.max_block_size untouched)",
            mapped_to=None,
        )

    pairs = _sample_blocked_pairs(blocks, n_pairs=_CALIBRATION_MAX_PAIRS, seed=ctx.seed)
    if len(pairs) <= _CALIBRATION_MIN_PAIRS:
        ctx.report.warn(
            "upgrade:fan_out",
            f"skipped: only {len(pairs)} blocked candidate pair(s) on the "
            f"sample; the fan-out risk gate needs more than "
            f"{_CALIBRATION_MIN_PAIRS} scored pairs -- no negative evidence "
            "suggested",
            mapped_to=_NE_MAPPED_TO,
        )
        return

    # Row lookup over matchkey fields + candidate columns (mirrors
    # _lever_calibration / train_em).
    from goldenmatch.core.frame import to_frame

    cols = [f.field for f in mk.fields if f.field is not None and f.field != "__record__"]
    lookup_cols = ["__row_id__"] + cols + [
        c.column for c in candidates if c.column not in cols
    ]
    row_lookup: dict[int, dict] = {}
    for row in to_frame(lf.collect()).select_dicts(lookup_cols):
        row_lookup[row["__row_id__"]] = row

    indexed_fields = [
        (k, f.field)
        for k, f in enumerate(mk.fields)
        if f.field is not None and f.field != "__record__"
    ]

    # Regular-field FS weight sums -- NE candidates contribute nothing here
    # (they are not comparison fields by construction).
    total_weights: list[float] = []
    for a, b in pairs:
        vec = comparison_vector(row_lookup.get(a, {}), row_lookup.get(b, {}), mk)
        total_weights.append(
            sum(em.match_weights[name][vec[k]] for k, name in indexed_fields)
        )

    # Per-pair posterior under the shared within-block prior re-estimate.
    prior = _estimate_within_block_prior(total_weights)
    prior_w = prior_weight(prior)
    posteriors = [posterior_from_weight(w, prior_w) for w in total_weights]
    confident = [p >= _FANOUT_POSTERIOR_CONFIDENT for p in posteriors]
    n_conf = sum(confident)
    posterior_sum = sum(posteriors)

    for cand in candidates:
        # The gate measures the EXACT predicate that will fire at runtime:
        # the NegativeEvidenceField carries the same threshold/scorer/
        # transforms tuple it is emitted with.
        ne = NegativeEvidenceField(
            field=cand.column,
            transforms=cand.transforms,
            scorer=cand.scorer,
            threshold=_DEFAULT_NE_THRESHOLD,
        )
        fired = [
            _ne_fired(row_lookup.get(a, {}), row_lookup.get(b, {}), ne)
            for a, b in pairs
        ]
        n_fired_conf = sum(1 for f, c in zip(fired, confident) if f and c)
        rate = n_fired_conf / n_conf if n_conf else 0.0
        # The measured contradiction rate is ALWAYS reported, gated or not
        # -- it is the fan-out diagnosis evidence.
        ctx.report.info(
            "upgrade:fan_out",
            f"candidate '{cand.column}': contradiction rate {rate:.4f} "
            f"among {n_conf} confident-merge pair(s) (posterior >= "
            f"{_FANOUT_POSTERIOR_CONFIDENT}); {n_fired_conf} firing (gate: "
            f"rate >= {_FANOUT_MIN_FIRE_RATE} and >= "
            f"{_FANOUT_MIN_FIRING_PAIRS} firing pairs)",
            mapped_to=_NE_MAPPED_TO,
        )
        if (
            n_conf == 0
            or rate < _FANOUT_MIN_FIRE_RATE
            or n_fired_conf < _FANOUT_MIN_FIRING_PAIRS
        ):
            continue  # the info finding above already reports why

        # Posterior-weighted m estimate + random-pair u estimate (train_em's
        # u route), both epsilon-clamped away from 0/1 before log2.
        m_fire = sum(p for p, f in zip(posteriors, fired) if f) / posterior_sum
        m_fire = min(max(m_fire, _PROB_CLAMP), 1.0 - _PROB_CLAMP)
        u_fire = _random_pair_firing_rate(row_lookup, ne, ctx.seed)
        u_fire = min(max(u_fire, _PROB_CLAMP), 1.0 - _PROB_CLAMP)
        w_fired = math.log2(m_fire / u_fire)
        if w_fired >= 0:
            ctx.report.warn(
                "upgrade:fan_out",
                f"candidate '{cand.column}': column does not discriminate "
                f"on this data (w_fired={w_fired:.4f} >= 0: firing is not "
                "rarer among likely matches than among random pairs) -- "
                "dropped",
                mapped_to=_NE_MAPPED_TO,
            )
            continue

        mk.negative_evidence = (mk.negative_evidence or []) + [ne]
        key = f"__ne__{cand.column}"
        em.m_probs[key] = [m_fire, 1.0 - m_fire]
        em.u_probs[key] = [u_fire, 1.0 - u_fire]
        em.match_weights[key] = [w_fired, 0.0]
        ctx.report.info(
            "upgrade:fan_out",
            f"negative-evidence field '{cand.column}' added "
            f"(scorer={cand.scorer}, threshold={_DEFAULT_NE_THRESHOLD}): "
            f"m_fire={m_fire:.4f}, u_fire={u_fire:.4f}, "
            f"w_fired={w_fired:.4f} bits (contradiction rate {rate:.4f})",
            mapped_to=_NE_MAPPED_TO,
        )


def _random_pair_firing_rate(
    row_lookup: dict[int, dict], ne: NegativeEvidenceField, seed: int
) -> float:
    """NE firing rate over random pairs of DISTINCT rows -- the u estimate
    (the same random-pair route ``train_em`` uses for u, scaled to
    ``_FANOUT_RANDOM_PAIRS`` draws; same-row draws are skipped, so "up to").

    Deterministic: the row ids are sorted before the seeded RNG samples
    (dict order follows insertion which follows a stable collect, but sort
    anyway for safety).
    """
    from goldenmatch.core.probabilistic import _ne_fired

    ids = sorted(row_lookup)
    if len(ids) < 2:
        return 0.0
    rng = random.Random(seed)
    drawn = 0
    n_fired = 0
    for _ in range(_FANOUT_RANDOM_PAIRS):
        a = rng.choice(ids)
        b = rng.choice(ids)
        if a == b:
            continue
        drawn += 1
        if _ne_fired(row_lookup[a], row_lookup[b], ne):
            n_fired += 1
    return n_fired / drawn if drawn else 0.0


# ── Cluster-guard tuning (Task F4) ───────────────────────────────────────────

_GUARD_MAPPED_TO = "golden_rules.max_cluster_size"


def _tune_cluster_guard(ctx: _LeverContext) -> None:
    """Reference-driven ``golden_rules.max_cluster_size`` tuning (spec:
    "Guard tuning").

    Reference priority: ``ctx.labels`` (true cluster sizes) ->
    ``ctx.splink_clusters`` (the user's old Splink output sizes) -> skip with
    an info finding (no invented reference). The reference joins the sampled
    rows through the same ``id_column`` mechanism measurement uses; when ids
    cannot be joined (positional fallback, zero overlap) the guard skips with
    an info finding naming ``id_column=``. With a joined reference the cap is
    SYMMETRIC -- ``max(_GUARD_MIN_CAP, 2 * reference_max_cluster_size)`` may
    tighten below or loosen above the default 100. Never fails the pass:
    ``_resolve_ids`` raises on a missing/duplicate explicit ``id_column`` and
    ``_load_reference`` on a bad path/shape; both become warn+skip here
    (measurement's later identical calls surface the same problems there).
    """
    # Lazy: the measure module pulls in metric helpers (mirrors how
    # splink_upgrade lazy-imports run_measurement; keeps fanout import-light).
    from goldenmatch.config.splink_upgrade_measure import (
        _POSITIONAL_ID_SOURCE,
        _load_reference,
        _resolve_ids,
    )

    if ctx.labels is not None:
        reference, ref_name = ctx.labels, "labels"
    elif ctx.splink_clusters is not None:
        reference, ref_name = ctx.splink_clusters, "splink_clusters"
    else:
        ctx.report.info(
            "upgrade:fan_out",
            "guard tuning skipped: no reference provided (pass labels= or "
            "splink_clusters=); golden_rules.max_cluster_size untouched",
            mapped_to=_GUARD_MAPPED_TO,
        )
        return

    try:
        ids, id_source = _resolve_ids(ctx.df, ctx.id_column)
    except SplinkUpgradeError as exc:
        ctx.report.warn(
            "upgrade:fan_out",
            f"guard tuning skipped: {exc} -- golden_rules.max_cluster_size "
            "untouched",
            mapped_to=_GUARD_MAPPED_TO,
        )
        return
    if id_source == _POSITIONAL_ID_SOURCE:
        ctx.report.info(
            "upgrade:fan_out",
            "guard tuning skipped: sample ids are positional row indices, "
            f"which cannot join the {ref_name} reference -- pass id_column= "
            "naming the data column that matches the reference ids",
            mapped_to=_GUARD_MAPPED_TO,
        )
        return

    try:
        mapping, n_ref_rows = _load_reference(reference, set(ids))
    except Exception as exc:  # bad path/shape: the lever never fails the pass
        ctx.report.warn(
            "upgrade:fan_out",
            f"guard tuning skipped: could not load the {ref_name} reference "
            f"({exc}) -- golden_rules.max_cluster_size untouched",
            mapped_to=_GUARD_MAPPED_TO,
        )
        return
    if not mapping:
        # Zero id overlap (or an empty reference) is an id-join failure, not
        # a clustering signal -- same posture as measurement's
        # _checked_reference. The info-vs-warn severity downgrade vs that
        # helper is INTENTIONAL: a skipped tune is lower-stakes than absent
        # metrics, and measurement's own later call still warns.
        ctx.report.info(
            "upgrade:fan_out",
            f"guard tuning skipped: the {ref_name} reference "
            f"({n_ref_rows} row(s)) shares no ids with the sample (ids came "
            f"from column '{id_source}') -- pass id_column= naming the data "
            "column that matches the reference ids",
            mapped_to=_GUARD_MAPPED_TO,
        )
        return

    # Cluster sizes restricted to the sampled ids.
    sizes = sorted(Counter(mapping.values()).values())
    ref_max = sizes[-1]
    ref_p99 = sizes[min(len(sizes) - 1, round(0.99 * (len(sizes) - 1)))]
    new_cap = max(_GUARD_MIN_CAP, 2 * ref_max)

    if ctx.upgraded_config.golden_rules is None:
        # "most_complete" is what the pipeline substitutes when golden_rules
        # is absent (core/pipeline.py), so creating the config with it
        # changes nothing but the cap we are about to set.
        ctx.upgraded_config.golden_rules = GoldenRulesConfig(
            default_strategy="most_complete"
        )
    rules = ctx.upgraded_config.golden_rules
    old_cap = rules.max_cluster_size
    rules.max_cluster_size = new_cap
    caveat = (
        " (data was subsampled; reference max may be understated)"
        if ctx.sampled
        else ""
    )
    ctx.report.info(
        "upgrade:fan_out",
        f"golden_rules.max_cluster_size tuned {old_cap} -> {new_cap} = "
        f"max({_GUARD_MIN_CAP}, 2 * {ref_max}) from the {ref_name} "
        f"reference (max cluster size {ref_max}, p99 {ref_p99}, over "
        f"{len(mapping)} joined sample id(s))" + caveat,
        mapped_to=_GUARD_MAPPED_TO,
    )
