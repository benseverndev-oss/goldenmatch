# Wave D investigation: Negative Evidence on Fellegi-Sunter matchkeys

**Status:** investigation outcome (Wave D)
**Issue:** https://github.com/benseverndev-oss/goldenmatch/issues/126
**Date:** 2026-05-21
**Spec:** `docs/superpowers/specs/2026-05-21-ne-fellegi-sunter-design.md`

## Summary

**Conclusion: document-and-close.**

After evaluating the three candidate formulations against Fellegi-Sunter's calibration properties, none cleanly fits without either (a) breaking the log-likelihood-ratio (LLR) additivity that makes FS Fellegi-Sunter, or (b) requiring labeled data that goldenmatch doesn't reliably have at auto-config time. The recommendation is to keep NE on weighted + exact matchkey types only (the current v1.12 surface) and surface this decision as an explicit non-goal.

Wave D closes #126 with this doc as the rationale.

## Background

Fellegi-Sunter (1969) scores each comparison-vector dimension as a per-field LLR:

```
m_i = P(field_i agrees | match)        # learned via EM
u_i = P(field_i agrees | non-match)    # learned (or fixed) from random pairs
LLR_i = log(m_i / u_i)  if agree
      = log((1-m_i) / (1-u_i))  if disagree
LLR_total = sum_i(LLR_i)
```

The crucial property: **LLR_total is the log-likelihood ratio of the pair being a match vs. non-match, given ALL evidence.** Sum-of-logs preserves the joint-probability semantics; multiply two independent evidence factors → add their LLR contributions.

Negative Evidence in goldenmatch v1.11/v1.12 is a flat penalty applied when an NE field disagrees:

```
final = max(0, score - penalty)
```

Applied to weighted matchkeys (sum of weighted similarities) and exact matchkeys (post-filter on the binary 1.0 result). Penalty operates in the [0, 1] match-score space.

The mismatch: FS scores live in LLR space (typically -20 to +20 range), not [0, 1]. A flat penalty doesn't have the same meaning at LLR=12 vs LLR=-3.

## Formulation A — Multiplicative on `m`

**Formula:**
```
m_eff_i = m_i * (1 - penalty_i)  for NE fields when they disagree
LLR_i_NE = log((1-m_eff_i)/(1-u_i))  # the disagree branch
```

### Math character

When NE field `i` disagrees:
- New LLR = `log((1 - m_i*(1-penalty)) / (1 - u_i))`
- Effect on totals depends on `m_i` and `penalty`. For `m_i=0.95, penalty=0.4` → `m_eff=0.57`, LLR shifts from `log(0.05/0.5) ≈ -2.3` to `log(0.43/0.5) ≈ -0.15`.
- This MAKES THE PAIR LOOK MORE LIKE A MATCH, not less. Multiplicative on `m` in the disagree branch actually softens the NE signal — the opposite of what we want.

### Fix attempt: multiply in opposite direction

```
m_eff_i = m_i * (1 - penalty_i)  for the AGREE branch
```

Now an NE disagreement isn't affected at all (we modified the agree branch); doesn't fire when we want it to.

### Verdict

**Multiplicative-on-m doesn't model "this disagreement is unusually strong evidence."** It models "this field's agreement is less informative" — different problem. Mathematically incoherent for NE.

## Formulation B — Bayesian posterior adjustment

**Formula:**

Treat NE disagreement as an additional likelihood factor:
```
P(disagree_NE | match) → small
P(disagree_NE | non-match) → large (or normal)
LLR_NE = log(P(disagree_NE | match) / P(disagree_NE | non-match))
       = a large negative number
LLR_total_NE = LLR_total + LLR_NE
```

### Math character

Mathematically clean. Preserves LLR additivity. Equivalent to adding NE as another comparison dimension with its own m/u probabilities.

### Implementation cost

Requires estimating `P(disagree_NE | match)` and `P(disagree_NE | non-match)`:
- For random pairs, `P(disagree_NE | non-match)` ≈ marginal disagreement rate, easy from a sample.
- For matches, `P(disagree_NE | match)` requires LABELED MATCH PAIRS. Goldenmatch's auto-config doesn't have these. Wave E (#129 adaptive tuning) gates on MemoryStore corrections ≥ 50, which is exactly the data needed.

### Verdict

**Mathematically right.** But strictly downstream of Wave E. The natural API: once #129 ships, NE-on-FS becomes a configured fact — `match_penalty` and `nonmatch_penalty` per field, stored alongside the EM-trained m/u. No special code path; it's just another comparison dimension.

Implementing it pre-Wave E would require synthetic/default `m_NE`/`u_NE` values, which defeats the calibration argument. Better to wait.

## Formulation C — Post-FS score floor

**Formula:**
```
fs_score = LLR_total
ne_disagree_count = count NE fields that disagreed
adjusted_score = fs_score - flat_penalty * ne_disagree_count
```

### Math character

Loses FS calibration. LLR scale is unbounded; `flat_penalty` becomes either trivially small (no effect on borderline pairs) or breaks unit-comparison semantics (penalty=10 LLR units swamps half the comparison dimensions). The "right" penalty changes per-dataset because LLR distributions vary.

### Verdict

**Implementable but semantically wrong.** Available via env var as an escape hatch for users who want NE-on-FS RIGHT NOW and accept the calibration loss, but not the default.

## Decision matrix

| Formulation | Math correctness | Implementation cost | Verdict |
|---|---|---|---|
| A: Multiplicative on m | Incoherent | Cheap | Reject |
| B: Bayesian factor | Correct | Needs labeled data (Wave E gates this) | **Defer to post-Wave E** |
| C: Score floor | Loses calibration | Cheap | Optional env escape hatch only |

## Decision

**Close #126 with this doc as the rationale.**

Concrete next steps:
1. Document `match_settings.negative_evidence` as "weighted + exact matchkeys only" in `config/schemas.py` field docstring.
2. Add `GOLDENMATCH_NE_FS_ESCAPE_MODE=floor` env var (Formulation C) for users who explicitly accept the calibration loss. Default off; not documented in the public surface. (Bundled here OR deferred — both fine. Leaning bundle.)
3. Re-evaluate when Wave E #129 ships: at that point, the `m_NE`/`u_NE` parameters can be learned from MemoryStore corrections and Formulation B becomes the right answer. The post-#129 follow-up issue should reference this doc.

## Cross-references

- Spec: `docs/superpowers/specs/2026-05-21-ne-fellegi-sunter-design.md`
- Issue: #126
- Roadmap: `docs/superpowers/specs/2026-05-21-v1-13-autoconfig-roadmap.md` Wave D
- Related: #129 adaptive NE tuning (Wave E) — provides the labeled-data substrate that makes Formulation B viable.
- ADR-0001 (`confidence_required` gate): the FS-NE failure case (no labeled data → can't compute m_NE) would surface as a `ControllerNotConfidentError` on `scoring` sub-profile if we shipped Formulation B without enough corrections. The pre-condition gate is correct.
