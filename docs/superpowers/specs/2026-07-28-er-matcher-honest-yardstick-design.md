# ER-Matcher "Honest Yardstick" — Design (SP3.5)

**Status:** approved design, pre-plan.
**Worktree/branch:** `D:/ER/gm-yardstick` on `feat/er-matcher-honest-yardstick` (off `main` after SP3 / PR #2222 merged).

## Problem

SP2's headline in-distribution F1 (0.983) is inflated and cannot be trusted as a training signal:

1. **Easy negatives.** Non-match pairs come from `sources/negatives.py::synth_negatives`: "hard" = two different entities sharing the lowercased **first whitespace token** of a block field, "easy" = random cross-block, `hard_frac=0.5`. These are deterministic synthetic near-misses, not calibrated hard negatives. The SP3 design already flags the resulting F1 as "almost certainly inflated."
2. **Split leakage.** Leipzig sources split at **record/pair** level, not entity level (`sources/leipzig.py:30-40`), so the same real-world entity can appear in both train and test.
3. **Broken calibration at the source.** Training uses **fixed** confidence targets (0.9 match / 0.1 no_match, `config.yaml:56-57`), which produced raw ECE ~0.46 — patched post-hoc by SP3 temperature scaling rather than fixed in training.

The honest held-out numbers are SP3's zero-shot suite: Walmart-Amazon F1 0.640, Beer 0.897.

## Goal

Make the training signal and the metric honest, and confirm it with a retrain, before investing in the larger Phase 1b data-diversity build. Three coupled changes plus one retrain:

1. **Entity-level splits** — kill the leakage.
2. **FS-mined hard negatives** — mine near-threshold non-matches from goldenmatch's own Fellegi-Sunter (FS) pipeline (dogfooding).
3. **FS-score-driven soft confidence targets** — replace fixed 0.9/0.1 with difficulty-aware targets derived from the same FS score, fixing calibration at the source.
4. **Retrain + re-measure** on the frozen SP2 config (Qwen2.5-3B-Instruct, bf16 LoRA, 2 epochs) and report against the SP3 zero-shot suite + ECE.

This is deliberately a data + measurement step. Model scaling (7B), hyperparameter sweeps, and the Rich Synthetic Generator (Phase 1b) are explicitly out of scope and follow later.

## Architecture (Approach A: unified post-blend FS-enrichment stage, with caching)

New module **`scripts/er_matcher/fs_enrich.py`**, one focused unit. Its decision logic is pure and box-testable; the heavy goldenmatch FS matcher is **injected** as a `scorer(a, b) -> float` (match probability) callable so the box suite never imports it. `build_corpus.py` wires the real goldenmatch scorer; unit tests wire a stub.

**Building the injected scorer/blocker (confirmed API, plan-level detail).** goldenmatch's real entry points are not zero-arg: `goldenmatch.core.scorer.score_pair(a, b, fields: list[MatchkeyField]) -> float` needs per-field weights/scorers, and `goldenmatch.core.blocker.build_blocks(lf: LazyFrame, config: BlockingConfig)` needs a Polars frame + blocking config. So `build_corpus.py` builds a **closure** that first resolves a matchkey + blocking config for each source — most likely via `auto_configure` keyed off the per-source `domain` field in `sources.yaml` — then adapts `score_pair`/`build_blocks` to the `scorer(a, b) -> float` and `candidates(records) -> pairs` interfaces `fs_enrich` consumes. The plan pins the exact config-resolution entry point; `fs_enrich` itself stays agnostic to how the scorer was built.

### Data flow

```
sources.yaml -> loaders (febrl/leipzig) -> blended labeled pairs
      |                                          |
      |  records_by_source                       |  existing match / non-match pairs
      v                                          v
  fs_enrich.enrich(records, pairs, scorer, cfg):
     1. soft targets : every pair -> confidence = f(FS score)          [calibration half]
     2. mine hard neg: block record pool -> FS-score candidates ->
                       keep near-threshold GROUND-TRUTH non-matches -> cap  [quality half]
      v
  entity-level split (fixed) -> corpus JSONL  (now carries per-row `confidence`)
      v
  train.py reads per-row confidence (no more constant 0.9/0.1) -> retrain
      v
  measure: in-distribution F1 + SP3 zero-shot suite + raw/calibrated ECE
```

Enrichment output is **cached**, keyed on a hash of (corpus content + scorer config), so rebuilds don't re-run the FS pass. Caching sits under Approach A as an optimization, not a separate source.

### Core 1 — FS score -> soft confidence

A monotonic map from the matcher's match-probability `s` to a target confidence, compressed toward 0.5 near the FS decision threshold `tau`, never 0/1:

- **match** pair: `conf = clamp(0.5 + 0.45 * (s - tau) / (1 - tau), 0.55, 0.97)`
- **non-match** pair: symmetric toward the low end, `conf in [0.03, 0.45]`.

So a match the matcher was sure of -> ~0.95; a match it nearly missed -> ~0.6. The exact curve/clamps are finalized in the plan; the invariant under test is: **monotonic in `s`, compressed near `tau`, never 0 or 1.** `conf` is the target used to render the SFT verdict (`render_target`), replacing the fixed 0.9/0.1.

### Core 2 — entity-level splits

Derive a stable **entity key** per record from each benchmark's gold match mapping: records connected by a gold match edge form one entity (connected components over the match graph). Split on the entity key so every record of an entity lands in exactly one split. The connected-components entity-keying helper lives in **`sources/splits.py`** (shared, mirroring how `split_of` is already shared there) and is consumed by `sources/leipzig.py`. FEBRL already splits at entity level (`_entity_of` from `rec_id`, `split_of` called once per entity), so it is unchanged. The Leipzig gold mapping is bipartite (tableA<->tableB); connected components still yield correct transitive clusters.

### Core 3 — hard-negative mining

1. Block the record pool (reuse goldenmatch's candidate generation — proper dogfooding).
2. FS-score the candidates.
3. Keep pairs whose score is in a near-threshold band `[tau - delta, tau + delta]` **and** whose ground-truth label is **non-match** (label comes from the gold benchmark mapping, not from FS).
4. Cap the mined count; blend alongside a reduced share of the old synthetic hard negatives (augment, not full replace — preserves a difficulty curriculum).

Band width `delta`, cap, and mined/synthetic ratio are plan-level knobs.

**FS is a hard-example selector, not the labeler.** It only chooses *which* ground-truth non-matches are hard; the label is always the gold benchmark label. This avoids training the LLM to merely mimic the FS decision boundary.

## Testing

Same box/GPU boundary as SP2/SP3.

- **Box-safe unit tests** (injected fake scorer, no GPU, no goldenmatch import):
  - `fs_enrich` soft-target map: monotonic in `s`, compressed near `tau`, clamped, never 0/1.
  - `fs_enrich` mining: near-threshold band selection; ground-truth-label filter keeps only true non-matches; cap + mined/synthetic balance; cache-key stability.
  - splits: connected-components entity keying correct + deterministic; **anti-leakage invariant as its own explicit test** (no entity id appears in two splits).
  - `train.py`: reads per-row `confidence`, no constant.
- **Retrain** validated by the real Modal run (GPU), not box-tested.

## Measurement / success criteria

- **In-distribution F1 should drop from 0.983** — that is the win; it means the metric stopped lying. No target number; the point is trustworthiness.
- **SP3 zero-shot suite (Walmart-Amazon, Beer) should hold or improve** — harder negatives should sharpen real-world discrimination. This is the true, held-out quality signal.
- **Raw ECE should improve sharply** (soft targets fix the source of the 0.46), ideally enough that post-hoc temperature scaling is unnecessary.
- **Run watch-items:** mined negatives really are near-threshold (mean band score); the leakage invariant holds; the soft-target distribution is not collapsed.

## Risks / mitigations

- *"Are we just teaching the LLM to mimic the FS scorer?"* No — FS selects hard examples; the label stays the gold benchmark label.
- *FS score miscalibration (garbage-in)* — sanity-check the score distribution before wiring; the `tau`-relative mapping tolerates absolute miscalibration.
- *Mining candidate blowup* — cap candidates, bound the band, cache.
- *Held-out integrity* — Walmart-Amazon/Beer stay `eval_only`; they never enter mining or training.
- *Two variables move at once (data + soft targets)* — accepted: they move largely different metrics (data -> F1/generalization, soft targets -> ECE), and SP3 reports F1 and ECE separately, so each remains mostly attributable.

## Out of scope (later tracks)

- 7B base model / hyperparameter sweep (epochs, lr, LoRA rank).
- Rich Synthetic Generator (Phase 1b data diversity) — the next step after this.
- New sources (MusicBrainz-20K, NCVR real-person loader).
