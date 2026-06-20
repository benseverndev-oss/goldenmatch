# 0022 — Corpus-dedup throughput benchmark + perf gate (#1086)

**Status:** accepted • **Shipped:** PRs #1134 / #1139 / #1142 / #1144 / #1147 (2026-06-20), issue #1086 (epic #1080)

## Context
The opt-in throughput tier (#1083) made a throughput claim — sketch-then-verify
corpus dedup at high recall, low cost — but it shipped validated only on 10-row
unit tests. "Defend the throughput claim" (the epic's done-bar for #1086) needs
(a) a measured number on a real public corpus and (b) a regression gate. The
catch: corpus text ships no near-dup labels, and shared CI runners have real
wall-clock variance, so a naive "fail if slower than X seconds" gate would flake.

## Decision
A dedicated `scripts/bench_corpus_dedup/` harness mirroring the proven
`bench_er_headtohead/` patterns (subprocess-per-datapoint isolation, loud-failing
runners, reused DuckDB evaluator), purpose-built for whole-document near-dup:

- **Pluggable corpus adapters** — FineWeb / C4 / Wikipedia (HF streaming) + a
  vendored offline Gutenberg slice (network-free). **Injected ground-truth
  near-dups** (exact / partial-overlap / paraphrase) make recall measurable on real
  text the corpus itself can't label.
- **Headline metric** is `docs/sec` + `MB/sec` (dispatch workflow), with datatrove
  as a live competitor and NeMo-Curator *cited* (GPU/RAPIDS, not CI-runnable).
- **The per-PR gate is deterministic, not wall-clock.** `throughput-gate` runs the
  tier on the offline corpus at a fixed size+seed and asserts *machine-independent*
  cost — candidate pairs, reduction ratio, measured recall — against a committed
  baseline. Wall-clock can't enter the verdict, so the gate can't flake on
  shared-runner noise; wall-clock is the dispatch headline's job.

## Consequence
- **Published number:** ~1,192 docs/sec · 3.6 MB/sec on a 70k-doc FineWeb slice at
  ~0.43 *measured* LSH recall. End-to-end docs/sec is auto-config-bound at this
  scale (the raw sketch dedup is ≈7,800 docs/sec); the honest LSH recall is reported
  (paraphrase/partial dups miss the bands) instead of the analytic `expected_recall`.
- **The bench did its job — the claim was overstated at scale.** Walking the tier up
  on real FineWeb surfaced and fixed four at-scale bugs the 10-row tests missed:
  (1) the GoldenCheck O(N²) `cell_quality` fuzzy scan on document text, (2) web text
  mis-classified as a non-text column so the tier *refused* (fixed via a shared
  `sketchable_text_cols` predicate keyed on length, not the semantic label),
  (3) the ≥100k RED-config refusal (`allow_red_config`), and (4) — still open — an
  O(N) `iter_rows` survivorship/golden ceiling above ~70k.
- **Tracked follow-ups:** datatrove recall-parse (it now runs but the cluster-output
  union-find reads 0 edges), and lifting the 100k+ ceiling via throughput-skips-golden
  (same shape as the quality-scan skip).
- **Lesson:** validate a perf/scale claim *at the claimed scale* before shipping —
  green 10-row unit tests are not a throughput proof.
