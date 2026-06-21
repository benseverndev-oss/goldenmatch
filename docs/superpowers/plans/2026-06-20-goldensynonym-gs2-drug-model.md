# GoldenSynonym GS2 — trained drug model (self-contained, public data)

> TDD. Builds on GS1 (the `synonym` framework). Per the user: **public substitute
> training data (no RxNorm/UMLS), fully self-contained.**

**Goal:** a TRAINED per-domain `SynonymModel` for `drug`, learned from public
brand↔generic + morphological pairs, plugged into GS1. Honest by construction: the
4 ER-KG-Bench drug families (ibuprofen/acetaminophen/sildenafil/warfarin + all their
brands) are HELD OUT of training entirely (positives and negatives), so GS3's
re-measure is a true generalization test.

**The honest prediction (stated up front, tested):** brand↔generic synonymy
(Advil↔ibuprofen) has **no morphological signal** — feature-wise an arbitrary brand
positive is indistinguishable from a cross-drug negative — so the trained classifier
**cannot learn it** and will sit near baseline on the held-out arbitrary brands. The
learnable signal is **morphological** (spelling/salt variants: amoxicillin↔amoxycillin,
cefalexin↔cephalexin), where the model DOES lift over Jaro-Winkler. GS2 tests both.

**Model:** a small **numpy logistic regression over pair-features** (char 2/3-gram
Jaccard, JaroWinkler, shared-prefix ratio, length ratio + bias). Self-contained
(numpy + rapidfuzz, already deps), CPU-instant, seeded. Weights commit as tiny JSON
and are reproducible from the committed pairs + seed (a test asserts re-train ==
committed within tolerance) — so NO CI training, no large artifact.

**Paths** (under `packages/python/goldenmatch/goldenmatch/synonym/`):
`data/drug_synonyms.train.jsonl` (public pairs), `data/drug_synonym_model.json`
(trained weights), `train.py` (trainer + pair-features), `drug.py`
(`DrugSynonymModel(SynonymModel)` + register for `drug`); tests under `tests/`.

---

### Task 1: public training data + provenance/disjointness test
**Files:** Create `data/drug_synonyms.train.jsonl`; Test `tests/test_drug_data.py`.

- [ ] **Step 1 — failing test.** `test_training_data_is_eval_disjoint`: load the JSONL;
  assert ≥30 generic groups; assert NONE of the held-out eval surface strings appear
  ANYWHERE (generic or brand), checked normalized — the held-out set = {ibuprofen,
  acetaminophen, paracetamol, sildenafil, warfarin} ∪ {advil, motrin, duexis, brufen,
  nurofen, proprinal, ibudone, tylenol, panadol, viagra, revatio, liqrev, vybrique,
  coumadin, jantoven, ...} (the corpus's brands). `test_has_morphological_pairs`:
  ≥5 groups are flagged `"morph": true` (spelling/salt variants) so the lift is
  measurable.
- [ ] **Step 2 — run, expect fail** (file missing).
- [ ] **Step 3 — author the data.** `drug_synonyms.train.jsonl`: ~40+ lines, each
  `{"generic": "<g>", "brands": ["<b1>", ...], "morph"?: true}`. Use widely-known
  PUBLIC brand↔generic facts EXCLUDING the eval families (metformin/Glucophage,
  atorvastatin/Lipitor, omeprazole/Prilosec, sertraline/Zoloft, …) + ~5-8 explicit
  morphological groups (`amoxicillin`/`amoxycillin`, `cefalexin`/`cephalexin`,
  spelling/salt variants, `morph: true`). Header comment: PUBLIC general-knowledge
  brand↔generic, NOT RxNorm; eval families excluded for held-out honesty.
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(synonym): public eval-disjoint drug training pairs (GS2 T1)`.

### Task 2: trainer + pair-features + committed weights
**Files:** Create `train.py`; Create `data/drug_synonym_model.json`; Test `tests/test_train.py`.

- [ ] **Step 1 — failing test.** `test_features_in_unit_range`: `pair_features("amox","amox")`
  all in [0,1], identical-string ≈ all-high. `test_train_reproducible`: `train(seed=0)`
  twice → identical weights; `test_committed_weights_match_retrain`: re-train from the
  committed pairs+seed == the committed `drug_synonym_model.json` within 1e-6 (no drift).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `train.py`: `pair_features(a, b) -> np.ndarray` (char-2/3
  -gram Jaccard, JaroWinkler.similarity, shared-prefix ratio, min/max length ratio,
  bias=1). `build_examples(groups, seed)`: positives = all within-group pairs
  (generic↔brand, brand↔brand); hard negatives = sampled cross-group pairs (seeded,
  ~2x positives). `train(examples, seed, iters, lr) -> weights` (numpy logistic
  regression, gradient descent, seeded init). A `__main__` writes
  `data/drug_synonym_model.json` (`{"features": [...names], "weights": [...], "seed":0}`).
  Run it once to produce the committed weights.
- [ ] **Step 4 — run, expect pass** (incl. the no-drift assertion).
- [ ] **Step 5 — commit:** `feat(synonym): numpy logistic pair-feature trainer + weights (GS2 T2)`.

### Task 3: DrugSynonymModel + register + HONEST behavior tests
**Files:** Create `drug.py`; Modify `synonym/__init__.py` (register the drug model); Test `tests/test_drug_model.py`.

- [ ] **Step 1 — failing test.**
  - `test_morphological_pair_beats_jw`: on a HELD-OUT morphological pair the model
    wasn't trained on (e.g. `cefuroxime`/`cefuroxim`), `model.score(a,b)` >
    `JaroWinkler.similarity(a,b)` (the learned morphological lift — the real win).
  - `test_arbitrary_brand_does_not_generalize`: `model.score("Advil","ibuprofen")` is
    LOW (< 0.5) — confirms the ceiling: the model canNOT resolve an unseen arbitrary
    brand (no morphological signal). This test ENCODES the honest prediction.
  - `test_registered_for_drug_domain`: after import, `resolve_synonym_model("drug")` is
    a `DrugSynonymModel` (not the stub).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `drug.py`: `DrugSynonymModel` loads
  `data/drug_synonym_model.json` (lazy, cached) + `score(a, b)` = `sigmoid(features·w)`;
  graceful: weights missing → returns None (stub behavior). `register_drug_model()` →
  `register_synonym_model("drug", DrugSynonymModel())`; call from `synonym/__init__.py`
  after the scorer registration.
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(synonym): DrugSynonymModel (learned, held-out-honest) (GS2 T3)`.

### Finish
- [ ] Full synonym suite (GS1 + GS2): `pytest .../goldenmatch/synonym/tests/ -q`.
- [ ] PR (stacked on GS1 #1153; rebase `--onto origin/main` after GS1 merges). Runs in
  the standard `ci.yml` python lane.

## Notes
- **Honesty is the deliverable.** The two behavior tests in T3 are the point: a
  measured morphological lift + a measured arbitrary-brand non-generalization. GS3
  carries this to the full ER-KG-Bench corpus (held-out) + writes RESULTS.
- numpy logistic over pair-features = genuinely trained, but its ceiling is the
  features' signal; arbitrary brand↔generic has none → near-baseline (expected, tested).
- Local test env: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_ANALYTICS=0 GOLDENMATCH_NATIVE=0`,
  PYTHONPATH-shadow the worktree goldenmatch.
