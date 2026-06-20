# GoldenSynonym — a trained, domain-aware synonym scorer framework

A pluggable, **trained** synonym scorer for goldenmatch that resolves domain
synonyms a string/embedding scorer can't — brand↔generic drug names first
(ibuprofen↔Advil↔Duexis), extensible to other domains. Motivated by ER-KG-Bench:
`synonym_brand` sits at the exact-match floor (0.33) because no scorer encodes
drug-synonym knowledge; the rest of the table is strong.

## The honest ceiling (this shapes the whole design)
Brand↔generic synonymy is mostly **arbitrary domain knowledge**, not a signal in
the text. "Advil"="ibuprofen", "Vybrique"="sildenafil" have no lexical or
generic-semantic similarity (a generic embedder already scored 0.44 on this
corpus). So a trained model can only win in two real ways, and we design + measure
for exactly these:
1. **Morphological / sub-lexical families** it CAN learn — salt/dosage variants,
   transliterations, "-profen"/"-cillin" stems, partial-overlap brands.
2. **Coverage** — it scores *every* pair, not just table hits.
For *purely arbitrary* brand names, a trained model just **memorizes the mapping
in its weights** — same information as a lookup table, worse reproducibility. So
the framework's defensible value is **the general pluggable scorer + a measured
held-out generalization number**, not "beating a table on arbitrary brands." The
eval is built to expose precisely how much real generalization there is.

## Architecture
A general `synonym` scorer that resolves a per-domain **trained model**, mirroring
two proven patterns already in the repo: the refdata scorer plugins
(`given_name_aliased_jw`) and the embedder's provider registry.

- **`SynonymScorer(ScorerPlugin)`** in `goldenmatch/synonym/scorer.py` — implements
  `score_pair(a, b) -> float | None` and the vectorized `score_matrix(values) ->
  np.ndarray` (the perf path the refdata scorers use). Registered via
  `PluginRegistry.instance().register_scorer("synonym", ...)`, selected by
  `scorer: synonym` in a matchkey. Late/graceful resolution: if no model is
  available it degrades to Jaro-Winkler (never errors).
- **`SynonymModel` provider registry** (`goldenmatch/synonym/providers.py`) —
  `resolve_synonym_model(domain: str) -> SynonymModel`, mirroring
  `goldenmatch.embeddings.providers.resolve_provider`. Each domain (`drug`, later
  `chemical`/`product`/…) supplies a trained model. The active domain is set on the
  scorer (config/env), defaulting to a domain-agnostic model.
- **Scoring path (trained-primary):** the model embeds both names in its learned
  space → cosine → a **calibrated threshold** maps to [0,1]. An optional refdata
  table (the training source) provides a known-equivalence fast path (→1.0) and a
  fallback when the model is absent; the table is also the **baseline** the eval
  compares the trained layer against.

## The trained model
- **Method:** contrastive metric learning. Positives = known synonym pairs;
  **hard negatives** = same-domain non-synonyms (e.g. two distinct drugs, similar
  strings) so the model learns the *knowledge*, not just string distance. Output =
  a fine-tuned sentence encoder; score = cosine in its space.
- **Base model (default):** fine-tune a small generic sentence-transformer
  (MiniLM-class) on the domain pairs — self-contained, and "the training is the
  point" (vs. leaning on a domain-pretrained biomedical model that already
  memorized drug synonymy during *its* pretraining, which would just relocate the
  lookup). A domain-pretrained base stays an opt-in `base_model` knob.
- **Drug training data:** synonym pairs derived from **RxNorm**. RxNorm RRF is NOT
  an anonymous download — it sits behind a **free UMLS license + UTS API key**. So
  the derivation is a **one-time, off-CI, by-a-licensed-human** step: run it locally,
  commit the resulting small `drug_synonyms.train.jsonl`. **CI never touches
  RXNCONSO/RXNREL.** Pin the exact filter for reviewability/reproducibility-in-
  principle: brand↔generic pairs from RXNREL `RELA ∈ {has_tradename, tradename_of}`
  joined to RXNCONSO atoms, restricted to the train RxCUI split with ALL surface
  atoms of held-out RxCUIs excluded (not just train-split positives — see the honesty
  section). The derivation script is committed but is dev tooling, not run by CI.
- **Calibration:** pick the cosine threshold on a held-out *validation* split
  (max-F1), store it with the model so `score_pair` returns a meaningful [0,1].

## The honesty design (load-bearing — non-negotiable)
The corpus's `synonym_brand` entities ARE RxNorm drugs, and the ground truth IS
RxNorm grouping. A model trained on all of RxNorm would be reading the answer key.
So:
- **Disjoint split — by RxCUI AND by surface string (leak-proof):** the drug model
  trains on RxNorm drugs with **zero entity overlap** with the ER-KG-Bench corpus
  (split by RxCUI), AND **every surface atom (all RXNCONSO strings) of the held-out
  RxCUIs is excluded from training entirely — positives AND negatives.** Excluding
  only positive pairs is not enough: if a held-out brand like "Advil" appears even as
  the negative half of a training pair, the model has seen the surface form and the
  generalization number is contaminated. The corpus's 4 drug families
  (ibuprofen/acetaminophen/sildenafil/warfarin + all their brands) are held out
  whole.
- The `synonym_brand` re-measure is then a **true generalization test**: does the
  model resolve synonyms of drugs whose strings it never saw? Report THREE numbers —
  in-domain (train RxCUIs), **held-out** (the corpus), and the **refdata-table
  baseline** on the same held-out set — plus, for the production scorer, the
  trained-model-only number (known-equivalence fast-path DISABLED) **separately** from
  the production number (fast-path on), so "what did training actually buy" is never
  masked by the table hits the model sits in front of.
- **Directional prediction (stated up front, so a near-baseline result reads as
  confirmation, not failure):** for arbitrary brand names the held-out number is
  *expected to land near the table baseline* — that IS the ceiling. A meaningful
  trained lift, if any, will show on the *morphological* subset (salt/dosage/stem
  variants), which `RESULTS` breaks out.
- `RESULTS` states the split + that arbitrary-brand resolution is memorization-bound
  (the table is the production path; the trained model is the coverage/morphology
  layer). No spin.

## Reproducibility + artifacts
Fine-tuned encoders are too large to commit. To stay reproducible without baking an
answer-key model into the repo:
- **Commit the derived training pairs** (the held-out-split RxNorm pair file, small).
- **Train only in the OPT-IN eval lane** (seeded) → an ephemeral model artifact,
  cached locally (gitignored), optionally uploaded to a GitHub release for reuse (the
  `bench-dataset-v1` pattern). A few-thousand-pair MiniLM-class contrastive fine-tune
  is expected ~5-15 min on a CI CPU runner — **this runs ONLY on the opt-in lane,
  NEVER on the deterministic `bench-er-kg` gate or the blocking pytest suite** (CI
  pytest is blocking — a real fine-tune there would be a timeout/flake bomb). GS2 must
  measure + record the actual wall-clock; if it exceeds a lane budget, switch to
  pre-train-once-and-upload.
- **GS2 unit tests use a TINY toy-fixture model trained in-process** (a handful of
  synthetic morphological pairs, ~seconds, seeded) to assert the trainer's contract +
  held-out morphological lift — they do NOT run the real RxNorm fine-tune. The real
  model + the corpus re-measure live in the opt-in lane only.
- No pre-trained model file is committed; the model is always reproducible from the
  committed pairs + a fixed seed.
- **Calibration** (the cosine threshold) is **recomputed on each train** from the
  validation split (not committed, so it can't drift from a re-derived model); GS2
  asserts it lands in a stable band.

## Where it plugs in
- **goldenmatch:** a registered `synonym` scorer (opt-in via `scorer: synonym` in a
  matchkey, or auto-config promotion later). Pure additive — no default change.
- **goldengraph / ER-KG-Bench:** opt-in. The `synonym` scorer becomes a matchkey
  field option in the goldengraph adapter's resolution config; the eval re-measure
  is an **opt-in lane step** (like `--with-frameworks`), NOT the deterministic gate
  (it needs a model + training, and is non-deterministic).

## Scope + staging (multi-PR, like the goldengraph phases)
1. **GS1 — framework:** `goldenmatch/synonym/` package — `SynonymScorer` plugin +
   `SynonymModel` protocol + provider registry + refdata-table fallback + registration
   (at module init, like `refdata/__init__.py::register_scorers()`). `synonym` needs
   **no change to `VALID_SCORERS`** — schema validation falls through to the plugin
   registry for unknown scorer names (state this explicitly so the implementer doesn't
   add a dead entry). Unit tests with a stub model (assert plumbing + graceful JW
   degradation). No training yet. Ships the reusable surface.
2. **GS2 — drug provider + training:** RxNorm→pairs derivation (committed split),
   the contrastive trainer (`scripts/train_synonym_model.py`), the drug `SynonymModel`,
   calibration. Tests on a tiny fixture corpus (train/eval disjoint) asserting the
   trained model beats JW on held-out *morphological* pairs.
3. **GS3 — eval re-measure (opt-in lane only):** wire `synonym` into the ER-KG-Bench
   drug rows; run the held-out generalization measurement; write the honest `RESULTS`
   — in-domain vs held-out vs table-baseline, AND trained-model-only (fast-path off)
   vs production (fast-path on), AND the morphological-subset breakout — plus the
   synonym_brand delta. State the directional prediction was/wasn't borne out.

## Determinism, honesty, testing
- The framework + plumbing tests are deterministic (stub model). The trained model +
  the eval re-measure are non-deterministic / model-bearing → opt-in, never on the
  `bench-er-kg` deterministic gate.
- Honesty: held-out split reported; table baseline reported; memorization ceiling
  stated; "trained model is the coverage/morphology layer, table is production" is
  the explicit framing if held-out generalization is weak (the likely outcome).
- Tests: scorer protocol + registration + JW degradation (GS1); trainer + held-out
  morphological win + calibration (GS2); eval wiring + RESULTS render (GS3).

## Out of scope / follow-ups
- Other domains (chemical/product/org) — the framework supports them; only `drug`
  ships first.
- Auto-config promotion of `synonym` (it stays opt-in until a sweep justifies it).
- A large pre-trained domain model as the committed default (kept an opt-in knob).
- Replacing the refdata tables (`given_name_aliased_jw` etc.) — GoldenSynonym is
  additive; the name/business alias scorers stay as-is.

## File structure
```
packages/python/goldenmatch/goldenmatch/synonym/
  __init__.py            # register the synonym scorer (like refdata/__init__.py)
  scorer.py              # SynonymScorer(ScorerPlugin): score_pair + score_matrix
  providers.py           # SynonymModel protocol + resolve_synonym_model(domain)
  model.py               # trained-model wrapper (embed + cosine + threshold) + stub
  data/
    drug_synonyms.train.jsonl   # RxNorm-derived pairs, eval-disjoint split (committed)
  tests/...              # GS1 plumbing (stub), GS2 trainer/held-out, GS3 wiring
scripts/train_synonym_model.py   # contrastive trainer (seeded, CPU-friendly)
```
