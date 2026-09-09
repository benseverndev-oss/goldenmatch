# 0065 — The FS field-dependence correction cannot reach the effect it was built for

**Status:** accepted (2026-09-09, Ben) • **Measured:** [run 34358803727](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34358803727) (diagnostic), [34372003892](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34372003892) and [34381649670](https://github.com/benseverndev-oss/goldenmatch/actions/runs/34381649670) (A/B) • **Code:** #2914, shipped default-OFF • **Frame:** decision [0064](0064-identity-control-plane-stays-on-postgres-sqlite.md)

## Context
Fellegi-Sunter sums per-field weights `log2(m_i/u_i)` assuming the comparison
components are independent *given match status*. Where two fields co-agree more
often than chance among non-matches, the true joint `u` exceeds `prod(u_i)`, FS
under-counts the denominator, and every pair agreeing on both is over-weighted
— the namesake double-count.

The effect is real and reproduces. On `historical_50k`, measured over random
non-match pairs:

| pair | lift | bits over-counted | % of false merges |
|---|---|---|---|
| **first_name × surname** | **5.57×** | **+2.48** | **97.0%** |
| surname × occupation | 1.42× | +0.51 | 94.4% |

~2.89 bits on the average false merge, across 23,726 false merges of 199,778
emitted pairs. That is unchanged from a July 2026 measurement, despite
`historical_50k` precision moving 0.75 → 0.939 in between. **The other levers
raised precision by different means and never touched the independence
assumption** — a prediction that it had been absorbed was wrong.

## Decision
**Keep the correction default-OFF and do not tune it. As designed it cannot
reach the measured effect on person-shaped data.**

`_compute_joint_corrections` evaluates only fields *outside* the blocking
conditioning (`always_conditioned`). On the real panel:

```
FS field-dependence: enabled, but only 1 comparison field(s) are outside the
blocking conditioning (street), so no field PAIR can be evaluated.
Excluded as blocking-conditioned: city, dob, first_name, surname.
```

`first_name` and `surname` — the pair carrying 97% of the effect — are both
excluded, leaving one eligible field. `combinations(1, 2)` is empty, so **zero
pairs are ever evaluated**. Three A/B runs returned `+0.0000` on all five
datasets; none was a verdict on the correction, because it never executed.

The exclusion is not a bug. A field the blocking key forces to agree carries no
discriminating information *within* the block, so FS conditions it out. But on
person data the blocking key **is** the correlated pair, so the correction's
population and the diagnostic's population do not overlap on the effect in
question. The diagnostic measures lift over RANDOM pairs across all fields; the
correction measures it over BLOCKED pairs across non-blocking fields.

## Consequence
- The correction ships (#2914) default-OFF, with the machinery intact. It is
  not deleted: on a shape where blocking does **not** cover the correlated
  pair it is still the right tool, and it now says so out loud either way.
- **Two warnings were added because silence was indistinguishable from
  success.** The lever logged only on a hit, so "enabled and found nothing"
  and "never enabled" looked identical — the same failure that hid the TF
  adjustment doing nothing for months. Both inert paths now warn, at
  `logger.warning`, because a flag the caller set that then does nothing is a
  warning condition. An earlier attempt at this used `logger.info`, which these
  runs filter, so the fix for silence was itself silent.
- **The likely right lever for this effect is marginal, not joint.** If a
  blocking-conditioned field still carries weight from a `u` estimated on
  RANDOM pairs, FS over-weights an agreement that blocking guaranteed. That is
  what `GOLDENMATCH_FS_POST_BLOCKING_U` exists for (deconvolving `u` from the
  blocked population), and it is the next thing to measure rather than
  redesigning the joint correction.
- The July spike's own open question — whether the correction survives
  posterior calibration rather than min-max — is now moot, and separately,
  **`GOLDENMATCH_FS_CALIBRATED=posterior` collapses `historical_50k` precision
  0.939 → 0.361** (F1 0.832 → 0.512). Found by accident while holding it
  constant across both arms. Unexplained, and its own investigation.

## Method note
The diagnostic that establishes the premise lived only on a spike branch and was
never merged, so its number was carried in project notes for seven weeks as
settled fact while being unreproducible. It is now committed
(`scripts/bench_er_headtohead/diagnose_field_dependence.py`) with its own lane.
A measurement you cannot re-run is a claim, not a result — and re-running this
one is what turned "the correction is neutral" into "the correction never ran".
