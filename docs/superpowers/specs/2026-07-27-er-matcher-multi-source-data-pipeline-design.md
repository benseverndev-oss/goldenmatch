# ER-Matcher Multi-Source Data Pipeline — Design

**Date:** 2026-07-27
**Status:** Design (approved for spec review)
**Scope:** Sub-project 1 of the "production-grade OSS ER-matcher" effort.

## Context

The OSS ER-matcher (`scripts/er_matcher/`, Qwen2.5-3B LoRA SFT, plan §Phase 3)
currently trains on a single synthetic source: `gen_pairs.py` emits ~2,844
train pairs from 3,000 synthetic entities across 3 domains. A P3a smoke run has
succeeded (GPU util 99.3%, fits A10G, ~7.2 s/step) and the full run on this data
is trivially cheap (~$0.75, ~15 min on A100-40GB — computed, not multi-hour).

The generator's diversity is capped well below what a *production-grade* matcher
needs: the name space is 30 first × 30 last = 900 combos (people/healthcare) and
8×8×6 = 384 (business); positives are self-corruptions over ~6 corruption types.
Raising `--n-entities` alone just resamples that small space — the learning curve
would flatten. Getting a genuinely capable model requires **more and more varied
data**, from real benchmarks and a much richer synthetic generator.

This sub-project builds the **multi-source data pipeline** that produces the
training/eval corpus. The trainer and the Modal run are intentionally untouched
here — they still consume `data/er_matcher/{train,val,test}.jsonl`. Re-sizing the
run for the larger corpus is sub-project 2.

## Goal

Produce a license-clean, multi-source, provenance-tracked training + eval corpus
for a production-grade person + product entity matcher, emitting the existing
JSONL row contract so the trainer needs no change.

Non-goals (separate sub-projects):
- Sub-project 2 — run re-architecture for scale (GPU tier, timeout, epochs,
  checkpoint/resume, the now-meaningful perf gate + learning-curve sweep).
- Sub-project 3 — cross-benchmark eval harness (per-source match-F1 + calibration
  comparable to published DeepMatcher/Ditto numbers).
- Sub-project 4 — release (quantize, publish, model card).

### Plan structure (decided)

Sub-project 1 is **one spec, two sequential phases** under a single implementation
plan, landing as two PRs:

- **Phase 1a — benchmark ingestion:** the loader interface, febrl/leipzig loaders,
  negative synthesis, fetch-at-build eval loaders, and `sources.yaml`/blend/splits/
  manifest (Architecture §1, §2, §4). This is the foundation and unblocks a first
  larger run on real+existing-synthetic data.
- **Phase 1b — Rich synthetic generator:** the `gen_pairs.py` rewrite
  (Architecture §3). Depends on nothing in 1a and can be developed in parallel, but
  is sequenced *after* 1a so the first scaled run isn't blocked on the generator
  rewrite.

The 1a/1b boundary is fixed here (not left to the planner): both are in scope for
this spec; the planner produces one plan with the two phases as distinct,
independently-mergeable stages.

## Data slate (license-vetted)

Verified against primary sources. The shipped model must be publishable
(PyPI/HF), so only redistribution-clean data is bundled.

| Source | License | Mechanism | Type | Role |
|---|---|---|---|---|
| **FEBRL** | ANUOS / MPL-1.1 | **bundle** | Person (synthetic) | Train + eval (person side) |
| **Leipzig** (Abt-Buy, Amazon-Google, DBLP-ACM, DBLP-Scholar, MusicBrainz-20K) | CC-BY + attribution | **bundle** (w/ credit) | Product / Citation / Music | Train + eval |
| **Rich synthetic** | project (MIT) | **generate** | Person / CRM / org / business | Train + eval, infinitely scalable |
| DeepMatcher/Magellan | none stated (cite-only) | **fetch-at-build** | Product / Citation | Eval-parity only; never committed |
| NCVR (NC voters) | public record, no grant; **real PII** | **fetch-at-build** | Person (real) | Optional eval-only generalization; never committed |

**Key implication (accepted):** the only license-clean *person/PII* data is
**synthetic** (FEBRL + the Rich generator). Real person data (NCVR) is precisely
what cannot be bundled into a published model, so shipped person-matching training
is synthetic-person; real PII benchmarks are fetch-at-build, eval-only, documented.
Two license caveats carried from vetting: (1) cite Leipzig as "CC-BY" (exact
version unconfirmed on-page) with attribution to the DB Group + VLDB2010 paper;
(2) pull Abt-Buy / Amazon-Google from **Leipzig** (CC-BY), not Magellan (same rows,
no license).

## Architecture

### 1. Unified row contract & loader interface

Every source emits the trainer's existing row contract, **extended with one
`dataset` provenance field** (the trainer ignores unknown keys — `read_jsonl` does
no key validation and `example_to_messages` reads only `a`/`b`/`label` — so the
added field is safe and requires no trainer change):

```
# existing contract: {a, b, label, domain, source, eid_a, eid_b}
# this pipeline adds:  dataset  (provenance; ignored by the trainer)
{a: {..fields}, b: {..fields}, label: "match"|"no_match",
 domain: str, source: str, dataset: str, eid_a, eid_b}
```

The shared serializer (`goldenmatch.core.er_matcher.prompt.build_chat`) renders
arbitrary fields, so **heterogeneous schemas are a feature** — mixing product
`{title, brand, price}`, person `{name, address, dob}`, and citation
`{authors, venue, year}` improves robustness. Native fields are preserved;
`domain`/`source`/`dataset` carry provenance and let eval slice by source.

A minimal pluggable interface (each loader is one focused, testable unit):

```python
class PairSource(Protocol):
    name: str                                       # == the `dataset` provenance value + registry key
    def splits(self) -> dict[str, Iterable[Row]]:   # {"train","val","test"}
        ...
```

`PairSource.name` is the single identifier used three ways: the registry key, the
`dataset` field written on every row it emits, and the `sources.yaml` key — one
string, no aliasing. A registry maps `dataset -> loader`. Four loader families:
- `febrl` — synthetic person; match status ships with the data.
- `leipzig` — one loader parametrized per CC-BY dataset (two source tables + a
  perfect-mapping gold file).
- `magellan_fetch` — fetch-at-build, DeepMatcher CSV format, **eval-only**.
- `synthetic` — the Rich generator.

### 2. Negative generation (positives-only sources)

DeepMatcher/Magellan CSVs already contain labeled negatives (post-blocking
candidate pairs) → direct read. Leipzig ships a perfect mapping (positives only)
+ the two source tables → negatives are synthesized:
- **hard negatives**: same blocking key, different entity (the boundary case).
- **easy negatives**: random cross-entity pairs.
- deterministic, seeded, ratio-controlled to hold the ~50/50 balance the current
  generator maintains.

A simple deterministic token/prefix blocker is used for reproducibility.
(Dogfooding goldenmatch's own blocker is a candidate enhancement but is kept out
of scope to keep sub-project 1 self-contained.)

### 3. Rich synthetic generator

A rewrite of `gen_pairs.py` into its own well-tested module, preserving the
current pure/deterministic discipline (pure helpers unit-tested without heavy
deps; byte-identical output per seed):
- **Census-scale vocab** with **Zipf-frequency sampling** (thousands of
  first/last names; realistic city/state/zip joint distributions) — removes the
  900-combo ceiling.
- **Configurable schemas** per domain: existing people / healthcare / business
  **+ CRM-contact + organization** (the PII/CRM emphasis).
- **Parameterized error-channel corruption model** — typo, OCR-confusion,
  phonetic, transliteration, field-swap, token drop/add, formatting variants —
  each with an independent, tunable rate, **calibrated to FEBRL's `dsgen`** so
  synthetic corruption matches an established person-matching benchmark rather
  than being ad-hoc. *Calibration target (concrete):* match `dsgen`'s per-field
  corruption **probabilities** and its **error-type mix** (the relative share of
  typo / OCR / phonetic / swap / drop), verified by comparing the corruption-type
  histogram of our generated pairs against a `dsgen`-generated reference set — not
  merely "looks similar."
- Deterministic in `--seed`; a `--profile` flag selects corruption intensity.

This is the heaviest component and is Phase 1b (see "Plan structure (decided)"):
sequenced after benchmark ingestion (1a) and landed as its own PR.

### 4. `sources.yaml`, blend, splits, manifest

One config is the single source of truth for **both the pipeline and the model
card**. The block below is **illustrative shape, not a literal schema** — the
`weight` values are placeholders (blend ratios are deferred; see below):

```yaml
sources:
  febrl:      {mechanism: bundle,   license: MPL-1.1,      domain: person,  weight: ...}
  abt_buy:    {mechanism: bundle,   license: CC-BY, attribution: "...",     weight: ...}
  synthetic:  {mechanism: generate, license: MIT,          weight: ...}
  magellan_*: {mechanism: fetch,    license: cite-only,    eval_only: true}
  ncvr:       {mechanism: fetch,    license: public-record, eval_only: true}
```

- **Splits**: preserve each benchmark's canonical train/val/test; for Leipzig
  (no fixed split) generate deterministic entity-level splits (reuse the current
  `_split_of` hashing so no entity leaks across splits). Note: `_split_of` today
  takes an **int** `eid` (`f"{seed}:{eid}"`); benchmark entity IDs are strings, so
  the reused helper must key on `str(eid)` — a required generalization, not an
  optional one, or non-int IDs silently break the hash.
- **Blend**: cap oversized sources (bundle MusicBrainz-**20K**; fetch larger
  variants only if ever needed) and **upweight person data** for the PII emphasis.
- **Output**: unified `data/er_matcher/{train,val,test}.jsonl` + `manifest.json`
  recording per-source provenance, counts, and licenses (feeds the model card).

## Testing & boundaries

Pure/deterministic where it matters — transform, negative sampling, splits, and
each corruption channel are unit-tested with **no network, no GPU** (mirroring
`test_gen_pairs.py`). Fetch loaders use committed fixtures and run behind a
network marker. Determinism tests assert same-seed → byte-identical JSONL. The
whole pipeline is CPU/box-safe; nothing here touches Modal.

## Open questions / risks

- **Bundle vs. Release asset**: FEBRL (~1–10k rows) and MusicBrainz-20K are small
  enough to commit; if the blended corpus grows large, host it as a GitHub Release
  asset (the existing `bench-dataset-v1` pattern) instead of committing raw JSONL.
  Decide during planning based on measured sizes.
- **Leipzig fetch script vs. vendored copy**: CC-BY permits bundling; prefer a
  committed vendored copy + attribution for reproducibility over a fetch that can
  rot. Confirm each file's size first.
- **Blend ratios** are a modeling knob; start balanced with a person upweight and
  tune once the sub-project 2 run + sub-project 3 eval exist.
- **FEBRL access path**: bundled files vs. the `recordlinkage` library's copy —
  pick the one with the clearest provenance during planning.
