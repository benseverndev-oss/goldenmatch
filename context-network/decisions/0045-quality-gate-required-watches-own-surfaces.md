# 0045 — quality_gate must watch every surface that moves its numbers, and it must block merges

**Status:** Accepted. **Shipped:** goldenmatch 3.4.0 (PRs #1847 coverage self-check, #1877 required-gate wiring; root cause tracked as #1846, still open).

## Context

`historical_50k f1_probabilistic` collapsed **0.83 → 0.33 on the native path on
`main`** and nothing caught it. Two independent failures let it through:

1. **Coverage hole.** `quality_gate` *measures* `f1_probabilistic` but its path
   filter did not *watch* `probabilistic.py` (nor the bucket/scorer/routing
   surfaces). So it **skipped** on exactly the PRs that could break it (#1829 FS
   bucket lane, #1834 EM missing-value, #1836 blocking-field prior) and only
   surfaced later on unrelated PRs that happened to touch a listed path — reading
   as "those PRs broke it" and costing a day of attribution.
2. **Non-blocking.** `quality_gate` was not in `ci-required`'s `needs` (the only
   required status check), so a red gate showed as mergeable `UNSTABLE` and
   merged anyway — how #1834 landed the regression.

These are silent, recall-only regressions (see
[0041](0041-fs-missing-value-semantics.md)); "byte-identical clusters" parity is
true and useless when Python and Rust are identically wrong. The gate is the only
thing standing between such a change and `main`.

## Decision

**A metric gate must (a) watch every surface that can move its number, (b)
self-check that coverage, and (c) block the merge.**

1. **Watch the surfaces (#1847).** `.github/filters.yml`'s `quality_gate` filter
   now lists `core/probabilistic*.py`, `core/fused_match.py`, `core/scorer*.py`,
   `core/cluster.py`, `core/pipeline.py` (routing picks which scorer runs),
   `backends/**` (the bucket reference lane), `core/learned_blocking.py`,
   `packages/rust/extensions/native/**` (the baseline is blessed native-on), and
   `filters.yml` itself.
2. **Self-check coverage (#1847, the actual fix).** `scripts/check_filter_coverage.py`
   — wired into `workflow_lint` — asserts each gated filter still matches the
   surfaces it claims to gate, with a reason + remediation, and was verified to
   fail when the #1846 hole is reintroduced. Same class of hole #435 already
   fixed for `benchmark_runner`; the self-check generalizes it so the next hole
   fails CI instead of merging silently.
3. **Make it required (#1877, fixes #1855).** `quality_gate` joins
   `ci-required.needs`. Safe because a path-filter skip → `result: skipped`
   counts as pass (it only gates PRs touching its paths); a real failure (the
   step is not `continue-on-error`) → `result: failure` blocks the merge queue.
   No branch-protection ruleset edit needed; works identically in `merge_group`.

## Consequence

- The gate now runs native-on (native routes the planner to `bucket`, the
  reference path), watches the scoring/routing/blocking/native surfaces, and
  **blocks** a merge that reds it. The class of "silent recall regression merged
  red" that produced #1834 and #1835 is closed at the process level.
- This ADR does **not** fix the underlying #1846 regression (that is the
  missing-value work in [0041](0041-fs-missing-value-semantics.md)); it fixes the
  reason the regression went unnoticed. Recurring lesson worth keeping: when a
  gate measures a number, the set of files that move that number is part of the
  gate's contract — enforce it with a coverage self-test, not vigilance.
