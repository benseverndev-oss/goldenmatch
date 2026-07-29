# ER-Matcher Phase 1b (Lean): Rich Synthetic Generator - Design

**Status:** approved design, pre-plan.
**Worktree/branch:** `D:/ER/gm-p1b` on `feat/er-matcher-p1b-synthetic` (off `main` after SP3.5 / PR #2237 merged).
**Supersedes (for the lean cut):** the Phase 1b scaffold in `docs/superpowers/plans/2026-07-27-er-matcher-data-pipeline.md` (tasks 10-15, intent-level) and `docs/superpowers/specs/2026-07-27-er-matcher-multi-source-data-pipeline-design.md` section 3.

## Problem / hypothesis

SP3.5 (PR #2237) established the honest yardstick and found that the current training data (FEBRL person + Leipzig product/citation) is **generalization-neutral**: zero-shot F1 on held-out Walmart-Amazon (0.645) and Beer (0.897) did not move vs SP2. Phase 1b's premise is "more diverse training data -> better generalization." That premise is now **cheaply testable** against the honest yardstick, so we build a **lean** synthetic generator first to test it, and only invest in the full designed generator (dsgen-calibrated corruption, all domains) if the test comes back positive.

## The lean test

- Build a real-but-minimal synthetic source adding **new entity/contact diversity** (CRM-contact, organization, business) absent from FEBRL/Leipzig.
- Add one **domain-matched held-out benchmark** so the test is conclusive: **Fodors-Zagats** (DeepMatcher Structured; restaurant/business-entity on `name/address/city/phone/type`), evaluated like WA/Beer.
- Rebuild the corpus with `synthetic` enabled, retrain on the frozen SP2 config, measure.
- **Decision driven:** if Fodors-Zagats zero-shot F1 **improves** (matched-domain generalization signal), the diversity hypothesis holds -> greenlight full Phase 1b. If Fodors-Zagats is **flat AND** WA/Beer flat, diversity does not transfer -> pivot to model scale (7B) instead. WA/Beer are the product-domain hold-or-improve guard.

## Architecture (Approach A: a `synthetic/` package; rewire `gen_pairs.py` to delegate)

New package `scripts/er_matcher/synthetic/`, four pure, box-testable units (no GPU/network/goldenmatch import):

### `vocab.py` - census-name sampler
- Dogfoods goldenmatch's already-vendored census surnames (`packages/python/goldenmatch/goldenmatch/refdata/data/`, with `PROVENANCE.md`): frequency-weighted (Zipf-like) draw over the real surname counts.
- Bundles a modest public-domain first-name list (SSA given names) + a US city/state table under `synthetic/data/` (provenance documented).
- `sample_name(rng)`, `sample_address(rng)`, etc. Deterministic per seed. Vocab size well past the old 900-combo ceiling.

### `schemas.py` - domain schemas
Three domains, each a field set + a declared **strong-id key** (the field a true entity keeps stable; hard negatives share names but conflict on it):
- `crm_contact`: `first, last, email, phone, company, title, street, city, state, zip` - strong id: `email`
- `organization`: `legal_name, dba, website_domain, ein, address` - strong id: `ein`
- `business`: `name, email, phone, city, state, website` - strong id: `website`

### `corruption.py` - error channels (lean, no dsgen calibration)
A small fixed set of concrete, seeded channels, each with a fixed per-field rate:
- char-typo (insert / delete / swap / substitute)
- case / whitespace variants
- phonetic / nickname (name -> nickname; phonetic near-miss)
- token drop
- format-variant (phone / email / address reformatting)
One `--profile` knob with two levels (`light` / `heavy`) mapping to rate multipliers. Channels validated by **direct unit tests** (each channel does what it says); the dsgen calibration histogram is explicitly **deferred**.

### `generate.py` - `SyntheticSource`
Implements the `PairSource` protocol (`name`, `splits() -> {train,val,test}` of `Row`s) plus **`record_pools() -> {split: [records]}`**:
- Generates N entities per domain; positives = an entity vs a corrupted self.
- Negatives reuse `sources.negatives` (DRY); splits via `sources.splits.split_of` at the entity level (leakage-free, consistent with the SP3.5 loaders).
- **`record_pools()` is required** so synthetic pairs flow through the SP3.5 FS enrichment (soft-confidence targets + `fs_mined` hard negatives) automatically - the same seam FEBRL/Leipzig use.
- Deterministic: same seed -> byte-identical JSONL (with FS enrichment OFF; enrichment is opt-in and output-invariant when off).

## Integration

- `sources.yaml`: add a `synthetic` entry (`loader: synthetic, mechanism: generate`, per-domain config, seed/profile).
- `sources_config._BUILDERS`: add a `"synthetic": lambda e, seed: SyntheticSource(...)` factory (the earlier plan omitted this; `generate` is already in `_VALID_MECHANISMS`).
- `build_corpus.py`: no change needed - the `generate` mechanism already folds a source into the corpus identically to `bundle` (`_ROW_MECHANISMS = {"bundle","generate"}`), and `_fs_enrich_source` already calls `record_pools()` via `getattr`.
- `gen_pairs.py`: **left untouched** for the lean cut. It is a legacy standalone generator (domains: people/healthcare/business) that is NOT wired into the corpus (it writes JSONL directly, has no `sources.yaml` entry). Rewiring it would force a domain reconciliation (its people/healthcare vs the new crm/org) for zero lean-test benefit, so we build `SyntheticSource` fresh and leave `gen_pairs.py` + `test_gen_pairs.py` alone (they stay green trivially). Consolidating `gen_pairs.py` into the `synthetic` engine is **deferred to full Phase 1b**.
- Eval: add **Fodors-Zagats** to `sources/magellan.py::_URL_NAMES`, a `sota_baselines.py` row (published DeepMatcher/Ditto F1), runnable via `zeroshot_eval --dataset fodors_zagats`. `eval_only` (held-out, never trains/mines). Exact fetch URL + SOTA numbers confirmed during implementation (as WA/Beer were in SP3).

## Testing

Box-safe pure units (no GPU/network/goldenmatch import):
- `vocab`: determinism per seed; draw follows the frequency table (frequent names dominate); vocab size >> 900.
- `schemas`: each domain yields its declared fields; strong-id key present.
- `corruption`: each channel's behavior (a typo mutates ~1 char, nickname maps known names, format-variant reshapes phone/email, etc.); seeded determinism.
- `generate`: same-seed -> byte-identical; ~50/50 match balance; all 3 domains present; `record_pools()` leakage-consistency (every record in exactly one split; gold-linked share a split); hard negatives share a name but conflict on the strong-id key.
- `gen_pairs.py` is untouched, so `test_gen_pairs.py` stays green with no changes.

The FS enrichment of synthetic pairs is verified by a real corpus build (`build_corpus --fs-enrich` with synthetic enabled -> synthetic rows carry FS-driven `confidence` + some `fs_mined` negatives). The retrain + eval are verified by the real Modal run (same boundary as SP2/SP3/SP3.5).

## Success criteria

- Corpus builds with `synthetic` enabled; synthetic rows carry a real FS-driven confidence spread and `record_pools()` is leakage-clean.
- Retrain on the frozen SP2 config completes; measured against Fodors-Zagats + WA + Beer zero-shot + honest in-distribution.
- **Primary read:** Fodors-Zagats zero-shot F1 vs the SP3.5 baseline (a fresh no-synthetic Fodors-Zagats number is measured first as the baseline). Improvement -> greenlight full Phase 1b; flat (with WA/Beer flat) -> pivot to model scale.

## Out of scope (deferred to full Phase 1b, only if the lean test passes)

- dsgen calibration histogram + matching test.
- healthcare + person synthetic domains (person is FEBRL's job; no clean person held-out benchmark exists).
- Richer `--profile` levels beyond light/heavy.
- Blend-ratio / person-upweight tuning.
- Additional held-out benchmarks.
- Consolidating the legacy `gen_pairs.py` into the `synthetic` engine.
