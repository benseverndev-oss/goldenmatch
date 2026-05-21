# ADR-0003: Matchkey suitability vs blocking suitability as orthogonal axes

**Status:** Accepted
**Date:** 2026-05-21 (PRs #409, #411, #414)

## Context

Pre-#408, auto-config picked the highest-identity-score column for BOTH the matchkey role AND the blocking-key role. On healthcare data with `dm_npi` (federal NPI — every record has a unique NPI), this produced:
- `matchkey: exact_dm_npi` — correct.
- `blocking: [dm_npi]` — catastrophic. 980,531 blocks for 1.13M rows; each block has 1-3 records compared only to themselves; sync ETA 22-60 hours producing zero useful clusters.

The user's diagnosis (#408): "autoconfig conflates 'good identity claim' with 'good blocking key.' They're orthogonal axes. A perfect identity claim like NPI is the worst-possible blocking key (1 row per block by definition)."

## Decision

Treat matchkey suitability and blocking suitability as independent column properties. A column may qualify for one, the other, both, or neither.

- **Matchkey candidate**: high cardinality + identity-shaped values. Existing rule chain (`compute_column_priors`, etc.) handles this unchanged.
- **Blocking candidate**: mid-cardinality (`0.001 ≤ ratio ≤ 0.5` by default, env-overridable). Below 0.001 → mega-block risk. Above 0.5 → singleton blocks.

When no single column qualifies for blocking, search 2-column composites via joint cardinality. When that also fails, raise `ControllerNotConfidentError(failing_sub_profile="blocking")`.

Cardinality is computed via **Chao1 sample-size correction** (ADR-0004) because auto-config profiles a sample, not the full frame.

Rejected alternatives:
- **Pick best for matchkey, force fall-through for blocking.** Tried first; the fallthrough silently committed `first_string` with `substring:0:5` → mega-block at full scale. Composite search and the fail-loud guard came in via #411.
- **LLM-suggest blocking when single-column fails.** Heavier; cardinality-bounded heuristic works without an LLM call.
- **Postgres-only `pg_stats.n_distinct` shortcut.** Tempting (exact, free) but doesn't generalize across connectors. Chao1 works universally.

## Consequences

Positive:
- NPI keeps being picked as the matchkey it should be; the blocking role is denied without affecting matching.
- Composite blocking auto-fallback on `zip + last_name` (or similar) lands without YAML config.
- `BLOCKING_DEGENERATE` failing sub-profile in `ControllerNotConfidentError` is now part of the user-facing error vocabulary, distinct from data / scoring / cluster failures.

Negative:
- The cardinality bounds (`0.001 ≤ ratio ≤ 0.5`) are tunable defaults. Users with edge cases (e.g. legitimate per-record blocking) need the env var (`GOLDENMATCH_BLOCKING_MAX_RATIO`) or the `confidence_required=False` opt-out.
- Sample-vs-full bias was a real shipping bug in #411 (caught by user as #410 v2). Tests passing the full df directly to `auto_configure_df` masked the gap. ADR-0004 fixed the underlying issue; this ADR notes the lesson: **test fixtures should sometimes be sub-sampled to surface controller-path behavior**.

Cross-references:
- Specs: `docs/superpowers/specs/2026-05-21-blocking-key-candidate-pool-design.md` + `2026-05-21-blocking-pool-followup-design.md`
- PRs: #408 (issue), #409 (gate), #410 (follow-up issue), #411 (Chao1 + composite), #414 (true n_rows threading)
- ADR-0004: Chao1 correction
- ADR-0001: `confidence_required` gate (composes with `BLOCKING_DEGENERATE`)
