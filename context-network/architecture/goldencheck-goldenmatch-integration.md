# GoldenCheck → GoldenMatch integration (data quality feeds entity resolution)

With GoldenCheck Arrow-native and expanded, its per-cell / per-column quality
signal now flows into GoldenMatch to improve entity-resolution **results,
recall, precision, and trust**. The channel is a set of **fail-open optional-dep
bridges** in `goldenmatch/core/quality.py` that call GoldenCheck's public APIs;
each is **default-OFF and benchmark-gated** until proven on the wired ER datasets.

**Status:** survivorship + blocking SHIPPED (merged); FD-NE + review OPEN.
**Decision:** [../decisions/0007-goldencheck-goldenmatch-integration.md](../decisions/0007-goldencheck-goldenmatch-integration.md).
**Code-level notes:** `packages/python/goldenmatch/CLAUDE.md`. **Docs-site:** `goldenmatch/data-quality.mdx`.
**Depends on:** [goldencheck-native-kernel.md](goldencheck-native-kernel.md) (the `cell_quality` / `functional_dependencies` APIs).

## The pattern
Every door is the same shape, so the blast radius stays small and honest:
- a `goldenmatch/core/quality.py` bridge (`_goldencheck_available()` guarded,
  reuses a GoldenCheck public API, returns `None` when goldencheck is absent OR
  the data is clean);
- additive at the consumer (never removes a match / a key / a decision);
- env-gated, **default OFF** (the #662 kill-switch precedent), so the flag-off
  path is byte-identical;
- the real go/no-go is a **measured** sweep on the wired ER benchmarks
  (DBLP-ACM / Febrl3 / NCVR, and DQbench for NE), not "more quality info must help".

## The four doors
| # | Lever | GoldenCheck signal → GoldenMatch hook | Flag | PR / status |
|---|---|---|---|---|
| — | **results** | `cell_quality` → golden-record survivorship (`quality_weighting`) — prefer the canonical spelling / real date when merging a cluster | `quality_weighting` (default on, no-op when clean) | #794 ✅ merged |
| 1 | **recall** | `cell_quality` → `blocking_risk` → `apply_quality_aware_blocking` adds a fuzzy-tolerant pass for edit-distance-fuzzy blocking keys (`Californa` co-blocks with `California`) | `GOLDENMATCH_QUALITY_AWARE_BLOCKING` | #795 ✅ merged |
| 3 | **precision** | `functional_dependencies` → `fd_identity_scores` → `promote_negative_evidence` admits data-driven identity anchors the name heuristic misses | `GOLDENMATCH_FD_NEGATIVE_EVIDENCE` | #797 🟡 open |
| 5/6 | **trust** | `cell_quality` → `row_quality_floor` → `gate_pairs` downgrades a confident auto-merge built on flagged cells to **review** (with the reason — door #6) | `GOLDENMATCH_QUALITY_GATED_REVIEW` | #798 🟡 open |

(Survivorship is the unnumbered "results" door — it wired the pre-existing
`GoldenRulesConfig.quality_weighting`, a documented no-op until #794.)

## The boundary (what each tool owns)
- **GoldenCheck** owns *value/column-level* data quality: is this cell a typo, is
  this column an identity anchor, is this value future-dated. It exposes signals.
- **GoldenMatch** owns *entity resolution*: which records are the same entity.
  Whole-ROW fuzzy matching stays here, not in GoldenCheck.
- The bridges are the only coupling, and they fail open — `goldenmatch[quality]`
  is optional; without goldencheck every door is a no-op.

## Honest limitations recorded per door
- **Blocking (#1):** conservative — no-ops when the auto-config already emits a
  substring/soundex pass (the person-path case); targets plain exact keys on
  fuzzy categoricals.
- **FD-NE (#3):** FD discovery excludes *perfectly-unique* determinants as
  trivial, so it catches anchors with cardinality in [0.5, 1.0), **not** perfect
  oddly-named keys (those need a format/structure signal — a future door).
- **Review (#5/#6):** wired only into the `goldenmatch review` CLI (the steward
  loop has the row-id'd frame); the TUI/agent `gate_pairs` calls are a one-liner
  follow-up.

## Verification
Each door: unit + behaviour tests, plus a regression proof that the flag-off path
is byte-identical (e.g. 58 NE/autoconfig tests for #3, 22 review-queue tests for
#5). The **accuracy** gate (recall/precision/F1 on the gitignored ER datasets)
runs in CI / on a runner — it is what flips a default ON.

## Remaining
**Door #2 (standardization-transform selection)** is unbuilt and flagged as the
weakest remaining door — heavy overlap with #1, GoldenFlow (the `goldenflow-native`
date/phone work), and GoldenMatch's existing `standardize` stage.

---
**Classification:** architecture/active • **Last updated:** 2026-06-07
