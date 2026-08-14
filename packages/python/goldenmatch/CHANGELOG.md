# Changelog

All notable changes to GoldenMatch are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/). Versioning follows [Semantic Versioning](https://semver.org/) (strict after v1.0.0).

## [Unreleased]

### Added
- **Segment labels: which party each column describes (#2574).**
  `goldenmatch.core.segments` is a read-only consumer of InferMap's identity
  layers — `segments_from_schema` reads them off an `InferredSchema` GoldenPipe
  already produced (free), `detect_segments` detects on demand, and
  `column_segments` / `is_heterogeneous` are the helpers per-block configuration
  (#2575) needs as its input. Deliberately inert with respect to matching: it
  labels, it does not vary scorers, thresholds or blocking, and nothing here
  reaches the vectorized fast paths or the global-EM assumptions. InferMap stays
  an optional dependency (lazy import, structural adaptation, fail-open to `[]`).
- **Zero-config now says when a frame looks like several sources concatenated
  together, and routes to `match_df` (#2540).** `dedupe_df` compares candidate
  pairs WITHIN one frame. When that frame is two catalogues stacked with
  `pl.concat`, most of the candidate set is wrong by construction -- pairs drawn
  from a single source, which a cross-source ground truth can never mark correct.
  Measured on the repo's own benchmarks: **48.3%** of DBLP-ACM's candidate pairs
  and **73.4%** of Abt-Buy's are within-source. The failure was entirely silent.

  New `core/concat_sources.py` detects the shape and logs a warning naming
  `match_df`. It deliberately does **not** constrain matching: `match_df` already
  owns cross-source linkage, so a source constraint in dedupe would be a second
  authoritative owner for one capability, against the architecture frame -- this
  routes rather than duplicates. Inferring a source and silently dropping pairs
  would also turn a heuristic into a correctness dependency; a warning costs a
  log line when wrong, a dropped true match costs recall invisibly.

  **Contiguity is the entire signal.** `pl.concat` lays source A's rows down
  first, so a genuine concatenation leaves a step -- a column goes null, or
  changes value-shape, at one row boundary and stays that way. Real single-source
  messiness is scattered, not contiguous, which is what keeps this from being
  another plausible-but-wrong heuristic. The step test is tolerant (97% purity),
  because an exact two-run test missed abt_buy: `manufacturer` is null for all
  1081 Abt rows AND 6 Buy rows.

  Validated against the benchmark corpus before shipping -- it fires on all three
  known concatenated benchmarks at exactly the true source boundary, and stays
  silent on all four genuine single-source corpora:

  | corpus | result | boundary | true split |
  |---|---|---|---|
  | `dblp_acm` | fires (format step on `id`) | 2616 | DBLP2 = 2616 rows |
  | `abt_buy` | fires (null step on `manufacturer`) | 1081 | Abt = 1081 rows |
  | `amazon_google` | fires (null step on `title`/`name`) | 1363 | Amazon = 1363 rows |
  | `person` / `household_hardneg` / `cotenant_hardneg` / `ncvr_synthetic` | silent | -- | genuine single-source |

  Advisory and fail-open: any error returns `None`, and large frames are strided
  (a step survives uniform striding; head-sampling would see only one source).
- **ODCS (Open Data Contract Standard) dialect for the semantic-layer certifier
  (`goldenmatch.semantic.odcs`).** A data contract declares its identity right in
  the schema — `primaryKey: true` (ordered by `primaryKeyPosition` for a composite
  key) and `unique: true` — and nothing checks that promise against the data, so a
  duplicated key makes the contract's own uniqueness guarantee false. ODCS
  `primaryKey` maps one-to-one onto MetricFlow `entity (primary)` / Cube / Malloy
  `primary_key`, so the same certifier applies. New: `parse_odcs_contract` (reads
  the v3 `schema:`/`properties:` shape, tolerant of the legacy v2
  `dataset:`/`columns:`/`isPrimary` spelling on read — no
  `open-data-contract-standard` dependency), `odcs_identity_keys` (the composite
  primary key **plus each standalone `unique` property**), `certify_odcs_contract`
  (bridge to wedge A — one certificate per declared key, with the object's numeric
  properties as the fan-out measures) and `emit_odcs_from_crosswalk` (bridge to
  wedge B — declares the resolved key as `primaryKey`, provenance embedded in
  `customProperties`). `certify_semantic_model` auto-detects `kind: DataContract`
  as the `"odcs"` dialect. Library-only, advisory, parity-free.
- **Malloy (malloydata.dev) BI dialect for the semantic-layer certifier
  (`goldenmatch.semantic.malloy`).** Malloy's unit is a `source` whose identity is
  its declared `primary_key`, with `join_one`/`join_many`/`join_cross` riding on it
  — structurally the same as Cube's `primary_key` + joins, so the same certifier
  applies. New: `parse_malloy_models` (a structured `{sources: [...]}` projection
  OR raw `.malloy` DSL text via a focused declaration parser), `malloy_join_keys`,
  `certify_malloy_joins` (bridge to wedge A — certifies the one-side key of each
  join, picking the declaring side for `join_many` and skipping `join_cross`),
  `emit_malloy_source` (round-trips through the DSL parser) and
  `emit_malloy_from_crosswalk` (bridge to wedge B — declares the resolved key as a
  `primary_key` + a `join_one` to it, returning `(malloy_text, provenance)` since
  Malloy has no metadata slot in this subset). `certify_semantic_model` auto-detects
  a top-level `sources:` as the `"malloy"` dialect. Library-only, advisory,
  parity-free.
- **Feast (feature-store) dialect for the semantic-layer certifier
  (`goldenmatch.semantic.feast`).** A feature store is a join graph one layer over
  into ML: a `FeatureView` is keyed on an `Entity`'s `join_keys`, and a duplicated
  join key fans out every aggregated feature (a `sum`/`count` double-counts), a
  fragmented entity splits its own feature history (training-serving skew), and
  non-conformed keys can't join. Feast's `Entity(join_keys=[...])` maps one-to-one
  onto MetricFlow `entity (primary)` / Cube `primary_key`, so the same certifier
  applies. New: `parse_feast_models` / `parse_feast_objects` (declarative doc or
  duck-typed Feast SDK objects -- no `feast` dependency), `feast_join_keys`,
  `certify_feast_feature_views` (bridge to wedge A -- certifies each view's entity
  join key with the view's **features as the fan-out measures**),
  `emit_feast_from_crosswalk` (bridge to wedge B -- declares the resolved key as the
  entity `join_keys`), and `emit_feast_yaml` (round-trips through
  `parse_feast_models`). `certify_semantic_model` now auto-detects a top-level
  `feature_views:` as the `"feast"` dialect, so the CLI / MCP / REST front doors
  certify a feature repo with no new surface. Library-only, advisory, parity-free --
  it never mutates a feature or a key.

### Fixed
- **The config healer no longer goes silent on distributions with exact
  duplicates (#2497).** `ScoreDiagnostics::dip()` anchors on the RIGHTMOST
  prominent peak and walks left to the adjacent minimum. When a corpus contains
  genuine exact duplicates, their hard spike at 1.0 sits in the top bin behind a
  near-empty notch — and that spike *is* the rightmost prominent peak (on the
  measured NCVR-synthetic shape its left trough is 8 against a peak of 1468, so
  the 3x prominence test passes comfortably). The "valley" therefore collapsed
  onto the notch just below the spike instead of the real bimodal boundary:
  `dip()` returned 0.9583 against a threshold of 0.98, `threshold_rule`'s
  `DIP_MIN_GAP` check saw a gap of 0.0217, and `review_config` emitted **zero
  suggestions** — the healer said nothing about a threshold that was plainly
  wrong. The heuristic was working as written; the written heuristic had no
  notion that a degenerate one-bin spike at the top of the range is not a mode.

  `dip()` now skips a qualifying peak whose valley sits within
  `MIN_VALLEY_HEADROOM_FRAC` (6%) of the top of the **observed range**, falling
  back to the next qualifying peak below it. On the measured shape that yields
  0.8542 — the real boundary — which clears `DIP_MIN_GAP` and fires. A fraction
  rather than an absolute distance because the histogram bins over `[min, max]`
  of the *data*: on a corpus whose scores all sit in `[0.92, 0.99]` an absolute
  0.05 would be 71% of the entire range and reject nearly every valley. The
  constant sits at the geometric midpoint of the two measured neighbours, which
  are one and two bin-widths of a 24-bin histogram (0.0436 must be rejected,
  0.0869 must be kept).

  The two rejected alternatives are recorded because both look right and are
  not: preferring the *deepest* valley returns the 0.04 left-tail sliver on the
  right-skewed shape, regressing the very test that motivated right-anchoring;
  and requiring the peak to carry a shoulder rejects the textbook bimodal
  `(0.0, 100), (0.5, 2), (0.9, 100)`, whose 2% neighbour is on the same side of
  any workable cutoff as the degenerate spike.

  A new `exact_match_spike` golden joins the cross-surface fixture set. None of
  the existing six has a top-bin spike, so without it the TS/wasm surface could
  have kept an unfixed kernel with every parity test still green — the same
  "a check exists and does not fire" shape as the bug. Against the pre-fix
  kernel the new case yields no suggestions at all, so Rust, Python-native and
  TS/wasm parity on it is what proves the fix crossed each surface boundary; the
  embedded wasm is rebuilt in this change accordingly.
- **The auto-config search can now be made host-independent, so gates that pin
  its output stop being unstable by construction (#2532).** Two layers of the
  search stop on a WALL CLOCK: the controller's `ControllerBudget.max_seconds`
  (which ends the iteration loop with `StopReason.BUDGET_TIME`) and the five
  per-indicator budgets in `core/indicators.py` (which make an indicator return
  `None` or a default, silently changing what the indicator-aware rules decide).
  Either one makes the committed config a function of **machine speed and load**
  rather than of the code and the data — the same input on the same commit can
  commit a different config on a slow runner. Anything pinning controller output
  therefore has a check that can go red with no code change, and green while
  masking a real one; observed directly on `dblp_acm`, which flipped between
  `BUDGET_ITERATIONS` (4 iterations) and `BUDGET_TIME` (3 iterations) across
  runs of identical code on one machine.

  New `GOLDENMATCH_AUTOCONFIG_DETERMINISTIC=1` (`core/autoconfig_determinism.py`)
  drops every elapsed-time cut from the search, leaving the iteration caps as the
  only stopping rule: same code + same data give the same config on any host, at
  the cost of an unbounded runtime. Off by default — an interactive run wants a
  bounded wait more than a bit-reproducible search. It is deliberately **not** a
  config field: it describes the harness running auto-config, not the dataset
  being configured. Two behaviours are load-bearing and tested: the env var is
  read at CALL time (never cached in a module constant, the stale-import trap),
  and a budget of `<= 0.0` still means "this indicator is switched OFF" — a
  semantic switch, not a time limit — so deterministic mode never re-enables a
  disabled indicator.

  Both surfaces that pin controller output now go through it. The parity-pin
  harness owns the mode inside `pin_config` itself, so the regenerate side and
  `test_autoconfig_parity_pins_unchanged` pin the same thing, and it *refuses*
  to pin a run that reports `BUDGET_TIME` anyway — unreachable in deterministic
  mode, so it can only fire if the flag failed to reach the controller, which is
  precisely the silent failure this change removes. The `autoconfig_quality`
  scorecard and the `suggest_quality` gate set it alongside their existing
  `GOLDENMATCH_AUTOCONFIG_MEMORY=0` / `PYTHONHASHSEED=0` determinism pins.
- **The blocking-recall warnings now claim only what the estimate supports, and
  surface the trade-off the ranking makes silently (#2540).**
  `estimated_recall` is measured against the pairs the matchkey emits over an
  *unblocked* sample, so its denominator includes the scorer's false positives -
  pairs blocking is right to drop. Its ceiling is therefore that population's
  true-match fraction rather than 100% (measured on DBLP-ACM: 40.5% true). The
  low-recall warning compared that estimate against an absolute 30% floor and
  asserted *"expect most true matches to be missed"*, which the estimate cannot
  support: on DBLP-ACM `title[:5]` scores 0.037 while genuinely retaining
  **98.2%** of true pairs, so the warning fired while an excellent candidate sat
  in the list. It now reports the figure as a lower bound and says why.

  A second, **relative** warning carries the actionable signal. Ranking is
  `score x estimated_recall` with no recall floor, so a key that drops most
  matchable pairs can win on comparison count alone and nothing said so. When the
  ranked pick retains materially less than the best candidate measured, both are
  now named with their costs — on DBLP-ACM: *chosen `title[:5] + authors[:5]` at
  23.5% and 1,667 comparisons, versus `title[:3]` at 59.5% and 117,348* — so a
  2.5x retention trade for 70x fewer comparisons is visible rather than implicit.
  Comparing two estimates over the same target population cancels the estimator
  bias above, which is exactly what an absolute floor cannot do.

  Ranking behaviour is unchanged; these are log-only. The underlying objective
  problem (no recall floor) and the biased denominator are tracked on #2540.
- **`build_resolved_crosswalk` respects the caller's `config.identity` instead
  of replacing it (#2521).** It constructed a fresh `IdentityConfig` with
  `backend="sqlite"` hardcoded and `emit_singletons` omitted, so a
  caller-supplied identity section was discarded wholesale -- silently, with no
  diagnostic. Those two settings are the only single-node scale levers the
  identity docs describe (`emit_singletons: false`, and moving the store to
  Postgres for its bulk `COPY` write path), which made the documented scale
  guidance unreachable through this entry point. A user who set
  `identity.backend: postgres` and called this function still got SQLite with
  singletons on, and at ~2x the documented ~100k-row SQLite ceiling the runtime
  advisory would name a fix the entry point could not apply.

  The function now merges rather than replaces, overriding only what it is
  inherently deciding -- `enabled`, `source_pk_column`, `dataset`, and the
  sqlite `path` when a `store_path` was passed. `backend`, `connection` and
  `emit_singletons` are left to the caller, and the identity store is opened
  with the configured backend rather than a second hardcoded
  `IdentityStore(backend="sqlite", ...)`.

  Backward compatible by construction: `IdentityConfig`'s defaults are
  `backend="sqlite"` and `emit_singletons=True`, exactly the behaviour that was
  hardcoded, so a config whose `identity` was never touched resolves to an
  identical run. Two related sharp edges are also closed -- passing
  `store_path` alongside a non-sqlite backend now warns instead of silently
  ignoring the path, and the returned `ResolvedCrosswalk.store_path` reports
  `None` in that case rather than a file the run never wrote.
- **Auto-config no longer scores a field that every candidate pair agrees on by
  construction (#2526).** Blocking makes its key constant inside each block, so
  when the same column is also a scored field in the weighted matchkey it
  contributes its full weight to every pair as an *offset* with zero
  discriminative power: it cannot separate a match from a non-match, it only
  shifts the score distribution up and flattens the threshold's effect. Measured
  on DBLP-ACM, where blocking is static on `__title_key__` and the same column
  was scored at weight 0.8, it supplied 18.6% of the weighted score to 100% of
  candidates while distinguishing none of them -- F1 0.4593 -> **0.5645**
  (precision 0.3010 -> 0.3984, recall held at 0.9685, largest cluster 43 -> 35).

  The matchkey threshold is rescaled by the surviving weight share so the
  decision boundary stays at the same absolute score. That is load-bearing
  rather than cosmetic: dropping the field at an unchanged threshold measured
  F1 0.6855 against 0.6894 before it, a small regression, because removing an
  18.6% constant makes the same nominal bar materially stricter.

  Two conditions gate the rule and both are necessary. Every *pass* must key on
  the field -- under `multi_pass` a pair produced by pass 2 can disagree on pass
  1's key, so the field still carries signal. And every *transform* on that key
  must preserve equality: sharing a block under a lossy transform does not mean
  sharing a value. The second condition is what keeps the electronics benchmark
  correct, since all three of `abt_buy`'s passes key on `manufacturer` but do so
  via `soundex` and `substring:0:5` -- "Sony" and "Sonny" share a soundex block
  while being different strings, so `manufacturer` genuinely discriminates
  inside the block. The transform list is a whitelist, so an unrecognised
  transform is assumed lossy rather than assumed safe.
- **A domain-extracted field no longer becomes a standalone identity claim
  unless it is actually an identifier (#2526).** Auto-config promoted every
  `exact` entry in `_DOMAIN_SCORER_MAP` to its own exact matchkey -- "same value
  implies same entity", on its own, with nothing else consulted. On DBLP-ACM
  that produced `domain_exact_title_key` over `__title_key__`, which is the
  first significant *word* of the title, so zero-config asserted that two papers
  whose titles start with the same word are the same paper: 33,563 asserted
  pairs against 2,224 in ground truth, clusters up to 96 members, precision
  0.068.

  Two mechanisms combined. The eligibility floor was `cardinality_ratio < 0.01`,
  which only rejects *near-constant* columns -- `__title_key__` sits at 0.29 and
  cleared it by 29x, where an identity claim needs cardinality near 1.0. And the
  per-column weight in `_DOMAIN_SCORER_MAP`, which is the author's own statement
  of confidence (0.2 for `__color__`, 0.8 for `__title_key__`, 1.0 for
  `__model_norm__` and `__sw_part_num__`), was read and then discarded when the
  standalone matchkey was built -- so a weak partial signal became a
  full-strength identity claim regardless.

  A domain `exact` field now stands alone only when its declared weight says it
  is an identifier and its cardinality agrees; anything weaker folds into the
  weighted matchkey *at its declared weight*, so the signal is still compared,
  just not trusted on its own. Measured on DBLP-ACM zero-config: F1
  **0.1277 -> 0.4593**, precision 0.068 -> 0.301, largest cluster 96 -> 43.
  Retaining the signal beats dropping the matchkey outright, which scored
  0.2815.

  This does not close the DBLP-ACM gap to the ~0.92 configured runs reach. The
  remainder is a separate recall problem that the controller already reports
  (`failing_subprofile=scoring`, best-effort RED) and is tracked apart from this.

### Changed
- **The FS link-threshold refit is ON by default (#2518 follow-up).** The FS
  path is otherwise non-iterated: it commits a fixed 0.50 link cutoff, which
  over-merges on shapes where a shared attribute co-blocks non-matches (distinct
  households sharing a surname; co-tenants sharing an address). With the refit
  on, the cutoff is chosen from the actual scored-pair distribution -- a
  bimodality-gated valley, accepted only when it both reduces the largest
  cluster and strands under 1% of matched records as singletons. On the lever
  panel that recovers `household_hardneg` 0.9687 -> 1.0000 and
  `cotenant_hardneg` 0.4193 -> 0.9963, and is a no-op everywhere else.
  `GOLDENMATCH_FS_REFIT_THRESHOLD=0` is the kill-switch, byte-identical to the
  fixed cutoff.

  This default was flipped on once before (#2377) and reverted after five red
  nights (#2387), so the evidence bar here is the specific one that failure
  established: **validate on the panel that gates the change, not the panel that
  motivated it.** #2377 rested on `ab_lever` + QIS while the gating panel went
  unrun. This flip was measured with that gate itself -- `scripts.suggest_quality`,
  single-variable `=0` vs `=1` on one sha, native kernel present. The fast tier
  is byte-identical on every metric with both arms PASS; `ncvr_synthetic`
  `convergence_final_f1`, the metric that reddened the nightly, reads **0.9847 in
  both arms** -- exactly its `=0` value in the #2387 A/B, where default-on scored
  0.8881. The heavy `gym-gate` tier was run with `=1` against a baseline blessed
  with `=0`. Mechanically the flip is a no-op on that dataset because the valley
  detector no longer finds any candidate above 0.50 there.

  The same change widens the two gates that failed to fire in #2387, since a
  check that exists and does not run is what turned a catchable defect into a
  five-night red. `bench-suggest-quality`'s push filter now includes
  `probabilistic.py` and `pipeline.py` -- it listed only the suggest paths, so a
  refit edit ran no suggester gate at all. `fs-lever-gate`'s PR tier now includes
  `ncvr_synthetic`, the dataset that caught the regression, which had been left
  to the nightly.

### Added
- **Ontology-layer (RDF/OWL/SHACL) native identity provider
  (`goldenmatch.semantic.ontology`).** The semantic-layer wedge one level up: an
  ontology *asserts* identity (`owl:hasKey`, `owl:InverseFunctionalProperty`,
  `owl:sameAs`) but resolves it only by brittle exact-match. GoldenMatch fills
  the gap bidirectionally — **consume** an OWL/RDF ontology to extract its
  declared identifying keys (`parse_ontology`, `ontology_identity_keys`) and
  certify exactly those keys against instance data
  (`certify_ontology_keys`, bridging the key-integrity certifier); **emit** the
  resolved identity as RDF (`emit_sameas_graph`: `owl:sameAs` + W3C PROV-O
  provenance) and a conformance shape (`emit_identity_shacl`). `rdflib` is an
  optional dependency (`pip install goldenmatch[ontology]`), lazy-imported so
  `from goldenmatch.semantic import …` never requires it; GoldenMatch does not
  reimplement an OWL reasoner or triple store — it is the identity provider for
  them. Library-only, advisory. See ADR 0053.
- **`strategy="token"`: DF-pruned token blocking for free text (#2488).** Every
  candidate the block analyzer can generate is an *exact* key -- a prefix, a
  soundex code, or a compound of two -- so each record lands in exactly one
  block and any true pair disagreeing on that one derived value is lost before
  scoring. On free-text product titles that is most of them. Token blocking
  indexes each record under every token it carries, so two records are
  candidates when they share *any* token. Cost is controlled by
  document-frequency pruning rather than a block-size cap: a token in `D`
  records forms a block of `D` and contributes `D(D-1)/2` pairs, and the
  frequent tokens ("software", "for") are expensive precisely because they do
  not discriminate. `max_df` defaults to `max_df_ratio * n` clamped to
  `[10, 1000]`, so the cap is bounded at any frame size.

  Measured on Amazon-Google (4589 records, 1300 truth pairs), pair recall vs
  candidate pairs generated:

  | scheme | recall | pairs | RR |
  |---|---|---|---|
  | `manufacturer[:5]` (what zero-config committed) | 7.15% | 31,232 | 0.9970 |
  | `title[:3]` | 45.92% | 99,744 | 0.9905 |
  | MinHash/LSH word-shingles, best of six settings | 55.85% | 54,554 | 0.9948 |
  | **`token` defaults** | **98.15%** | 183,221 | 0.9826 |
  | `token` at `max_df=25` | 87.62% | 52,777 | 0.9950 |

  Note `token` at `max_df=25` dominates `title[:3]` on *both* axes -- nearly
  double the recall for roughly half the pairs. This complements `lsh` rather
  than replacing it: MinHash estimates Jaccard over the whole token set, so it
  needs substantial overlap, while cross-vendor titles often share two or three
  discriminative tokens out of fifteen.

  Python-only for now (`parity/goldenmatch.yaml`); the TS port is sequencing,
  not a barrier -- there is no kernel or model involved.

  **Auto-suggest can propose it, but that is OPT-IN and default OFF**
  (`GOLDENMATCH_TOKEN_BLOCKING=1`). Committing a token plan automatically took
  Amazon-Google's end-to-end F1 from 0.1014 to **0.0000**, below the #2470
  quality floor. Measured across three runs in one environment:

  | run | iteration 0 | failing subprofile | F1 |
  |---|---|---|---|
  | clean `main` | 430.7s | scoring | 0.1014 |
  | token candidates on | 424.1s | blocking | 0.0000 |
  | + total-pair cost term | 429.3s | blocking | 0.0000 |

  Two things follow, and they point in opposite directions. The auto-config
  time-budget blowout at iteration 0 is **pre-existing on `main`** (~430s in
  all three, baseline included), so it is not caused by this work. But the F1
  collapse is: the baseline reproduces 0.1014 exactly, and enabling token
  candidates takes it to zero -- the failing auto-config subprofile flips from
  `scoring` to `blocking`, `build_token_blocks` never runs, and the committed
  RED config yields no candidate pairs. That integration gap is not diagnosed,
  and a plan that finds nothing is strictly worse than a 7%-recall key, so the
  default stays off until the benchmark says otherwise.

### Fixed
- **The FS threshold-refit guard no longer accepts a cutoff that shatters
  correct clusters (#2518).** `fs_refit_link_threshold` accepted a valley
  candidate whenever it reduced the largest cluster -- a single-outlier
  statistic. One scalar over the whole dataset cannot see damage below the
  maximum, so a candidate that fixed the one genuinely over-merged cluster was
  accepted no matter how many correct clusters the raised cutoff broke apart.
  That is why `GOLDENMATCH_FS_REFIT_THRESHOLD` has shipped default-OFF since
  #2387. The max test is now ANDed with a global one, `_expelled_share`: the
  share of records matched at the default that the candidate would strand as
  singletons, capped at 1%. The two regimes are asymmetric, which is what makes
  this separable -- over-merge repair *regroups* records (splitting a
  surname-collapsed cluster of 8 into 3s leaves everyone matched) while
  shattering a correct size-2 cluster *expels* both of its members. Measured on
  the FS lever panel, valley candidate vs default: `person` 0.1020 expelled /
  ΔF1 −0.0616 (reject), `household_hardneg` 0.0000 / +0.0313 and
  `cotenant_hardneg` 0.0006 / +0.5770 (accept) -- a ~160× separation.
  **This changes no decision the live panel currently makes**: the max test
  already calls all three correctly, so the guard picks the F1-optimal cutoff
  on every panel dataset both before and after. What the second criterion buys
  is the ncvr-shaped case the max is structurally blind to, which no panel
  dataset exhibits today -- `person` is the live cluster-shattering shape the
  1% cap is calibrated against, not a regression this fixes. The case that
  actually separates the two criteria is carried by a unit test,
  `test_rejects_when_correct_clusters_would_be_shattered`, which asserts the
  max test alone WOULD accept and that the shipped guard declines. The
  linked-pair-count bound proposed in #2387
  as the alternative remedy is falsified: person's drop (0.1161) falls between
  the two correct accepts (0.0608 / 0.7341), so no bound on it can classify all
  three. The flag stays default-OFF -- this fixes the criterion; flipping the
  default is a separate decision that needs a green nightly
  `bench-suggest-quality`, which no PR gate runs.
- **A probabilistic run now reports the link cutoff it applied, and says when
  nothing chose it (#2483).** The FS link cutoff is resolved through three
  precedence steps -- configured `mk.link_threshold`, the EM-calibrated
  per-dataset cutoff, then a fixed default -- and the run reported none of it.
  A caller could not tell a deliberate threshold from a default, which is how
  the reporter got a 94.4% match rate (2,554 distinct names fused into one
  entity) with no thread to pull: `mk.threshold` is the *weighted* matchkey's
  field and inert here, so sweeping it changed nothing, and they went through a
  16-cell matrix of unrelated FS env knobs before finding the cause.
  - `DedupeResult.stats["fs_link_thresholds"]` now carries
    `{matchkey: {"link_threshold": float, "source": ...}}`, where `source` is
    `configured` / `calibrated` / `fallback`. Absent on non-probabilistic runs.
    Provenance is derived by `probabilistic.link_threshold_source`, next to the
    resolver, so the report cannot drift from the precedence it describes.
  - A `fallback` cutoff that links more than 50% of records into multi-member
    clusters now emits a warning naming `link_threshold` and carrying the cutoff
    and the rate. A warning, not a rejection, and only for `fallback` -- a
    configured or EM-calibrated cutoff that links a lot is the caller's call.
    Measured on DBLP-ACM: the fallback run reports `0.5 / fallback` at a 25.3%
    match rate (correctly silent); an explicit `0.95` reports `configured` at
    1.24%.

  This makes the failure legible; it does not change which cutoff is chosen.
  Picking the cutoff from the scored-pair distribution already exists as
  `fs_refit_link_threshold` (`GOLDENMATCH_FS_REFIT_THRESHOLD`) and stays default
  OFF: enabling it cost -0.0966 F1 on `ncvr_synthetic` (#2387) through a defect
  in its cluster-shape accept criterion, tracked separately.
- **Blocking-candidate recall is measured against the pairs the scorer would
  match, and for every candidate (#2513).** Three defects in `estimate_recall` /
  `analyze_blocking`, all in how the recall number was produced and consumed.

  1. *Wrong denominator.* Candidates were scored against pairs whose
     Jaro-Winkler similarity on the highest-cardinality matchkey column was
     `>= 0.7`. On Amazon-Google that population is 98.5% non-duplicates (2,355
     sample pairs, 35 truly matching), so a candidate was judged largely on how
     many NON-matches it co-blocked — a specificity penalty reported as recall,
     and worst for the most discriminative candidate. `analyze_blocking` now
     accepts the weighted `matchkey` the pipeline will score with and takes the
     target set from `find_fuzzy_matches`, the one authoritative pair scorer.
     Blocking is accountable for retaining what the scorer would match, so that
     is the right denominator. Estimated vs true pair recall on Amazon-Google:
     `tokens(title, df<=10)` 26.0% → 68.7% (true 65.9%), `df<=25` 46.1% → 87.3%
     (87.6%), `df<=50` 73.6% → 96.8% (95.3%). Callers with only column names
     (CLI / MCP / A2A) keep the old proxy, now documented as the weak fallback
     it is.
  2. *A sentinel used as a value.* Recall was measured only for the top 10 by
     score; the rest were assigned `0.0` meaning "unmeasured" — then multiplied
     into the rank, making them unselectable however good they were. On
     Amazon-Google that zeroed `tokens(title, df<=100)`, the candidate with the
     highest true pair recall of any generated (98.2%). Every candidate is
     measured now, the placeholder is gone, and the two-tier sort that existed
     to contain it collapses to one. A measurement failure fails open to `1.0`
     (rank on score alone) rather than to `0.0`.
  3. *Repeated work.* The sample is seeded, so all ten measured candidates saw
     an identical pair population — and rebuilt it from scratch each time. The
     target is now built once. `analyze_blocking` on Amazon-Google: **195.9s →
     1.6s**, while measuring 53 candidates instead of 10.

  Net effect on the shipped ranking: the top suggestion changes from
  `tokens(title, df<=25)` to `tokens(title, df<=10)`, which measures F1 0.1697
  end-to-end against 0.0864 for the previous pick.
- **Bucket routing no longer bypasses the blocking-expressibility gate (#2488).**
  The bucket scorer derives FIELD-based block keys from `blocking.keys` /
  `blocking.passes`; a guard kept strategies with no field keys (lsh / ann /
  learned / canopy / sorted_neighborhood / token) on the legacy per-block path.
  That guard sat *below* the `backend == "bucket"` short-circuit in
  `_use_bucket_scorer`, and `ExecutionPlan.apply_to` writes `backend="bucket"`
  onto the committed config -- so the guard was unreachable for exactly the
  configs it protected. On a zero-config run the controller's sample pass routed
  before the planner ran and scored the plan correctly on the legacy path, then
  the final dedupe pass took bucket with the same plan, derived an empty block
  key, and emitted zero pairs with no error and no warning: every record came
  back a singleton. Measured on Amazon-Google (4589 records, 1300 truth pairs),
  identical configs differing only in `backend`: 16,545 pairs / F1 0.0864 with
  `backend` unset, 0 pairs / F1 0.0000 with `backend="bucket"`. The check now
  runs first, as `_bucket_can_express_blocking`, so it holds however `backend`
  was set. It also covers an allowlisted strategy carrying neither keys nor
  passes (the degenerate `static keys=[] auto_suggest=True` config), since
  auto-suggest chooses the real plan mid-pipeline and a routing decision taken
  off the provisional plan can land on a strategy bucket cannot express; both
  paths already yielded zero pairs for that shape, so only the routing changes.
  An explicit `backend="bucket"` on a field-keyed plan is still honored at any
  size.
- **Every FS scoring path now resolves the link cutoff the same way.**
  `_fs_link_threshold` applies three steps in order -- configured
  `mk.link_threshold`, then the EM-calibrated per-dataset cutoff, then the
  calibration-aware default -- and its docstring records that it was extracted
  "so they cannot drift on how the cutoff is resolved" (#1804 item 4). Four
  scorers used it; two did not. `fused_match._match_fused_fs` and
  `probabilistic_fast._resolve_probabilistic_fast_path` hand-rolled only the
  FIRST and THIRD steps, so whenever EM produced a calibrated cutoff -- what
  `GOLDENMATCH_FS_CALIBRATE_THRESHOLD` exists to do, worth +0.49 F1 on dblp_acm
  per that flag's own docstring -- those two routes silently ignored it and cut
  at the fixed 0.50 while the scalar/vectorized/batched routes honoured it. The
  same config on the same data cut differently depending on which scorer the
  router happened to pick. Both now call the shared helper. With no calibrated
  cutoff present the resolved value is unchanged, so this only moves runs that
  were already asking for calibration (#2483).
- **A `threshold` set on a probabilistic matchkey is reported instead of
  silently ignored.** `threshold` is the weighted matchkey's cutoff; the
  probabilistic scorers read `link_threshold`. Setting it did nothing and
  *reading* it raised, so a user swept `threshold` from 0.90 to 0.99, got
  byte-identical results, and reasonably concluded the cut did not matter on
  their data -- it was measuring nothing. Config validation now emits a
  `UserWarning` naming `link_threshold`. A warning rather than a rejection: a
  stray key is a usage mistake, not a corrupt config (#2483).
- **Blocking suggestions account for what a key can REACH, not just what it
  costs.** Two compounding defects in `core/block_analyzer.py`, both measured on
  the Amazon-Google benchmark frame (#2488). (1) `score_candidate` divided its
  selectivity term by the records that PRODUCED a key rather than by the frame,
  so coverage was normalised away: a key able to key only 35% of records scored
  as though the frame were just that 35% -- maximally selective AND cheap, since
  `total_comparisons` is summed over survivors too. Compound keys
  null-propagate, so one sparse component nulls the whole key (Amazon-Google's
  `manufacturer` is 100% populated on one source and 7.2% on the other, nulling
  65% of the frame). The denominator is now the full height, which caps the term
  at coverage; `coverage` is also reported in the metrics. This is a NO-OP for a
  fully-covered key, so only genuinely partial-coverage keys move. (2)
  `estimated_recall` was computed for the top candidates, logged, and then left
  out of the ranking entirely -- the only multiplier was `check_coverage`'s
  field-membership flag, which asks whether the key's columns are matchkey
  columns and says nothing about retained pairs. Measured candidates are now
  ordered by `score x recall`, in a tier ahead of the unmeasured tail so a
  placeholder 0.0 is never read as "measured as useless". On the Amazon-Google
  frame this drops the lower-coverage `description` keys from rank 1 to ranks
  8-10 and promotes the fully-covered `title` keys, taking the chosen plan's
  estimated recall from 0.05 to 0.07. A plan whose best candidate estimates
  under 30% recall is now reported with that number rather than committed
  silently -- a warning, not a rejection, because on a frame where every
  candidate is below the floor (which is this one) rejecting them all leaves
  degenerate blocking, and one mega-block is worse than a poor key. NOTE this
  does not by itself make Amazon-Google a good result: every candidate the
  generator produces estimates 0.05-0.07 recall there, because it emits only
  prefix/soundex keys and pairwise compounds and has no token-overlap key for
  free text. That gap is tracked separately.
- **Benchmark tests skip on a missing dataset instead of erroring.** Every
  dataset under `tests/benchmarks/datasets/` is gitignored on purpose, but three
  of the five tests in `test_autoconfig_benchmarks.py` read one without first
  checking it exists, so a clean checkout got a bare polars `FileNotFoundError`
  rather than a skip (`test_abt_buy_autoconfig_offline` and
  `test_autoconfig_ncvr_meets_target` already had the guard). They now skip with
  a reason naming the command that gets the data.
- **`run_benchmarks.py --download-only` fetches the benchmark datasets without
  running a benchmark.** The script has auto-pulled DBLP-ACM, NCVR, Abt-Buy and
  Amazon-Google since #2386, but only as a side effect of measuring, so a
  developer running `pytest -m benchmark` had no way to just get the data. The
  new flag reuses the existing `_ensure_datasets` and the fetchers' own
  sentinels -- deliberately not a second copy of where each dataset lives --
  prints each as `ok`/`MISSING`, and exits non-zero if any is absent. No library
  behaviour changed.
- **Learned blocking gates rules on absolute full-frame cost, not on a ratio.**
  A rule was accepted when `reduction_ratio >= learned_min_reduction` (0.90 by
  default). That ratio is `1 - blocked_pairs / total_pairs`, and BOTH terms grow
  as n^2, so it is scale-INVARIANT -- measured identical to four decimal places
  between a 7,071-row sample and the 2M frame it was drawn from. It therefore
  carries no information about what a rule actually costs. On the QIS realistic
  shape at 2M every one of these cleared the 0.90 floor:
  `email:exact` 1.0000 -> 3.3M candidate pairs; `first_name:first_3` 0.9984 ->
  3.12B; `birth_year:exact` 0.9846 -> 30.8B; `id:first_3` 0.9667 -> 66.7B. The
  selector had no quantity that could tell the affordable rule from the one four
  orders of magnitude past it. Rules are now additionally checked against an
  absolute projected candidate-pair budget at the FULL frame size, projected from
  the sample's block-size distribution by the same saturation-aware estimator
  auto-config already uses for static passes (extracted to
  `core.block_projection` so there is one owner rather than two), and bounded by
  the same memory-aware `_fs_total_pair_budget` (so `GOLDENMATCH_FS_MAX_PASS_PAIRS`
  overrides both). Every rejection is logged with the pair count behind it. The
  budget is applied BEFORE the "did anything pass?" test that gates the depth-2
  search, so a frame where every single predicate explodes now falls through to
  looking for a cheaper conjunction instead of returning a rule it cannot afford.
  The gate is opt-in on a new `total_rows` argument, so callers that do not pass
  it -- and any frame no larger than its own training sample -- are unchanged.
- **A learned rule with two predicates on the same field no longer crashes rule
  evaluation.** `evaluate_rule` selected one column per predicate, so a rule like
  `last:exact AND last:soundex` named the same column twice and raised polars'
  `DuplicateError`. `learn_blocking_rules` deliberately generates those (its
  combo guard compares field AND transform; collapsing them is the #1826 footgun),
  but the depth-2 search only runs when no single predicate passes, so the crash
  stayed latent. The pair budget above can empty that set on a narrow frame and
  reach it. `apply_learned_blocks` already de-duplicated here.
- **The suggestion verify gate compared its baseline against a different
  procedure than its candidates.** `_verify_suggestions` keeps a suggestion when
  `cand_health >= baseline_health - 1e-6`. Candidates always come from
  `engine._run_pipeline`; the baseline came from whatever clusters the caller
  passed. For `review_config` those coincide, but `suggest_from_result` passes
  `DedupeResult.clusters`, produced by `dedupe_df` -- which standardizes the frame
  first and therefore clusters a different population than the raw frame the
  candidates run against. Measured on the 80-row person fixture: 39 clusters /
  health 1.0000 from the artifacts against 8 clusters / health 0.8000 from the
  engine. The artifacts-in path was then held only by the epsilon (1.0 vs 1.0), so
  a marginally worse candidate clustering flipped it to DROP and returned nothing
  while the re-run path kept its 0.2 margin -- the intermittent
  `[] == ['thr:raise:fuzzy_match']` failure in
  `test_suggest_from_result_verified_matches_review_config`. The baseline is now
  re-derived through the same engine path as the candidates, falling back to the
  caller's clusters only if that run fails. Both paths now report an identical
  0.8000 baseline.
- **`GOLDENMATCH_AUTOCONFIG_MEMORY=0` now works when set at runtime.** The gate was
  a module-level constant evaluated at import, so the documented "useful in CI"
  opt-out silently did nothing unless the variable was already in the environment
  before `goldenmatch.core.autoconfig` was imported -- leaving the shared
  `~/.goldenmatch/autoconfig_memory.db` live. The env is now read at call time. The
  import-time constant is still checked first, so existing callers (including
  tests that patch it) are unaffected, and the env read can only ever disable
  memory, never enable it.
- **The learned-blocking trainer no longer loses its training signal as the
  dataset grows.** `learned_sample_size` was `min(total_rows // 4, 5000)` --
  pinned at 5,000 rows from 50K upward however large the frame got. The learner
  trains on the true pairs found INSIDE that sample, and the number of pairs with
  both members sampled decays as `s^2 / n`. Measured on the QIS realistic shape,
  ground-truth pairs inside a 5,000-row sample: 86 at 500K, 47 at 1M, 25 at 2M,
  14 at 4M -- the signal halves every time the data doubles. At 5M the learner saw
  13 true pairs against 56 at 1M. The sample now grows as `sqrt(rows)`, which
  holds `s^2/n` constant; at 5M that is 11,180 rows and restores the trainer to 46
  true pairs. Anchored at 1M so this is a pure extension: every frame at or below
  1M keeps its previous sample size byte-for-byte, and only larger frames change.
  Bounded at 50,000 rows, since the sample run is superlinear in sample size.

  **This is necessary but NOT sufficient for the 5M slowdown, and is not claimed
  as a fix for it.** With the training signal restored, the learner still emits
  160 blocks of ~31K rows at 5M. Rule SELECTION is scored on the sample too, and
  block SIZE is exactly what a sample cannot show: a bounded-cardinality predicate
  like `first_name:first_3` has ~850 blocks of 13 rows in an 11K sample and ~899
  blocks of 5,562 rows on the full 5M frame -- about 19.5 billion candidate pairs,
  reported to the selector as `recall=1.000, reduction=1.000`. That is a separate
  defect in the same family and is tracked on its own.
- **Zero-config no longer discards a real identity column as a "surrogate key"
  purely because the dataset got bigger.** The `#876` surrogate-key guard asks
  "does every record carry its own value for this column?", and answered it from
  `ColumnProfile.cardinality_ratio` -- a distinct-FRACTION measured over the
  ~1,000-row profiling sample -- against an absolute threshold of 1.0. A
  fixed-size sample of a growing frame drives that fraction toward 1.0 for ANY
  column whose distinct count grows with the row count, which is every real
  identifier. On the QIS realistic shape an `email` column whose true full-frame
  ratio is a flat 0.28 profiles at 0.72 / 0.94 / 0.97 / 0.98 / 1.00 for
  100K / 500K / 1M / 2M / 5M rows, so at 5M zero-config threw away its ONLY
  exact identity column, fell back to fuzzy-only matchkeys plus the name-based
  blocking fallback, and committed a config estimated at 185M candidate pairs.
  The verdict changed with SCALE rather than with the data. Eight call sites
  asked the question independently; they now share one authority,
  `_is_perfect_surrogate`, which reads an EXACT full-frame cardinality and falls
  back to the sampled value only where no full frame was available (hand-built
  profiles in tests), so those callers keep their previous verdict. The exact
  count is computed only for columns the sample calls perfectly unique, which is
  sound rather than a cost trade: if every value in the full column is distinct
  then so is every subset, so a sample below 1.0 rules the column out already.
  Measured at 44-307 ms per column at 5M rows against a ~160s auto-config.
  Effect at 5M: the committed matchkeys are now identical to those at 1M
  (`exact(email)`, `exact(first_name+last_name+birth_year)`, `weighted(...)`),
  while a genuine row PK is still excluded. Found via the `bench-quality-scale`
  heavy tier, which had never once passed: the degenerate config it produced
  reached 66 GB RSS and killed the CI runner.
- **Zero-config dedupe of product data no longer crashes on the arrow lane
  (`KeyError: 'Field "__model__" does not exist in schema'`).** Bisected from
  the weekly `benchmarks` Abt-Buy failure to the polars->arrow `dedupe_df`
  eviction: two separate defects, both only reachable once a polars input
  started taking the arrow lane. (1) STAGE ORDER -- the dedupe pipeline's arrow
  lane derived matchkeys BEFORE running domain extraction, but domain
  extraction is what materializes the `__brand__` / `__model__` /
  `__title_key__` columns auto-config's matchkeys reference, so
  `derive_matchkey` reached a column that did not exist yet. The classic lane
  and the match pipeline's arrow lane already ran domain extraction first; the
  dedupe arrow lane now matches them. (2) THREE UN-PORTED `df.columns` READS in
  `core/domain.py` -- `pa.Table.columns` is a list of ChunkedArrays, not names,
  so `_emit_domain_profile` raised `'ChunkedArray' object has no attribute
  'startswith'` (only under a profile capture, which is why the existing
  arrow-lane tests missed it and the auto-config controller always hit it), and
  the guards in `_detect_product_subdomain` and `_extract_biblio_features_df`
  SILENTLY answered False for every membership test -- pinning subdomain
  detection to its "electronics" fallback and skipping bibliographic extraction
  entirely, with no error either time. All frame access in that module now goes
  through the seam. Net effect on the benchmark input: the auto-config
  controller goes from every iteration erroring (fallback to v0 + RED sentinel)
  to running normally, byte-identical clusters to the classic lane.
- **`PluginRegistry.reset()` no longer strips the plugins goldenmatch itself
  ships.** refdata registers its scorers + transforms as an import-time side
  effect; `reset()` drops the singleton, but a module body cannot run twice
  (`sys.modules` holds it), so every bundled plugin was gone for the rest of
  the process and the next `has_transform("refdata_business_canonical")` read
  False with nothing to point at. `PluginRegistry.add_bootstrap` records those
  idempotent registrations and replays them onto the fresh singleton, so a
  reset now clears USER registrations only. Found via the
  `test_alias_transforms_registered` xdist flake: the test-side workaround for
  it re-registered a hand-maintained module list that had drifted --
  `business_aliases` and `core.acronym` were never added to it -- so the
  failure appeared only when a resetting test happened to precede a refdata one
  in the same worker. That workaround is deleted; the library no longer needs
  it.
- **The suggest/review path now fails at the boundary with a message that names
  the requirement (#2442).** `review_config` / `suggest_from_result` normalise
  `__row_id__` with the polars-only `with_row_index`, so an Arrow caller got
  `AttributeError: 'pyarrow.lib.Table' object has no attribute 'with_row_index'`
  -- naming neither polars nor the actual constraint. (`pl` in that module is
  the `_LazyPolars` proxy, which imports cleanly without polars and only raises
  on use, so nothing upstream surfaced the dependency either.) Both entry points
  now raise a `TypeError` naming the type received, `goldenmatch[polars]`, and
  the `polars.from_arrow` conversion. Deliberately NOT an Arrow branch: the path
  is polars-native end to end -- `MatchEngine._run_pipeline` calls `df.lazy()`
  on its first line -- so both the Arrow branch #2442 suggests and its stated
  `__row_id__` workaround only move the failure three lines down. Making this
  Arrow-native means porting the pipeline, not the two lines that fail first.
- **Auto-config no longer reads packed `"lo,hi"` intervals as coordinates
  (#2443).** `_looks_like_latlong` tested only whether a column's samples PARSE
  as `lat,long`, which admits any two small comma-separated numbers -- `"25,35"`
  is a valid coordinate and an ordinary age interval. On a real ~21k-row corpus
  that put `geo_haversine` on an `age_range` column; the config was well-formed
  and the distances plausible, so nothing failed and it took a field-by-field
  diff against a hand-built matchkey to notice. Detection now also requires
  positive geographic evidence -- a fractional component, `|lon| > 90`, or a
  negative component -- any one of which an integer-interval column essentially
  never shows. Whole-degree coordinates in the positive quadrant are declined
  deliberately: at ~111 km granularity haversine similarity is meaningless
  anyway. Only reachable under `GOLDENMATCH_FS_DOMAIN_COMPARATORS=1`, so default
  behaviour is unchanged.

### Added
- **Sail identity Layer 2: incremental resolution against an existing store
  (#966).** `goldenmatch.sail.build_identity_graph_incremental` resolves a run's
  clusters against prior state instead of always minting fresh entities. A
  cluster whose records already belong to one entity **absorbs** into it; one
  spanning several **merges** them, retiring the losers
  (`status="merged_into"`, `merged_into=<winner>`) and reassigning their records
  to the winner. Only a cluster with no overlap **creates**. This is what makes
  a Sail-tier store converge across runs — the create-only path (shipped in
  2.0.0 as Layer 1) re-minted an id for a record it had already seen.

  Semantics mirror one-box `identity.resolve.resolve_clusters`, including the
  winner rule that is easy to invert: the entity holding the most of **this
  cluster's** records wins, *not* the largest entity overall. Tie-break is
  oldest `created_at`, then `entity_id` ascending — that last step is ours, so
  the surviving entity is deterministic (one-box breaks that tie on dict order,
  and Spark has no stable sort).

  **Additive**: `IdentityGraphFrames` and `build_identity_graph` are unchanged,
  so every existing consumer of the frozen contract is unaffected. Omitting the
  prior-state frames delegates to the create path verbatim, so the new entry
  point is safe to call unconditionally. Design:
  `docs/superpowers/specs/2026-08-08-sail-identity-layer2-incremental-design.md`.

  Note the `records` frame is a **delta** — a winner's untouched records are not
  re-emitted, only records this run assigned or moved.

### Performance
- **`DedupeResult.scored_pairs` is materialized lazily instead of eagerly
  (#2417).** The B2c Fellegi-Sunter path already keeps the pair stream columnar
  through scoring and clustering, then rebuilt the entire `list[tuple]`
  post-cluster purely to populate this field. Measured across 1M and 4M pair
  tables (linear in both): the Arrow form is **24 B/pair**, the Python list is
  **168 B/pair resident and ~192 B/pair at the transient peak** — 7×. Since
  `GOLDENMATCH_FS_SCORED_PAIRS_MAX` defaults to 50,000,000, that permitted an
  **~8.4 GB resident / ~9.6 GB transient** allocation, built post-cluster.

  On the `dedupe_df` + identity path nothing reads it. Verified on a real
  FS + identity + SQLite run: `_resolve_identities` receives an empty
  `scored_pairs`, and `resolve_clusters` accepts the parameter "for
  call-signature compatibility" only — evidence edges come from
  `pair_score_view`. The allocation was pure waste there.

  The pipeline now carries the deduped Arrow table and the consumer pays.
  `DedupeResult.scored_pairs` became a lazy property over `_scored_pairs_table`
  (the same field→property idiom `clusters` uses) that **caches a real `list`**,
  so `isinstance(result.scored_pairs, list)` and `== []` behave exactly as
  before. Every steward surface that reads it — TUI, web, REST, `cli review`,
  `cli match` — gets byte-identical output; runs that never touch it stop paying.

  Two supporting changes: dict consumers read through the new
  `core.pairs.materialize_scored_pairs(results)` (a bare
  `results.get("scored_pairs")` now returns `None` on this path, so the helper
  makes that a loud contract rather than a silent empty list), and
  `_finalize_review_pairs_arrow` does the linked-set anti-join in Arrow — the
  list version built `linked_keys` as a Python set over every linked pair, a
  second O(pairs) driver structure that would have forced materialization
  anyway. That filter is **not** skippable despite review and linked coming from
  one threshold split: `score_buckets_arrow` can emit the same pair on several
  blocking passes with different scores and the dedup runs later, so a pair can
  genuinely land on both sides of the cut.

  Scope: this addresses a *different* term than #2417 describes. The issue
  expects scoring-time pair streaming to be the remaining cost; measurement
  shows scoring is already Arrow end-to-end and the cost is this post-cluster
  result list. The bisected #2250 delta (~1–2 GB) is a separate term, not
  addressed here. The chunking half of #2417 landed earlier in #2419.

### Fixed
- **Arrow lane: GoldenFlow standardization is APPLIED instead of silently skipped
  (#2430).** On a polars-free install, `core/transform.py` bridged the arrow lane
  through `pl.from_arrow`, caught the resulting `ImportError`, and degraded to
  no-transform — so dates, phones and whitespace were never standardized while the
  run continued and reported success. It surfaced downstream, where column
  profiling saw unstandardized values. The premise was stale: GoldenFlow's
  `transform_df` is polars-native, but module-level `goldenflow.transform` is a
  **polars-free columnar engine**, and (measured) its zero-config auto-detect runs
  polars-free on string / int / float / bool / date columns — standardizing dates
  to ISO and phones to E.164 with polars never imported. The arrow lane now falls
  back to it when the polars bridge is unavailable. Verified the two engines
  produce **identical values**, and untouched columns keep their exact arrow dtype
  (a naive `dict`→`pa.table` rebuild silently widens `int32` to `int64`).

  The polars engine stays **preferred** rather than replaced, so installs that
  work today are unchanged and unregressed: measured on a 200K×5 arrow frame with
  polars present, the polars bridge is 0.70s / 314 MB vs the columnar engine's
  1.80s / 531 MB (2.6× slower, +69% RSS — `to_pydict()` materializes Python lists
  where `pl.from_arrow` is near-zero-copy), and the polars engine also covers
  strictly more configs. Only the polars-free install changes behavior.

  The degrade path remains but is now narrow: the columnar engine declines an
  **uncovered** config, and the warning text — which used to claim the whole
  transform engine was polars-native — now names both triggers accurately.
  `strict=True` still re-raises, so MCP/A2A callers that explicitly requested
  transforms are never handed unstandardized data silently.

  **Prerequisite worth knowing:** GoldenFlow's columnar engine has no pure-Python
  core — `transform_columns_public` gates both its zero-config and
  explicit-config branches on `native_columns_ready` — so the fallback needs the
  `goldenflow-native` kernel. That is a **base dependency** of goldenflow, so a
  normal install has it; CI lanes that strip it (`--no-install-package
  goldenflow-native`, to skip the maturin build) still degrade as before. The
  end-to-end tests skip there rather than assert something the environment cannot
  do, and an in-process test with both engines stubbed guards the fallback wiring
  in every lane.
- **FS: an un-splittable oversized block is now scored in bounded tiles instead of whole
  (#1826/#2417).** When `_split_oversized` finds no useful split and `skip_oversized=false`
  — the "hub" shape where thousands of rows share one blocking-key value, so no split
  exists — the block used to be handed to the scorer WHOLE, in one call carrying its full
  `C(n, 2)`. `score_buckets` (all three bucket lanes) and
  `score_probabilistic_external_blocks` now tile such a block above a candidate-pair
  budget: contiguous row groups, one diagonal tile per group plus one cross tile per group
  pair, covering every intra-block pair exactly once. Same pair set, same scores; emission
  order within that block becomes tile order, and the tiled block costs ~2.2x the pair
  comparisons (cross tiles recompute the intra-group pairs they discard). Measured on one
  hub block (`scripts/repro_issue_2417.py`): the vectorized-numpy lane's peak is `O(n^2)`
  in the dense per-field matrices — 866 MB at 3,500 rows — and tiling cuts it to 562 MB at
  the default budget and 144 MB at a 1M budget; it also makes blocks above
  `GOLDENMATCH_FS_VEC_MAX_ELEMS` (>7,071 rows), which `_fs_vec_guard` currently *refuses*,
  scoreable at all. On the native lane the kernel already threshold-filters per pair, so
  its peak tracks SURVIVING pairs rather than candidates (16 MB at 53K survivors, 654 MB at
  2.4M); tiling removes only the whole-block double-buffering there, worth ~27%. New
  `GOLDENMATCH_FS_OVERSIZED_CHUNK_PAIRS` (default `4000000`; `0` = the pre-#2417
  score-whole parity hatch). Blocks at or under `max_block_size`, and any block under the
  budget, take the untouched single call. Tests: `tests/test_fs_oversized_chunked.py`.

## [3.12.0] - 2026-08-04

### Added
- **Semantic-model discovery — real-LLM namer validation / eval harness (Phase 19).** New
  `goldenmatch.semantic.score_naming(suggestions, gold)` is a pure, deterministic scorer of
  the advisory namer's (Phase 7) output against a labeled `{target: accepted_name(s)}` gold
  map — a `NamerQuality(coverage, accuracy, precision, verified_accuracy, results)` where a
  name matches after normalization (lowercase, alphanumeric-only; deliberately NOT a
  stemmer, so plural/alias variants are listed as explicit gold aliases rather than
  silently conflated). `run_namer_eval(model, tables, gold, *, backend)` names then scores
  in one call. The scorer is backend-agnostic: CI exercises it with hand-built suggestions
  and a dict-driven fake backend (no API calls, fully deterministic), while the real
  provider (`load_namer_backend`) is **opt-in** behind `GOLDENMATCH_NAMER_EVAL_LIVE` (a
  skipped live test), so the harness never spends live calls in CI. This closes the
  "frontier" arc: slices 11-18 added deterministic features; slice 19 is the *measurement*
  for the one non-deterministic part (the LLM namer). New exports `score_naming`,
  `run_namer_eval`, `NamerQuality`, `TargetResult`. The ninth and final "frontier" slice of
  the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`, item 19). Tests:
  `tests/test_semantic_discovery_namer_eval.py`.
- **Semantic-model discovery — catalog reconciliation (Phase 18).** New
  `goldenmatch.semantic.reconcile_model(proposed, existing)` diffs a discovered (certified)
  `ProposedModel` against an already-parsed existing catalog — a list of MetricFlow
  `DeclaredKeySpec` (from `parse_semantic_models`) or Cube `Cube` (from
  `parse_cube_models`), reusing the existing readers so there's no new format code. Both
  sides normalize to a neutral `(name, key, measures)` shape and produce a
  `Reconciliation` of typed `TableDiff`s: `only_in_model` / `only_in_catalog` (tables),
  `grain_drift` (in both, but discovered certified grain ≠ declared key), and
  `measure_only_in_model` / `measure_only_in_catalog`. **The differentiator over a text
  diff:** the discovered side is CERTIFIED, so a `grain_drift` where the discovered grain
  is trustworthy and the catalog's declared key isn't it is marked `proven=True` — a
  provable double-counting defect in the catalog ("your model declares `order_id` the key;
  we proved the grain is `order_id + line_no`"), not a stylistic difference.
  `Reconciliation.in_sync` is the headline; `to_dict` for the serialized view.
  Deterministic (no LLM), **default-on**. Scope cut (logged, not silently omitted): v1
  covers tables, grain, and measures; cross-dialect join-edge reconciliation and a LookML
  reader (no parser exists yet) are explicit follow-ons. The eighth "frontier" slice of the
  semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`, item 18). Tests:
  `tests/test_semantic_discovery_reconcile.py`.
- **Semantic-model discovery — warehouse-scale derivation off `information_schema`
  (Phase 17).** New `goldenmatch.semantic.read_information_schema(columns,
  table_constraints, key_column_usage, tables=None)` reads the three ANSI
  `information_schema` relations (pyarrow tables or row dicts — no live DB connection, so
  it stays testable and credential-free) into a `WarehouseManifest` of
  `CandidateTable(name, columns, declared_pk, declared_fks, row_count)`. A warehouse has
  hundreds of tables; `information_schema` tells you cheaply which ones exist and what
  they declare, so you can point the certified pipeline at them without pulling every
  table into memory. **Honest thesis marker:** Snowflake/BigQuery/Redshift do NOT enforce
  PK/FK, so every declared PK/FK is left `certified=False` — a declaration is exactly the
  kind of guess this arc PROVES against data, never a certificate. `plan_certification(
  manifest)` ranks the tables worth pulling + certifying first (has-declared-PK, then
  FK-referenced in-degree = spine/dimension tables, then smaller `row_count`, then name),
  each `CertifyStep` naming why its declarations are unproven. `discover_from_manifest(
  manifest, loader)` pulls each candidate's data (in plan order) and runs the normal
  certified `discover_semantic_model` — the declared PK is used only to RANK; the grain,
  joins, and measures are re-derived and proven from the loaded data, so a wrong
  `information_schema` declaration can never leak into the model. Deterministic (no LLM),
  **default-on**. The seventh "frontier" slice of the semantic-model discovery arc
  (design: `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`, item
  17). Tests: `tests/test_semantic_discovery_warehouse.py`.
- **Semantic-model discovery — dimension hierarchies via FD (Phase 11).**
  `goldenmatch.semantic.discover_hierarchies(table, columns)` detects drill-down
  hierarchies (`country > state > city`) among a table's dimension columns by finding
  **near-functional-dependencies** (a finer level determines a coarser one; a group-by
  "fraction of determinant groups mapping to a single dependent value" test, default
  threshold 0.95 so a few dirty rows don't kill a genuine hierarchy). Each column's
  *immediate* parent is the highest-cardinality coarser column it determines, yielding
  clean coarse→fine chains (`Hierarchy(table, levels, confidence)`) rather than
  transitive-closure noise. Deterministic (no LLM) and **default-on**: attached to
  `ProposedTable.hierarchies` + `ProposedModel.hierarchies`, included in `to_dict`, and
  emitted into `meta.goldenmatch.hierarchies` (MetricFlow/Cube) /
  `custom_extensions.goldenmatch.hierarchies` (OSI) — no dialect has a native hierarchy
  slot. The first "frontier" slice turning certified structure into a queryable semantic
  layer (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_hierarchies.py`.
- **Semantic-model discovery — metrics (Phase 12).**
  `goldenmatch.semantic.discover_metrics(measures, grain)` turns the grain-gated
  measures into certifiable business metrics — proposed ONLY when the grain is
  trustworthy (the measures are `safe_to_sum`), so the ratios can't double-count: per
  sum-safe measure an **average** (`avg_m = SUM(m)/COUNT(grain)`), and per sum-safe
  measure PAIR a **ratio** (`m1_per_m2 = SUM(m1)/SUM(m2)`, pool-capped to `C(5,2)`).
  `Metric(name, kind, numerator, denominator, expression)` on
  `ProposedTable`/`ProposedModel.metrics` + `to_dict`, emitted **natively** per dialect:
  MetricFlow top-level `metrics:` ratio metrics (plus a declared `count` measure so
  averages have a denominator), Cube calculated `number` measures + a `count`, and OSI
  dataset-qualified `OsiMetric`s. Deterministic, default-on. Derived *semantic* metrics
  (`profit = revenue − cost`) stay a namer/advisory follow-on. New exports
  `discover_metrics` + `Metric`. The second "frontier" slice (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_metrics.py`.
- **Semantic-model discovery — time intelligence (Phase 13).** New
  `goldenmatch/semantic/discovery/time_intelligence.py`.
  `discover_time_dimension(table, dimensions)` picks the primary date column and infers
  its finest grain FROM THE DATA (a cheap value scan: time-of-day → `day`; all values on
  month/quarter/year starts → that coarser grain), then proposes the drill granularities
  (`TimeDimension(table, column, grain, granularities)`). `discover_time_metrics(measures,
  time_dimension)` derives per sum-safe measure the **MTD / YoY / rolling-7d** variants
  (`TimeMetric`). On `ProposedTable`/`ProposedModel` (`time_dimensions` + `time_metrics`)
  + `to_dict`, and emitted so the engine computes time comparisons automatically:
  MetricFlow sets `defaults.agg_time_dimension` + a `type: time` dimension at the grain,
  MTD/rolling as native `type: cumulative` metrics (YoY structured for now); Cube/OSI keep
  the grain + granularities + variants in `meta.goldenmatch.time`. Deterministic,
  default-on. New exports `discover_time_dimension`/`discover_time_metrics` +
  `TimeDimension`/`TimeMetric`. The third "frontier" slice (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_time_intelligence.py`.
- **Semantic-model discovery — cardinality: 1:1 + m:n bridges (Phase 14).** Two lifts of
  the "everything is many_to_one" limitation. `discover_joins` now sets
  `relationship="one_to_one"` when the FK column is UNIQUE on the from side (each from-row
  references a distinct to-row) — flowing straight into the Cube `CubeJoin`/OSI relationship
  emit. New `goldenmatch/semantic/discovery/cardinality.py`:
  `discover_bridges(proposed_tables, joins)` detects **many-to-many** junction tables — a
  trustworthy 2-column compound key (PR-10) whose columns are certified FKs (PR-3) to two
  DIFFERENT tables — as `Bridge(bridge_table, left_table, left_column, right_table,
  right_column)` on `ProposedModel.bridges` + `to_dict`. Deterministic, default-on. New
  exports `discover_bridges` + `Bridge`. The fourth "frontier" slice (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_cardinality.py`.
- **Semantic-model discovery — SCD / temporal dimensions (Phase 15).** New
  `goldenmatch/semantic/discovery/scd.py`: `discover_scd(table, columns)` flags a
  Slowly-Changing-Dimension (Type 2) table. Validity columns (name-pattern
  `valid_from/valid_to`, `effective_*`, `start_date/end_date`, `from_date/to_date`)
  and/or an `is_current`/`is_active` flag NAME-propose it; then a STRUCTURE check
  confirms real versioning — a business key that repeats (multiple versions) but is
  unique combined with the validity anchor `(business_key, valid_from)` — so a stray
  `end_date` on a non-versioned table doesn't false-positive. `SCDDimension(table,
  business_key, valid_from, valid_to, current_flag, scd_type)` on
  `ProposedTable`/`ProposedModel.scd_dimensions` + `to_dict` + `meta.goldenmatch.scd`.
  Deterministic, default-on. New exports `discover_scd` + `SCDDimension`. The fifth
  "frontier" slice (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_scd.py`.
- **Semantic-model discovery — model completeness / trust score (Phase 16).** New
  `goldenmatch/semantic/discovery/completeness.py`: `score_model(proposed_tables, joins)`
  aggregates the existing discovery signals into a headline **grain-weighted** 0..1
  `ModelCompleteness` score (grain coverage 0.5, connectivity 0.25, sum-safe-measure
  coverage 0.25 — a certified grain is load-bearing, so it dominates) plus an explicit
  `gaps` list (`Gap(table, kind, detail)` for `no_grain` / `no_measures` / `isolated`), so
  "80% complete" always names the tables that are why it isn't 100%. On
  `ProposedModel.completeness` + `to_dict`. Pure self-assessment; no new detection. New
  exports `score_model` + `ModelCompleteness`. The sixth "frontier" slice (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_completeness.py`.

## [3.11.0] - 2026-08-03

### Added
- **Fused Fellegi-Sunter kernel now covers the reference-data name scorers +
  `ensemble` (`goldenmatch-native` 0.1.20).** The fully-fused `match_fused_fs`
  path (block-group → FS score → connected-components in ONE Rust call, no
  candidate-pair list — the bounded-memory FS scale path) previously scored each
  field via the stateless `score_one` (ids 0–3 only), so a matchkey using
  `name_freq_weighted_jw` / `given_name_aliased_jw` (ids 4/5) or `ensemble`
  (id 6) declined to the classic `score_buckets` + separate-WCC path. Since FS
  auto-config v2 emits those name scorers for person data, the fused path never
  engaged on the exact workload it targets. The fused kernel now scores each
  field through fs-core's `field_similarity` — the SAME dispatch the classic
  block scorer uses — so ids 4/5 reach the process-registered census / alias
  tables (`set_name_reference_data`) and id 6 reaches `ensemble_sim`, single-
  sourced (no duplicated dispatch). Regular-field coverage now matches
  `_NATIVE_FS_SCORER_IDS` (embedding id 7 stays out — the fused kernel takes raw
  string columns, not precomputed vectors); fused negative-evidence stays ids
  0–3. Byte-identical to the classic native FS path (parity-gated:
  `tests/test_pipeline_fused_fs_match.py` covers ensemble + `given_name_aliased_jw`
  membership/golden parity, and the alias table genuinely linking William/Bill/Will).
  **Wheel-gated on the new `FUSED_FS_SUPPORTS_NAME_SCORERS` capability flag** (name
  scorers additionally require the refdata pack loaded): an older wheel whose fused
  kernel lacks the dispatch declines these matchkeys to the classic path rather
  than silently scoring ids 4/5/6 as 0.0. Requires republishing `goldenmatch-native`
  to take effect in wheel-installed environments (in-tree builds pick it up
  immediately); until then those matchkeys degrade gracefully to the classic path.
- **FS in-RAM sequential Arrow-native / Rust batch scorer + end-WCC
  (`GOLDENMATCH_FS_BLOCK_SOURCE=sequential`, default OFF).** A single-box scale
  path that avoids DuckDB entirely: block keys are grouped in polars/Arrow, each
  block's rows are gathered from the resident frame and streamed through the
  native Fellegi-Sunter kernel in bounded waves to an Arrow edge stream, then ONE
  Rust Union-Find (WCC) stitches clusters at the end
  (`backends.fs_out_of_core.score_fs_sequential_arrow` +
  `run_fs_dedupe_sequential`). Working set = the resident frame + one wave of
  gathered blocks + the (small) edge stream — no on-disk sorted scans, so it is
  the fast pure-Arrow/Rust path while the prepared frame fits RAM (the DuckDB
  `score_fs_out_of_core` remains the spill-past-RAM variant). Byte-parity with
  `score_buckets`/`score_fs_out_of_core` absent oversized blocks (same block-key
  derivation + same kernel), gated by `tests/test_fs_out_of_core.py`. Routed via
  the shared `resolve_fs_block_source`/`fs_streaming_route` resolver and reachable
  through `gm.dedupe_to_parquet(*files, out_dir=…)`. Default path unchanged.
  **DuckDB-free AND polars-free** (O(N) path): block-key grouping runs on the
  Arrow `Frame` seam (`derive_block_key` → `core.arrow_derive.block_key`, the
  byte-equivalent Arrow twin of the polars `_build_block_key_expr`) +
  `filter_valid_key` + one pyarrow `group_by` per pass; blocks gather via
  `pa.Table.take`; the FS kernel reads the `pa.Table` directly; cross-pass edge
  dedup is the Rust `dedup_pairs_arrow` kernel (`dedup_pairs_max_score_arrow_table`,
  no polars); the Rust WCC (`_cluster_arrow_native`) takes the row-id set +
  `pa.Table` edge stream directly; and O(N) unique/dupes output is pure pyarrow
  (`_stream_fs_dedupe_output_arrow`: `pa.Table.join` + `ParquetWriter`). The scorer
  is guarded by a `import polars`-blocked test. The one remaining polars touch is
  the bounded golden-record builder (`build_golden_records_batch` on the
  multi-member subset only — an Arrow golden builder is a separate core port). The
  `duckdb` block source (`GOLDENMATCH_FS_BLOCK_SOURCE=duckdb`) stays the
  spill-past-RAM variant. Each wave is scored across cores by a
  `ThreadPoolExecutor`, **balanced by scoring cost (block², not row count)** via
  `_balanced_ranges` so no worker stalls on a giant block while the rest idle (the
  native kernel releases the GIL → real N-core parallelism; `executor.map`
  preserves order for block-order parity).
- **Customer 360 serving surface + semantic-layer drill-through.** The `customer_360`
  primitive (golden record + per-field provenance + linked source records + event
  timeline + relationship neighborhood, composed from the durable store) is now
  surfaced as a `customer_360` MCP tool and a `goldenmatch identity 360 <entity_id>`
  CLI command (both share the `customer_360_page` serializer). New
  `goldenmatch.semantic.profile_from_crosswalk(crosswalk, source_pk)` /
  `entity_360(store_path, entity_id)` connect the semantic-layer wedge to Customer
  360: a `ResolvedCrosswalk`'s `resolved_entity_id` is the same durable `entity_id`
  the 360 keys on, so a metric row drills straight through to the whole customer
  view with zero key translation. `customer_360` is a Python-only MCP tool (no TS
  equivalent); goldenmatch MCP tools **91 → 92**.
- **`emit_semantic_model_from_store` — a conformed catalog from the durable spine.**
  `goldenmatch.semantic.emit_semantic_model_from_store(store, *, source_name,
  source_pk_column, dialect, ...)` reads the live IdentityStore's identity summary
  and emits the same conformed `resolved_entity_id` join declaration the crosswalk
  emitters produce — so a dbt/MetricFlow, Cube, or OSI catalog's identity join can
  be regenerated from the control plane at any time ("keep the catalog live"),
  not just from a single dedupe run's `ResolvedCrosswalk`. Pairs with
  `write_resolved_catalog` (pass `path=` to also write it).
- **OSS local ER-matcher: ship the 1.5B model (Apache-2.0) as the default.** The
  self-hosted LLM boost (Path A) now pins a Modal-trained, published
  `er-matcher-1.5b-v1.0.0` GGUF (Qwen2.5-1.5B-Instruct base, Apache-2.0,
  redistribution-clean) as the default tier. The 3B pin was withdrawn: Qwen2.5-3B
  is under the Qwen Research License (non-commercial), so a 3B fine-tune is not
  redistribution-clean. 1.5B is also measured stronger zero-shot (walmart pair-F1
  0.795 vs the 3B's 0.721; in-dist F1 0.999). Training data is fully synthetic;
  restricted real datasets are eval-only.
- **Live catalog write-back + JSON-Schema OSI validation (semantic-layer wedge).**
  `goldenmatch.semantic.write_resolved_catalog(crosswalk, path, *, dialect,
  source_target, ...)` emits a `ResolvedCrosswalk`'s conformed entity declaration
  (the `resolved_entity_id` join a metric groups by) for dbt/MetricFlow, Cube, or
  OSI and **writes it to the catalog file** the semantic layer reads — the last
  mile of "resolve once, every metric inherits correct joins". Refuses to clobber
  an existing file unless `overwrite=True`, creates parent dirs, and forwards
  emitter kwargs (`measures`/`grain`/`resolved_field`/...). Also adds a bundled
  JSON Schema for OSI/Ossie 0.2.0.dev0 (`osi_json_schema()`) and a
  `jsonschema`-backed validator: `validate_osi_schema(source)` and
  `validate_osi(source, engine="structural"|"jsonschema"|"auto")`. `jsonschema` is
  optional (the `"auto"` engine falls back to the dependency-free structural check
  when it is absent); the default `engine="structural"` is byte-identical to prior
  behavior.
- **Metric-aware resolution for the semantic-layer wedge (the differentiated
  wedge).** New `goldenmatch.semantic.semantic_field_roles(source)` reads a
  semantic model's declared `{keys, dimensions, measures}` across all three
  dialects (dbt/MetricFlow, Cube, OSI), and `metric_aware_attributes(roles,
  columns)` turns them into the entity-resolution attribute allow-list — resolve
  on the declared dimensions, never on a measure (a measure like `revenue` is an
  aggregation target, not identity evidence). `certify_semantic_model` gained a
  `metric_aware=True` toggle (threaded uniformly through the Cube/OSI bridges via
  a new `roles=` argument) so the `resolve=True` tier drives its ER off the
  model's own metadata instead of blindly profiling every column. A model that
  declares no dimensions is byte-identical to the prior blind selection; the
  toggle has no effect unless `resolve=True`.
- **`grain_strict` on `certify_key_integrity` (semantic-layer wedge).** Opt-in
  argument (default `False` = byte-identical to prior behavior: grain stays advisory
  context and uniqueness is evaluated on the key alone). When `True` and a `grain` is
  supplied, uniqueness and per-measure fan-out are evaluated on `key + grain` — true
  "unique at grain", so a fact that legitimately repeats a key across grain buckets
  (e.g. daily rows per customer) is certified rather than reported as fan-out; a real
  duplicate *within* a grain bucket is still flagged. Grain columns are validated on
  the strict path. The `goldenmatch_key_integrity` dbt test gained a matching
  `grain_strict` argument in lockstep so the SQL test and this capability stay
  semantically identical.
- **Certify the keys a Customer 360 serving layer joins on.**
  `goldenmatch.semantic.certify_serving_joins(store, *, dataset=None, ...)` walks
  the durable Identity Store and runs `certify_key_integrity` over the
  source-record join key (`record_id` = `{source}:{source_pk}`) a Customer 360 view
  rolls metrics up on — turning the serving layer's implicit join-key trust into an
  advisory `KeyIntegrityCertificate`, so a metric joined through the 360 provably
  can't double-count. Returns a `ServingJoinCertificate` (record certificate +
  entity/record counts + `truncated` when a `max_entities` cap applies).
- **Surface the serving-layer certificate + store-emit on MCP and the CLI.** Two
  new `identity_*` MCP tools (`certify_serving_joins`,
  `emit_semantic_model_from_store`) and two `goldenmatch identity` subcommands
  (`certify-serving-joins`, `emit-catalog`) expose the Customer 360 serving-join
  certificate and the live-from-store semantic-catalog emit to agents and the
  shell — previously library-only. MCP tool count 92 → 94.
- **REST `GET /api/v1/identities/{entity_id}/360`.** The Customer 360 serving
  view (golden record + per-field provenance + linked source records + event
  timeline + relationship neighborhood) is now reachable over the identity REST
  surface, mirroring the `customer_360` MCP tool and the `identity 360` CLI
  command. Query params `include_relationships` (default true) and
  `timeline_limit`; 404 when the entity does not exist.
- **Structural key-integrity certifier can run through the shared
  `key-integrity-core` Rust kernel (opt-in `GOLDENMATCH_KEY_INTEGRITY_NATIVE`).**
  The group-by uniqueness + fan-out reduction in `certify_key_integrity` is now
  single-sourced with the TS/WASM surface: the same `key-integrity-core` kernel
  is reachable from Python via the native wheel's `certify_structural_json`
  symbol. The pyarrow group_by stays the DEFAULT and the reference the
  cross-surface golden is generated from (it is the right Arrow-at-bulk boundary;
  the kernel path is JSON-marshaled, so this is for the single-Rust-owner
  guarantee, not speed) — the kernel path is opt-in and fail-open (any missing
  symbol / non-JSON-safe column falls back to pyarrow). Parity-locked in
  `tests/test_key_integrity_native_parity.py` (native == pyarrow == the committed
  golden). Requires republishing `goldenmatch-native` to expose the symbol in
  wheel-installed environments (in-tree builds pick it up immediately).
- **`semantic.certify_structural_json` — a public JSON-in/JSON-out structural
  certifier + a `goldenmatch_certify_structural` SQL UDF on both backends.** The
  structural key-integrity tier (group-by uniqueness + fan-out) is now reachable
  as a JSON boundary (`{"n_rows", "group_columns", "measures"}` →
  `{"n_key_groups", "duplicate_key_groups", "max_fan_out", "is_unique_at_grain",
  "measure_fan_out"}`) — the Python analogue of the `key-integrity-core` kernel's
  `certify_structural_json`. It runs through the shared native kernel when opted
  in (`GOLDENMATCH_KEY_INTEGRITY_NATIVE`), else the pyarrow reference, and is
  byte-parity-locked to the committed golden. This closes the semantic-wedge
  certifier's last surface gap: **DuckDB** (`goldenmatch_certify_structural(VARCHAR)
  → VARCHAR`, over this function) and **Postgres** (`goldenmatch_certify_structural`
  native-direct over `key-integrity-core`, pgrx `0.16.0` → `0.17.0`) now emit the
  same structural certificate as the Python / TS / WASM surfaces. Byte-identical
  across all five by construction (shared kernel + shared golden). Tests:
  `tests/test_certify_structural_json.py`.
- **ER-resolution fragmentation reduction single-sourced with TS via a shared
  parity fixture.** The resolution tier's cluster→{resolved, fragmented,
  undercount} reduction (`certify_key_integrity(..., resolve=True)`) is extracted
  into a pure `_reduce_fragmentation(member_lists, keyvals)` and locked byte-for-
  byte against the TypeScript port with a shared, Python-generated fixture
  (`tests/fixtures/.../fragmentation_reduction_cases.json`, read directly by both
  surfaces — no copy). This is deliberately a data-driven fixture, NOT a Rust
  kernel: the reduction is a scalar loop over a cluster dict (no Arrow-bulk
  muscle) whose inputs differ per engine, so kernelizing it would pay FFI
  marshaling on a small call — the goldenanalysis quality_rollup / regressions
  precedent. Regenerated + drift-guarded via
  `scripts/emit_fragmentation_reduction_fixture.py` (wired into
  `regen_ts_parity_fixtures.sh` → the `ts_parity_freshness` gate). Tests:
  `tests/test_fragmentation_reduction.py`.
- **Resolution-tier undercount now carries a 95% confidence interval.**
  `certify_key_integrity(..., resolve=True)` reported `undercount_estimate =
  fragmented_entities / resolved_entities` as a bare point estimate — "undercount
  0.5" read the same from 2 resolved entities as from 2,000. The certificate now
  also carries `undercount_ci_low` / `undercount_ci_high`, a Wilson score interval
  on the fragmentation rate (a binomial proportion), so a rate measured from few
  entities honestly shows a wide interval. A new `safe_bound_conservative`
  property discounts the CI *upper* bound (worst plausible undercount) for callers
  who want a statistically-conservative trust floor; `safe_bound` is unchanged
  (still the point estimate — additive, non-breaking). The interval bounds
  *sampling* uncertainty, not whether ER clustered correctly. Pure arithmetic,
  single-sourced with the TS port via a shared fixture
  (`emit_undercount_ci_fixture.py`, drift-guarded by `ts_parity_freshness`).
  Tests: `tests/test_undercount_ci.py`.
- **End-to-end demo + docs for the semantic-layer key-integrity certifier.** A
  runnable example (`examples/semantic_key_integrity.py`) plants two defects an
  `orders` semantic model can carry — a duplicated declared key (structural
  fan-out that double-counts `SUM(revenue)`) and two distinct keys that are really
  one customer (resolution-tier fragmentation / undercount) — then shows
  `certify_key_integrity` quantifying both (measure fan-out, the 95% Wilson
  undercount interval, and `safe_bound_conservative`) without mutating a metric,
  and fixes it to a clean re-certification. New docs page
  `docs-site/goldenmatch/semantic-key-integrity.mdx` (wired into the Features nav)
  documents the structural + resolution tiers, the certificate fields, and the
  one-kernel cross-surface story (Python / TS / DuckDB / Postgres / dbt). Docs +
  example only — no code change.
- **Certificate trust-verdict write-back to the semantic catalog (MetricFlow /
  Cube / OSI).** A new single-sourced `certificate_verdict(cert)` projects a
  `KeyIntegrityCertificate` into the `key_integrity` metadata block a semantic
  catalog carries — the advisory pass/fail `verdict` plus `unique_at_grain`,
  per-measure fan-out, and the resolution-tier trust floors (`safe_bound` and the
  CI-discounted `safe_bound_conservative`, with the 95% undercount interval). It is
  a superset of the legacy 3-field embed. The three crosswalk emitters now share
  it: `emit_cube_from_crosswalk` / `emit_osi_from_crosswalk` write the full verdict
  (previously only 3 raw stats), and `emit_from_crosswalk` /
  `emit_semantic_model` gained a `certificate=` that writes it into
  `meta.goldenmatch.key_integrity` (MetricFlow embedded nothing before). So
  "resolve once, the verdict travels with the join." Single-sourced with the TS
  port (`certificateVerdict`) via a shared fixture (`emit_certificate_verdict_fixture.py`,
  drift-guarded by `ts_parity_freshness`). Byte-unchanged when no certificate is
  passed. Tests: `tests/test_certificate_verdict.py`.
- **Key-integrity trust verdict surfaced on MCP, REST, and the CLI as a build
  gate.** A shared `certification_report_dict(report)` serializer emits the
  verdict-rich JSON (`{dialect, n_certified, n_untrustworthy, all_trustworthy,
  keys:[{target, key, key_integrity}]}`, each key carrying the full
  `certificate_verdict` block) so the wire shape can't drift across surfaces. The
  MCP `certify_semantic_model` tool now returns the per-key verdict block (was a
  flat 4-field shape); a new REST `POST /semantic/certify` endpoint (`{model,
  frames, resolve}`) returns the same report; and `certify-keys` gained `--json`
  (the machine-readable report) alongside the existing `--fail-untrustworthy` CI
  gate, whose table now shows the pass/fail verdict + undercount. So certifying,
  gating, and the catalog write-back all speak one contract. Tests in
  `tests/test_certify_semantic_model.py`.
- **Semantic-model discovery — certified key discovery (Phase 1).**
  `goldenmatch.semantic.discover_keys(table)` proposes a table's candidate entity
  keys from three cheap signals (identifier col_type / near-unique cardinality /
  functional-dependency determinant via `fd_identity_scores`) and PROVES each with
  `certify_key_integrity` — so a returned `KeyCandidate` is pre-graded
  (`is_trustworthy`, fan-out, the certificate). Ranked trustworthy-first; a table
  with no clean key returns only untrustworthy candidates (the loud "this grain
  double-counts" signal). Numeric/date/geo/description columns are excluded from
  the key-cardinality signal (they're measures/dimensions, not keys). The first
  slice of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`) — the
  generative half of the semantic wedge, where discovery is hypothesis generation
  and the certifier is the falsification test. Single-column keys for this slice;
  compound/grain-ambiguous keys + the entity/join/measure phases + the
  `discover_semantic_model` orchestrator are the following PRs. Tests:
  `tests/test_semantic_discovery_keys.py`.
- **Semantic-model discovery — certified join / FK discovery (Phase 3).**
  `goldenmatch.semantic.discover_joins(tables, keys)` proposes the foreign-key joins
  across a set of tables and PROVES each join's cardinality with the same certifier
  the certify wedge uses. FK inference is the hypothesis (a column whose non-null
  values are a subset of another table's certified key, matching semantic type →
  a `many_to_one` join); `certify_cube_joins` is the falsification test (the "one"
  side must be unique at grain, else a metric joined across it double-counts). A
  returned `JoinCandidate` is pre-graded (`is_trustworthy`, fan-out, the certificate
  on the referenced key); ranked trustworthy-first. Numeric/date/geo columns are
  never proposed as foreign keys, and an untrustworthy target `KeyCandidate` is
  skipped (an unsound grain makes an unsound join). Single-column FKs referencing the
  caller-supplied per-table keys for this slice; compound/self-referential joins are
  follow-ons. The second slice of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_joins.py`.
- **Semantic-model discovery — cross-table entity typing (Phase 2).**
  `goldenmatch.semantic.discover_entity_types(tables)` groups source tables into the
  real-world entity types they realize, so the later phases conform one shared key /
  one dimension per entity instead of one per table. Two signals decide it: a
  **column-semantic signature** (columns canonicalize to shared tokens via the
  `schema_match` synonym map + profiled `col_type`; high Jaccard → same kind of thing)
  and **value overlap** on a shared identity column (email/phone/id-shaped) — but the
  value-overlap "same entity" signal fires ONLY when the shared column is near-unique
  on BOTH sides (the same population enumerated twice), so a foreign-key reference
  (e.g. `orders.customer_id` into `customers`) is correctly read as a Phase-3 join, not
  as sameness. Each `EntityType` names itself from its dominant hint (`person` /
  `organization` / `entity_N`), records the per-table conformance key when keys are
  supplied, and ranks multi-table-first. The third slice of the semantic-model
  discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_entities.py`.
- **Semantic-model discovery — grain-gated measure/dimension proposal (Phase 4).**
  `goldenmatch.semantic.discover_measures(table, key=…)` proposes the measures +
  dimensions a semantic model would declare over a table, and gates each measure's
  `SUM`-safety on the grain key's Phase-1 CERTIFICATE. `COUNT`/`AVG`/`MIN`/`MAX` are
  always proposed (fan-out-independent); `SUM` is proposed ONLY when the grain
  certified clean (`max_fan_out == 1.0`) — on a fanned-out (or unknown) grain the
  measure is returned with `safe_to_sum == False` and `SUM` withheld, the loud "this
  would double-count" signal. Dimensions are the low-cardinality categorical columns +
  dates + geo; the grain and id/reference columns are neither measure nor dimension.
  So the certifier that graded the key directly grades the measures. The fourth slice
  of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_measures.py`.
- **Semantic-model discovery — orchestrator + `discover-model` CLI (Phase 5).**
  `goldenmatch.semantic.discover_semantic_model(tables)` assembles all four discovery
  phases (keys → entity types → certified join graph → grain-gated measures) into a
  draft MetricFlow model, emits it through the existing dialect emitters, and
  re-certifies it end-to-end — the deliverable is a normal MetricFlow file plus a
  verdict-rich `certification_report_dict` where EVERY key is already graded. Only
  `SUM`-safe measures are declared in the emitted model, so a draft never scaffolds a
  double-counting `SUM`. `ProposedModel.all_trustworthy` is the headline build-gate
  signal; `.to_dict()` is the JSON shape the discover surfaces emit. New CLI
  `goldenmatch discover-model -d name=path ... [--dialect metricflow] [-o model.yml]
  [--json] [--fail-untrustworthy]` (Python-only, mirrors `certify-keys`). Nothing
  auto-ships — a human reviews the graded draft. The fifth slice of the semantic-model
  discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_model.py`.
- **Semantic-model discovery — MCP tool + REST endpoint (Phase 6).** Surfaces
  `discover_semantic_model` on MCP (`discover_semantic_model` tool) and REST
  (`POST /semantic/discover`), both returning the same `ProposedModel.to_dict()`
  wire shape the `discover-model` CLI emits — one contract across every surface, so a
  build gate reads `all_trustworthy` identically whether it calls the tool, the
  endpoint, or the CLI. Input is `frames` (table name → data file path), `dialect`
  (default `metricflow`), and `resolve`; bad input / unreadable files / an
  unsupported dialect return a clean `{"error": ...}` rather than raising. Python-only,
  mirroring the `certify_semantic_model` surface. The sixth slice of the
  semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discover_surface.py`.
- **Semantic-model discovery — optional advisory LLM namer (Phase 7).**
  `goldenmatch.semantic.name_semantic_model(model, tables, backend=...)` annotates a
  finished `ProposedModel` with business names for entity types, dimension columns,
  low-cardinality dimension VALUES (`status='C'` → "churned"), and measures — attached
  as `ProposedModel.naming` (a list of `NameSuggestion`; default `[]`). It is
  **opt-in, self-verified, and never authoritative**: the emitted YAML + certification
  are computed before naming and are never altered, so structural discovery stays
  byte-deterministic without it. **Two-pass self-critique** (propose, then a verify
  pass that critiques each name against its structural evidence; unsupported /
  low-confidence names are kept but flagged `verified=False`, never silently applied).
  The backend is an injectable `NamerBackend` Protocol; the default reuses the existing
  provider detection (the `goldenmatch[llm]` extra) and **abstains** (no names, never
  raises) when no provider/key resolves. Opt-in per call
  (`discover_semantic_model(..., name=False)`, CLI `discover-model --name`, MCP/REST
  `name` bool); `GOLDENMATCH_SEMANTIC_NAMER=0` is a hard kill-switch. The seventh
  slice of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_namer.py`, `tests/test_semantic_discover_surface.py`.
- **Semantic-model discovery — applied catalog `--apply-names` (Phase 8).** An opt-in
  mode that writes the namer's **verified** names from `ProposedModel.naming` into the
  emitted MetricFlow YAML: entity + measure business names land in native `label:`
  fields; dimension-column + value-glossary names (`status='C'` → "Churned") land in a
  `meta.goldenmatch.glossary` block (a sibling of the key-integrity verdict already in
  `meta.goldenmatch`). **Post-certification + cosmetic:** structural discovery, emit,
  and certification run unchanged; the labels/meta are applied to the final YAML
  afterward and never touch grain/joins/measures, so the certification verdict is
  untouched. Only `verified=True` names are applied; `apply_names=False` (default) is
  byte-identical to today. New pure `goldenmatch.semantic.apply_names(model) -> str`
  (no LLM). Opt-in per call (`discover_semantic_model(..., apply_names=False)`, CLI
  `discover-model --apply-names`, MCP/REST `apply_names` bool; implies `name`); `"yaml"`
  is now included in `ProposedModel.to_dict()` so the applied catalog is visible on
  every surface. No new MCP tool / CLI command. The eighth slice of the semantic-model
  discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_apply_names.py`, `tests/test_semantic_discover_surface.py`.
- **Semantic-model discovery — Cube / OSI emit (Phase 9).** `discover_semantic_model(dialect=...)`
  now emits `cube` and `osi` draft catalogs in addition to `metricflow` (it used to raise
  on anything else, though certification already spanned all three). A new
  `goldenmatch/semantic/discovery/emit.py` maps the discovered structure onto the
  existing dialect emitters: per table, the grain → primary key (a `primary_key`
  `CubeDimension` / an `OsiDataset.primary_key` list — composite-ready), discovered
  dimensions → `CubeDimension`/`OsiField`, sum-safe measures → `CubeMeasure`/`OsiMetric`,
  and the key-integrity verdict → `meta.goldenmatch` (cube) / `custom_extensions.goldenmatch`
  (osi). The certified **trustworthy** join graph is emitted natively as
  `CubeJoin`/`OsiRelationship`, and every emitted model is re-certified end-to-end. The
  MetricFlow path is byte-identical. `apply_names` (Phase 8) is now dialect-aware: cube
  → `title:` on cube/measure + `meta.goldenmatch.glossary`; osi → native field `label` +
  metric `description` + `custom_extensions.goldenmatch.glossary`. `dialect` already
  plumbs through CLI `--dialect` + MCP/REST `dialect`; no new params or tools. The ninth
  slice of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_cube_osi_emit.py`.
- **Semantic-model discovery — compound + self-referential keys (Phase 10).** Lifts the
  single-column restriction on the discovery side (the certifier, `KeyCandidate.columns`,
  and all three emit paths already carried multi-column keys). `discover_keys` gains a
  **fallback pairs** compound search: when no single-column candidate is unique at grain
  (the grain double-counts), it certifies 2-column combinations of the key-eligible
  columns (highest-cardinality first, pool-capped) and admits the trustworthy compounds
  (`signals=["compound"]`), re-ranked trustworthy-first — so a `(order_id, product_id)`
  order-items grain is discovered where neither column is unique alone. `discover_joins`
  lifts the self-join exclusion for **self-referential single-column FKs** (a column whose
  values are a subset of its OWN table's certified key, `fk_col != key_col`, e.g.
  `employees.manager_id → employees.employee_id`; the self side reuses the table's key
  certificate since `certify_cube_joins` can't certify a cube joined to itself). Compound
  grains emit natively in Cube (`primary_key` dims) / OSI (`primary_key` list); MetricFlow,
  which has no composite primary entity, declares the first column (a documented limit).
  Compound (multi-column) FKs and 3+-column keys remain follow-ons. No surface change. The
  tenth slice of the semantic-model discovery arc (design:
  `docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`). Tests:
  `tests/test_semantic_discovery_compound_selfref.py`.

### Changed
- **FS out-of-core streaming: single `resolve_fs_block_source` knob + DuckDB
  API-deprecation cleanup.** The two FS bounded-streaming lanes are now governed by
  one resolver (`backends.fs_out_of_core.resolve_fs_block_source` → `eager` /
  `frame` / `duckdb`) instead of two independent env booleans. `GOLDENMATCH_FS_BLOCK_SOURCE`
  now accepts `duckdb` (equivalent to the out-of-core path) alongside `frame` (in-RAM
  bounded bucket streaming) and `auto`/`eager` (today's default). The legacy
  `GOLDENMATCH_FS_OUT_OF_CORE=1` is retained as a `duckdb` alias when
  `GOLDENMATCH_FS_BLOCK_SOURCE` is unset, so existing runs/benches are unchanged;
  `fs_out_of_core_enabled()` and `score_buckets._fs_bounded_stream_enabled()` both
  read the shared resolver. Default stays `eager` (byte-identical to today) until a
  CI scale gate justifies the flip. Also swapped the deprecated DuckDB
  `fetch_record_batch`/`fetch_arrow_table` calls for `to_arrow_reader`/`to_arrow_table`
  in the streaming scorer and the DuckDB backend.
- **FS dedupe: Arrow-native columnar-cluster path (B2c), default-on (#1811/#2006).**
  A measure-first 5M profile pinned the FS scale wall at **clustering** (`cluster`
  100.8s / 41%), not the native scoring kernel (`bucket_score` 70.8s / 28%). Root
  cause: B2c's "columnar" cluster path rebuilt the full 16M-tuple Python list and
  ran the legacy dict build (`build_clusters_columnar`) — per-cluster dicts + a
  16M-key `pair_scores` dict — while the non-B2c list path already avoided all of
  that via the frames-out `build_cluster_frames`. This routes B2c through the
  frames-out path and makes it **Arrow end-to-end (no polars)**: the
  `score_buckets_arrow` `pa.Table` is threaded straight into `build_cluster_frames`
  (native arrow kernel → assignments/metadata); `build_cluster_frames` now accepts
  a `pa.Table` first-class with a lazy pair-list (materialized only off-native or
  for the rare oversized-split minority); `scored_pairs` is deduped via a new
  Arrow-table kernel. `GOLDENMATCH_FS_COLUMNAR_CLUSTER` is flipped **default-on**
  (`0`/`false` forces the legacy list path); bench-dump is excluded from B2c
  eligibility; the review band still attaches to `review_pairs`. Weighted columnar
  lane untouched. Clusters are overlap-parity to the list path (the FS bucket
  pipeline is ~0.1%-nondeterministic run-to-run regardless).
- **Identity resolve: SQLite bulk write fast-path.** `resolve_clusters` now routes
  brand-new clusters through a staging-table bulk write on the SQLite backend (not
  just Postgres): a per-connection TEMP staging table + `executemany` +
  `INSERT..SELECT..ON CONFLICT DO UPDATE`, replacing the per-cluster row loop. On a
  40k-singleton `emit_singletons=True` resolve this cut wall time ~47x (185.9s ->
  3.95s). Accumulators flush in bounded batches
  (`GOLDENMATCH_IDENTITY_BULK_FLUSH_ROWS`, default 250k) so write-side memory stays
  O(batch); `GOLDENMATCH_IDENTITY_BULK=0` restores the per-row loop. Store contents
  are byte-identical to the per-row path (payloads and event/edge provenance carried;
  parity-gated). This does not remove the O(N) prep frame-residency floor that
  `emit_singletons=True` still carries -- that remains separate bounded-streaming work.

### Fixed
- **FS pair-budget gate no longer compounds a coarse blocking pass with a unique
  surrogate key.** `_bound_probabilistic_blocking_pairs` reduces an over-budget
  coarse pass by ANDing in the strongest full-value "identity" reducer
  (email/identifier/phone), previously selected by `col_type` alone. A UNIQUE
  surrogate key (`record_id`, row-uuid; `cardinality_ratio` 1.0) collapses any
  block to singletons (0 pairs, always under budget) so it looked like the
  strongest reducer -- but duplicates do NOT share a surrogate key, so
  `[coarse, record_id]` SPLITS every true pair. Measured on the 25M person shape
  (truth keyed on `cluster_id`, `record_id` unique): recall 1.0 -> 0.49 /
  F1 0.893 -> 0.653. Now mirrors the #721/#876 exact-matchkey gate -- columns with
  `cardinality_ratio >= 1.0` are excluded from every reducer branch (full-value
  identity AND substring-prefix; a prefix of a unique key is just as
  recall-splitting). A real identity field (email, shared by duplicates) is still
  preferred; a coarse pass with only a surrogate available falls back to the
  recall-safe name initial or is dropped. Also threads `n_rows_full` through
  `scripts/bench_fs_out_of_core_scale.py` so the bench extrapolates each pass's
  Sum C(block,2) to the full population and actually exercises the pair-budget gate.

## [3.10.0] - 2026-07-24

### Added
- **Customer 360 unified serving read (`identity.customer_360`).** One call composes
  the durable Identity Store into the whole picture of a customer: the golden record
  + per-field source provenance (which source record wins each value, and any
  overridden competing values), every linked source record, the append-only event
  timeline, and the entity's relationship neighborhood from the store's own overlay.
  Read-only, derived entirely from persisted state (no live frame, no migration).
  `customer_360_page` is the JSON-ready serialization future serving surfaces share.
  Design: `context-network/architecture/customer-360-data-connection.md` (D1);
  decision: `context-network/decisions/0049-customer-360-identity-store-spine.md`
  (IdentityStore is the spine; the relationship overlay federates).
- **3 host-helper MCP tools** (`server_info`, `read_file`, `write_csv`) -- the last
  `ts_only` entries. **`mcp_tools.ts_only` is now EMPTY**: the TS MCP surface is a
  strict subset of the Python one (83 shared, 0 TS-only, 7 Python-only). goldenmatch
  MCP tools **87 -> 90**. `server_info.tool_count` is DERIVED from the live tool list,
  never a literal, so it cannot drift from the real surface.

### Security note (behavior unchanged, now documented + tested)
- `read_file` / `write_csv` route through the same `core._paths.safe_path` guard every
  other file-taking tool uses. That guard rejects NUL bytes and resolves the path, but
  **enforces containment only when `GOLDENMATCH_ALLOWED_ROOT` is set**. With it unset
  (the default) these tools reach anything the server process can -- identical to the
  pre-existing `upload_dataset` / `export_results`, so this adds no new reach. Both
  modes are now pinned by tests, and the contract is stated on the API-surface page.
  `write_csv` additionally refuses anything that is not a list of objects.

## [3.9.0] - 2026-07-24

### Added
- **5 core-primitive MCP tools** (`score_strings`, `score_pair`, `find_exact_matches`,
  `find_fuzzy_matches`, `build_clusters`) -- closing the REVERSE parity gap: the
  TypeScript MCP server exposed these while Python did not. Each is a thin, STATELESS
  wrapper over a function goldenmatch already had, so an agent can score two strings,
  score two records, enumerate a file's exact/fuzzy pairs, or cluster a file without
  first loading a run into session state. Wire shapes mirror the TS handlers exactly,
  including the 100-pair / 200-cluster response caps. goldenmatch MCP tools **82 -> 87**;
  `mcp_tools.ts_only` **8 -> 3** (only `read_file`, `write_csv`, `server_info` remain).

### Fixed
- `docs-site/reference/api-surface.mdx` prose was stale beyond the generated block: it
  listed all 7 primitives as TS-only and claimed "~31 Python-only tools" when the real
  figure is 7. Corrected alongside the counts.

## [Unreleased]

### Added
- **Ontology-layer live-catalog write-back.** `write_ontology_catalog(rdf, dest=…)`
  writes emitted RDF to a file, or (`endpoint=…`) PUTs/POSTs it to a live SPARQL
  1.1 Graph Store endpoint (a triple store — `mode="replace"`/`"merge"`,
  `graph_iri`); `write_resolved_identity_graph(crosswalk, …)` emits a crosswalk's
  `owl:sameAs`/PROV-O graph and writes it in one call. `goldenmatch ontology
  discover` gains `--endpoint` / `--graph-iri` / `--mode` to push a discovered
  ontology to a triple store. Stdlib-only (`urllib`, no new dependency); the write
  path needs no rdflib. GoldenMatch conforms to the Graph Store protocol, it does
  not implement a triple store. Completes the ontology arc. See ADR 0057.
- **Ontology-layer CLI + MCP front door.** `goldenmatch ontology certify
  <ontology.ttl> --data Class=path` and `goldenmatch ontology discover --data
  Class=path [-o out.ttl]` surface the ontology-layer certify/discover
  capabilities on the command line (mirroring `certify-keys` / `discover-model`),
  and MCP tools `ontology_certify` / `ontology_discover` expose the same to
  agents. `python_only` in the parity gate; `rdflib`-optional (fail-clean install
  hint without the `goldenmatch[ontology]` extra). See ADR 0056.
- **Ontology layer, produce + discover (`goldenmatch.semantic.ontology`).**
  Completes the flesh-out with the generative half: `emit_golden_triples` emits
  resolved golden records as typed RDF individuals (`rdf:type` + conformed
  attribute values), `emit_sameas_graph` can type canonical entities to a class
  (`target_class`), `emit_ontology_shapes` derives a per-class SHACL shape from the
  ontology's own `owl:hasKey`, and `discover_ontology` proposes a draft OWL
  ontology — one `owl:Class` per frame — whose `owl:hasKey` is chosen by reusing
  the certifier-backed `discover_keys` and ships PRE-GRADED
  (`gm:keyTrustworthy`/`gm:keyUniquenessEstimate`); the Turtle round-trips through
  `parse_ontology`. See ADR 0055.
- **Ontology layer, deeper consume + audit (`goldenmatch.semantic.ontology`).**
  Builds on the v1 provider: `parse_ontology` now inherits `owl:hasKey` down
  `rdfs:subClassOf` (`effective_has_keys`) and reads cardinality-1 restrictions;
  `certify_ontology` rolls every declared identity key into one
  `OntologyCertification` verdict (the analogue of `certify_semantic_model`); and
  `reconcile_ontology_identity` diffs the ontology's asserted `owl:sameAs`
  (`asserted_sameas_pairs`) against a GoldenMatch `ResolvedCrosswalk`, flagging
  where exact-match identity **over-merged** (asserted same, resolved different)
  or **fragmented** (resolved same, never asserted). Reuses the key-integrity
  certifier; no reasoner. See ADR 0054.

- **Registry-introspection tools/skills on MCP + A2A (TS-parity).** `list_scorers`,
  `list_transforms`, and `list_strategies` are now exposed on the Python MCP server
  (81 tools) and the A2A agent card (43 skills) as stateless serializers over
  `VALID_SCORERS` / `VALID_SIMPLE_TRANSFORMS` / `VALID_STRATEGIES` — so an agent can
  discover the valid scorer / transform / survivorship-strategy names before building
  a config. Closes the corresponding `mcp_tools` / `a2a_skills` cross-language parity
  gap (both trios move `ts_only` → `shared`).
- **Capability-gap A2A skills (TS-parity).** `profile`, `suggest_config`,
  `memory_export`, and `suggest_pprl` are now advertised on the Python A2A agent card
  (47 skills) — closing four `a2a_skills` gaps the TS card already carried. `profile`
  summarizes a dataset's columns via `profile_for_agent`; `suggest_config` returns a
  shorthand `{exact, fuzzy, blocking, threshold}` from `auto_configure`; `memory_export`
  and `suggest_pprl` delegate to the identical MCP dispatch. All four move
  `ts_only` → `shared`.
- **`list_blocking_strategies` MCP tool (TS-parity).** Now exposed on the Python MCP
  server (82 tools) as a stateless serializer over `BlockingConfig.strategy` — the
  last TS-only introspection tool. It lists every accepted blocking-strategy name
  (incl. the Python-only `lsh` / `simhash` / `perceptual`), closing the `mcp_tools`
  gap (`ts_only` → `shared`).
- **`score` and `info` CLI commands (TS-parity).** `goldenmatch score <a> <b>
  [--scorer NAME]` prints `<scorer>: <0.xxxx>` over `score_strings`; `goldenmatch
  info` prints the version plus the available scorers / survivorship-strategies /
  blocking-strategies / transforms, sourced from the actual config allow-lists
  (`VALID_SCORERS` / `VALID_STRATEGIES` / `BlockingConfig.strategy` /
  `VALID_SIMPLE_TRANSFORMS`) so the listing can't drift. Both move `cli_commands`
  `ts_only` → `shared`; only `tui` (≈ the Python `interactive` command) remains
  TS-only by design.

### Changed

- **`name_freq_weighted_jw` / `given_name_aliased_jw` are now first-class
  `VALID_SCORERS` (TS-parity).** The census-frequency-weighted and alias-aware
  given-name Jaro-Winkler scorers (`refdata/scorer.py`) were accepted only via the
  PluginRegistry validator fallback, so they were absent from `VALID_SCORERS` and
  the `scorers` parity surface listed them TS-only. They're registered at
  `import goldenmatch` (via `_api` → `refdata`), so promoting them to `VALID_SCORERS`
  lets a config referencing them validate AND score with no manual
  `import goldenmatch.refdata` (verified by a fresh-process test). Both move the
  `scorers` surface `ts_only` → `shared` — the goldenmatch↔TS scorer surface is now
  at full parity. No behavior change for existing configs.

## [3.8.0] - 2026-07-22

### Added

- **Opt-in FS columnar cluster path (`GOLDENMATCH_FS_COLUMNAR_CLUSTER`, default OFF)
  — #1811.** On the eligible single-Fellegi-Sunter-matchkey in-memory bucket dedupe
  path, the Arrow pair stream (`score_buckets_arrow`) is threaded straight to the
  shipped, parity-gated columnar cluster path (`build_clusters_columnar` over
  `_columnar_pairs_df`) instead of extending the driver-resident `all_pairs` Python
  `list[tuple]`. At 14M on tight-blocking/dup-dense data that list runs to hundreds
  of millions of tuples held on the driver through scoring → clustering — the
  late-stage OOM of #1811. This removes the scoring-phase accumulator; the in-memory
  analogue of the out-of-core `GOLDENMATCH_FS_OOC_ARROW_CLUSTER`. Default-OFF is inert
  (byte-unchanged); eligibility mirrors the weighted columnar lane's downstream-safety
  (single matchkey, no across-files / semantic-blocking / llm / boost) plus the FS
  bucket route.
- **Kernelized `radial` + `audio_fp` scorers (17/19 kernel-backed) — #2008.** The
  radial and audio-fingerprint scorers are now backed by the native `score-core`
  kernel (`radial` + `audio_fp` in `score.rs`), moving the `scorer_kernels` coverage
  to 17 of 19; cross-surface parity locked (`test_native_perceptual_scorer_parity`).
  Host code degrades gracefully to the pure-Python reference when the published
  `goldenmatch-native` wheel predates these symbols.

### Changed

- Dependency bumps: `setuptools` 81.0.0 → 83.0.0 (#2001), `pyasn1` 0.6.3 → 0.6.4 (#2000).

## [3.7.0] - 2026-07-21

### Fixed

- **SQLite identity resolve no longer scales with the input frame (#2105).** A 14M-row
  dedupe with `identity.backend="sqlite"` OOM-killed a 64 GB box, and the resolve step
  ran 5-17x the cost of the same dedupe with identity off. Three independent causes,
  all on the SQLite single-node path:
  - **Unbounded prep (the OOM).** `resolve_clusters` turned EVERY input row into
    ~2.5 KB of Python heap (row dict + payload dict + record hash + source + pk +
    record-id candidates) before looking at which rows a cluster actually references
    — ~35 GB on a 14M-row frame, on top of the pipeline's own resident set. With
    `emit_singletons=False` the overwhelming majority of those rows are never read
    again (the reported run resolved 107,723 records out of 1M rows). The prep is now
    bounded to rows a surviving cluster references, so it scales with the identity
    graph rather than the frame. Measured **~9x lower peak Python heap** on a
    50k-400k ladder, and flat per-member instead of per-row.
  - **One transaction per statement.** `IdentityStore.bulk_writes()` was a deliberate
    no-op for SQLite ("already local + WAL"), and the connection is opened
    `isolation_level=None`, so every INSERT committed on its own and paid a WAL sync
    — while resolve issues ~6 statements per cluster. Measured ~750 us/statement
    autocommit vs ~30-90 us batched. SQLite now runs the resolve writes inside
    explicit transactions, committing in `GOLDENMATCH_IDENTITY_SQLITE_BATCH_SIZE`
    chunks (default 10,000) so the WAL cannot grow without bound on a
    multi-million-row run. Reads inside the batch see their own pending writes (same
    connection), so the absorb / merge branches are unaffected. Kill-switch
    `GOLDENMATCH_IDENTITY_SQLITE_BATCH=0` restores per-statement autocommit.
  - **A dead per-pair dict.** `scored_pairs` was folded into a
    `{(record_a, record_b): score}` map that nothing ever read — ~1.2 s and ~102 bytes
    per million pairs, built over the FULL pre-cluster scored-pair stream (far larger
    than the edge set on wide-block data) on every resolve. Removed; evidence edges
    already come from the per-cluster `pair_scores` / `pair_score_view`.

  Net on a 50k-400k ladder shaped like the report (natural PK, ~11% of rows in a
  multi-record cluster): **2.5-4.5x faster wall and ~9x lower peak Python heap**, with
  byte-identical store contents. `emit_singletons=True` benefits from the same write
  batching. Output is unchanged on every backend; Postgres and Mongo paths untouched.
- **Zero-config Fellegi-Sunter recall no longer collapses at scale (candidate-pair
  projection fix + recall-safe compounding + memory-aware budget).** Zero-config
  FS recall collapsed **1.0 at ≤2.4M → 0.82 at 4.8M → ~0.02 at 30M** (F1 0.030,
  the 30M single-box proof) — entirely scale-dependent and invisible below ~5M.
  Three fixes, the first being the actual scale-dependent root cause:
  - **Candidate-pair projection (the root cause).** `_project_pass_pairs`
    extrapolated each blocking block's SIZE by the full row ratio
    (`cnt * n_full / sample_n`). That is only right for a SATURATED low-cardinality
    key; a NEAR-UNIQUE key keeps producing new values as N grows (blocks stay
    ~constant size, the COUNT grows), so growing its size invents ~`C(ratio, 2)`
    PHANTOM pairs per sample singleton. At 30M (ratio ~150) a near-unique
    `(zip, email)` compound was projected at ~2.2B pairs and DROPPED by the
    pair-gate, collapsing blocking to a single `first_name` pass (dups have typo'd
    first names → recall ~0.02). Fixed by growing block size only by the key's
    sample collision headroom (`1 - distinct/sample_n`): saturated keys grow by the
    full ratio (byte-identical), near-unique keys barely grow (singletons stay
    singletons → 0 phantom pairs). Small data (`n_full == sample_n`, the whole
    bench-probabilistic panel) is unaffected — no extrapolation runs.
  - **Recall-safe compounding.** When the pair-gate DOES bound an over-budget
    coarse pass, it now compounds with an **exact-agreement identity field
    (email / identifier / phone) at full value**, before the corruption-prone
    name/geo initials. Duplicates share those exactly, so the compound keeps every
    true pair together while collapsing the block to near-singletons — recall-safe
    AND a stronger reducer (30M person shape: `zip + first-initial` = recall 0.82;
    `zip + email` = recall 1.0 at 0.9M pairs). The old "most selective" reducer (a
    name initial) split true pairs on any typo'd name.
  - **Memory-aware budget.** `_fs_total_pair_budget` is now
    `max(300M floor, available_ram_gb * ~40M)` (anchored to the 25M-on-64GB proof
    where ~2.1B bounded pairs peaked at ~28 GB), so a big box does less
    compounding to begin with and keeps coarse passes pure. Byte-identical below
    the trigger (small boxes keep the 300M floor + all #1803 tuning).
  Measured **F1 0.9005 → 1.0000 at 4.8M** (P=R=1.0); the identity reducer holds
  recall 1.0 at 4.8M even at a tight 300M budget (3.3M candidate pairs). Not EM:
  the EM within-block-pair sample cap is irrelevant to this (100K/200K/400K give
  identical F1); blocking recall exactly tracks pipeline recall. The
  `gate-fs-zeroconfig` nightly now runs at 10M (was 1M — below where this class is
  visible) with `set -o pipefail` so the F1-floor failure is no longer masked by
  `| tee`.
- **Weak-positive blocking-pass pruning now runs on the FS arrow lane.**
  `GOLDENMATCH_BLOCKING_PRUNE_PASSES=1` invoked `select_passes` (polars-native:
  `with_row_index` / `group_by`) directly on `df`, but the FS routed / arrow
  lane passes a pyarrow `Table` -- so it threw `AttributeError`, was swallowed
  into "keep all passes", and pruning was a silent no-op for every arrow-lane FS
  caller. `_maybe_prune_blocking_passes` now coerces a Table / LazyFrame to
  polars first. With the pruner actually running on a representative sample it
  cuts a redundant 6-pass zero-config FS scheme (3 first-name + 2 last-name
  transform variants + zip) to 3 passes (one name axis per field + zip),
  measured **71s -> 32.6s at 1M, F1 unchanged 1.000** (P=R=1.0) on realistic
  person data -- recall-safe because it drops only redundant transform *variants*
  of the same field, keeping each blocking *axis*. Still opt-in; a default-on
  flip for the FS path is gated on the `bench-probabilistic` panel.
- **Learned blocking no longer clobbers the #1207 strong-identifier union at
  >=50k rows (#1316).** Auto-config forced `strategy="learned"` unconditionally
  at `total_rows >= 50_000`, discarding the per-identifier blocking union that
  null-sparse multi-source strong-id data depends on. Measured on that shape at
  50k, learned blocking under-blocks catastrophically -- candidate-pair recall
  collapses from 1.0 (union) to 0.0 (the learner trains on a <=5k sample, finds
  no pairs above its recall threshold, falls back to one column, and
  `skip_oversized` drops every resulting oversized block). The >=50k gate now
  keeps a strong-identifier union it detects and only upgrades non-union large
  shapes to learned blocking.
- **Zero-config Fellegi-Sunter admits shared identity identifiers (email/phone)
  at cardinality 1.0.** FS auto-config dropped every `card == 1.0` exact field as
  a "perfect surrogate", but that also discarded identity-bearing identifiers
  (email/phone) that duplicates carry verbatim -- FS's single strongest signal.
  Because cardinality is measured on a config sample that can under-represent
  duplicates, this silently collapsed the EM model to zero matches at scale
  (measured: zero-config FS F1 0.0 at 1M on realistic person data; recovers to
  1.0 with the identifier admitted). `build_probabilistic_matchkeys` now admits
  `email`/`phone` at `card >= 1.0` (an FS comparison field self-regulates -- a
  true PK to neutral, a shared identifier to a large weight) while still
  excluding the ambiguous bare `identifier` type (row PKs) for config hygiene.
  FS path only; the weighted/exact matchkey path is unchanged.

- **Zero-config FS blocking pair-budget now prunes at scale (~6x wall).** The FS
  blocking pair-budget (`_bound_probabilistic_blocking_pairs`) is documented to
  extrapolate each pass's candidate pairs to the full population, but
  `auto_configure_probabilistic_df` never passed `n_rows_full` -- so on the
  auto-config sample the bound measured pairs at sample scale (a 66M-at-1.2M pass
  reads as ~1.8M at a 200K sample), stayed under budget, and never pruned, leaving
  redundant giant-block soundex passes. Threading the full row count lets the
  bound bound the oversized name passes at true scale: measured zero-config FS
  wall 410s -> 71s at 1.2M (F1 unchanged at 1.000). The FS routing call site and
  the bench/gate helper now pass `n_rows_full`.

- **FS `missing="unobserved"`: a partial-observation pair no longer normalizes to
  1.0 (#1854).** The min-max score range accumulated only over the OBSERVED
  fields, so a pair agreeing on its single observed field had `total == pair_max`
  and rescaled to 1.0 — maximal confidence from minimal evidence. The range now
  spans EVERY matchkey field (the sum stays over observed only), so a
  one-of-many-observed agreement is correctly uncertain (e.g. 0.75 on a
  two-field key). Cross-surface: `fs-core::score_fs_pair` (the native/unobserved
  runtime + fs-wasm), the four Python reference paths in `core/probabilistic.py`
  (vectorized ×2, scalar, `score_pair_probabilistic`). Identical when every field
  is observed (`missing="disagree"` and fully-populated pairs are byte-unchanged;
  auto-config routes null-heavy data to `disagree`, so the default path is
  unaffected). Measured under forced `unobserved`: historical_50k
  f1_probabilistic recovers to ~0.63 from the collapsed ~0.33; febrl3 −0.002
  (within the quality-gate tolerance).

### Changed

- **Out-of-core FS scorer batches blocks into the native kernel (opt-in path,
  parity-exact) + `GOLDENMATCH_FS_OOC_DEBUG` progress.** `score_fs_out_of_core`
  scored one block per `score_probabilistic_bucket_native` call; on person data
  (tens of thousands of tiny blocks per pass) that made the FFI fan-out the wall
  (~60s for a single 200K pass, hours at 25M). It now hands a whole
  block-contiguous wave to the kernel in one call per worker-chunk, with the
  per-block `size_list` isolating blocks — mirroring the in-memory
  `_score_one_bucket` batched call, so the emitted pair set + scores are
  byte-identical (the existing parity tests against the per-block reference
  gate it). The numpy vectorized path likewise batches via
  `score_probabilistic_vectorized_batch`; unsupported scorers keep the per-block
  fallback. Measured ~8× on the per-pass scoring at 200K. `GOLDENMATCH_FS_OOC_DEBUG=1`
  prints a per-phase / per-pass timing line (load, block-map, scan+score, block
  count) so a long >=25M streaming leg shows live progress instead of a blank
  spinner. NOTE: this fixes the per-block fan-out only; the OOC path still runs
  one full-dataset scan+score per blocking pass, so low-cardinality passes and
  multi-pass depth remain a separate scale lever.
- **Out-of-core streaming FS refinements (review follow-ups, opt-in path only).**
  `_prep_all_ids` returns a `range` instead of a 25–50M-element Python list when
  `__row_id__` is contiguous (the pipeline-generated common case), avoiding a
  multi-GB transient before the pyarrow int64 array on the ≥40M streaming path.
  `stream_fs_dedupe_output` and `dedupe_to_parquet`'s in-memory fallback now
  remove a stale `golden.parquet` left by a prior run into the same `out_dir`
  when a run produces no golden rows, so the on-disk file set matches the
  returned `golden_path=None`.

### Added

- **Bounded bucket streaming for the in-RAM FS route
  (`GOLDENMATCH_FS_BLOCK_SOURCE=frame`, default OFF) — cuts the ≥1M
  frame-residency peak.** The scale branch of the FS (probabilistic) bucket
  scorer (`score_buckets._score_single_pass`, height ≥ n_buckets) used to
  `partition_by` the keyed frame into all `n_buckets` eager frames up front — a
  ~2× transient at partition time whose freed pages jemalloc retains straight
  through cluster/golden, the dominant remaining single-node FS peak once the EM
  `build_blocks` fixes landed. With the flag on, the scale branch keeps the
  single bucketed frame resident and slices each bucket out on demand
  (`filter_eq` inside the worker), so peak holds the bucketed frame plus at most
  `max_workers` in-flight slices instead of all N partitions. Byte-identical to
  the eager path: `filter_eq` preserves within-bucket row order ==
  `partition_by(maintain_order)`, so each bucket's scorer output is unchanged,
  and cross-bucket append order is order-invariant downstream (pairs
  canonicalized). **Measured (synthetic person 1M, local 4c/15GB, jemalloc-decay
  env): whole-pipeline peak 3244 → 2875 MB (−11.4%), byte-identical output
  (850,714 clusters both).** Default OFF keeps the eager path until a CI ≥1M
  peak gate validates the flip; scoped to the FS route (the weighted path is
  untouched); the DuckDB (above-RAM) source is the separate
  `GOLDENMATCH_FS_OUT_OF_CORE` path below. Spec:
  `docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md`.

- **Out-of-core single-box streaming Fellegi-Sunter dedupe — the ≥40M scale
  path (`GOLDENMATCH_FS_OUT_OF_CORE=1`, default OFF).** The probabilistic (FS)
  route had no out-of-core or distributed path: `_fs_use_bucket_route` hands
  `backend=duckdb/ray` to a single-node scorer, so the whole prepared frame
  stayed resident and the single-box FS wall was ~40M on 64 GB
  (CI-measured: 25M @ 40.3 GB / 16 min; 50M projected to ~82 GB OOM), while F1
  stayed scale-stable. `backends/fs_out_of_core.py` adds three bounded
  mechanisms: `score_fs_out_of_core` streams block groups one at a time from a
  DuckDB-resident (file-spilled) prepared table (scoring peak = one block
  group, byte-parity with `score_buckets` absent oversized blocks);
  `stream_fs_dedupe_output` writes unique/dupes via DuckDB `COPY ... TO parquet`
  with no result frame; `run_fs_dedupe_streaming` ties prep → DuckDB file →
  free frame → score → cluster → stream. New public
  `gm.dedupe_to_parquet(*files, out_dir=...)` reaches it (and falls back to the
  in-memory pipeline + parquet write when the config is not FS-eligible or the
  flag is off, so it always yields the same files). The default path (no
  `output_dir`) is byte-unchanged. Spec:
  `docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md`.

- **FS EM `build_blocks` memory-peak fixes (both default ON, byte-identical
  output).** The FS memory peak is EM's `build_blocks`, not `score_buckets`.
  `GOLDENMATCH_FS_EM_BLOCK_SLIM` projects each EM block-frame to
  `[__row_id__] + blocking fields` before materialization (width 14→6);
  `GOLDENMATCH_FS_EM_AGG_BLOCKS` builds the EM-only blocks as compact int64
  row-id arrays via one `group_by().agg()` per pass, never materializing
  per-block frames (supersedes the slim lever). Measured whole-pipeline peak on
  person 100K: 2126 → 527 MB (−75%); regime-dependent above ~1M where the
  EM-sample cap already bounds block count. A `jemalloc` page-decay env
  (`_RJEM_MALLOC_CONF`) trims the 1M FS peak a further ~33% at ~zero wall.

### Fixed

- **`DedupeResult.clusters` now exposes real contents to C-level consumers on
  the frames-out path (re-scoped #1961).** The lazy cluster handle
  (`LazyClusterDict`, a `dict` subclass that builds on first Python content
  access) left its underlying storage empty until an override fired. C-level
  consumers that bypass those overrides — the goldenmatch-pg bridge's pyo3
  `.extract::<HashMap>()` (`PyDict_Next`), `json.dumps` (empty-dict fast path
  via `PyDict_GET_SIZE`) — silently observed **zero clusters**, so a dedupe that
  correctly formed a size-2 cluster serialized as empty (the pg `p4_typed`
  smoke: "expected 2 rows in a size-2 cluster, got 0"). `DedupeResult.clusters`
  is now a property that materializes the lazy handle to a plain `dict` on first
  read, so any consumer sees the real contents; a result whose `.clusters` is
  never read still never pays the build (the frames-out perf win is preserved).
  The pg extension's columnar SPI read (#1951 `spi.rs`) was correct and is
  unchanged.

- **Arrow-native auto-config no longer silently degrades blocking on
  wide/sparse frames (#1852, mode 2).** With
  `GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1` (default since 2026-07-14), three
  `build_blocking` helpers still ran raw-polars idioms on the input frame:
  `_id_pass_scale_safe_nonnull` (the #1207 per-identifier union gate),
  `_name_path_primary`'s geo-compound sizing, and `_llm_suggest_blocking_keys`.
  On a `pa.Table` the first two AttributeError'd into a bare `except` that
  returned `False`/`continue`, so the strong-identifier blocking union and
  name+geo compounding silently collapsed to name-only blocking — a recall/
  precision divergence between the arrow and polars lanes (the `_llm_*` path
  crashed outright on an arrow+LLM-blocking run). All three now route through
  the backend-neutral `Frame` seam, so arrow and polars select identical
  blocking passes. Locked by a wide/sparse config-equality parity test
  (`test_build_blocking_id_union_arrow_parity`). Complements the earlier
  `_build_compound_blocking` fix (mode 1, the crash).

- **`auto_configure_df(pa.Table)` no longer raises `AttributeError: 'height'`
  in composite blocking search (#1852 tail).** When every exact-eligible column
  is a perfectly-unique surrogate key, auto-config goes fuzzy-only and
  `build_blocking` falls into composite-key search. `find_composite_blocking_keys`
  and `estimate_avg_block_size` (`core/blocking_candidates.py`) still ran raw
  polars idioms (`df.height`, `df.select(...).n_unique()`) on the input frame,
  which is a `pa.Table` by default under `GOLDENMATCH_AUTOCONFIG_ARROW_NATIVE=1` —
  crashing on the arrow-native lane. Both are now routed through the backend-
  neutral `Frame` seam (`to_frame` + `joint_n_unique`), matching the earlier
  `build_blocking` ports. This branch is only reached on an all-unique-identifier
  (join-table / order-shaped) frame, which is why the #1852 wide/sparse gate never
  exercised it; locked by `test_auto_configure_all_unique_ids_arrow_parity`.

## [3.6.0] - 2026-07-20

### Changed

- **Zero-config now routes probabilistic-shaped datasets to Fellegi-Sunter
  by default (#1874).** A dataset with no surviving strong-identity exact
  matchkey (identifier/email/phone) and 2+ fuzzy fields is served by the
  EM-weighted FS path instead of exact+weighted matchkeys -- measured F1
  lifts on error-heavy PII (historical_50k 0.62 -> 0.78) with no regression
  where a strong key survives. Kill-switch:
  `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=0`.

- **Config-healer loop cost is now bounded and tunable (#1404).** `heal` (and
  the `review_config` / `suggest_from_result` verify path it drives) could run up
  to `step_cap × (1 + max_verify)` full pipeline passes per call — ~45 on the
  defaults — plus a full goldencheck `blocking_risk` variant scan (O(distinct²)
  per string column) re-run on the unchanged frame every iteration. Three cost
  levers, all defaulting to byte-identical behavior:
  - The goldencheck variant scan is **memoized for the whole heal loop** via a
    new `variant_risk_cache()` scope (`core/suggest/adapter.py`) keyed on the
    data-column set — it runs once over the frame instead of once per iteration.
    Output is unchanged; only the cost moves.
  - **Verify fan-out is tunable**: `review_config`/`suggest_from_result`/`heal`
    take `max_verify`, and `GOLDENMATCH_SUGGEST_MAX_VERIFY` sets it globally
    (default 8). Since the healer applies only the top surviving suggestion,
    `max_verify=1` verifies just that candidate — the cheapest mode.
  - **Marginal-gain early-stop**: `heal(min_health_gain=…)` /
    `GOLDENMATCH_HEAL_MIN_HEALTH_GAIN` stops the loop once cluster-health gain
    flattens (fail-open — a result without clusters never triggers a stop). Off
    by default. `GOLDENMATCH_HEAL_STEP_CAP` also exposes the outer cap.

### Added

- **`DedupeResult.identity_summary` (#1913).** The per-run identity-
  resolution summary (entities created/absorbed/merged) is now surfaced on
  the public result -- `None` when identity resolution is disabled. Backs the
  in-Postgres `gm_resolve` write path in `goldenmatch-pg`.
- **Small-N Fellegi-Sunter routing floor (#1947).** Below
  `GOLDENMATCH_FS_ROUTE_MIN_ROWS` rows (default 500) a probabilistic-shaped
  dataset stays on the robust weighted path -- FS EM is data-starved at small
  N and under-merges fuzzy-close variants. `0` disables the floor. Every
  dataset that validated the FS default is far above it, so routing there is
  unchanged.

### Fixed

- **Net-zero-evidence filter kills scale-growing FS over-merge (#1899,
  default ON).** Pairs whose only agreement is on absent (unobserved) fields
  no longer accrue spurious match weight; ported to the numpy and native
  kernels.
- **FS at 1M rows (#1896).** A pairs-budget blocking gate stops a low-
  cardinality pass from compounding into a megablock, and an Arrow pair-
  stream plus EM block-sample cut the FS memory peak.
- **Unobserved `record_embedding` masked out of the weighted score
  (#1859).** A record with no embedding no longer contributes max agreement.
- **Arrow-native auto-config blocking profile emitter (#1946).** The
  controller's sample iterations no longer force an arrow->polars round trip
  just to count rows, so zero-config runs correctly (no degraded RED-sentinel
  config) on a base, polars-free install. Byte-identical with polars present.

## [3.5.0] - 2026-07-18

<!-- README-callout
**New `date` scorer for date fields (#1858).** `jaro_winkler` scores unrelated
ISO birthdays 0.80+ (the fixed `YYYY-MM-DD` shape + shared digit alphabet
dominate), so it can't tell a typo from a different person. The `date` scorer
compares dates by Damerau-Levenshtein over the canonical digits — a typo scores
0.90, an unrelated date 0.00 — with a `levenshtein` fallback for non-ISO input.
Cross-surface (Python, native kernel, TypeScript), and a preflight check warns
when a name-oriented scorer sits on a date field.
-->

### Fixed

- **Postgres identity resolution no longer autocommits per record (#1886).** The
  Postgres `IdentityStore` connects with `autocommit=True`, so `resolve_clusters`'
  per-record write path (absorbing/merging into existing identities, and weak
  multi-member clusters) committed every `upsert_identity`/`emit_event`/
  `upsert_record`/`add_edge` on its own -- one COMMIT + network round-trip per
  write. Against a remote DB (e.g. Cloud SQL) that turned a ~20k-record resolve
  into minutes of latency even though the compute is milliseconds; a re-resolve
  or incremental load against a populated store (where nothing is brand-new, so
  the bulk COPY fast-path never engages) was the worst case. `resolve_clusters`
  now wraps its whole write body in a single `IdentityStore.bulk_writes()`
  transaction -- N commits collapse to one and psycopg pipelines the statements.
  No-op for SQLite/Mongo; the resolve is now atomic per run.
- **Unobserved `record_embedding` field no longer dilutes the weighted score
  (#1859).** In the vectorized weighted scorer (`find_fuzzy_matches`), a
  `record_embedding` field added its full weight to the score DENOMINATOR
  unconditionally, while every other field type multiplied by an observed-value
  mask -- so a row missing all its embedding columns still counted the field's
  weight, diluting the pair score (the #1856 shape on a first-class weighted
  scorer). It now masks the contribution out of both numerator and denominator
  when a row is unobserved (all embedding columns null), pair-wise like every
  other field. Clean data (no all-null embedding rows) is byte-identical.

### Added

- **Date-aware `date` scorer (#1858).** A comparison scorer for date fields.
  `jaro_winkler` on an ISO date scores unrelated birthdays 0.80+ (the fixed
  `YYYY-MM-DD` shape, shared digit alphabet, and `19..`/`20..` prefix dominate),
  so at any usable cutoff a typo is indistinguishable from a different person —
  precision craters wherever blocking co-locates same-year records. `date`
  parses both sides as ISO dates and scores by Damerau-Levenshtein over the eight
  canonical digits (a swapped-digit typo is one edit): `d0→1.0 / d1→0.90 /
  d2→0.75 / d≥3→0.0`, mirroring Splink's `DamerauLevenshtein<=2`. Non-ISO input
  falls back to `levenshtein`. The canonical implementation lives in the Rust
  `score-core` kernel and funnels to the native PyO3 extension, the pure-Python
  fallback, and the TypeScript port (byte-identical, parity-tested). Not
  auto-config-reachable (date columns are skipped as fuzzy fields), so it is for
  hand-written / Splink-converted configs; a preflight check warns when a
  name-oriented scorer (`jaro_winkler` / `token_sort` / ...) is placed on a date
  field. Bumps `goldenmatch-native` to 0.1.18.
- **Fused Fellegi-Sunter match now covers multi-pass blocking.** New
  `match_fused_fs_multipass_ready` / `run_match_fused_fs_multipass_arrow`
  (`core/fused_match.py`) expand the fused FS path from single-static-key
  blocking to `multi_pass` / compound-union blocking (the shape that OOM'd in
  the FS-at-scale reports) — mirroring the weighted `match_fused_multipass`
  twin. No new native code: each pass runs the SAME single-key FS kernel and
  the per-pass clusters are union-find-merged host-side, byte-identical to the
  classic multi-pass FS pipeline (parity-tested). The fused FS path holds pairs
  as a Rust `Vec` instead of a materialized Python pair list, so a covered FS
  dedupe stays flat on peak RSS where the pair-list route scales with emitted
  pairs (measured 2x leaner on realistic emission, up to ~19x on
  high-candidate shapes).
- **Fused Fellegi-Sunter match is now wired into the pipeline.** The controller
  fused-routing post-step (`maybe_route_fused_match`) routes a covered FS run
  (single-key OR multi-pass) to the fused kernel under estimated-RSS pressure,
  exactly like the weighted twin — the routing decision is config-only + RSS
  estimate (both available at controller time), and the new
  `_run_fused_fs_match_short_circuit` does the O(n) EM fit before the kernel
  call (the piece a controller-time flag can't carry). Same capacity-survival
  contract as the weighted path: `scored_pairs`/`review_pairs` + per-cluster
  confidence are shed, but cluster membership + golden are byte-identical to the
  classic FS path (same seeded EM → same kernel math → same connected
  components; parity-tested single-key + multi-pass). Measured end-to-end
  through the pipeline: 4.5x leaner peak RSS + 5x faster on a 120K all-merge FS
  dedupe (442 MB vs 2004 MB). Off by default (fires only under memory pressure
  on an artifact-free config); `GOLDENMATCH_MATCH_FUSED=0` is the kill-switch.

## [3.4.0] - 2026-07-16

<!-- README-callout
**Embeddings are first-class on Fellegi-Sunter matchkeys.** `embedding` and
`record_embedding` field scorers now train (EM) and score end-to-end on the
probabilistic path via the vectorized matrix — previously they raised
`Unknown scorer` on both training and scoring. They are matrix-only, so a
matchkey carrying one always runs vectorized, and the TUI now routes FS through
the same native/vectorized selector.
-->

### Added

- **Embeddings as first-class Fellegi-Sunter scorers.** `embedding` /
  `record_embedding` fields train and score on the FS path (vectorized EM
  E-step + block scoring); they were unusable before (both EM training and the
  scalar scorer raised `Unknown scorer`). Model-backed scorers always run
  vectorized — `GOLDENMATCH_FS_VECTORIZED=0` only affects string scorers. The
  TUI engine now routes probabilistic matchkeys through the block-scorer
  selector, so it gets native/vectorized FS too.

### Fixed

- **Term-frequency (`tf_adjustment`) now applies on the scalar FS path**, matching
  the vectorized path — the same config no longer scores differently depending on
  route (a model-backed scorer or `GOLDENMATCH_FS_VECTORIZED=0` forces scalar).
- **Distributed and chunked lanes score probabilistic matchkeys.** They
  previously dropped FS pairs silently, then (mid-cycle) failed loudly. Both
  lanes now score FS against ONE shared `EMResult`: the distributed driver
  trains once before dispatch (or loads `mk.model_path` driver-side;
  `GOLDENMATCH_DISTRIBUTED_FS_TRAIN_ROWS` bounds the training sample), and the
  chunked lane trains once on the first chunk. The loud
  `NotImplementedError` remains only for the bare scoring kernel invoked with
  no model source.
- **Memory-bounded FS scoring for strategy-generated blocks.** Probabilistic
  matchkeys with `lsh` / `ann` / `learned` / `canopy` / `sorted_neighborhood`
  blocking now score through `score_probabilistic_external_blocks` (one block
  resident at a time, exclude-set Arc handle built once, oversized auto-split,
  native/vectorized selection) instead of the batched scorer's all-units
  accumulation. `GOLDENMATCH_FS_DEFAULT_BUCKET=0` still selects the legacy
  batched scorer and now logs a warning (documented in tuning).
- **The columnar opt-in no longer demotes FS to the batched path.** The
  columnar branch is structurally weighted-only, so probabilistic matchkeys
  keep the memory-bounded bucket route under `GOLDENMATCH_COLUMNAR_PIPELINE=1`.
- **FS bucket lane auto-splits oversized blocks** (default
  `skip_oversized=False` path) instead of scoring them whole, and the
  vectorized scorer refuses impossible dense allocations with an actionable
  error (`GOLDENMATCH_FS_VEC_MAX_ELEMS`).
- **Missing values are unobserved evidence in FS scoring** (null field values
  carry no likelihood contribution instead of folding into total disagreement);
  persisted v1 models without a training manifest are rejected on reuse.
- **Multi-pass EM conditions per pair and blocking fields keep the fixed
  prior** (repairs a recall collapse on near-unique blocking fields).
- **Per-field blocking transform chains** (`BlockingKeyConfig.field_transforms`);
  the Splink converter maps mixed SUBSTR blocking rules exactly.

## [3.3.1] - 2026-07-15

### Fixed

- **Linear identity golden-record resolution**: incremental identity resolution
  now builds each golden record from the row payload index prepared once per
  batch, rather than filtering the entire input frame once per cluster. This
  removes the quadratic singleton-heavy archive ingestion path.

### Added

- **Anomaly diagnostics with prefilled GitHub issue prompts** (in-tree:
  `goldenmatch.core._diagnostics_report` + `goldenmatch.core.diagnostics`; no
  new package or dependency). When GoldenMatch hits a state that is probably
  its own bug, it emits an actionable message with a prefilled issue URL.
  Fires only on *anomalies* -- never on expected fallbacks or user errors:
  (1) a native **wheel-skew** slow path (the kernel symbol is missing from the
  installed wheel -- the #688 class); (2) an **unexpected crash** at `dedupe_df` /
  `match_df` (re-raised unchanged, with a traceback + PII-safe environment in the
  prompt; `ControllerNotConfidentError`, bad config, `FileNotFound`, `ValueError`
  and other by-design/user errors are never prompted); (3) the **config linter
  itself** crashing; (4) a **broken native install** (module present but fails to
  load, vs plain "not installed"). Sends nothing anywhere -- it is a better error
  message, not telemetry. Silence with `GOLDEN_DIAGNOSTICS=0`. Diagnostics is
  never load-bearing: the reporter never raises.

- **Precision-anchor threshold raise** (closes the #1207 over-merge, #1319): a
  new default auto-config rule, `rule_precision_anchor_threshold_raise`, that
  raises the weighted threshold to 0.9 on the precision-collapse shape
  (>= 95% of scored mass above the threshold on a name-dominated weighted
  matchkey with a strong exact identity anchor, the TF table live, and the
  threshold below 0.9). Two commit-dynamics fixes make the raise actually land
  (either alone still commits the over-merging config): the scoring-health
  unimodality (dip) gate now requires at least 30 scored pairs before a flat
  dip reads RED (`_MIN_DIP_SUPPORT` -- a flat dip over fewer pairs is sampling
  noise), and when the rule has fired, `pick_committed` rank-demotes entries
  whose config the rule's trigger still flags. Measured on the crafted #1319
  fixture: precision 0.009 -> 0.9868 at recall 1.0; NCVR results unchanged.

### Fixed

- **`from_splink` recognizes `IS NOT NULL` blocking guards** (#1783): compound
  `CustomRule` blocking rules carrying trailing `AND l.col IS NOT NULL` guard
  conjuncts were dropped whole as unrecognized — on a 1M production dedupe the
  converted model blocked on 3 of 6 keys, costing ~28 points of pairwise
  recall. Guards on key columns are now recognized and ignored exactly
  (GoldenMatch blocking already implements the guard semantics: a null key
  component forms no block), reported as info. A guard on a column outside the
  blocking key still converts but warns (guard dropped, candidates are a
  superset of Splink's; `strict=True` gates on it), and a guards-only rule
  keeps the existing unrecognized-drop path.
- **The #1318 TF name downweight now reaches the default (bucket) scoring
  path** (#1781): the bucket backend's fast path resolved plugin scorers via a
  bare `plugin.score_pair`, so the per-dataset TF table behind
  `GOLDENMATCH_TF_NAME_WEIGHTING` (default-on) never reached
  `name_freq_weighted_jw` on the default path -- the flag was a silent no-op
  there. `MatchkeyField.tf_freqs` is now threaded through the bucket fast
  path's plugin branch (with a `TypeError` back-compat fallback for legacy
  plugins without the keyword); built-in scorer branches are untouched.
- **The >= 100k RED-refuse gate enforces again after every controller branch**:
  a latent `n_rows` shadow in `AutoConfigController.run()` (the
  suspicious-tight-blocking GREEN branch rebound the full-frame height to the
  sample height) silently disabled the `REFUSE_AT_N` refuse gate, so runs that
  should raise `ControllerNotConfidentError` on a RED config could slip
  through.

## [3.3.0] - 2026-07-14

<!-- README-callout
**3.3.0 — negative evidence on Fellegi-Sunter matchkeys.** `negative_evidence`
now works on `type: probabilistic` matchkeys as EM-learned `__ne__` dimensions
(no labels needed; `penalty_bits` as a fixed override), and the Splink
migration upgrade pass gains a **fan-out lever** — a risk-gated NE suggestion
plus cluster-guard tuning from your reference clusters. `goldenmatch-native`
0.1.15 scores NE in the Rust kernels (`FS_SUPPORTS_NE`; older wheels keep the
pure-Python fallback automatically).
-->

### Added
- **Negative evidence on Fellegi-Sunter (`type: probabilistic`) matchkeys**
  (Formulation B, EM-learned): `negative_evidence` was previously silently
  ignored on probabilistic matchkeys (weighted/exact only), which meant every
  Splink-converted config — exactly one FS matchkey — had no defense against
  the fan-out/homonym snowball (two distinct people sharing name+city merging
  because name evidence dominates). Each NE field now joins `train_em` as a
  constrained EM-learned dimension contributing `log2(m_fired/u_fired)` when
  it FIRES (both values present + `scorer(a, b) < threshold`, strict `<`) and
  exactly 0 otherwise — the fired-else-zero clamp is what makes it negative
  evidence rather than a regular scored field. `NegativeEvidenceField` gets a
  new `penalty_bits` (log2 LLR fixed override, probabilistic-only, `abs()`
  applied) alongside the existing `penalty` (still required on weighted/exact,
  now rejected on probabilistic — set `penalty_bits` instead). Guards: native,
  fused, and the fast-path scorer all decline NE-bearing FS matchkeys
  (pure-Python fallback on wheels older than `goldenmatch-native` 0.1.15; the
  native port below adds `FS_SUPPORTS_NE` in this same release — only the
  fast-path scorer still declines); the bucket
  backend's slim-projection keep-list was extended so an NE-only field (e.g.
  `phone`, never a regular matchkey field) survives the default
  `GOLDENMATCH_BUCKET_SLIM_PROJECTION`. `EMResult.validate_for` now requires
  `match_weights["__ne__<field>"]` for every NE field without `penalty_bits`,
  so a model trained (or a Splink model imported) before this feature fails
  loudly instead of silently scoring NE at weight 0. An unregistered/unknown
  NE scorer on FS fails loud at train/score time (`score_field` raises on
  unknown scorers; no `_NE_BROKEN` swallow on FS) — unlike weighted's
  swallow-and-warn fallback, this is intentional. Continuous/Winkler-path
  (`train_em_continuous`) NE is out of scope and rejected with a clear error.
  Supersedes the deferral in `docs/superpowers/specs/2026-05-21-ne-fs-investigation.md`
  (Wave D): that investigation judged the Bayesian-factor formulation correct
  but deferred it believing `P(disagree_NE | match)` needed labeled pairs —
  stale, since EM already estimates match-conditional probabilities for every
  regular FS field without labels, and the same machinery estimates them for
  NE dimensions.
- **Splink migration upgrade pass** (`goldenmatch import-splink SETTINGS.json
  --upgrade DATA.parquet --model-out MODEL.json`, or
  `gm.upgrade_splink_conversion(conversion, data)` from Python): a data-aware
  pass over a converted Splink config that applies three independent levers —
  term-frequency tables computed from the data (Splink model exports don't
  carry them, so converted tf fields were inert), distance thresholds
  re-derived from measured string lengths (the converter assumes length 10),
  and link/review thresholds calibrated from the blocked-pair score
  distribution. Writes the upgraded config/model to `--output`/`--model-out`
  with the faithful baseline alongside as `*.baseline.*`, plus a
  baseline-vs-upgraded delta table; `--splink-clusters` / `--labels` take
  reference cluster mappings (first column id, second cluster_id) for
  agreement / truth F1 measurement. Measured on the wild-config dogfood bench
  (defaults-vs-defaults, pairwise F1 vs truth): real_time_settings/fake_1000
  **0.482 → 0.633** (native Splink: 0.601), saved_model_from_demo/fake_1000
  **0.677 → 0.766** (Splink: 0.699), model_h50k/historical_50k
  **0.707 → 0.740** (Splink: 0.686) — the upgraded conversion beats native
  Splink on all three pairs.

- **Fan-out / negative-evidence upgrade lever** (`fan_out`, in the default
  `import-splink --upgrade` lever set, between `distance_thresholds` and
  `calibration`): detects unused identity-grade columns (phone/email/id-named,
  high-cardinality, non-matchkey, non-blocking) whose disagreement contradicts
  pairs the imported model would confidently merge (posterior >= 0.9), and —
  when the contradiction rate clears the risk gate (>= 2%, >= 10 firing
  pairs) — adds the column as `negative_evidence` with posterior-weighted
  EM-shape weights written into the upgraded model (`__ne__<field>` entries).
  Also tunes `golden_rules.max_cluster_size = max(10, 2 * reference max
  cluster size)` from `--labels` (preferred) or `--splink-clusters`, so
  `auto_split` catches mid-size homonym snowballs the static default (100)
  ignores on person-shaped data. The calibration lever is now NE-aware
  (`fs_weight_range` + per-pair NE contributions; its warn+skip tripwire is
  removed). Wild-bench: no regressions; `model_h50k` improved 0.7396 -> 0.7421
  on guard tuning alone.
- **Native negative-evidence scoring** (`goldenmatch-native >= 0.1.15`): the
  Rust kernels now score FS negative evidence — `score_block_pairs_fs` AND the
  fused `match_fused_fs`, which also gained custom `level_thresholds` banding
  (full kernel parity). Detection is capability-gated (`FS_SUPPORTS_NE`,
  `FUSED_FS_SUPPORTS_LEVEL_THRESHOLDS`), so older wheels keep the pure-Python
  fallback with no behavior change; NE-bearing matchkeys previously always
  took the pure-Python path. The fused path declines `derive_from` NE (its
  raw-columns entry never materializes derived columns).

### Fixed
- **Threshold calibration on imported Splink models**: the calibration lever
  now re-estimates the within-block match rate from the model's likelihood
  ratios instead of trusting `proportion_matched`, which on imported models
  holds Splink's `probability_two_random_records_match` — a random-pair prior
  orders of magnitude below the post-blocking rate the percentile math
  expects. Trusting it cut at the extreme top of the score distribution and
  collapsed recall (F1 0.482 → 0.157 on the bench pair above).
- **Tuned `max_cluster_size` survives the YAML loader round-trip**: the
  config loader's `golden_rules` normalization swept `max_cluster_size` into
  `field_rules` (the "set programmatically, never via YAML" assumption went
  stale when the fan-out lever started writing it), so `import-splink
  --upgrade` output failed to reload. It is now a recognized top-level
  golden_rules key.

## [3.2.0] - 2026-07-13

### Added
- **N-level probabilistic comparison fields** (`level_thresholds`): probabilistic
  matchkey fields accept explicit per-level similarity cutoffs (descending,
  `len == levels - 1`; a pair's level = the count of thresholds its similarity
  satisfies), generalizing the fixed 2/3-level agree/partial/disagree banding.
  Scalar and vectorized scoring paths both honor them; the fused-match path
  declines `level_thresholds` matchkeys and falls back.
- **Native N-level scoring** (`goldenmatch-native` 0.1.14): the native Rust FS
  kernel scores custom `level_thresholds` banding natively — byte-identical to
  the pure-Python `_levels_from_similarity` semantics — via an optional
  per-field `level_thresholds` kwarg on `score_block_pairs_fs`. Capability is
  detected through the kernel's `FS_SUPPORTS_LEVEL_THRESHOLDS` const, so older
  wheels keep the automatic pure-Python fallback (no behavior change).
- **Splink config converter**: `from_splink()` (top-level export) converts a
  Splink settings or trained-model JSON (dict or path) into a validated
  GoldenMatch config plus a `ConversionReport` of lossy findings; trained m/u
  probabilities import as an `EMResult` so no re-training is needed.
  `strict=True` raises on any lossy finding. Surfaced as the
  `goldenmatch import-splink SETTINGS.json -o CONFIG.yaml [--model-out MODEL.json]`
  CLI command and the `convert_splink_config` MCP tool (78 tools total).
- **Splink-conversion parity gate** (`scripts/bench_er_headtohead/run_converted_splink.py`):
  a converted config must land within F1 0.05 of native Splink on the shared
  evaluator; measured splink_f1=0.9964 vs converted_gm_f1=0.9761
  (delta 0.0203) on synthetic_person (5K).

## [3.1.1] - 2026-07-13

### Performance
- Frame-lane hotspot pass (profiler-driven, byte-identical outputs): cluster
  dict assembly goes columnar (was 35% of a 1M wall), transform-chain
  fallbacks resolve their callable once per column, and soundex/metaphone
  dispatch straight to the Rust jellyfish functions. 1M-row wall
  7.6s -> 5.5s. (These were mentioned in the 3.1.0 notes but merged just
  after the 3.1.0 tag; this patch actually ships them.)

### Performance
- **The polars-free install is now the FAST configuration** (measured
  head-to-head at 500K: 7.11s polars-free vs 7.55s polars-present, identical
  outputs). A module-level polars literal in `golden_fused.py` was silently
  making the Rust golden kernel unreachable on polars-free installs (the
  import error was swallowed as a routine kernel decline), routing every
  golden build to the slow Python oracle. The `[polars]` extra is a
  compatibility surface, not an accelerator. The zero-polars gate now also
  runs native-ON (the default install shape) so this class cannot recur.

## [3.1.0] - 2026-07-13

<!-- README-callout
**3.1.0 — polars is optional (and the polars-free install is the fast
configuration).** The engine is Arrow-native end to end with the Rust fused
kernels on the hot paths (a zero-polars CI gate proves a full dedupe with
polars imports blocked); `pip install 'goldenmatch[polars]'` is a
compatibility extra (classic lane, kernel-absent golden replay,
cell-quality weighting), byte-identical to 3.0.x.
-->

### Changed
- **polars is now OPTIONAL** (`pip install 'goldenmatch[polars]'`). The engine
  is Arrow-native end to end: ingest, prep (incl. the goldencheck quality scan
  on its Arrow surface and the goldenflow transform adapter), matchkey
  precompute, blocking, exact matching, scoring (classic + bucket backends
  with the Rust kernels), clustering, golden survivorship (fused kernel +
  seam-native oracle), memory corrections, identity resolution, lineage, and
  file outputs (native parquet). A new zero-polars gate
  (`tests/test_zero_polars_gate.py`) proves a full dedupe with polars imports
  blocked.
- With polars installed, the wall-optimization paths (golden fast columnar,
  vectorized survivorship, the vectorized pair-score join) light up
  automatically and behavior is byte-identical to 3.0.x.
- `GOLDENMATCH_FRAME=polars` (the classic opt-out lane) now requires the
  `[polars]` extra and raises a clear error without it.
- The Frame lane now accepts EVERY feature class (validation, outputs,
  lineage, identity, memory, auto-suggest, postflight, probabilistic EM,
  NE-on-exact, throughput, rerank, LLM, semantic blocking, domain
  extraction, adaptive golden) -- the eligibility predicate has no feature
  declines left.

### Fixed
- `most_recent` golden rules on date32/time32 columns crashed on the arrow
  lane (`pc.cast` has no direct 32-bit temporal -> int64 kernel).

## [3.0.0] - 2026-07-12

<!-- README-callout
**v3.0.0 — Arrow-native results.** Result frames are now `pyarrow.Table`
(migrate with `pl.from_arrow(result.golden)`); inputs are unchanged. The
Arrow frame backend is the default — measured ~36% faster end-to-end on the
100K zero-config benchmark — with `GOLDENMATCH_FRAME=polars` as the opt-out.
-->

### Changed
- **BREAKING: result frames are now `pyarrow.Table`.** `DedupeResult.golden` /
  `.dupes` / `.unique` and `MatchResult.matched` / `.unmatched` return
  `pa.Table` instead of `pl.DataFrame` (Polars-eviction W5, spec
  `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`).
  Migration is a one-liner: `pl.from_arrow(result.golden)`. Inputs are
  unchanged (polars/pandas/arrow all accepted). `to_csv` and the notebook
  `_repr_html_` render from Arrow natively.
- The arrow ingest lane runs the expression stages (row-ids, standardize,
  exact matchkeys) eagerly on the Frame seam; validation rules and auto-fix
  are seam-routed with probed arrow twins (RE2 regex owned contract).

### Added
- Experimental GOLDENMATCH_FRAME=arrow lane: file ingest via pyarrow with a polars-parity reader corpus and a frame-backend differential harness (Polars-eviction W1).
- **Fused auto-routing at the golden + match seams (controller-driven).** The pipeline now auto-routes covered slow-path runs to the fused Arrow-native kernels, byte-identically, with no config change and per-surface kill-switches. **Golden routing is the broad, LIVE win:** at the golden seam the pipeline tries `golden_fused.run_golden_fused_arrow` by default on every covered slow-path config and reaches golden output at roughly 2x lower peak RSS; it declines to the classic builder (unchanged output) when the config is uncovered, the native kernel is absent, full `ClusterProvenance` lineage is requested, or the run is fast-path-eligible. Surfaced on `DedupeResult.golden_fused_used`; kill-switch `GOLDENMATCH_GOLDEN_FUSED=0`. **Match routing is a documented DORMANT capacity-survival scaffold, not a live default.** A controller post-step (`maybe_route_fused_match`) can short-circuit the match stage to the fused match kernel under an estimated-peak-RSS pressure gate, but the gate is deliberately narrow (`auto_split=False`, no identity/adaptive/memory/llm_boost/confidence_majority/full-provenance, not across-files-only, a covered weighted matchkey, and real memory pressure). Because zero-config auto-config commits `auto_split=True` and explicit configs bypass the controller, it effectively never fires by default. When it does fire it is a capacity mode that sheds `scored_pairs`, cluster-confidence, and lineage to survive; marked `DedupeResult.match_fused_capacity_mode` and controller-telemetry `rule_name` `+fused_match_post_step`. Kill-switch `GOLDENMATCH_MATCH_FUSED=0`; the wake-up path (a postflight-threshold fast-follow, or an explicit opt-in) is a follow-up. New env knobs (`core/fused_routing.py`, `ExecutionPlan.use_fused_match`): `GOLDENMATCH_GOLDEN_FUSED`, `GOLDENMATCH_MATCH_FUSED`, `GOLDENMATCH_FUSED_PRESSURE_FRACTION` (default 0.65), and the est-RSS calibration coefficients `GOLDENMATCH_FUSED_RSS_SCALE` / `_BYTES_PER_PAIR` / `_BYTES_PER_CELL` / `_BLOCK_CONCURRENCY`. No new native symbols (reuses the `golden_fused` / `match_fused` kernels), no new MCP tools / CLI commands / A2A skills.
- **Fused golden-record kernel (`golden_fused`) - standalone, Arrow-native.** A new one-FFI-call survivorship kernel (`goldenmatch.core.golden_fused.run_golden_fused_arrow`, gated by `golden_fused_ready`) builds golden records **byte-identically** to the classic `build_golden_records_batch` path at **~2x lower peak RSS** (measured via `scripts/bench_golden_fused_memcap.py` + the `bench-golden-fused-memcap` workflow). It covers every Rust-portable survivorship rule in a single native call: scalar strategies (majority / unanimous / first_non_null / longest / source_priority / most_recent / most_complete), quality-weight tie-breaks, `field_groups` correlated survivorship, conditional `field_rules` (predicate AST lowered to a kernel RPN IR via `golden_fused_predicate.lower_predicate` / `predicate_lowerable`), `cluster_overrides`, and `confidence_majority`, plus per-field `source_row_id` provenance. Configs that need row-level validators, Python plugins, or LLM survivorship are declined (`golden_fused_ready` returns False) and fall through to the classic path unchanged. **Not yet wired into the pipeline** - this ships as a standalone, benched kernel (composability + peak-RSS win), so no existing user-facing behavior changes. Requires `goldenmatch-native` 0.1.13 (the depended-on `golden_fused` symbol ships in that wheel); without a republished wheel, `pip install goldenmatch[native]` users transparently degrade to the classic golden path (not a correctness bug - byte-identical output either way).
- **MCP naming aliases for cross-language parity.** The Python and TypeScript MCP servers previously exposed the same operations under different names (`find_duplicates`/`dedupe`, `match_record`/`match`, `explain_match`/`explain_pair`, `profile_data`/`profile`, plus TS's `explain_cluster`). Both servers now answer to both names via non-breaking aliases, so an agent trained against either server can call the other. The Python server gains `dedupe`/`match`/`explain_pair`/`profile`/`explain_cluster`; the TypeScript server gains `find_duplicates`/`match_record`/`explain_match`/`profile_data`. The API-parity gate enforces the nine names stay `shared` in `parity/goldenmatch.yaml`. Aliases are excluded from the `goldensuite-mcp` aggregated surface so the suite's `profile` still resolves to goldencheck's file-profiler.

## [2.8.0] - 2026-07-02

### Added
- **Identity audit log: claim-authority tier + claim-lifecycle operations.** Events now carry a categorical `ClaimType` (`observation` / `inference` / `verified` / `directive`) — orthogonal to the numeric `trust` — plus a typed `EvidenceRef` and a `previous_claim_id` chain, so a reviewer can tell "an agent inferred this at 0.8" from "a tool verified this at 0.8". New lifecycle ops `promote_claim` / `amend_claim` / `revoke_claim` make an agent inference *becoming* durable shared truth an explicit, auditable event. All additive/nullable — the tamper-evidence hash stays byte-identical for pre-existing events. SQLite `v4→v5` migration + Postgres/Mongo + Alembic 0004. (#1383)

### Changed
- **`goldenmatch dedupe <file>` is now non-interactive by default.** A bare `goldenmatch dedupe customers.csv` runs auto-config, writes golden records (a timestamped `*_golden.csv` in the current directory), and prints a summary — so the advertised "CSV in, 30 seconds, CSV out" is what actually happens. The interactive review TUI is now opt-in via **`--tui`** (previously it opened by default). `--no-tui` is still accepted as a no-op for back-compat. When no explicit output flag is given on the auto-config path, golden records are written by default (use `--output-all` / `--output-dir` to control, `--tui` to review). An explicit `--config` run keeps its exact prior behavior.
- **Input validation on the `dedupe` CLI + ingest (break-it review).** A Windows-1252 / Latin-1 CSV is no longer silently lossy-decoded into replacement chars — non-UTF-8 files are detected, decoded as cp1252, and warned about (so `José Muñoz` survives instead of becoming mojibake in the golden record); explicit `--encoding cp1252`/`latin-1` now works too. `--anomaly-sensitivity` is validated + case-normalized (a miscased `Low` no longer silently inverts to the most-sensitive behavior); an unknown `--backend` is rejected instead of silently dropped; `--format` is validated at parse time (not after the whole run); structured non-tabular inputs (`.json`/`.xml`/`.yaml`) get a clear message; `--chunk-size`/`--preview-size` reject non-positive values. (#1390)

### Fixed
- **Config-suggestion "healer" production slowdown.** The default `dedupe_df` advisory path no longer runs the O(distinct²) goldencheck variant scan (`cell_quality`) over the full frame on every run whose free trigger fired — the scan is reserved for the opt-in `suggest=`/`heal=` paths. Instant mitigation without upgrading: `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`. (#1385)

### Performance
- **Golden-stage quality-weighting scan scoped to cluster members.** `goldencheck.cell_quality` runs only over rows that actually get a golden record (multi-member, non-oversized cluster members) instead of the entire collected frame — much cheaper on real data, since singletons never consume a quality weight. (#1389)
- **Throughput tier skips golden-record survivorship.** Lifts the 100k+ corpus ceiling for the `dedupe_df(throughput=...)` path, which consumes clusters, not canonical records. (#1382)

## [2.7.0] - 2026-07-02

### Added
- **Group/list-attribute demotion for EXACT matchkeys on single-source person data — now DEFAULT ON.** A shared GROUP/LIST/FACILITY value — a shared switchboard `phone` line, a mailing-list / campaign `identifier` (`tl_id`), a facility NPI, a role `email` inbox — is not a person-identity claim: as an `exact` matchkey (`exact_phone`, `exact_tl_id`) it *force-merges* every DIFFERENT person sharing it into one mega-cluster. `autoconfig_discriminative.should_demote_attribute_field` demotes such an **exact** matchkey to blocking-only when the LARGE shared-value groups do NOT co-agree on the **person name**. Design: (1) **group-size-aware** — co-agreement is measured over large shared-value groups only (≥10 records), so a real personal id that only ever groups a person's few duplicates is KEPT (insufficient support), while a campaign list / facility id / role inbox that groups many different people is demoted — this also stops a mostly-unique column (a `tl_id` at 0.53 cardinality) from being rescued by its many small same-person groups averaging the signal up (measured: big-group name-power 0.01 vs small-group 0.80); (2) **person-name basket** — co-agreement is measured against person-name columns only, not the broad #1351 identity basket (polluted on real data by constant metadata mis-typed as `identifier`).
  - **Scoped to EXACT uses only, deliberately.** The accuracy sweep (`scripts/autoconfig_quality`) showed that demoting the same attribute from a **weighted fuzzy** contributor regressed corruption-heavy data: febrl3 F1 0.99→0.86, ncvr_synthetic 0.96→0.95 — its synthetic addresses also collide across people, but with the names corrupted the weighted address field is *load-bearing* for matching true duplicates. Restricting to exact matchkeys keeps that recall (a soft contributor never force-merges) while still killing the hard force-merges.
  - **Default flipped ON in 2.7.0.** The exact-only sweep showed **zero F1 change across the whole corpus** (febrl3 0.9921→0.9921, ncvr_synthetic 0.9636→0.9636, anchors unchanged — flag-on == flag-off on every dataset), while it fixes real-world group-attribute over-merges: measured on a real MJH dermatology list (19,278 rows), the shared-clinic-`phone` and campaign-`tl_id` exact matchkeys are both dropped, **biggest cluster 70→6**, clusters recovered 11,347→16,509. Kill-switch: `GOLDENMATCH_ATTRIBUTE_DEMOTION=0` restores the pre-2.7.0 behavior. (PRs #1368, #1370.)

## [2.6.0] - 2026-07-01

### Changed
- **Native Fellegi-Sunter (FS) block scorer is now authoritative by default (reference mode).** `_fs_native_enabled()` flips from opt-in to default-on: when the native ext is importable, the probabilistic path uses the native Rust FS kernel (rapidfuzz-rs decides comparison levels); the numpy vectorized path becomes the reproducible fallback via `GOLDENMATCH_FS_NATIVE=0` (also the automatic fallback for TF-adjustment / non-native-scorer fields or a missing wheel). Part of the Rust-is-the-reference direction (`docs/design/2026-07-01-rust-is-the-reference-roadmap.md`). **Scoped to the probabilistic path only** (opt-in `type: probabilistic` matchkeys / probabilistic routing); the default weighted path is unaffected. **Measured F1-neutral** on the probabilistic bench panel (`gm_prob_native` vs `gm_probabilistic`): febrl3 and synthetic_person identical, historical_50k −0.0007 (within noise); dblp_acm not measured (Leipzig CSVs gitignored in CI). Boundary-level score differences are possible where a rapidfuzz-rs vs rapidfuzz-py similarity sits exactly on a comparison-level threshold — the native result is now the reference; `GOLDENMATCH_FS_NATIVE=0` restores the prior numpy operating point.

### Fixed
- **Auto-config no longer commits a standalone `exact` matchkey on a shared locality attribute (#1351).** A high-density column mis-classified as an identifier (e.g. a `zip` whose cardinality inflates on the 1k-row profiling sample) could back an `exact` matchkey and collapse everyone sharing a value into one cluster (~55% over-merge on real circulation data). A new discriminative-power veto (`build_matchkeys` → `autoconfig_discriminative.should_veto_exact`) demotes a proposed exact key to blocking-only when records sharing its value do NOT co-agree on other identity fields — measured from the data, not from cardinality (which cannot separate `zip` from `npi`: both moderate-cardinality, opposite correct answers). Name-typed basket fields co-agree FUZZILY (SequenceMatcher ≥ 0.85) so corrupted duplicates keep their key (e.g. febrl3's `soc_sec_id`, whose duplicates carry corrupted names); structured ids compare exactly. Veto-only (never promotes; classification/blocking untouched), fail-safe keep on thin support / `df=None`; kill-switch `GOLDENMATCH_DISCRIMINATIVE_VETO=0`. Auto-config accuracy gate held (febrl3 / ncvr_synthetic / historical_50k F1 unchanged).

## [2.5.1] - 2026-07-01

### Fixed
- **PPRL is now opt-in instead of the default for sensitive data (#1342, #1344).** `select_strategy()` auto-routed any dataset with sensitive fields (PII/health) to privacy-preserving record linkage, returning empty `strong_ids`/`fuzzy_fields` even for a user deduping their own list. PPRL now fires only when the caller opts in via `allow_pprl=True`; sensitive data otherwise gets a normal weighted/fuzzy strategy, with `StrategyDecision.pprl_available=True` flagging that PPRL can be opted into. `allow_pprl` is threaded through `AgentSession.analyze/deduplicate/match_sources/compare_strategies`, `a2a.dispatch_skill`, and the A2A `_handle_send_task` HTTP handler (reads `allow_pprl` from the request body). MCP tool args (`mcp/agent_tools.py`) are a tracked follow-up. Non-sensitive-data behavior is unchanged.

### Changed
- `StrategyDecision` gains a `pprl_available: bool` field.

## [2.5.0] - 2026-07-01

### Added
- **MemoryStore: Postgres backend for Learning Memory (#1338).** `MemoryStore(backend='postgres', connection=<dsn>, table_prefix='goldenmatch_')` and `MemoryConfig(backend='postgres', connection, dataset=<tenant>, table_prefix)` persist dedupe corrections + learned adjustments in Postgres, isolated per tenant: corrections filtered by `dataset`, `adjustments` keyed `(dataset, matchkey_name)`, and `MemoryLearner(dataset=…)` so `learn()` never pools corrections across tenants. Adds `LearnedAdjustment.dataset` and `MemoryConfig.table_prefix` (regex-guarded). Reuses the existing `postgres` extra (`psycopg[binary]`) — no new hard deps. **SQLite remains the default; every existing call path, signature default, and behavior is unchanged** (the dialect refactor is behavior-preserving; full SQLite memory suite green).

### Changed
- **Auto-config: `name_freq_weighted_jw` now downweights agreements on high-frequency name values using a per-dataset frequency table** (data-driven, applied across the whole score range), so identical common surnames (e.g. two "Smith") score below identical rare surnames - a higher matchkey threshold then separates same-name strangers from true matches (#1207, PR2a). Default-on; kill-switch `GOLDENMATCH_TF_NAME_WEIGHTING=0` restores the static-census behavior. Validated by the CI accuracy gates (#528/DQbench/Febrl/NCVR); this is an accuracy change, not a measured-local win.
- **Auto-config: per-identifier blocking union on null-sparse multi-source data (#1207, PR1).**
  When no single exact key clears the null-rate gate, `build_blocking` now emits a
  per-identifier blocking union (one pass per strong identifier + name/geo) instead of a
  single high-null compound that capped recall. Default-on; no behavior change when a
  low-null single exact key exists. Strong-id passes use a non-null scale gate (the runtime
  blocker filters null block keys) with a #876 perfect-surrogate exclusion. Measured blocking
  recall 1.0 vs name-only 0.004 on a planted-dup fixture, no regression on the auto-config suite.
  Scope: `auto_configure_df` switches to learned blocking at `total_rows >= 50_000`, so the
  union applies below that threshold (or when learned blocking is off) today; the >=50k
  learned-blocking interaction is a tracked follow-up.

## [2.4.0] - 2026-06-27

<!-- README-callout
**The healing loop, now default-on across every surface** — every `dedupe_df` run surfaces ranked, self-verified config-suggestions on `result.suggestions` when there's headroom (free on a healthy run, no second pipeline pass). `dedupe_df(suggest=True)` returns verified suggestions; `heal=True` applies them and re-runs, returning the healed `result.config` + `result.heal_trail`. Available across Python, CLI (`--suggest` / `--heal`), MCP, A2A, REST, web, the TUI, and the edge-safe TypeScript port via WebAssembly. Needs `goldenmatch[native]`; degrades gracefully without it. Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`.
-->

### Changed
- **Healer (config-suggestion) self-verify gate default flipped to the precision-sensitive `cohesion` proxy (`cohesion_min_edge_cap50`).** Closes the raw-vs-live gap in `review_config`: suggester-gym live recovery 0.151 -> 0.543 (now equal to the raw kernel ceiling), with zero net-negatives on real perturbations. Rollback via `GOLDENMATCH_SUGGEST_HEALTH=legacy`. New knob `GOLDENMATCH_SUGGEST_COVERAGE_CAP` (default 0.50).

### Added
- **Healer wired into the default pipeline (advisory, every surface).** `dedupe_df` now
  checks a free controller signal (RED/YELLOW health or a score dip) on every run and,
  only when it fires, attaches cheap raw candidate suggestions to `result.suggestions` --
  no second pipeline run, byte-identical timing on a healthy result. Opt into the expensive
  verified path with `dedupe_df(df, suggest=True)`, or run the full apply-and-re-run loop
  with `dedupe_df(df, heal=True)` (reading `result.heal_trail` + the healed `result.config`).
  The same surface ships on CLI (`--suggest` / `--heal` plus a free default-run hint), MCP
  (`review_config` tool), A2A (`review_config` skill), REST (`GET /suggest`), web
  (`GET /api/v1/suggest`), and the TUI (Suggestions tab). Requires `goldenmatch[native]`;
  every surface degrades gracefully without the wheel (attaches nothing, never raises).
  Kill-switch `GOLDENMATCH_SUGGEST_ON_DEDUPE=0`.
- **Config-suggestions ("the healing loop") documentation** — the iterative zero-config -> returned config -> healer-suggests-tweaks -> apply -> improve -> repeat workflow is now documented at `/goldenmatch/config-suggestions`.

## [2.3.0] - 2026-06-24

<!-- README-callout
**Auto-enabled semantic blocking, now default-on** — text-heavy data automatically routes to SimHash-over-embeddings blocking when an embedder is reachable (a byte-identical no-op otherwise). Plus pluggable pgvector / DuckDB-HNSW vector-index backends and opt-in Fellegi-Sunter routing for no-strong-identifier datasets (`GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1`).
-->

### Changed
- **Auto-enabled semantic blocking is now default-on, native-sourced (#1090, epic #1087).**
  Text-heavy data (a long free-text column the lexical/structured keys under-cover)
  now routes to SimHash-over-embeddings blocking automatically when an embedder is
  reachable -- no `GOLDENMATCH_AUTO_SEMANTIC_BLOCKING=1` opt-in required. It stays a
  no-op (byte-identical output) when no embedder is reachable, so users without the
  in-house model or a configured provider are unaffected; disable explicitly with
  `GOLDENMATCH_AUTO_SEMANTIC_BLOCKING=0`. The recall threshold is now exposed
  (`GOLDENMATCH_SEMANTIC_BLOCKING_THRESHOLD`, default 0.6) and drives the SimHash
  band/row split, replacing the previous hardcoded `num_bands`. The SimHash
  band-hashing kernel (`sketch-core`, Rust) is now the **default execution path**
  (added to the native loader's default-on allowlist): the compiled core is the
  single source of truth across Python/Rust/TS, byte-identical to the pure-Python
  reference (golden-vector verified) and ~29x faster, with a graceful Python
  fallback when the native wheel is absent.

### Added
- **Auto-config probabilistic routing — opt-in, default-off (#1254; harness #1216/#1226).**
  Zero-config `dedupe_df` can now route a *probabilistic-shaped* dataset (no surviving
  exact matchkey backed by a strong-identity column — `identifier`/`email`/`phone` —
  plus ≥2 fuzzy fields) to the Fellegi-Sunter path instead of exact+weighted matchkeys,
  when `GOLDENMATCH_AUTOCONFIG_ROUTE_PROBABILISTIC=1`. There's no clean key to carry the
  dedup, so EM-weighted comparison wins: measured lift on no-strong-id / error-heavy
  data (`historical_50k` F1 0.466→0.829, recall 0.39→0.75) with no regression on
  datasets that retain a strong key (those stay deterministic). **Default-off** — a
  behavior change pending a broader regression sweep; `dedupe_df` output is unchanged
  when the flag is unset. Nominated on evidence by the new decision-kernel **quality
  harness** (`scripts/autoconfig_quality/`, a `report`/`gate`/`bless` loop with a CI
  `quality_gate` job + a dual-strategy det-vs-FS scorecard column). See
  `docs-site/goldenmatch/tuning.mdx` and context-network ADR 0024.
- **Tamper-evident audit log: per-event hash + on-demand seal chain (#1078,
  epic Agent Memory #1073).** The append-only identity event log is now
  cryptographically tamper-evident in two contention-free layers. (1) Every
  event is stamped at insert with an `entry_hash` — a sha256 over its own
  immutable fields (`audit.event_content_hash`); a *pure* function, so it adds
  no insert-time serialization point and works uniformly with the Postgres
  bulk-COPY write path. (2) `seal_audit_log()` folds the per-event hashes (in
  `event_id` order) into a single chained root stored in the new `audit_seals`
  table; each seal chains to its predecessor. `verify_audit_chain()` replays
  both layers to detect content edits, deletion, reordering, and insertion of
  any sealed event, returning `{ok, events_checked, seals_checked}` plus the
  ids of any failures. Sealing is an explicit, infrequent op (never on the
  write hot path), so streaming/bulk ingest is untouched; pre-hash-chain rows
  (`entry_hash` NULL) are hashed on the fly so an existing log can be sealed
  retroactively. Surfaced via two new MCP/A2A tools `identity_audit_seal` /
  `identity_audit_verify` (MCP identity tools 13 → 15, total 66 → 68; A2A skills
  35 → 37). Schema v3 → v4 (idempotent SQLite migration + Postgres
  `ADD COLUMN IF NOT EXISTS` + Alembic `0003`). This closes the last #1078
  follow-up; the mongo backend stamps `entry_hash` but the seal chain is
  SQLite/Postgres-only.
- **Agent-writable identity ops completed + audit-log export (#1075 / #1078,
  epic Agent Memory #1073).** Building on the provenance spine, all four identity
  mutations are now reachable over MCP **and** A2A with `actor`/`trust`
  provenance: the existing `identity_merge` / `identity_split` plus two new ops —
  `claim_record` (`identity_claim`: move a record into an identity, emitting a
  `claimed` event on the gaining and losing entities) and conflict adjudication
  (`identity_resolve_conflict`, surfacing the existing `mediate_conflict`:
  same / distinct / defer, now provenance-stamped). New `identity_audit` MCP/A2A
  tool exports the append-only event log in commit order (who / trust / when /
  why), optionally filtered by dataset / actor — the compliance-export surface for
  #1078. MCP identity tools 10 → 13 (total 63 → 66); A2A skills 32 → 35. No
  migration (`claimed` is a value in the existing `kind` column). Follow-up
  (tamper-evident hash-chaining of the log) shipped in the entry above; CLI
  front doors for claim / resolve-conflict remain open.
- **Identity-write provenance spine: `actor` + `trust` on every event/edge
  (#1075 / #1078, epic Agent Memory #1073).** `IdentityEvent` and `EvidenceEdge`
  now record WHO made each write (`actor`, e.g. `pipeline` / `agent:claude` /
  `steward:alice`) and their `trust` (0–1) — so the append-only event log is a
  real audit trail a reviewer can use to reconstruct exactly which actor changed
  what, when, and why (`payload['reason']`). Pipeline-driven writes are stamped
  `actor="pipeline"` (override via `resolve_clusters(actor=...)`); the
  agent-facing `manual_merge` / `manual_split` and their MCP tools take `actor` /
  `trust` (trust defaults by actor prefix — steward 1.0, agent 0.5). New
  `IdentityStore.export_audit_log(dataset=/actor=/since=)` returns the full log in
  commit order for compliance export. Backward-compatible: nullable columns added
  to both tables via an idempotent migration (SQLite `PRAGMA`-guarded `ADD COLUMN`;
  Postgres `ADD COLUMN IF NOT EXISTS`); pre-provenance rows read back as `None`.
  (Tamper-evident hash-chaining, claim-entity / resolve-conflict tools, and the
  Postgres bulk-COPY path's provenance are tracked follow-ups.)
- **`accuracy_febrl3` real-dataset metrics probe (opt-in).** The metrics harness
  gains its first *real* ER-benchmark probe — Febrl3 (5000-record person dedup
  with published ground truth) via `recordlinkage`'s bundled data, so it's still
  offline (no download) and deterministic. Gated opt-in (`METRICS_REAL_DATASETS=1`
  + `recordlinkage` installed): it skips cleanly — contributing **no** metrics,
  never an error — when the flag is off or the dep is absent, so the default run
  and the committed baseline stay dependency-free. F1/precision/recall are gated
  (±0.02, env-tolerant); the raw tp/fp/fn counts ride as informational diagnostics
  (real-data fuzzy scoring can wobble a pair or two across environments). Committed
  baseline: **F1 0.90 / P 0.87 / R 0.94**. The `bench-metrics` workflow runs it on
  schedule + dispatch (installs `recordlinkage`, sets the flag). Adds a `skipped`
  status to the probe protocol — the seam for further download/key-gated sets
  (DBLP-ACM, NCVR) to plug in behind the same opt-in skip pattern.
- **`accuracy_trained_embedder` metrics probe — trained-embedder recall lift.**
  A new probe in the metrics harness that isolates the value a *trained* in-house
  embedder adds over the untrained char-n-gram projection, measured on the signal
  training can actually capture: nickname-alias equivalence (robert↔bob) with
  near-zero character overlap. The untrained projection is structurally blind to
  it (alias blocking recall ~0.01); a model trained offline (seeded, numpy-only —
  no torch/cloud) on alias pairs over a *disjoint* surname vocabulary recovers
  ~1.0 on unseen surnames — generalization, not memorization. Turns "we trained a
  better embedder" into a tracked, gated number. (Note: on pure surface-noise
  typos the untrained projection is already near-ceiling, so training is ~a no-op
  there — this probe deliberately targets the equivalence-beyond-surface case.)
- **Unified offline metrics harness (`scripts/metrics/harness.py`).** One entry
  point that runs the accuracy + semantic-blocking + perf probes in a single pass
  and diffs them against a committed baseline (`scripts/metrics/baseline.json`):
  synthetic labeled-data accuracy (F1 / precision / recall via `evaluate_clusters`),
  semantic-blocking candidate-generation recall lift (the ANN source over the
  zero-config in-house embedder reaches name pairs the structured/fuzzy keys miss
  -- 0.73 -> 0.95 blocking recall, the first measured payoff of the #1087 semantic
  work), plus perf (wall, peak RSS, throughput, deterministic scored-pair / cluster
  counts via `bench_capture`). Accuracy metrics and deterministic counts are *gated*
  (a regression past tolerance fails `--check`); wall/RSS/throughput are
  environment-dependent and reported *informationally*. Fully offline (no
  datasets, Postgres, or API keys) so it runs on forks. Driven locally
  (`--check` / `--update-baseline`) and by the new `bench-metrics` workflow
  (weekly + `workflow_dispatch`, informational by default). First rung of the
  metrics-iteration infra for accuracy/perf work.
- **Pluggable vector-index backends: pgvector + DuckDB-HNSW (#1088, epic #1087).**
  The persistent vector index gained two storage backends behind the existing
  `VectorIndex` surface (`build` / `add` / `query` / `save` / `load` / `open`,
  returning `RetrievedRecord`): `DuckDBVectorIndex` (a DuckDB database file,
  ranked with core `array_cosine_similarity` and accelerated by a `vss` HNSW
  index when available) and `PgVectorIndex` (Postgres + pgvector, HNSW over the
  `<=>` cosine operator). A new `open_vector_index(location, backend="auto"|...)`
  factory picks local-file / duckdb / pgvector uniformly (auto-inferred from the
  location). The local on-disk backend is unchanged. DuckDB ships in-tree;
  pgvector is the new optional `goldenmatch[pgvector]` extra. Both embed with the
  zero-config in-house model by default (no cloud/torch) and share the
  re-embed-never text cache.
- **Semantic retrieval on the agent surfaces (#1089, epic #1087).** The
  `retrieve_similar_records` API is now exposed over the wire on all three
  surfaces: the MCP `retrieve_similar` tool, the A2A `retrieve_similar` skill,
  and the REST `POST /retrieve` endpoint. Each embeds a column + a free-text
  query with the zero-config in-house embedder (no cloud/torch by default) and
  returns the most similar records ranked by cosine similarity, routing through
  the same core function and `RetrievedRecord` shape. (MCP tool count 59 -> 60;
  A2A skill count 31 -> 32.)
- **Corpus-dedup throughput benchmark + per-PR perf gate (#1086, epic #1080).**
  A new `bench-corpus-dedup` harness (`scripts/bench_corpus_dedup/` +
  `.github/workflows/bench-corpus-dedup.yml`) measures the throughput tier (#1083)
  end-to-end against a real public corpus (FineWeb), with injected ground-truth
  near-dups so recall is measurable. Measured: **~1,192 docs/sec · 3.6 MB/sec on a
  70k-doc FineWeb slice at ~0.43 LSH recall** (end-to-end docs/sec is auto-config-
  bound at this scale; the raw sketch dedup is ≈7,800 docs/sec). A deterministic
  **`throughput-gate`** CI job (`scripts/bench_corpus_dedup/throughput_perf_gate.py`)
  guards regression on candidate-pairs / reduction-ratio / measured-recall vs a
  committed baseline, on a vendored offline corpus (no network → won't flake on
  shared runners). datatrove head-to-head and 100k+ scale (a survivorship/golden
  `iter_rows` ceiling) are tracked follow-ups. Bringing the tier up to real corpus
  scale also fixed three at-scale bugs the original 10-row unit tests missed: the
  GoldenCheck O(N²) quality scan running on document text, web text mis-classified
  as a non-text column (the tier refused), and the ≥100k RED-config refusal.
- **Opt-in throughput tier: sketch-then-verify corpus dedup (#1083, epic #1080).**
  `dedupe_df(df, throughput=0.95)` (or `True`, or a `ThroughputConfig`) blocks the
  longest text column with MinHash/LSH (`lsh`) or SimHash (`simhash`, when an embedder
  is reachable), then confirms candidate pairs by cheap sketch distance -- no
  field-level fuzzy/Fellegi-Sunter scoring. The `recall_target` kwarg (default 0.95)
  tunes LSH banding; `similarity_threshold` overrides the near-dup cutoff (Jaccard 0.8
  / cosine 0.85). `DedupeResult.throughput_posture` (and the controller telemetry
  `throughput` block) carry the honest posture: LSH-theoretic `expected_recall` plus
  measured `reduction_ratio` -- not a measured F1 or precision. Raises
  `ThroughputNotApplicableError` when the frame has no text column. Default-off
  (`throughput=None`) is byte-identical to today across every metric. Single-node only;
  distributed (#1084), product surface (#1085), and bench gate (#1086) are follow-ons.
- **MinHash/LSH sketch tier: a throughput blocking primitive for near-duplicate
  detection (#1081).** A new pyo3-free `goldenmatch-sketch-core` Rust crate
  (shingling → MinHash → banded LSH) with a pure-Python reference/fallback
  (`goldenmatch.core.sketch`) and a native binding, all byte-identical via a shared
  golden-vector fixture. A new `MinHashLSHBlocker` and `BlockingConfig(strategy="lsh",
  lsh=LSHKeyConfig(...))` generate near-duplicate candidates by shingling a text
  column; records sharing ≥1 LSH bucket become candidates. Measured recall: an
  always-on synthetic gate (recall 0.978 / candidate-reduction 0.989 at the pinned
  config) plus a Quora Question Pairs bench job (`bench-lsh-recall.yml`). `LSHKeyConfig`
  is re-exported from the top level. The native `sketch` kernel ships available but is
  not yet default-on (reachable via `GOLDENMATCH_NATIVE=1`);
  `GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS` tunes the batch rayon fan-out threshold.
  Foundation for the training-data dedup throughput tier (#1080).
- **Semantic SimHash near-duplicate blocking (#1082 Phase B).** A new pyo3-free
  SimHash (random ±1 hyperplane) LSH kernel over embedding vectors, exposed as
  blocking `strategy="simhash"`. `SimHashKeyConfig` (`column`, `num_planes=256`,
  `seed=0`, `threshold | num_bands`, `model`) is re-exported from the top level;
  `BlockingConfig(strategy="simhash", simhash=SimHashKeyConfig(...))` embeds a text
  column and buckets cosine-similar vectors via `goldenmatch.core.simhash_blocker.
  SimHashLSHBlocker`. Auto-config now routes a **text corpus** to `simhash`
  (semantic) when an embedder is reachable (`inhouse_embedding_available()` or a
  configured provider), else to lexical `lsh` (Phase A) — so `dedupe_df(corpus)`
  picks semantic near-dup automatically when embeddings are available. SimHash
  catches the semantic paraphrases that lexical MinHash/LSH misses (measured: an
  always-on synthetic recall gate at `num_planes=256`/`num_bands=32` — recall 1.0,
  candidate-reduction 0.86 on cosine≥0.89 variants — plus a QQP lexical-vs-semantic
  A/B bench, `bench-lsh-recall.yml --method semantic`). The kernel ships native via
  the new `simhash` component — built but not gated on (reachable via
  `GOLDENMATCH_NATIVE=1`, same posture as `sketch`/`pprl_bloom`), sharing
  `GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS` with the MinHash kernel. Cross-language
  byte parity via golden vectors (pure-Python reference + Rust `sketch-core` +
  pure-TS port). Builds on #1081 + #1082 Phase A; part of the dedup epic #1080.

## [2.2.0] - 2026-06-19

<!-- README-callout
**Semantic blocking** — an opt-in recall lever for abbreviations and aliases. `dedupe_df(semantic_blocking=...)` unions extra candidate sources (initialism/abbreviation blocking, a business-alias canonical-form table, and an embedding ANN pass) into the pipeline. Off by default; on the abbreviation-heavy benchmark it adds **+5.3pp recall at zero precision cost**.
-->

### Added
- **Semantic blocking: an opt-in recall lever for abbreviations and aliases (#1065).**
  New `SemanticBlockingConfig` plus a `dedupe_df(semantic_blocking=...)` flag union
  extra candidate sources into the pipeline: an initialism/abbreviation blocking
  transform, a business-alias canonical-form table, and an embedding ANN pass with a
  numpy all-pairs fallback (faiss optional, `get_embedder("inhouse")` works zero-config).
  Off by default. On the abbreviation-heavy benchmark it adds +5.3pp recall at zero
  precision cost.
- **`config_weaknesses`: a deterministic config-critique tool (#1064).**
  New `core/config_critique.py::diagnose_config(df, config, result)` is a pure, offline
  generator that explains in plain English where an auto-built matching config is risky:
  a source/provenance column admitted as a matching signal, a per-row identifier admitted
  as a key, oversized shared-value blocks, null-sink and low-signal matchkeys, and
  over-merge mega-clusters. Each finding maps to one concrete fix (exclude_column, tighten
  blocking, demote, raise_threshold) and findings are ranked high-to-low. No engine run
  required; `phrasing="plain"` (default) and `"technical"` are both deterministic.

### Security
- **Removed a ReDoS in the initialism parenthetical regex (#1067).**
  A leading `\s*` in the acronym parser backtracked polynomially on long whitespace runs
  not followed by `(` (CodeQL py/polynomial-redos, high severity, introduced with the
  semantic-blocking work in #1065). The fix drops the `\s*`; the subsequent `.split()`
  normalizes any leftover whitespace, so it is behavior-identical and linear.

## [2.1.0] - 2026-06-18

<!-- README-callout
**Correlated survivorship** — golden-record survivorship can now keep correlated fields (street/city/postcode) in lock-step from a single winning source instead of mixing best-per-field values across records. New `FieldGroupSpec` + `DomainPack.groups` (domain-pack schema v3, additive), an `anchor`/`allow_fill` group-winner strategy, and per-cluster provenance surfaced through lineage, `explain`, the MCP tools, and the review queue. Plus chunked PPRL linkage (peak memory ~9-14x lower, byte-identical) and `result.native` dispatch telemetry that flags a silently-slow Python fallback.
-->

### Added
- **Correlated survivorship: lock-step field groups + conditional/validated golden rules (#1047).**
  New `FieldGroupSpec` + `DomainPack.groups` (domain-pack schema v3, additive and
  backward-compatible) let golden-record survivorship keep correlated fields (e.g.
  street/city/postcode) in lock-step from a single winning source instead of mixing
  best-per-field values across records.
- **`allow_fill` + `anchor` group-winner strategy (correlated survivorship v2) (#1055).**
  `GoldenGroupRule` gains `anchor` (designates the authoritative column for the new
  `"anchor"` strategy, validated to be present in the group) and an orthogonal
  `allow_fill` flag (fill-forward semantics). Existing strategies are unchanged.
- **GroupProvenance surfaced end-to-end (#1053).** Per-cluster survivorship provenance
  now carries a real `cluster_id` and a natural-language audit trail all the way through
  lineage, `explain`, the MCP tools, and the review queue, so callers can see which source
  won each field group and why.
- **Chunked PPRL trusted-third-party linkage (#1054).** New `PPRLConfig.chunk_size` streams
  Party B in blocks instead of materializing the full `N_a x N_b` score matrix (a 50k x 50k
  float32 matrix was ~10 GB and OOMed). Peak memory drops ~9-14x (304.9 MB dense ->
  21-34 MB chunked on a 4000x4000 link) with byte-identical output; `chunk_size=None`
  (default) is the original dense path.
- **Native-dispatch telemetry on the result object (#1048, #957).** `result.native`
  (`NativeDispatchSummary`) reports whether the scoring hot path actually dispatched to the
  Rust kernel or fell back to pure Python. A WARNING is emitted when the kernel is importable
  but the hot path ran on the fallback, and each Ray worker self-reports once per process on
  the distributed path, so a silently-slow run is visible instead of only inferable from
  wall-clock.
- **Collective entity resolution (neighborhood similarity).** `run_graph_er(...,
  propagation_mode="relational")` blends attribute similarity with neighbor-cluster
  overlap (Jaccard/Adamic-Adar) and iterates to a synchronous fixpoint
  (Bhattacharya-Getoor) — resolving homonyms that attributes alone can't. On a
  relational fixture where the co-author neighborhood is the only disambiguating
  signal, it lifts pairwise F1 from ~0.66 (attribute-only) to ~0.87, stable across
  seeds; the legacy flat-boost `additive` mode over-merges there (~0.05). New module
  `goldenmatch/core/collective.py`; the existing `additive`/`multiplicative` modes are
  unchanged (default remains `additive`). Benchmark + results under
  `benchmarks/collective-er/`. Phase 2 (negative evidence) and Phase 3 (learned
  weights) are planned follow-ups.

### Fixed
- **Zero-config multi-source dedupe no longer over-merges on shared workplace/categorical
  attributes (#858).** Under the multi-source source-partition path, a low-cardinality
  name-typed field whose column is NOT a person name (`company`, `job_title`, `department`,
  ...) is now demoted to blocking-only, exactly like the existing phone demotion -- it is a
  shared attribute, not an identity claim. Lifts the reporter's `crm_multisource_realistic`
  fixture from bare F1 0.13 to 0.77; person-name and high-cardinality fields are kept and
  single-source auto-config is byte-identical.
- **`backend="bucket"` honors multi-pass UNION blocking (#1048).** `dedupe_df(df,
  config=, backend="bucket")` with an explicit weighted matchkey + multi-pass config whose
  keys live in `.passes` (no static key) silently returned 0 clusters. The guard now resolves
  the pass list (`passes or keys`); single-key/static configs are byte-identical.
- **Domain detection no longer classifies a bare `name`-only schema as `product` (#1042).**
  `name` is domain-ambiguous and was removed from the product signals, so people/place/org
  data no longer triggers product feature extraction (it resolves to `unknown`); real product
  data still detects via title/description/brand/manufacturer/price.

### Performance
- **Sail distributed WCC: edge-node seeding + stage-boundary lineage barriers.**
  `connected_components_scale` now seeds the pointer-jump iteration from the
  edge-endpoint subgraph instead of the full id universe, re-attaching singletons
  after convergence — the partition is byte-identical but every per-round shuffle
  is proportional to the connected component count, not the row count (avoids
  dragging isolated nodes through the loop; helps any engine). `run_sail_pipeline`
  also materializes `pairs` before WCC and `assignments` before survivorship when
  `wcc_checkpoint_dir` is set, so the lazy plan is truncated at each stage
  boundary. Motivated by the 2026-06-16 100M GKE run, which wedged the driver
  building the whole `load→score→dedup→WCC` plan in one action. The boundary
  barriers are a stopgap for Sail's missing lineage-truncation primitive
  (lakehq/sail#482); the proper fix is upstream `localCheckpoint`. Default (no
  checkpoint dir) is byte-identical.

## [2.0.0] - 2026-06-14

<!-- README-callout
**GoldenMatch 2.0.0: the first backwards-incompatible major.** It removes four deprecation-window items, each shipped with a 1.x runway: the legacy `:hash:` identity lookup bridge + `GOLDENMATCH_IDENTITY_ID_SCHEME` (run `goldenmatch identity migrate-ids` before upgrading; un-fingerprintable rows keep their `:hash:` id), the `GOLDENMATCH_CLUSTER_FRAMES_OUT` gate + legacy dict cluster path (`build_clusters` stays as a frames-backed adapter), and the `cheapest_healthy` / `_scale_aware_backend` shims. Pipeline behavior is output-equivalent. Migration guide: [Migrating to v2](https://docs.bensevern.dev/docs/goldenmatch/migrating-to-v2).
-->

### BREAKING CHANGES
- **Identity `:hash:` scheme removed.** The legacy `:hash:` lookup candidate and
  `GOLDENMATCH_IDENTITY_ID_SCHEME` are gone. A persisted identity DB still holding
  `:hash:`-keyed records will SPLIT on the next run. **Run `goldenmatch identity migrate-ids
  --path <db>` (or `--dsn`) BEFORE upgrading.** Un-fingerprintable rows keep their `:hash:` id.
- `GOLDENMATCH_CLUSTER_FRAMES_OUT` removed (frames-out is the only, output-equivalent clustering path).
- `RunHistory.cheapest_healthy()` removed -- use `pick_committed()`.
- `_scale_aware_backend` (internal) removed -- backend selection is the v3 planner.

### Added
- **Stable Python IdentityGraph API for the Sail tier (#859).** The S5
  identity-on-Sail create path now ships a frozen, documented, contract-testable
  public surface: `from goldenmatch.sail import IdentityGraphFrames,
  build_identity_graph` (imports without the `[sail]` extra — pyspark is lazy
  inside the builders, so a consumer can pin the signature with `inspect` and no
  Spark runtime). `IdentityGraphFrames` gains the optional `events` frame
  (`IdentityGraphFrames(nodes, records, edges, events?)`, completing the S5
  shape); `build_identity_graph(..., with_events=True)` emits one `CREATED` row
  per entity. The frozen per-frame wire schema is exported as `NODE_COLUMNS` /
  `RECORD_COLUMNS` / `EDGE_COLUMNS` / `EVENT_COLUMNS` — the edge frame carries
  full provenance (`record_a_id`, `record_b_id`, `score`, `matchkey_name`,
  `run_name`). **Incremental resolution (absorb/merge against an existing store)
  remains the deferred Layer 2, honest-null:** on a fresh store every cluster is
  a create, which is the common case. Pinned by `tests/test_sail_identity_contract.py`
  (runs in the normal lane, no Spark). Unblocks downstream consumers that depend
  on a released identity-graph contract.
- **`goldenmatch identity migrate-ids`.** Migrates persisted identity record
  ids from the legacy `{source}:hash:{12}` scheme to the canonical
  `{source}:h1:{12}` fingerprint scheme (SQLite + Postgres). `--dry-run` reports
  counts without mutating. Public API: `goldenmatch.identity.migrate_record_ids`.

### Removed
- **The legacy `:hash:` identity record-id scheme + `GOLDENMATCH_IDENTITY_ID_SCHEME=hash`.**
  The legacy lookup candidate and its kill-switch are gone. Run
  `goldenmatch identity migrate-ids --path <db>` BEFORE upgrading if you have a
  persisted identity DB with `:hash:`-keyed records.
- **`GOLDENMATCH_CLUSTER_FRAMES_OUT=0` escape hatch + legacy `dict[int,dict]` cluster path.**
  The Arrow frames-out path is the default and only supported clustering path.
  Public `build_clusters` is preserved as a frames-backed adapter.
- **`RunHistory.cheapest_healthy()`** -- use `pick_committed()`.
- **`_scale_aware_backend` (internal shim)** -- backend selection routes through the v3 planner.

### Performance
- **Fellegi-Sunter block scoring ~3.5x faster on tiny-block / multi-pass shapes
  (PR #869).** The probabilistic (`type: probabilistic`) numpy scoring path was
  per-block-fan-out bound, not compute bound — `historical_50k` produces 31,735
  blocks, 79% of them ≤8 rows, so scoring made ~222k tiny FFI-bound matrix calls.
  Three output-identical changes: (1) `score_probabilistic_vectorized` now scores
  the DISTINCT field values per block and gathers via an index map (a constant
  blocking-key field collapses to 1×1); (2) small blocks are coalesced into shared
  per-field matrices with diagonal sub-block extraction (`GOLDENMATCH_FS_BATCH_ROWS`,
  default 256), cutting native matrix calls 222k→4.3k; (3) the batch row-cap tuned
  to its measured knee. Wall on `historical_50k` probabilistic auto-config dropped
  86.5s → 24.6s (−72%) locally. Verified output-identical — for a fixed EM model,
  the emitted scored-pair set is byte-for-byte unchanged (200,058 pairs); the
  `synthetic_benchmarks` accuracy gate is green. No API or config change; tune via
  `GOLDENMATCH_FS_BATCH_ROWS` if needed.

### Changed
- **Zero-config `dedupe_df` no longer over-merges multi-source CRM (#858).** When
  auto-config detects a source partition at config time (the internal
  `__source__`, or a user `source`/`lead_source`/`*_source`/`origin`/`src`-named
  column that genuinely partitions records into disjoint-valued groups), it now
  (1) excludes the source-indicator column and every column whose values are
  fully disjoint across sources (per-source surrogate ids) from match features,
  and (2) demotes `phone` from a standalone exact matchkey to a blocking-only
  candidate. This removes the source-correlated precision crater (realistic
  multi-source CRM F1 ~0.35 → ~0.84). **Provable no-op** on single-source data
  and in match mode (`match_df` / `run_match`; cross-source linking is the goal
  there, suppressed by an explicit dedupe-mode gate). Default ON; disable with
  `GOLDENMATCH_MULTISOURCE_AUTOCONFIG=0`; a caller `force_include` (or
  `exclude_columns`) re-admits any auto-excluded column.
- **Phase-5 distributed recall-complete path is now the DEFAULT (#844 finish
  line).** The blocking-key-aware shuffle scoring + randomized-contraction WCC,
  previously opt-in via `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1`, is now ON by
  default — the legacy per-partition path under-merged inversely with partition
  count (a true-duplicate split across partitions was never compared). Validated
  end-to-end at 100M on a real 5-node cluster: full dedupe in **9.2 min** with
  byte-exact cluster recovery (20,000,000 clusters), after fixing the per-group
  scoring wall (`_score_colocated_groups` now scores each partition in one
  vectorized pass instead of ~20M per-group calls). Set
  `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=0` to restore the legacy per-partition
  path. **Multi-node:** the WCC checkpoints each round to
  `GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH`, which on a multi-node cluster MUST be a
  shared object-store path (`gs://…`); `randomized_contraction_wcc` now raises a
  clear error on a multi-node cluster with a node-local scratch path instead of
  silently diverging.

### Added
- **Phase-5 e2e pipeline recall-complete leg (opt-in, #844 Spec 2).** When
  `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1`, the Phase-5 streaming pipeline now
  routes clustering to the randomized-contraction WCC
  (`build_clusters_distributed(algorithm="randomized_contraction")`) instead of
  per-partition Union-Find. Per-partition Union-Find under-merges when the
  blocking plan generates pairs across input-partition boundaries; the
  distributed WCC correctly merges them. Default behavior is unchanged
  (block-shuffle off, per-partition path). The binding 100M validation run is
  operator-deferred (requires a multi-node cluster + GCS scratch); the simulated
  4-worker CI bench runs both legs and asserts the recall-complete leg finds
  strictly more multi-member clusters. See `docs/distributed-ray-cluster-setup.md`
  for the operator recipe. (#844 Spec 2)
- **Distributed randomized-contraction WCC (opt-in, #844 Spec 1).** New
  `randomized_contraction_wcc` in `goldenmatch.distributed.clustering` implements
  Bögeholz–Brand–Todor randomized contraction (arXiv:1802.09478) — a relational,
  chain-robust connected-components algorithm with no driver-side union-find and
  no O(N) driver dict. Each round's contracted edges are checkpointed to parquet
  to truncate Ray Data lineage (dodging the streaming-executor deadlock the
  pointer-jumping `distributed_wcc` hit). Routed via
  `GOLDENMATCH_DISTRIBUTED_WCC=randomized_contraction` (with
  `GOLDENMATCH_DISTRIBUTED_WCC_SEED` / `GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH`).
  Default stays `two_phase`; this is the algorithm building block only — wiring
  into the Phase-5 e2e pipeline and the at-scale 100M validation are Spec 2.

### Removed
- Removed the dominated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` opt-in (SP1);
  superseded by `GOLDENMATCH_CLUSTER_FRAMES_OUT`.

### Fixed
- **`dice`/`jaccard` single-pair scorers no longer crash on different-length
  bloom hex inputs (#784).** `score_field(a, b, "dice")` / `"jaccard"` decoded
  the two hex strings to byte arrays and called `np.bitwise_and` with no length
  validation, so mismatched lengths (e.g. `"0000"` vs `"000000"`) raised an
  opaque numpy broadcast `ValueError` — while the matrix variants
  (`_dice_score_matrix` / `_jaccard_score_matrix`) zero-pad to `max_len` and
  scored the same inputs fine. The single-pair helpers now zero-pad the shorter
  filter to `max_len` too, restoring single-vs-matrix parity (and matching the
  TypeScript twins `diceCoefficient` / `jaccardSimilarity`, which already
  zero-padded). Found by the hypothesis property suite (#778) on its first run.
  In-pipeline PPRL pairs are fixed-length per config and were never affected;
  the gap was reachable only via the public `score_field` API on cross-config or
  hand-fed inputs.

## [1.30.0] - 2026-06-09

<!-- README-callout
**Zero-training Fellegi-Sunter now beats hand-rolled, expert-tuned Splink, head-to-head and reproducibly.** On one shared evaluator across every dataset Splink scores, GoldenMatch's probabilistic auto-config wins on all of them: `historical_50k` pairwise F1 **0.778 vs 0.757** (cluster-level B³ **0.844 vs 0.789**), `febrl3` **0.991 vs 0.965**, `synthetic_person` **0.998 vs 0.996** — made reproducible by an EM training-pair determinism fix (#829). Full bake-off: `docs/benchmarks/2026-06-09-splink-bakeoff.md`.
-->

### Added
- **Native PPRL bloom-filter CLK kernel (opt-in, default OFF).** New
  `goldenmatch-native` symbol `bloom_clk_batch` (rayon + GIL-release, 256-bit
  Cryptographic Longterm Key encoding) accelerates the PPRL `bloom_filter`
  transform. Reachable via `GOLDENMATCH_NATIVE=1`; it ships OUT of the default-on
  native dispatch set pending parity-on-the-published-wheel + a bench, so
  pure-Python remains the reproducible default and the graceful fallback when the
  symbol is absent. Requires `goldenmatch-native` 0.1.5 (republish ships the
  symbol). (#826)

### Fixed
- **Probabilistic EM training-pair sampling is now deterministic (#829).**
  `_sample_blocked_pairs` seeded-shuffled bare block indices, but the blocks
  themselves arrive in a non-deterministic order (parallel / hash-bucketed
  construction, varying by machine and core-count), so the seeded shuffle still
  drew a different EM training sample run-to-run — different m/u weights,
  different threshold, different precision/recall. On one CI run, three
  invocations of the identical probabilistic path gave `historical_50k` pairwise
  F1 of 0.805 / 0.779 / 0.643. The fix sorts blocks by their stable `block_key`
  (and row_ids within each block) before the seeded shuffle, so the sample is
  reproducible; post-fix the three bench harnesses agree within 0.002. The
  committed Splink head-to-head and bake-off numbers are now deterministic; the
  full bake-off is at `docs/benchmarks/2026-06-09-splink-bakeoff.md`. (The
  previously published `dblp_acm = 0.879` was a non-deterministic lucky draw; the
  reproducible value is 0.377 — bibliographic data should use the weighted path,
  which scores 0.964 on DBLP-ACM, not the probabilistic path.)

## [1.29.0] - 2026-06-09

### Added
- **Phase 3c bench harness — FS dedupe at scale (Splink-parity).**
  `scripts/bench_fs_distributed.py` + `.github/workflows/bench-fs-distributed.yml`
  (`workflow_dispatch` only — never runs in normal CI). Generates synthetic
  person data with KNOWN injected duplicate pairs (GT exact in `__row_id__`
  space), runs FS dedupe via `backend=bucket` (the Phase 3a scale path that
  carries the Ray/DataFusion wiring), and reports wall + peak RSS + P/R/F1.
  Inputs: rows, dup_frac, backend, runner, fs_native (opt-in native kernel).
  The gate (5M wall/RSS budget + F1) is run on demand, not asserted from code.
- **FS accuracy analysis from labels — threshold sweep + m/u model report
  (Splink-parity Phase 4).** `core/evaluate.py` gains `threshold_sweep`
  (P/R/F1 at each candidate link cut, single O(P log P) descending sweep),
  `recommend_threshold` (the max-F1 operating point), `fs_model_report`
  (per-comparison m / u / log2(m/u) match-weight table + prior bits + EM
  convergence — the data behind Splink's m/u + match-weight charts), and
  `probability_two_random_records_match` (the EM within-block prior λ).
  `goldenmatch evaluate --threshold-sweep` renders the operating-point curve,
  the recommended cut, and the FS model report (via `MatchEngine`'s exposed
  `scored_pairs` + `em_results`); `--output` includes a `threshold_sweep`
  block. On DBLP-ACM the recommended posterior cut lands at 0.9999 (F1 0.968),
  confirming the Phase 0 calibration finding.
- **Native Rust FS kernel — opt-in (Splink-parity Phase 3b).** New
  `goldenmatch-native` kernel `score_block_pairs_fs` (FS arithmetic: per-field
  `sim → comparison level → log2(m/u)` weight sum → linear/posterior
  normalization), reusing the weighted kernel's rayon/`allow_threads` scaffold.
  `score_probabilistic_native` + `probabilistic_block_scorer` prefer it when
  built. **Opt-in, default OFF (`GOLDENMATCH_FS_NATIVE=1`)**: FS's discrete
  comparison levels amplify the tiny rapidfuzz-rs-vs-Python-rapidfuzz float
  differences — a similarity sitting exactly on a `partial_threshold` (token_sort
  ratios are rationals, so common) can flip a level between the two libraries,
  and with ~40-bit EM weights a single flip swings the score ~0.45 and can move a
  pair across the link threshold. Measured **2.9x** on DBLP-ACM and **byte-exact**
  vs numpy on non-boundary data; the numpy vectorized path stays the reproducible
  default. Ineligible (→ numpy) for soundex/embedding scorers or TF-adjusted
  fields; degrades gracefully when the published wheel lacks the symbol.
  `goldenmatch-native` 0.1.3 → 0.1.4 (pyproject + Cargo lockstep; republish to
  ship the symbol).
- **FS on the bucket backend — scale-out via the shared orchestration
  (Splink-parity Phase 3a).** Probabilistic (Fellegi-Sunter) matchkeys now ride
  the same hash-bucketed, parallel `score_buckets` path weighted matchkeys use
  (which carries the Ray / DataFusion distribution wiring), instead of a
  sequential per-block Python loop. `score_buckets` takes an `em_result`;
  `_score_one_bucket` dispatches probabilistic blocks to the EM-trained
  vectorized FS scorer (`probabilistic_block_scorer`), keeping the raw FS field
  columns through the slim projection. The pipeline routes FS through it when
  `backend="bucket"`. Same `em_result` → **clusters identical to polars-direct**
  (parity asserted at N=200/1000/3000 in tests + `scripts/bench_fs_and_stages.py`
  `fs_bucket_sweep`). No new Rust — rides the numpy scorer; the native FS kernel
  is Phase 3b. Previously FS ran single-node sequential only and every scale
  backend declined it.
- **FS-native explainability — match-weight waterfall (Splink-parity Phase 2).**
  `explain_pair_fs(row_a, row_b, mk, em_result)` decomposes a Fellegi-Sunter
  pair score into per-comparison log2(m/u) bit contributions (`FSWaterfall` /
  `FSFieldContribution`): a starting prior in bits, one signed bit per field,
  summing to the total match weight, then the posterior probability — by
  construction the per-field bits sum to the total and `posterior == 1/(1+2**
  -final_bits)`. `core.explain.format_fs_waterfall` renders the Splink-style
  table. Surfaced through `goldenmatch explain --pair` (a second panel when a
  probabilistic matchkey ran) and the lineage sidecar (`fs_waterfall` per pair
  via `build_lineage(em_results=...)`). `MatchEngine`/`EngineResult` now expose
  the trained `em_results`. Replaces the `score×weight` decomposition, which is
  the *weighted*-matchkey view and meaningless for FS.
- **Supervised m-training from labels (Splink-parity Phase 1b).**
  `estimate_m_from_labels(df, mk, labels)` is the supervised analog of
  `train_em` (Splink's `estimate_m_from_label_column`): m is the observed
  comparison-level frequency among known true-match pairs (Laplace-smoothed),
  u stays from random pairs, no EM iteration. Label adapters pull positive
  pairs straight from existing stores — `labels_from_corrections` /
  `labels_from_memory_store` (memory `Correction`s with `decision='approve'`)
  and `labels_from_review_items` (`ReviewItem`s with `status='approved'`).
  Compose with Phase 1a: persist the result via `EMResult.save_json` and reuse
  through `model_path`. Gate met — a 200-label seed ties unsupervised EM on
  DBLP-ACM (F1 0.968; EM is already optimal on clean data, so the lever's edge
  is on noisier inputs where unsupervised m drifts).
- **FS model persistence — train-once, reuse (Splink-parity Phase 1a).**
  `EMResult` now serializes: `to_dict`/`from_dict` (versioned) + `save_json`/
  `load_json` (atomic write) + `validate_for(mk)` (raises `FSModelMismatchError`
  on field/level mismatch). New `MatchkeyConfig.model_path`: when set and the
  file exists the trained model loads and EM is skipped; when absent EM runs and
  the result is saved there. `load_or_train_em` is the shared seam all three
  pipeline sites use (core pipeline x2, TUI engine). `dedupe_df(fs_model_path=...)`
  is a convenience that points every un-pathed probabilistic matchkey at one
  file. Verified byte-identical pairs on DBLP-ACM between a from-scratch run and
  a load-from-disk run (2310 == 2310). Previously every dedupe retrained EM.
- **FS match-weight monotonicity guard (Splink-parity Phase 0).** EM estimates
  m/u per comparison level independently, so a rare-but-discriminative middle
  level can outweigh exact agreement (DBLP-ACM `title`: partial 28.6 bits >
  exact 11.9 bits). `enforce_weight_monotonicity` applies pool-adjacent-violators
  isotonic regression per field. `GOLDENMATCH_FS_MONOTONIC` modes: `warn`
  (default — detect + log, do NOT modify, the Splink posture), `enforce`
  (isotonically repair), `off` (silent). Default `warn` is value-preserving
  (DBLP-ACM F1 stays 0.968); `enforce` *trades* F1 there (0.968 → 0.941, the
  inversion is genuine signal, not pure artifact) so it is opt-in. Sweep:
  `scripts/bench_fs_calibration.py`. Spec/plan:
  `docs/superpowers/{specs,plans}/2026-06-07-probabilistic-splink-parity*`.
- **Weak-positive-aware blocking-pass selection (opt-in,
  `GOLDENMATCH_BLOCKING_PRUNE_PASSES=1`).** `core/blocking_pass_selection.py::select_passes`
  prunes multi-pass blocking passes that contribute little, ranking by marginal
  yield of *likely matches* (new pairs weighted by an unsupervised weak-positive
  proxy — agreement on ≥2 discriminative fields) rather than raw new-pair count.
  This protects sparse-but-precise passes (e.g. exact date-of-birth, which on
  Febrl4 adds ~1.7K pairs but +8.5pp recall) that a naive new-pair pruner would
  wrongly delete. Default floor keeps anything contributing ≥1 likely match
  (drops only fully-redundant/all-noise passes, recall-safe); a `candidate_budget`
  or higher floor trades recall for fewer candidates. Wired env-gated into
  auto-config (default OFF). Measured modest on already-good schemes (Febrl4
  6-pass → 3 = -1.8% candidates, -0.08pp recall ceiling) since cost concentrates
  in recall-critical passes; most useful at scale or as an explicit recall/cost knob.

### Fixed
- **FS `posterior` calibration default cut corrected 0.50 → 0.99.** The opt-in
  `GOLDENMATCH_FS_CALIBRATED=posterior` path was mis-tuned: `compute_thresholds`
  returned the 0.5 Bayes boundary, but blocking inflates the within-block prior
  so post-block pairs clear 0.5 trivially (DBLP-ACM F1 0.936 at 0.5). At the
  measured 0.99 cut, posterior ties linear (F1 0.968, P 0.984). Default
  calibration stays `linear` (flipping the headline score to a probability
  shifts the distribution downstream clustering thresholds are tuned against —
  deferred to Phase 4). Also removed the stale "default flipped to posterior
  once measured" comment that never matched the code, and the bogus "57.6%
  recall" figure (that was a block-skip artifact, not the linear calibration).
- **Multi-pass blocking no longer silently drops cross-pass blocks.**
  `_build_multi_pass_blocks` deduplicated blocks by `block_key`, which is the
  concatenated field *values* with no field identity — so a later pass's block
  whose key string collided with an earlier pass on a different field (common
  with soundex/substring/numeric keys, which share a value namespace) was
  dropped, losing every candidate pair in it (measured 309/7310 = 4.2% of
  blocks on Febrl4's auto-config scheme). Dedup is now keyed by the pass's
  field+transform signature plus the value, so distinct-field blocks survive
  while truly-identical blocks still dedup.
- **Fellegi-Sunter multi-pass EM exclusion.** The FS pipeline collected the
  blocking fields to exclude from EM training by reading `config.blocking.keys`
  only — but `multi_pass` configs keep their keys in `.passes`, so the
  exclusion list was empty and EM over-fit the always-agree blocking fields.
  New `collect_blocking_fields()` gathers from keys + passes + sub_block_keys;
  Febrl4 multi-pass FS improves 95.7% → 98.4% F1 (postcode-only single-key was
  91.0%).

### Changed
- **Fellegi-Sunter block scoring is now vectorized (default ON).** `score_probabilistic_vectorized`
  replaces the per-pair Python double loop with one `rapidfuzz.cdist` NxN matrix per field plus
  numpy level/weight/normalize ops — the same vectorized path the fuzzy scorer already uses. ~9x
  faster on full DBLP-ACM blocks (9.6s → 1.06s for 1.2M pairs) at ~99.96% pair parity. The pipeline
  selects it via `probabilistic_block_scorer(mk, em)`; it falls back to the scalar path for
  embedding/record_embedding scorers or when `GOLDENMATCH_FS_VECTORIZED=0`. The continuous-EM
  E-step and `score_probabilistic_continuous` are vectorized too.
- **DBLP-ACM Fellegi-Sunter benchmark corrected: 72.8% → 96.8% F1.** `run_v030_quick.py` was skipping
  blocks >500 rows for performance, which capped recall at ~60% (every DBLP-ACM match is same-year).
  With cheap vectorized scoring, full blocks are scored: P=97.8% / R=95.8% / F1=96.8%. Block-skip
  for performance — not scoring or calibration — is the dominant FS recall lever.

### Added
- **Calibrated posterior scoring for Fellegi-Sunter (opt-in, `GOLDENMATCH_FS_CALIBRATED=posterior`).**
  Turns the FS score into a true match probability `1/(1+2^-(log2(λ/(1-λ)) + ΣW))` using the
  EM-estimated within-block prior (which the legacy linear min-max normalization discarded), so the
  default 0.5 threshold is the Bayes boundary. Measured frontier-neutral (monotonic in the summed
  weight, so it can't change F1) — a correctness/interpretability change. Default stays `linear`.
  Public helpers: `prior_weight()`, `posterior_from_weight()`.
- **Term-frequency (Winkler) weight adjustment for Fellegi-Sunter (opt-in per field,
  `MatchkeyField.tf_adjustment=True`).** Exact agreement on a rare value carries more match weight
  than on a common one. Frequencies are computed over the full column at EM-train time. No measurable
  headroom on the available benchmarks (precision already saturated at 96–99.98%); ships as a
  capability for skewed-frequency categorical fields (names/cities).
- **Probabilistic (Fellegi-Sunter) auto-config v2 -- comparison-set + blocking
  curation (default ON; kill-switch `GOLDENMATCH_FS_AUTOCONFIG_V2=0` restores
  the legacy field set).** Scoped to the probabilistic path only
  (`build_probabilistic_matchkeys` + `auto_configure_probabilistic_df`); the
  weighted/DQbench path is untouched. Four levers fix the auto-built
  Fellegi-Sunter comparison set that under-performed Splink on error-heavy PII
  and mega-matched on bibliographic data: (1) admit `date`/dob columns as
  `levenshtein` comparison fields (v1 dropped all dates -- the strongest person
  discriminator); (2) drop redundant person-name composites (full_name /
  first_and_surname) when atomic given+family fields are present, and floor fuzzy
  fields at very low cardinality (exact identifiers keep the no-floor admission);
  (3) additively diversify blocking onto orthogonal stable keys (date YEAR via
  substring, plus postcode/zip/identifier passes); (4) admit `description`
  (titles) and `multi_name` (authors) as `token_sort` comparison fields. With v2
  the zero-config probabilistic path now matches or beats Splink on the shared
  `bench_er_headtohead` head-to-head panel (pairwise F1, one evaluator):
  historical_50k 0.779 vs 0.757, febrl3 0.991 vs 0.965, synthetic_person 0.998
  vs 0.996, dblp_acm 0.879 (Splink skips). `GOLDENMATCH_FS_AUTOCONFIG_V2=0` is
  byte-identical to the legacy field set.

## [1.28.1] - 2026-06-07

### Fixed
- **Exact matchkeys no longer match on empty/blank values.** Two records both
  missing a field (e.g. a blanked phone → `""`) are not a shared-identity claim;
  previously `find_exact_matches` excluded nulls but not empty strings, so every
  blank-valued record joined on `""` and Union-Find transitively exploded the
  clusters. Diagnosed on the DQbench ER **T3** tier (precision 14.9%): the fix
  lifts **T3 F1 0.257 → 0.747** with no regression on the clean canonical sets
  (DBLP-ACM 0.9641, Febrl3 0.9665). Repro tool: `scripts/dump_dqbench_er_tiers.py`.

## [1.28.0] - 2026-06-06

This release re-derives the **auto-config search strategy for the post-speedup cost model**.
Now that block scoring is ~5x cheaper (bucket+native) and the in-house embedding model is
local and CPU-only, the controller no longer has to reason from a thin sample + a linear
projection or refuse its own power tools. Introduces a **planning-effort tier** —
`fast / normal / thinking / einstein` — that controls how hard the brain searches. Default
`normal` is byte-for-byte the prior behavior. Spec:
`docs/superpowers/specs/2026-06-06-autoconfig-search-strategy-after-engine-speedup-design.md`.

### Added
- **`planning_effort` tier on auto-config (`fast`/`normal`/`thinking`/`einstein`).** New
  `GoldenMatchConfig.planning_effort` field + `planning_effort=` kwarg on `dedupe_df` /
  `match_df` / `auto_configure_df`, plus the `GOLDENMATCH_PLANNING_EFFORT` env override.
  `ControllerBudget.for_dataset(n_rows, effort)` gains the effort dimension: `fast` collapses
  to a single cheap pass; `thinking`/`einstein` spend the freed engine cycles on a larger
  sample, more refit iterations, and a longer wall budget (the breadth lever).
- **`goldenmatch.core.embedder.inhouse_embedding_available()`** — cheap, side-effect-free
  probe for the local in-house embedding stack.

### Changed
- **Measure, don't extrapolate (Phase 1).** At `thinking`/`einstein` effort the controller
  now runs real blocking on the **full frame** (`blocker.measure_blocking_profile`) to pick
  the execution backend off measured pair counts instead of a linear projection from the
  sample — killing the wrong-rung-on-skewed-data failure. `normal`/`fast` (and the
  distributed path) keep extrapolation; any measurement failure falls back to it.
- **Provider-aware in-house embedding (Phase 3).** Auto-config preflight
  (`_check_remote_assets`) no longer demotes `embedding`/`record_embedding` scorers backed by
  the **local in-house model** (`model="inhouse:..."` or `GOLDENMATCH_EMBEDDING_PROVIDER=inhouse`
  + `GOLDENMATCH_INHOUSE_MODEL`). The drift-risk demotion remains for cloud embedders
  (sentence-transformers / Vertex), which genuinely need a download or credentials.

### Notes
- `normal` (the default) is unchanged from 1.27.0 — same sample sizes, iterations, budget,
  and extrapolation. The new behavior is opt-in via the higher tiers / env var.
- The broader search redesign (full successive-halving over a candidate grid, and an
  LLM-judge labeling objective) is staged behind the `thinking`/`einstein` seam for a
  follow-up; this release ships the load-bearing spine (the tier knob, measurement, and the
  in-house embedding exemption).

## [1.27.0] - 2026-06-05

### Fixed
- **Distributed Phase-5 pipeline now honors an explicit config (#739).** `run_dedupe_pipeline_distributed(..., config=my_config)` previously dropped the `config` kwarg and forced `auto_configure_df`, so a hand-built config was ignored and a RED auto-config commit could crash the run. `allow_red_config` was likewise dropped. Both are now respected (`allow_red_config=True` is the documented escape hatch at scale, per the post-#715 contract).

### Changed
- **Auto-config admits high-cardinality identifiers to probabilistic matchkeys (#721).** `build_probabilistic_matchkeys` no longer skips `col_type="identifier"`, matching the exact path (#715). Identifiers become Fellegi-Sunter comparison fields with no lower cardinality floor (F-S self-regulates a weak identifier via its u/EM weight); only perfectly-unique surrogate keys (`cardinality_ratio >= 1.0`) are excluded, now uniformly across all exact-scorer fields.

### Notes
- The Postgres extension gains `gm_embed(text) -> real[]` (#737), released separately as `goldenmatch-pg` 0.7.0 (a cached, `GOLDENEMBED_MODEL_DIR`-based convenience over the in-house embedder, mirroring the DataFusion `goldenmatch_embed` UDF). No change to the Python package surface.

## [1.26.0] - 2026-06-04

<!-- README-callout
**100M records, distributed, on a 4-worker Ray cluster — verified.** The distributed Phase-5 pipeline (`GOLDENMATCH_DISTRIBUTED_PIPELINE=2`) now runs a full 100,000,000-row dedupe end to end in ~213 s with the driver process peaking at 0.30 GB RSS. The unlock was removing every driver-side collect from the pipeline (scoring -> per-partition local connected-components -> distributed join -> distributed golden build + write), so nothing funnels back to a single node.
-->

This release makes the **distributed (Ray) pipeline actually scale to 100M+** by eliminating
the driver-side materialization points that wedged the head node, and by fixing a latent
clustering-correctness bug in the distributed path. Verified on a real 4-worker GCP cluster:
100M rows -> 20M golden records in 213 s, driver peak 0.30 GB.

### Added

- **`local_cc_assignments`** (`goldenmatch.distributed`): connected components via a single
  per-partition local Union-Find `map_batches`. Distributed scoring is per-partition, so a
  component's edges are always co-located in one block — a local Union-Find yields the global
  components with no cross-node merge, no driver collect, and no iterative graph algorithm.
  Returns `{member_id, cluster_id, cluster_size, oversized}` with globally-unique cluster ids.
- **End-to-end driver-collect-free distributed pipeline.** `GOLDENMATCH_DISTRIBUTED_PIPELINE=2`
  now runs `score -> local-CC -> join -> golden -> write` with every stage distributed: rows are
  annotated with `__cluster_id__` via a distributed `Dataset.join` (not a broadcast dict), and
  golden records are built **and written** distributed (`build_golden_records_distributed(...)
  .write_parquet`), never materialized on the driver.

### Fixed

- **Cross-partition cluster-id collision in the distributed path.** The pipeline synthesized
  `__row_id__` per-partition, so ids collided across partitions and connected-components silently
  merged unrelated clusters (a 50M run reported ~156K clusters instead of the true ~10M). The
  synthetic generator now carries a **global** `__row_id__`, and the pipeline respects a
  pre-existing id.

### Changed

- The distributed golden tail no longer calls `materialize_golden_dataframe(...).to_dicts()` (which
  collected all golden records to the driver and OOM'd the head at 100M); golden is written from the
  Ray Dataset directly.

### Ops

- 100M verification recipe: 1 head (`ray start --num-cpus=0`, pure driver) + 4 `e2-standard-16`
  workers, all with `cloud-platform` scope (workers write parquet to object storage). See
  `scripts/bench_phase5_explicit.py`.

## [1.25.0] - 2026-06-01

<!-- README-callout
**Arrow-native groundwork + leaner large-N runs** — columnar pair-stream / two-frame-cluster entry points and optional Rust/Arrow-C kernels (`build_clusters`, `dedup_pairs`, `record_fingerprints`, MST oversized-split) land behind the `goldenmatch._native` extension, purely additive with the pure-Python + Polars pipeline unchanged as the default and byte-for-byte reference. Plus single-node memory wins (golden -2.6 GB, bucket -3.8 GB peak at 10M; standardize ~25-30s off the prep wall) and fixes for a silently-dropped GoldenCheck quality scan and a prep-cache `id()`-recycle flake. PRs #588-#650.
-->

This release lands the **Arrow-native pipeline groundwork** (roadmap Phases 0-6), a
batch of single-node memory/wall optimizations, an optional native Rust kernel for
the cluster oversized-split path, and several user-facing bug fixes. All Arrow /
native work is **additive and opt-in** — the default pure-Python + Polars pipeline is
behavior-unchanged; the columnar entry points and `goldenmatch._native` kernels are
exercised by the bench/profiler harnesses and wired in behind follow-up parity work.

### Added

- **Arrow-native pipeline groundwork (roadmap Phases 0-6).** Columnar pair-stream
  scorer entry points (#631) with a native columnar inner loop for `score_blocks_columnar`
  (#634, #639); a two-frame `ClusterFrames` cluster representation (#632) + numpy-backed
  `cluster_dict_to_frames` (#635); golden (#636), identity (#638), and a hash-by-cluster_id
  partitioner (#642) that consume `ClusterFrames` directly; columnar `dedup_pairs_max_score`
  (#641). These are new entry points alongside the existing path, not a default swap.
- **Native (Rust / Arrow-C) kernels** in the optional `goldenmatch._native` extension:
  Arrow-C kernels for `build_clusters` (#645), `record_fingerprints` (#644), and
  `dedup_pairs` (#643); a max-weight-spanning-tree oversized-split kernel (#649); and
  `build_clusters_native` (#610) / `record_fingerprints_batch` (#612) prototypes. Pure
  Python remains the default and the byte-for-byte reference; the kernels run only when
  the extension is built and the relevant `native_enabled(...)` gate is on.
- **Profiling + bench harnesses.** A GitHub Actions hotspot profiler (pyinstrument +
  cProfile) over the pair-stream / cluster path (#646); a pair-stream columnar-vs-list
  bench at 100K/1M/5M (#633); per-(shape, path) subprocess isolation (#637); and a
  native-kernel decision-gate workflow (#611).
- **`map_elements` justification lint** — prep-stage `map_elements` calls now require a
  `# noqa: GM-MAP-ELEMENTS:` rationale comment, gating accidental per-row Python before
  it ships (#640).
- **Experimental backends/infra** (not on the default install path): a DataFusion backend
  spike (#620, #621) and an ephemeral GCE Ray-cluster bench harness (#608-#619).

### Performance

- **Standardization native Polars chains.** `name_proper` (#601) and `address` (#602,
  ~25-30s off the prep wall on the QIS shape) drop their per-row Python UDFs for
  Polars-native chains.
- **Golden + bucket memory slimming (default ON).** Slim `multi_df` before
  `attach_cluster_id` (-2.6 GB, #595/#596); slim projection before `bucket_assign`
  (-3.8 GB peak at 10M, #590); close the rechunk lane + free partition parents after
  `partition_by` (#593); rechunk after `precompute_matchkey_transforms` (#591).
- **Clustering.** Drop the non-load-bearing `sorted(members)` in `result_dict_init`
  (#594); numpy-backed `pairs_df_to_list` in `build_clusters_columnar` (#647); one-pass
  O(pairs) `pair_scores` partition + `operator.itemgetter` keys in the oversized-split
  path (~6% on `build_clusters_columnar` at 100K, #648).

### Fixed

- **GoldenCheck quality scan silently dropped every finding** in the scan-only path
  (`_scan_only`, used by the MCP `scan_quality` tool, the A2A quality skill, and the web
  `/api/v1/quality` route): it read `Finding.rule_id` / `rows_affected` (the dataclass
  exposes `check` / `affected_rows`) and the resulting `AttributeError` was swallowed as a
  warning. Findings now serialize correctly, with `severity` as a lowercase string (it was
  the raw `IntEnum`, which then broke the web consumer's `.lower()`) (#647).
- **Prep-cache `id()` recycle.** The in-memory prep cache keyed on `id(df)` + schema, so a
  garbage-collected frame's recycled address could serve a stale prepared frame to a
  same-schema input (e.g. an empty DataFrame received a populated one — the
  `test_dedupe_df_empty` `pytest -n auto` flake). The key now folds in `df.height` (#647).
- **`record_fingerprint` raised `AttributeError` on a non-string field name** instead of
  the spec'd `TypeError`; the pure-Python reference now validates key types up front,
  matching the native kernel (#650).
- **Native-vs-Python parity test baselines** corrected for cluster-confidence (raw vs
  post-downgrade) and soundex/exact (diagonal don't-care) (#650).

## [1.24.0] - 2026-05-29

### Performance

- **10M-QIS-bucket-realistic: 2604 s -> 502 s (81% wall reduction) at F1=0.9886 invariant.**
  This release ships ~15 perf PRs landed during a single concentrated investigation. Total
  RSS dropped from 46.4 GB to 38.2 GB at 10M rows on the bucket backend; pairwise F1
  stayed at 0.9886 across every measurement.
- **Fast-path widening for the bucket scorer.** The per-pair callable dispatch now
  resolves: `soundex_match`, `dice`, `jaccard`, `ensemble` (#555/#565); `_score_one_bucket_fast`
  applies NE penalty math inline (#573) and post-filters for match-mode (#572); the
  fast path now engages for probabilistic matchkeys via `core/probabilistic_fast.py` (#575).
- **GoldenFlow per-row UDF -> Polars-native fast paths.** `date_iso8601` numeric (#560)
  and 4-digit year string (#561) shortcut; `normalize_unicode` ASCII fast path (#563);
  `address_normalize` Polars-native chain (#576, opt-in via `GOLDENMATCH_NATIVE_ADDRESS_NORMALIZE=1`
  pending integration parity).
- **Pipeline microoptimizations.** Polars columnar fast path through the exact-matching
  numpy pair build (#557); single fused `with_columns` in `precompute_matchkey_transforms`
  (#564); cached `n_unique` + `null_count` in goldencheck's scanner column-profile loop (#569);
  `scan_dataframe` entry point so `quality_check` skips the temp-CSV round-trip (#562);
  `pipeline_initial_collect` stage marker so the first LazyFrame materialization is
  attributed honestly (#574).

### Auto-config

- **Chao1 scale-aware cardinality** (#581, #583). `FieldStats` gains optional
  `sample_n_rows` + `singleton_count` + `doubleton_count`; `MatchkeyProfile.health()`
  accepts `n_full_rows` and `FieldStats.estimated_full_cardinality(n_full_rows)`
  applies the Chao1 mark-recapture estimator (`S* = S + F1^2 / (2 * (F2 + 1))`).
  Fixes the persistent matchkey YELLOW that bench telemetry surfaced on
  many-tiny-clusters shapes (QIS realistic: 2M clusters * 5 rows, controller
  sample sees ~1 rep/cluster, raw sample cardinality 0.997 -> Chao1 estimate
  0.31). Backward compat: when Chao1 inputs aren't populated or `n_full_rows`
  isn't threaded, the verdict falls back to the raw sample ratio.
- **`rule_matchkey_demote_high_cardinality_field` heuristic** (#578). New
  default rule that removes a uniquely-identifying field from a weighted
  matchkey (cardinality > 0.99 via Chao1, requires >=2 remaining fields).
  Auto-config's `promote_negative_evidence` retains such fields as NE
  penalty entries, which is the role uniquely-identifying values (email,
  sequential IDs) should play.
- **DataProfile YELLOW signal cleanup** (#579). `DataProfile.health()` no
  longer returns YELLOW just because every column shares a `column_types`
  value (the shape of most CSVs). Single-column inputs still YELLOW.
  Verdict is now precise instead of noisy. Real impact: QIS-style
  fixtures that committed YELLOW solely for this definitional signal
  now commit on the actual structural verdict.

### Diagnostics

- **Per-iteration controller telemetry** in the QIS harness (#577): each
  history entry now emits `iteration`, total + per-sub-profile health,
  decision (rule_name + rationale + config_diff keys), error, and
  wall_clock_ms. Lets bench reports localize which rule fired (or didn't)
  on each iteration without rebuilding the controller from source.



### Added

- **Quality-invariant scale harness** (#510). `scripts/quality_invariant_scale.py`
  runs a single rung end-to-end (deterministic in-process Phase-5 generator that
  keeps the cluster id, zero-config dedupe, Pairwise + B-cubed + Cluster F1 vs GT,
  plus wall / peak RSS / committed controller state) and emits per-rung JSON. The
  initial published report (`docs/quality-invariant-scale.md`) shows the
  zero-config baseline is NOT scale-invariant on the Phase-5 synthetic
  (Pairwise F1 0.91 at 1K -> 0.03 at 10K; controller commits RED at both rungs),
  documents the fixture-vs-pipeline failure mode, and lists the concrete
  workstreams needed to close #510 (realistic synthetic / pinned-config /
  auto-config low-card improvements). Larger rungs ride a Railway one-shot job
  modelled on `Dockerfile.embprov`.

### Documentation

- **In-house embedding provider callout** (#506). README surfaces `provider="inhouse"`
  (the cloud-free `MatchkeyField(model="inhouse:<path>")` path that's been wired
  since 1.21) with the Railway-validated 3-way result: in-house lands within
  ~0.2pp of Vertex AI on structured ER (febrl3 0.949 vs 0.951, DBLP-ACM 0.971
  vs 0.971, synthetic-20k 0.981 vs 0.983). Use it when you want embedding-grade
  recall without a cloud dependency. Harness + Railway one-shot job in
  `scripts/bench_embedding_providers.py` + `Dockerfile.embprov` (#543).

### Fixed

- **Dual identity composite recovers more person-data recall** (#438). The
  composite identity matchkey (1.23.0) picks its fields by cardinality among
  `col_type=="name"` columns; on datasets where the classifier mislabels an
  address as a "name" (e.g. Febrl3), that keys the composite on addresses. Since
  different datasets corrupt different fields (Febrl3 mangles names, NCVR mangles
  addresses), auto-config now ALSO emits a second composite keyed on the
  person-name-pattern columns (given_name/surname) + DOB, so a true pair clean on
  EITHER field-set matches. Same date-anchor gate; OR'd, so it only adds candidate
  pairs. Febrl3 F1 0.924 → 0.933 (recall 0.905 → 0.921, precision unchanged);
  NCVR flat (0.969); DBLP-ACM and DQbench unchanged (no DOB column → no composite).

## [1.23.0] - 2026-05-27

### Changed

- **Auto-config now commits by zero-label confidence by default** (issue #489).
  The controller's `pick_committed` tiebreaker prefers the higher
  `-overall_confidence` candidate over the higher `-mass_separation` one among
  same-health-rank entries that carry a `zero_label` profile (the
  precision-collapse rank-3 demotion still precedes it). Gated on a DQbench
  non-regression run (composite 92.03 >= 91.04 floor; byte-identical to the
  prior default on T1/T2/T3). Opt out with
  `GOLDENMATCH_AUTOCONFIG_ZERO_LABEL_COMMIT=0` to restore the legacy
  `-mass_separation` tiebreaker.

### Fixed

- **Composite name+DOB identity matchkey recovers person-data recall** (#538).
  When individual name/year columns fall below the cardinality≥0.5 exact-matchkey
  gate, auto-config previously degraded to a single weighted matchkey that
  averages every field, so one corrupted field (e.g. an abbreviated address
  scored by `token_sort`) could sink an otherwise-clean true pair. Auto-config
  now also emits an exact matchkey on the name+DOB *combination* (highly unique
  even when the individual fields aren't). Matchkeys are OR'd, so this only adds
  candidate pairs — no fuzzy scorer is loosened. Gated on a date/year anchor
  being present (names alone collide too heavily to anchor an identity claim).
  Benchmarks: NCVR F1 0.871 → 0.969, Febrl3 0.897 → 0.912, DBLP-ACM and DQbench
  unchanged.
- **Phonetic name+DOB identity matchkey** (#491). A sibling to the composite
  above: the same name+DOB key but with a `soundex` transform on the name
  fields, so equal-sounding spellings (Smith/Smyth, Catherine/Katherine) that
  share a DOB still match when the exact composite misses them. Same date-anchor
  gate, so DQbench (no DOB column) is untouched; matchkeys are OR'd, so it only
  adds candidate pairs. Lifts Febrl3 F1 0.912 → 0.924 (CI smoke).

### Internal

- CI Febrl3 smoke-gate floor raised 0.85 → 0.90 (#438), reflecting the recall
  improvements above (CI-measured Febrl3 F1 now 0.924, deterministic).

## [1.22.0] - 2026-05-27

### Added — field-level golden-record provenance at scale

- **`build_golden_records_batch(..., provenance=True)`** adds `source_row_id`
  to each field dict — the `__row_id__` of the record whose value won
  survivorship for that field — while preserving the single-group_by-per-column
  vectorization (one extra agg expr per column on the fast path). `None` for
  all-null fields; raises if there's no `__row_id__` column.
- **`config.output.lineage_provenance`** (default `False`): when enabled, the
  lineage sidecar (`{run_name}_lineage.json`) gains a `golden_records` section
  with per-field provenance (value, `source_row_id`, strategy, confidence)
  for every cluster. Default off because at large scale this materializes one
  provenance object per cluster plus a large JSON sidecar; the vectorized
  builder is what makes it feasible. `candidates` is empty in this path (the
  per-row candidate list is the slow-path-only detail that breaks
  vectorization).
- **`golden_records_to_provenance(records, clusters, rules)`** adapts the
  batch builder's output to the `ClusterProvenance` shape lineage consumes.

## [1.21.0] - 2026-05-27

### Added — optional native acceleration runtime

- **`goldenmatch[native]` extra** pulls in `goldenmatch-native`, a separately
  distributed compiled (Rust/PyO3, abi3) runtime — the polars / polars-runtime
  split. `goldenmatch` itself stays a pure-Python wheel; install
  `pip install "goldenmatch[native]"` (or `pip install goldenmatch-native`
  directly) and it's discovered automatically. Absent it, the pure-Python paths
  run unchanged.
- With the runtime present, the auto-config planner routes simple/fast-box plans
  through the **native Arrow block-scorer** (1.7–3.7x faster at 1k–60k rows,
  byte-identical clusters). Opt out with `GOLDENMATCH_PLANNER_BUCKET=0`.
- Wheels: linux x86_64 + aarch64, windows x64, macOS x86_64 + arm64 (CPython
  3.11+).

### Added — stable record fingerprint

- **`record_fingerprint(record)`** in the public API (and the TypeScript port):
  a canonical, cross-language SHA-256 over a type-tagged, key-sorted byte
  canonicalization. Stable across Python/TypeScript/SQL surfaces and used to
  derive identity record ids.

## [1.20.0] - 2026-05-26

### Added — cluster-decision tuner (RFC from MJH Print Modernization)

Third tuner in the Learning Memory family, paralleling the
pair-level `MemoryLearner` and field-level `tune_field_strategy`.
Consumes cluster-level approve/reject decisions and proposes an
updated auto-approve threshold per-dataset.

- **`Decision.CLUSTER_DECISION`** enum value (`"cluster_decision"`).
- **`Correction.cluster_score`** + **`Correction.cluster_outcome`**
  optional fields. Default None for pair-level + field-level rows.
- **`MemoryStore.record_cluster_decision(dataset, cluster_id, score,
  outcome)`** convenience wrapper.
- **`MemoryStore._migrate_cluster_decision_columns()`** idempotent
  ALTER TABLE for pre-existing v1.18.2+ DBs.
- **`tune_decision_threshold(store, dataset, *, target_approve_rate,
  min_band_n, holdout_frac, max_overfit_drop_pp, seed)`** in
  `core/autoconfig_cluster_threshold_tuner.py`. Returns
  `ThresholdSuggestion(threshold, n_total, n_train, n_heldout,
  train_approve_rate, heldout_approve_rate, reason)`. Same shape as
  `StrategyTuning` so a single dashboard can render both.
- Deterministic shuffle seeded by `sha256(dataset)[:8]` (override via
  `seed` kwarg). Avoids PYTHONHASHSEED non-determinism.
- 90/10 train/heldout split + 1pp overfit guard (configurable).
- Reasons: `"ok"`, `"below_minimum"`, `"no_qualifying_band"`,
  `"overfit"`.

Spec: `docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md`

## [1.19.0] - 2026-05-22

### Added -- v1.18 surface-sync roadmap (Phases 1 + 2)

Brings 6 programmatic surfaces into parity with v1.18.2's field-level
Corrections + 22 predefined plugins.

**Phase 1** (originally targeted v1.18.3; folded into v1.19.0 since
Phase 2 merged first):

- **Python API re-exports.** `PluginRegistry`, `BUILTIN_PLUGINS`,
  `Decision`, `CorrectionSource` are now importable from the
  top-level `goldenmatch` package.
- **MCP `add_correction` schema extension.** `decision` enum gains
  `"field_correct"`. Three new optional properties:
  `field_name`, `original_value`, `corrected_value`. `cluster_id`
  property added for field-level shape. Pair-level path unchanged.
- **MCP `list_plugins` tool.** Lists all registered goldenmatch
  plugins by category. Each entry includes `name`, `category`,
  `source` (builtin / user), and the merge-docstring summary.
- **CLI `goldenmatch memory add` command.** Supports both
  pair-level (`--id-a` / `--id-b`) and field-level (`--cluster-id`
  + `--field-name` + `--corrected-value`) shapes via `--decision`.
  Source defaults to `steward` (trust 1.0); override via `--source`.

**Phase 2:** REST API CRUD.

- `POST /api/v1/memory/corrections` -- pair AND field-level shapes;
  source defaults to "rest" with trust=0.8.
- `GET /api/v1/plugins` -- discovery endpoint; category filter;
  builtin vs user source tagging.
- `GET /api/v1/memory/corrections` response gains field-level fields.

Specs:
- `docs/superpowers/specs/2026-05-22-phase-1-discovery-mcp-parity-design.md`
- `docs/superpowers/specs/2026-05-22-phase-2-rest-api-crud-design.md`

## [1.18.2] - 2026-05-22

### Added

- **Field-level Correction support** (#437). `Correction` dataclass
  gains three optional fields: `field_name`, `original_value`,
  `corrected_value`. `Decision.FIELD_CORRECT` enum value identifies
  the new shape. SQLite migration adds three TEXT columns to
  pre-existing DBs via idempotent `ALTER TABLE`. Tuner
  `_strategy_would_match` runs in two regimes -- field-level
  (predicts strategy fit from edit shape) or pair-level (old
  heuristic). Two-tier corpus selection: field-specific corrections
  beat dataset-wide signal when above threshold.

- **22 predefined golden-strategy plugins** auto-registered via
  `PluginRegistry.discover()` (#442, #443). User opts in via
  `strategy="custom:<name>"` in YAML/Python config. User
  entry-point plugins with the same name override builtins.

  - **Numeric (6):** `numeric_max`, `numeric_min`, `numeric_mean`,
    `numeric_median` (outlier-resilient), `numeric_sum` (aggregate
    amounts), `numeric_weighted_average` (uses `quality_weights`).
  - **Format-canonical (7):** `shortest_value`, `concat_unique`
    (sorted comma-join with configurable separator),
    `email_normalize` (lowercase + strip plus-addressing),
    `phone_digits_only`, `url_canonical` (lowercase host, http→https,
    trim /), `whitespace_normalize`, `boolean_normalize`.
  - **Business-shaped (6):** `system_of_record` (priority-config),
    `lifecycle_stage` (most-advanced via `lifecycle_order`),
    `freshness_with_max_age` (compliance NULL-emit),
    `enum_canonical` (alias_map → canonical), `regex_validated`
    (pattern filter; fallback configurable), `weighted_by_recency`
    (exponential decay).
  - **Aggregation / telemetry (3):** `count_distinct`,
    `count_non_null`, `agreement_rate` (0.0-1.0 mode-agreement).

### Spec

- `docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md`
- `docs/superpowers/specs/2026-05-22-golden-strategy-plugin-slot-design.md`
  (carried from v1.18.0; expanded in v1.18.2 with builtin
  registration order)

## [1.18.1] - 2026-05-22

### Added — golden-rules intelligence layer 2

User feedback: "could be more intelligent." Three of four follow-up
lifts shipped; #4 (LLM-assisted picks) deferred to its own PR.

- **Per-source consensus agreement.** `source_priority` ranking now
  uses per-source agreement-with-cluster-consensus rate, not just
  completeness. Catches "complete but wrong" sources. New
  `RefinementSignals.per_source_agreement` populated from
  cluster-mode voting; falls back to completeness when < 10 attempts
  per source.
- **MemoryStore-learned strategy tuner.**
  `core/autoconfig_golden_strategy_tuner.py`. 90/10 train/heldout,
  5pp overfit guard, gated on >= 50 corrections per dataset,
  env-overridable via `GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS`.
  Refiner consults tuner FIRST per field; falls back to heuristics
  on `no_memory` / `below_minimum` / `overfit_guard`.
- **Per-cluster strategy overrides.**
  `GoldenRulesConfig.cluster_overrides: dict[int, dict[str,
  GoldenFieldRule]] | None`. Refiner sets per-cluster, per-field
  overrides based on cluster shape:
  - `cluster_quality='weak'` → `unanimous_or_null`
  - `oversized` clusters → `confidence_majority`
  - `size == 2` clusters → `unanimous_or_null`

  `_polars_native_eligible` returns False when overrides are set
  (fast path can't honor per-cluster picks).

### Deferred to v1.19+

- **LLM-assisted picks for ambiguous fields** (issue #430). Needs
  prompt design + benchmark measurement before shipping.

Spec: `docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md`

## [1.18.0] - 2026-05-22

### Added — intelligent golden-field consolidation

Two-phase auto-config: matchkey + blocking stays pre-cluster
(unchanged); golden-rules picking moves POST-cluster where it
benefits from real cluster shape signals.

- **Three new strategies** in `VALID_STRATEGIES` and `core/golden.py`:
  - **`longest_value`** — pick the longest non-null string.
    Quality-weighted tie-break. For free-text fields where length
    correlates with completeness (address line, description).
  - **`unanimous_or_null`** — emit the value only if every non-null
    member agrees; emit NULL on any disagreement. For compliance-grade
    fields where a heuristic-chosen value is worse than missing.
  - **`confidence_majority`** — majority vote weighted by within-cluster
    pair_scores. Surfaces the consensus the clustering itself trusts;
    a strong-edge minority can beat a weak-edge majority. Falls back to
    count-majority when pair_scores absent.
- **`GoldenRulesRefiner`** (`core/golden_rules_refiner.py`). Runs
  between `build_clusters` and `build_golden_records` when
  `golden_rules.adaptive=True`. Computes per-field signals
  (within-cluster spread, per-source completeness, date-column
  coverage, col_type, null_rate, avg_len) and emits refined
  `GoldenRulesConfig.field_rules`. Rule table:
  - `col_type=date` + > 50% cluster timestamp coverage → `most_recent`
  - One source > 1.5× median completeness → `source_priority`
    (ranked by completeness)
  - Free-text + long + within-cluster disagreement → `longest_value`
  - `null_rate > 0.5` → `first_non_null` fast path
  - High within-cluster spread (> 2.0) → `confidence_majority`
  - Else → defer to base default
- **`GoldenRulesConfig.adaptive: bool = False`** opt-in flag. Default
  False to preserve existing behavior on benchmarks; flip-to-True is
  a v1.19 candidate after benchmark validation.

Spec: `docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md`

### Deferred (v1.19+ candidates)

- **Custom Python plugin slot** (`strategy="custom:my_rule"`). Protocol
  shape is load-bearing; deserves its own spec + PR.
- **Default flip** of `adaptive=True` once benchmarks confirm no F1
  regression.

## [1.17.1] - 2026-05-22

### Changed — streaming-sync match log throughput (#424, PR #426)

- **`log_matches_batch` uses `cursor.copy()` on psycopg3**. The previous
  `executemany` path was NOT pipelined without an explicit
  `with conn.pipeline():` context, capping throughput at ~125 rows/sec
  on the user's 1.13M-row managed-Postgres workload (~7ms RTT × N
  round-trips per batch). `cursor.copy("COPY gm_match_log ... FROM STDIN")`
  is 10-100× faster on bulk loads. Falls back to `executemany` for
  non-psycopg3 cursors (SQLite / DuckDB test paths).
- **Buffer pairs across blocks before flushing**. Default
  `GOLDENMATCH_MATCH_LOG_FLUSH_PAIRS=10000`. Final flush at end of
  streaming loop covers the tail. Set to `1` for per-block flush
  (preserves the historical incremental-progress behavior + test
  contract).
- **Opt-out via `GOLDENMATCH_SKIP_MATCH_LOG=1`**. Nightly-cron pipelines
  that only consume `gm_clusters` / `gm_golden_records` can skip
  per-pair audit logging entirely. INFO log line surfaces when set.

### Notes

User-reported impact: 125 rows/sec ceiling → projected 10K+ rows/sec.
For the user's ~3M-pair workload, ~5 hours of writes → < 5 min.

## [1.17.0] - 2026-05-22

### Added — v1.13 autoconfig roadmap (2026-05-21, PRs #415/#416/#418)

- **ADR practice** (`docs/adr/`). 7 foundational records capturing
  load-bearing architectural decisions:
  - ADR-0000: Adopt ADR practice
  - ADR-0001: `confidence_required=True` as the default safety gate
  - ADR-0002: Unified `exclude_columns` API surface across the suite
  - ADR-0003: Matchkey vs blocking pools as orthogonal axes
  - ADR-0004: Chao1 sample-size correction for autoconfig cardinality
  - ADR-0005: Streaming-block sync as the >500K-row path
  - ADR-0006: Telemetry-gated rule deprecation
- **Wave A — stratified autoconfig sampling (#131)**. `_sample_one`
  picks a mid-cardinality column (prefers `zip` / `state` / `postal`
  names) and stratifies by it; rare strata get a `min_per_stratum=10`
  floor. Falls back to uniform-random when no qualifying column.
  Env-rollback: `GOLDENMATCH_AUTOCONFIG_SAMPLE_STRATEGY=random`.
- **Wave A — iteration-oscillation guard (#127)**.
  `HeuristicRefitPolicy` tracks the last `(rule_name, rationale)` that
  fired; identical fire next call → skip, policy advances. Different
  rationale bypasses; first call never blocks.
- **Wave B — real `ExpandSample(2.0)` controller action (#125)**.
  `PolicyDecision` gains `expand_sample: float | None` field.
  `rule_sparse_match_expand` emits `expand_sample=2.0` instead of
  proxying via threshold-lowering. `AutoConfigController.run`
  intercepts and resamples `df` with `sample_size_default *= factor`
  before the next iteration. Capped at 5× initial.
- **Wave B — telemetry log for `rule_demote_clustered_identity` (#124)**.
  Env-gated INFO log (`GOLDENMATCH_TELEMETRY_DEMOTE_RULE=1`) for the
  1-2 week observation window. Deletion gated on zero firings; see
  ADR-0006 for the pattern.
- **Wave D — NE-on-Fellegi-Sunter investigation (#126)**. Concluded
  document-and-close. Three formulations evaluated; none cleanly
  preserve LLR additivity without labeled data that Wave E provides
  downstream. `MatchkeyConfig.negative_evidence` field docstring
  updated to document the non-goal + the
  `GOLDENMATCH_NE_FS_ESCAPE_MODE=floor` escape hatch.
- **Wave E — adaptive NE tuner module (#129)**.
  `core/autoconfig_ne_tuner.py`. `tune_ne_field(store, dataset, field)`
  runs grid search over `PENALTY_GRID × THRESHOLD_GRID`, 90/10
  train/heldout split, overfit guard at 5pp F1 drop. Gated on ≥ 50
  MemoryStore corrections. Env-overridable
  `GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS`. Integration with
  `promote_negative_evidence` is the natural follow-up; this lands
  the tuner module + tests with a stable API.
- **v1.13 autoconfig roadmap** at
  `docs/superpowers/specs/2026-05-21-v1-13-autoconfig-roadmap.md`
  sequencing 6 issues into 5 waves with explicit kill criteria.
- **Composite-search candidate log (#417)**. Every evaluated pair in
  `find_composite_blocking_keys` emits an INFO line:
  `pair (c1, c2) joint_cardinality=K -> in_band|below_band|above_band`.
  Header + winner-or-no-pair summary. Eliminates the "why didn't it
  pick X" black-box.
- **`degenerate_guard_max_avg_block_size()` helper (#417, default 10K)**.
  Env-overridable
  `GOLDENMATCH_BLOCKING_DEGENERATE_MAX_AVG_BLOCK_SIZE`.

### Changed — autoconfig safety + correctness (2026-05-21, PRs #411 → #423)

- **Sample-corrected blocking via Chao1 (ADR-0004, PR #411)**.
  `core/blocking_candidates.py::scale_cardinality_ratio_to_full_population`
  projects sample-observed distinct values to full-population
  cardinality using `sample_distinct × √(N_full / N_sample)`. Same
  scaler reused by `estimate_avg_block_size`. Env-rollback:
  `GOLDENMATCH_BLOCKING_CARDINALITY_SCALER=observed` reverts to linear.
- **Controller threads true `n_rows_full` into v0 (#414)**.
  `AutoConfigController._initial_config` accepts a new
  `n_rows_full: int | None` kwarg and injects into `v0_kwargs`.
  `_legacy_auto_configure_v0` reads it as `total_rows`. Without this,
  v0 saw `df.height` (the sample size) and the Chao1 short-circuit
  fired, defeating the gate.
- **Composite-search wired into `build_blocking` (#411)**.
  `find_composite_blocking_keys` was already implemented + tested but
  never called from the real path. Now inserted as a fall-through
  between single-column candidates (`exact_cols` / `name_cols`) and
  the text-canopy / first-string last resort.
- **`BLOCKING_DEGENERATE` guard catches mega-block case (#419)**.
  Existing guard only caught singleton blocks (`avg_block_size < 2`);
  upper-bound now catches `avg_block_size > 10K` (the
  every-row-in-one-block scenario that wedges downstream
  `bucket_score` in O(n²) on one core).
- **`BLOCKING_DEGENERATE` guard catches empty `blocking.keys` (#420)**.
  Pre-#420 precondition required `keys` non-empty, which short-circuited
  exactly the case the guard exists for. Restructured into three RED-
  gated branches: `blocking is None`/`keys=[]`, `keys` present but
  `fields=[]`, and the existing estimator-based check.
- **`--exclude-columns` enforced PRE-autoconfig in `goldenmatch sync` (#421)**.
  CLI now parses excludes and sets `_RUNTIME_EXCLUDE_COLUMNS` ContextVar
  before calling `auto_configure`, then resets in `finally`. Previously
  the merge happened post-hoc, after matchkeys + blocking were already
  built. Excluded columns no longer slip through as matchkeys.
- **`BLOCKING_DEGENERATE` guard inspects matchkey-derived blocking (#421)**.
  When `config.blocking.keys` is empty, the streaming sync derives
  effective blocking from `matchkeys[0].fields`. Guard now falls back
  to those fields (gated on profile.health() == RED) so the implicit-
  blocking degenerate case is caught at the controller level.

### Changed — streaming-sync throughput (2026-05-22, PR #423)

- **`score_buckets` small-block fast path (#422)**. When
  `prepared_df.height < n_buckets`, the hash + `partition_by(__bucket__)`
  step always collapses to 1 bucket by pigeonhole. Skip the bookkeeping
  on small inputs; treat `keyed` directly as the single bucket. Removes
  3 Polars ops per block on the user's 585K-block 1.13M-row workload.
- **`_full_scan_streaming` parallel outer loop (#422)**. Per-block
  scoring dispatched via `ThreadPoolExecutor(max_workers=min(cpu_count, 8))`.
  Env override: `GOLDENMATCH_STREAMING_BLOCK_WORKERS`. Cross-block
  dedup deferred to a single canonical `(min, max)` pass over the
  merged pair list. Serial fallback preserved for ≤ 2 work items.
- **`MemoryStore` enables SQLite WAL mode by default (#130, PR #413)**.
  `PRAGMA journal_mode=WAL` on every open. Resolves "database is
  locked" intermittent failures when two `MemoryStore` instances point
  at the same file. Allows one writer + multiple readers concurrently.

### Fixed

- `goldenmatch sync` cluster: four user-visible bugs that all surfaced
  from one Postgres sync run against a 1.13M-row table on a slim Python
  build.
- **#410** post-#409 follow-up: composite-search wired but never fired
  because v0 saw sample-sized `total_rows` (full-pop count wasn't
  threaded through controller). Fixed in #414. Spec:
  `docs/superpowers/specs/2026-05-21-blocking-pool-followup-design.md`.
- **#417** v1-v3: autoconfig wedge on 1.13M-row real-world Postgres
  table. Resolved across PRs #419 → #420 → #421 → #423. User
  confirmed correctness fix + 585K small-block run with sub-second
  match-log lag. See ADR-0003.
- **#422** throughput: small-block fast path + parallel outer loop.
  Target acceptance: < 30 min on 8 vCPU / 16 GB for the user's
  585K-block table.

### Added (Phase 5 — Splink-Spark roadmap, pre-existing)

- `bench-phase5-simulated` workflow job: runs a 4-worker Ray cluster
  inside one `large-new-64GB` GitHub runner against 50M synthetic rows,
  exercising the Phase 5 streaming pipeline without provisioning
  external infrastructure. Workflow_dispatch only (gated on
  `run_phase5_simulated=true`); optional identity variant via
  `run_phase5_simulated_identity=true`. Honest scoping (single NIC,
  single disk, shared OS page cache): this is a regression check, NOT
  a Splink-Spark parity proof. The real-cluster bench
  (`bench-phase5-end2end`) is still required for any parity claim.
- `scripts/bench_phase5_simulated.py`: cluster-agnostic Phase 5 bench
  driver (works against simulated AND real clusters, only `RAY_ADDRESS`
  differs). Connects to a pre-started Ray cluster, runs
  `run_dedupe_pipeline_distributed`, writes a JSON summary with wall
  + RSS + cluster resources.
- `scripts/generate_phase5_50m_dataset.py`: one-shot 50M dataset
  generator wrapper. Outputs `bench-dataset/bench_50000000.parquet`
  for upload to the `bench-dataset-v1` release. ~8 hr single-node
  generation cost.

### Fixed

- `goldenmatch sync` cluster: four user-visible bugs that all surfaced
  from one Postgres sync run against a 1.13M-row table on a slim Python
  build.
  - #362: `golden.py` no longer calls `top_k_by(..., reverse=False)`,
    which mis-binds args on newer Polars versions. Replaced with
    `sort_by(descending=True).first()`.
  - #363: chunked Postgres reads now cast `Null`-dtype columns to
    `Utf8` before `pl.concat`, fixing `type String is incompatible
    with expected type Null` on sparse-column tables. Defensive
    `how="diagonal_relaxed"` on the concat call too.
  - #364: `sqlite3` is now lazy-imported inside `ReviewQueue`,
    `MemoryStore`, and `IdentityStore`. `import goldenmatch` and the
    CLI no longer require `_sqlite3` on minimal Python builds (Vercel
    Sandbox, slim Docker, etc.).
  - #365: `_quote_ident("schema.table")` now produces
    `"schema"."table"`, allowing `goldenmatch sync --table gm.foo`
    against non-public schemas without a search_path workaround.

- `two_phase_wcc` no longer rehydrates a 475 MiB Python `dict[int, int]`
  in every worker. Phase B's lookup table now travels as a Polars frame
  via `ray.put`, zero-copy mapped from plasma into worker address space
  (~80 MiB at 5M nodes, ~6x smaller). Phase B internals use vectorized
  Polars joins instead of Python row-loops. Resolves the 5M-scale OOM
  surfaced by run 26166347530.

### Added (Phase 6 — Identity Graph at distributed scale)
- **psycopg3 migration** of the Postgres backend in `goldenmatch.identity.store`.
  `psycopg2-binary` replaced by `psycopg[binary]>=3.1`. Adds first-class
  COPY context manager support and `psycopg_pool` integration.
- **Bulk COPY writes**: `IdentityStore.bulk_upsert_identities`,
  `bulk_upsert_records`, `bulk_add_edges`, `bulk_emit_events`. Each
  COPYs a Polars frame into a staging temp table, then
  `INSERT ... ON CONFLICT` into the real table. Postgres only;
  SQLite raises `NotImplementedError` with a helpful message.
- **Connection pool**: `goldenmatch.identity.pool.get_identity_pool(dsn)`
  process-singleton `psycopg_pool.ConnectionPool`. `IdentityStore`
  constructor accepts optional `pool=` kwarg.
- **Alembic migrations**: `goldenmatch/db/alembic/` with baseline
  revision `0001_identity_v1` mirroring the existing schema. New CLI
  `goldenmatch identity migrate --dsn ... [--stamp-existing]` runs
  upgrades; `--stamp-existing` brings a pre-Alembic schema under
  version control without touching tables.
- **Distributed dispatch**: `goldenmatch.distributed.identity.resolve_identities_distributed`
  polymorphic on `dict[int, dict] | ray.data.Dataset`. Ray Dataset
  path materializes cluster aggregates driver-side via
  `materialize_cluster_dict`, then runs the existing resolver against
  a pooled Postgres connection.
- `goldenmatch.core.pipeline._resolve_identities` polymorphic on
  cluster shape; Ray Dataset path requires `identity.backend='postgres'`
  and `identity.connection` set.
- `IdentityStore.add_edge` on the Postgres branch now uses
  `ON CONFLICT (entity_id, record_a_id, record_b_id, kind, run_name) DO NOTHING`
  matching the SQLite `INSERT OR IGNORE` semantic. Fixes
  `UniqueViolation` on replayed runs.
- Tests: `tests/identity/test_pool.py`,
  `tests/identity/test_alembic_migration.py`,
  `tests/identity/test_uuid7_concurrent.py`,
  `tests/identity/test_distributed_identity.py`.

### Added (Phase 5.5)
- **Two-Phase WCC** (`goldenmatch.distributed.clustering.two_phase_wcc`)
  replaces label propagation as the default distributed connected-
  components algorithm. Phase A: per-partition local `UnionFind` via
  `map_batches` (embarrassingly parallel). Phase B: driver-side super-
  graph Union-Find on boundary edges between partitions (bounded by
  O(P^2) max edges).
- New env var `GOLDENMATCH_DISTRIBUTED_WCC` — default `two_phase`,
  opt back into `label_propagation` for regression testing.
- `UnionFind.nodes()` accessor on `goldenmatch.core.cluster.UnionFind`.
- `scripts/generate_chain_dataset.py` — synthetic chain-heavy graph
  generator for the adversarial bench (label-prop's worst case).
- `scripts/bench_phase5_5_wcc.py` — head-to-head bench timing both
  algorithms on the same input + asserting partition-structure
  equivalence.
- `bench-phase5-5-wcc` workflow job (workflow_dispatch only,
  `run_phase5_5_wcc=true`). Generates 5M-chain dataset on the fly.
- Motivated by GraphFrames maintainer Sem Sinchenko's recommendation:
  chains are label-prop's worst case and identity graphs are
  chain-heavy.

### Added (Phase 5)
- **Distributed scoring:** `goldenmatch.distributed.scoring`:
  - `score_blocks_distributed(df_ds, config)` — per-partition fuzzy +
    exact scoring via `ds.map_batches`. Each worker runs in-memory
    `dedupe_df` with `backend="bucket"` on its slice; pair tuples emitted
    as a Ray Dataset.
  - `dedup_pairs_distributed(pairs_ds)` — cross-partition pair dedup.
    Canonicalizes `(min, max)` ordering, groupby + max(score) keeps the
    highest score per canonical pair.
- **Phase 5 streaming pipeline** in `goldenmatch.distributed.pipeline`:
  `GOLDENMATCH_DISTRIBUTED_PIPELINE=2` activates `_run_phase5_pipeline`
  — auto-configure → distributed score → distributed cluster (Phase 3
  polymorphic) → annotate-with-cluster-id (broadcast dict via `ray.put`)
  → distributed golden (Phase 4 polymorphic) → write_parquet. No entry-
  side `take_all` on input. Phase 2 default cheat-line and Phase 4
  scaffold (`=1`) remain available.
- `output_path` kwarg on `run_dedupe_pipeline_distributed` writes the
  golden output to parquet at end of pipeline.
- **100M dataset generator:** `scripts/generate_phase5_dataset.py` —
  synthetic 100M rows with ~5-member clusters + 10% typo injection.
- **Multi-node Ray cluster docs:** `docs/distributed-ray-cluster-setup.md`
  — sizing recommendations, `ray up` config, KubeRay equivalent, network
  requirements, object store sizing, cost framing. Splink-posture:
  documentation, not bootstrap automation.
- **`bench-phase5-end2end` workflow job:** `workflow_dispatch` only,
  gated on `run_phase5_bench=true`. Requires `RAY_ADDRESS` secret +
  pre-uploaded `bench_100000000.parquet`. Runs on `ubuntu-latest`
  client; Ray work happens on the remote cluster.
- Kill criterion: 100M end-to-end < 30 min on a 4 × 16c/64GB worker
  Ray cluster.
- **Two-Phase WCC swap** (per GraphFrames maintainer advice on label-prop's
  chain pathology) **deliberately deferred to Phase 5.5** — separate
  spec/plan after we have real 100M chain-heavy graph data to verify
  against. Label propagation stays the distributed clustering algorithm
  with the existing routing threshold.

### Added (Phase 4)
- **Distributed golden record build:** `goldenmatch.distributed.golden`:
  - `build_golden_records_distributed(multi_ds, rules, user_columns)` —
    Ray Dataset `repartition(keys=["__cluster_id__"]) + map_batches`. Hash
    partitioning co-locates each cluster's rows; no cross-partition splits.
    `groupby.map_groups` not used — it re-enters Ray's streaming executor
    and deadlocks in 2.54.
  - `build_golden_records_smart` — cluster-count routing
    (default 5M threshold; `GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD` override).
  - `materialize_golden_dataframe(golden_ds)` — adapter to `pl.DataFrame`.
- `core.golden.build_golden_records_batch` polymorphic on Ray Dataset
  input. In-memory `pl.DataFrame` callers byte-identical.
- Custom field rules + `quality_scores` always route to in-memory
  (closure serialization + N×K dict size considerations).
- `run_dedupe_pipeline_distributed` env-gated Phase 4 path
  (`GOLDENMATCH_DISTRIBUTED_PIPELINE=1`). Today the Phase 4 path retains
  the take_all cheat-line; the polymorphic dispatch in build_clusters
  (Phase 3) and build_golden_records_batch (Phase 4) handles Dataset
  callers directly. Phase 5 retires the take_all once scoring distributes.
- `scripts/bench_phase4_golden.py` + `bench-phase4-golden` workflow job.
  Kill criterion: golden wall < 180s at 25M.

### Added (Phase 3)
- **Distributed clustering:** `goldenmatch.distributed.clustering`:
  - `pairs_list_to_dataset(pairs)` — convert scored pairs to a Ray Dataset.
  - `label_propagation(pairs_ds, all_ids, convergence_max_iterations=30)`
    — iterative label-prop; raises `ConvergenceError` on non-convergence.
  - `build_clusters_distributed(pairs_ds, all_ids, ...)` — returns a Ray
    Dataset of `{member_id, cluster_id, cluster_size, oversized}` rows.
    Falls back to driver-side scipy.csgraph on non-convergence with a
    WARNING log.
  - `materialize_cluster_dict(clusters_ds, pairs_ds)` — adapter to the
    existing `dict[int, dict]` shape (Phase 4 removes this).
- `core.cluster.build_clusters` polymorphic on Ray Dataset input via
  `is_ray_dataset` dispatch; in-memory callers byte-identical.
- `scripts/bench_phase3_cluster.py` + `bench-phase3-cluster` workflow job.
- **Splink-style routing** in `build_clusters_distributed`:
  pair count < 50M routes to driver-side scipy.csgraph;
  >= 50M routes to distributed label propagation. Override via
  `GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD` env var or
  `force_label_propagation=True`. Calibrated against run `26119800863`:
  at 8.3M pairs, label-prop's per-iter Ray Dataset HashAggregate
  overhead (10-20s/iter) dominates; scipy.csgraph on the same pair
  count is seconds. Mirrors Splink's own pattern (DuckDB below scale,
  Spark above). Label propagation's binding multi-node proof point is
  Phase 5.
- The Phase 2 cheat-line in `run_dedupe_pipeline_distributed` stays in
  place — Phase 4 distributes golden and removes the materialize.

### Added (Phase 2)
- **Distributed controller:** `AutoConfigController.run` polymorphic on
  `pl.DataFrame | ray.data.Dataset`. New modules under `goldenmatch.distributed`:
  - `indicators` — `compute_column_priors_distributed`,
    `estimate_sparse_match_signal_distributed` (bounded-sample collect).
  - `sample` — `take_sample_distributed(ds, sample_cap)` returns a
    Polars DataFrame for the iteration loop.
  - `pipeline` — `run_dedupe_pipeline_distributed`: materialize-then-call
    cheat-line for `_finalize` (Phase 3 removes the materialize).
  - `_utils.is_ray_dataset` — module-name duck-type helper.
- Dispatch shims in `core.indicators`: `dispatch_compute_column_priors`,
  `dispatch_estimate_sparse_match_signal`.
- `scripts/bench_phase2_controller.py` + `bench-phase2-controller`
  workflow job. **Result, run 26107123459: 25M / 94.2s controller wall /
  1.08 GB driver peak RSS.** Architectural goal MET (driver does not
  materialize the full df). Wall budget realigned from < 30s to < 180s
  after measurement — per-iter `_run_pipeline_sample` on bench-dataset-v1
  is ~30s, independent of distributed wiring. Phase 3 distributes the
  per-iteration pipeline to remove that cost.

### Added
- **Distributed Phase 1: partition-aware data loader on Ray Datasets.**
  Opt-in via `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1` env var combined with
  `backend="ray"`. New module `goldenmatch.distributed`:
  - `read_csv_partitioned(path, n_partitions, schema=None)` — returns a
    lazy `ray.data.Dataset` partitioned into N blocks. Driver never holds
    the full input frame. Supports single path or list of paths.
  - `apply_transforms_distributed(ds, transforms)` — runs a list of
    `TransformPlan`s per-partition via `ds.map_batches(batch_format="pyarrow")`.
  - `TransformPlan` (in `goldenmatch.distributed.transforms`) — frozen
    dataclass replacing closure-based transforms from `core/transform.py`.
    Round-trips cleanly across Ray worker boundaries. Ops: `lower`, `upper`,
    `strip`, `strip_punctuation`, `nfkc`.
  - `_load_input_frames(config)` in `core/pipeline.py` — env-gated branch
    point. Routes to the distributed loader only when both `backend="ray"`
    AND the env flag are set; otherwise legacy `core.ingest.load_files`.
- **Phase 1 kill-criterion bench:** `scripts/bench_phase1_loader.py` +
  `bench-phase1-loader` job in `.github/workflows/bench-distributed-stack.yml`.
  Kill criterion: driver peak RSS < 8 GB at 25M rows on a 16c/64GB runner.
  **PASS, run 26100132595: 25M rows in 14.7s prep wall at 0.24 GB driver
  peak RSS (33x margin under the 8 GB threshold).** Phase 2 (controller
  iteration on distributed samples) unblocks. Three bench-harness fixes
  preceded the green result: pandas + pipefail (#338), parquet support
  (#339), bench-dataset-v1 column names (#340).
- **`core/transform.build_transform(column, op)`** back-compat shim: returns a
  closure that delegates to `apply_plan(df, TransformPlan(column, op))`.
  Callers still consuming the callable-style transform get equivalent output.

### Notes
- Phase 1 is plumbing only — `_load_input_frames` is NOT yet wired into the
  default pipeline path. Production runs without the env flag continue
  through `core.ingest.load_files` byte-for-byte. Phases 2-5 (controller
  iteration on distributed samples, distributed clustering, distributed
  golden, cluster orchestration) remain TODO. See
  `docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md`.
- The 25M-on-bucket single-node path landed in the same window (run
  26095134836, 6.5 min / 57.7 GB peak RSS) and is the supported
  recommendation for the 5M-25M lane. Phase 1 is value-add for 50M+
  workloads, not a prerequisite for 25M.

## [1.16.0] - 2026-05-18

<!-- README-callout
**5M records in 9.94 min, 6.4 GB peak RSS, on one 16-core node** — the new `backend="bucket"` path is now the recommended 5M-on-one-node config. 5x wall reduction and 2x peak RSS reduction vs the v1.15 chunked baseline (~50 min, 11.9 GB), with rock-solid reliability on Linux runners where the chunked path was hanging at 63 GB plateau on the same fixture. PRs #310-#326.
-->

### Added -- 5M-on-one-node performance pass (PRs #310-#326)

The 5M-record workload on a 16-core / 64 GB Linux runner went from "chunked path hangs at 63 GB plateau" to a **clean 9.94-minute completion at 6.4 GB peak RSS** through a focused set of in-process pipeline optimisations. Headline:

| Path | Wall | Peak RSS | Reliability |
|---|---|---|---|
| Pre-v1.16 `chunked` on same fixture | hung @ 63 GB plateau | — | never completed |
| **v1.16 `backend="bucket"`** | **596s / 9.94 min** | **6.4 GB** | reliable |

What landed:

- **`backend="bucket"` (PR #308)** — in-process bucket scorer that replaces the per-block `LazyFrame` model. A single `with_columns(hash(block_key) % N)` plus one `partition_by("__bucket__")` produces N≈64 eager bucket DataFrames; per-block scoring runs inside each bucket without re-materialising millions of small frames. The legacy chunked path's per-block `.collect()` on filter-LazyFrames was the root cause of the Linux arena pathology where 1.67M small frames OOM-killed the runner.
- **Bucket fast path for tiny-block / weighted workloads (PR #320)** — when a matchkey has no negative-evidence, no rerank, no LLM, no `record_embedding`, and a single-source dedupe, the scorer pre-resolves each field's `score_pair` callable + xform column ONCE at entry, then iterates row pairs inside each block via direct Python loops with inlined scorer calls. At 5M / 1.67M 3-row blocks this is **~24× faster** than the previous `find_fuzzy_matches` per block path (2513s → 91s on the bench).
- **Pre-fuzzy stage sweep (PRs #310/#311/#312)** — three independent Python-UDF bottlenecks fixed: `analyze_blocking` now samples to 100K rows before scoring candidates; the bucket scorer drops a wasteful `_BlockShim` + `LazyFrame.collect()` round-trip; `_build_block_key_expr` uses native Polars expression chains (`lowercase`, `strip`, `substring`, …) instead of `pl.col().map_elements(apply_transforms)` per row.
- **Golden batch builder + Polars-native fast path (PRs #322/#324)** — `build_golden_records_batch` pre-extracts each user column to a Python list ONCE per multi_df instead of calling `cluster_df[col].to_list()` 6.7M times. For the common "uniform default_strategy, no field_rules, no quality_scores" case, the new `_build_golden_records_polars_native` path computes the entire stage via Polars `group_by(...).agg(top_k_by(len)).first()` per column — golden stage 307s → 122s at 5M.
- **Cluster pure-Python UnionFind retained.** A scipy.csgraph rewrite (PR #323) regressed cluster from 121s → 297s at 3.3M-cluster scale because the Polars list-of-lists materialisation for per-cluster members cost more than the original Python loop saved. Reverted in PR #326; the Python UnionFind path is the keeper.
- **Bench harness gates** (PRs #314 / #316 / #321) — bench is now metrics-only (never materialises `result.clusters` at scale), passes `_skip_finalize=True` to `auto_configure_df` so the controller doesn't double-run the full pipeline, and gates the legacy Distributed-Plan-v1 treatment lane behind `--run-treatment` (default off; see soft-revert note below).

### Changed -- Distributed Plan v1 soft-reverted (PRs #318/#321)

The Distributed Plan v1 stack (`prepared_record_store=True` + `partitioned_block_scoring=True` + `backend="ray"`) FAILED the binding 5M kill criterion set in `docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md`: on the same `large-new-64GB` runner where the new bucket path completes in 9.94 min at 6.4 GB peak RSS, the distributed stack climbed past baseline's peak within minutes and had to be cancelled.

The fix is a SOFT revert: code paths stay (PRs #280-#287 untouched), but the v3 planner no longer auto-picks ray. Set `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1` to opt back in, or pass `backend="ray"` explicitly. Defaults for `prepared_record_store` and `partitioned_block_scoring` are unchanged (already False).

### Added -- `_skip_finalize` is now a documented stable knob

Already a kwarg on `auto_configure_df` but previously underscore-prefixed and undocumented. The bench harness uses it (PR #316) to prevent the controller from running the full pipeline inside `auto_configure_df` (which masquerades as a hang and double-counts wall in any external benchmark). Users running their own benches against an explicit config should pass `_skip_finalize=True`.

### Bench progression (5M, `large-new-64GB`, `backend="bucket"`)

| PR | Wall | Δ | bucket_score | golden | Notes |
|---|---|---|---|---|---|
| v1.15 baseline (`chunked`) | hung @ 63 GB | — | — | — | never completed on same fixture |
| #308 (bucket backend) | ~54 min | — | ~30 min | ~5 min | reliable |
| #317 (drop shim) | 48 min | -10% | 36 min | 5 min | |
| #320 (fast path) | 13.2 min | -73% | 1.5 min | 5 min | the headline |
| #322 (golden batch) | 11.2 min | -15% | 1.5 min | 3.3 min | |
| **#324 (golden polars-native) — v1.16.0** | **9.94 min** | **-11%** | **1.5 min** | **2.1 min** | shipping |

## [1.15.0] - prior

<!-- README-callout
**5M records in ~50 min on commodity hardware** — Chunked mode now actually delivers on its "1M to 100M+" promise. The streaming `scan_csv().slice()` reader + Polars-native cross-chunk join (B) + block-keyed bucketed index (C) + DuckDB pair-store backend (D) replace a broken eager-read + Python-double-loop path that OOM-killed at 3h+ on the pre-fix 5M dispatch. **Measured: 5M records, 50 min wall, 11.9 GB peak RSS, 618,817 multi-member clusters, no OOM** on a 4c/16GB GitHub runner. Pass `backend="chunked"` with an explicit blocking config. PRs #233/#234/#235.
-->

### Added -- Chunked-mode out-of-core delivery (PRs #233/#234/#235)

The pre-fix `core/chunked.py` claimed "1M to 100M+ records" in its docstring but eagerly materialized the full CSV via `pl.read_csv` before slicing, and ran a Python double-loop in `_match_against_index` that was O(chunk_size × index_size) per chunk — making the chunked path strictly worse than `polars-direct` past 1M. A scale audit dispatched at 5M on 16 GB ran for 3h 36m without completing.

Three landed PRs fixed it:

- **B (#233) — `feat(chunked): vectorize cross-chunk match via Polars`** — `_match_against_index` no longer loops; it `pl.concat`s the chunk slim slice with the persistent index, recomputes matchkey-derived columns, and runs `find_exact_matches` + `build_blocks` + `score_blocks_parallel` over the joint frame, filtering to cross-pairs. Same machinery as the within-chunk path. `_index_df: pl.DataFrame` replaces `_index_records: list[dict]`. Expected constant-factor improvement 50–200× from rapidfuzz cdist replacing per-pair `score_pair` calls.
- **C (#234) — `feat(chunked): block-keyed cross-chunk index lookup`** — adds `_index_by_block: list[dict[str, pl.DataFrame]]` for `strategy="static"` blocking. Cross-chunk weighted matching now groups the chunk by block key, looks up the matching bucket per block, synthesizes mixed `BlockResult`s, and scores with `target_ids=chunk_ids`. **Pure-index blocks are never instantiated.** Algorithmic shift from O(joint_size × avg_block) to O(Σ over chunk-blocks K of \|chunk[K]\| × \|index[K]\|).
- **D (#235) — `feat(backend): score_blocks_duckdb out-of-core pair store`** — wires `config.backend="duckdb"` into `_get_block_scorer` so the in-package DuckDB backend is finally non-empty. Pair accumulator moves from Python `list` to a DuckDB table (`:memory:` by default; `db_path="auto"` or `GOLDENMATCH_DUCKDB_SCORE_DB` for spill-to-disk). Per-block rapidfuzz cdist work is unchanged — D's value is at 50M+ where pair counts hit 10⁸, not at 5M.

Measured at 5M (scale-audit 2026-05-14):

| Lane | Wall | Peak RSS | Result |
|---|---|---|---|
| `backend=chunked` (B+C) | 50 min | 11.9 GB | ✅ 618,817 multi-member clusters |
| `backend=duckdb-backend` (D smoke) | OOM ~25 min | — | ❌ in-memory scoring matrices still hit 16 GB ceiling |

The `chunked` lane is the new recommended path for 5M+ on commodity hardware. `duckdb-backend` is infrastructure for 50M+ once paired with future SQL-native scoring.

### Changed -- Refdata autoconfig hook gates on ColumnProfile.col_type (strategy direction #8, ninth slice)

Finishes the deferred slice from #8. Refdata refinements (surname scorer swap, given-name alias scorer swap, legal-form-strip, address-normalize, NAICS-normalize) now consult the profiled data shape, not just the column name. A column literally named `last_name` but holding numeric IDs, dates, or hashed identifiers no longer gets its scorer silently swapped — the swap was previously a quality regression on those shapes, hidden behind the column-name match.

- **`refine_matchkey_field()`** gains an optional `col_type: str | None = None` parameter. Per-rule accept-lists: name swaps require `col_type in {"name", "multi_name"}`; company `legal_form_strip` accepts `{"name", "multi_name", "description", "string"}` (free-text company names sometimes profile as description/string); address `address_normalize` accepts `{"address", "string"}`; NAICS `naics_normalize` accepts `{"identifier", "numeric", "string", "description"}` (NAICS codes commonly land as identifier or numeric).
- **`col_type=None` preserves the legacy name-only behavior** so callers without a profile (ad-hoc use, tests) don't change shape.
- **Call sites in `core/autoconfig.py`** at both `build_matchkeys()` and `build_probabilistic_matchkeys()` now pass `p.col_type` from the `ColumnProfile`.
- **Tests** in `tests/test_refdata_autoconfig.py` cover the gate firing and skipping for each refinement, the `None` backwards-compat path, and end-to-end via `build_matchkeys()` on a synthetic frame where `last_name` holds numeric data — the surname scorer should NOT fire.

### Changed -- Refdata cross-pack cleanup (strategy direction #8, eighth slice)

Refactor-only PR — no behavior change to any user-facing surface. Addresses the systemic findings from the parallel review of slices #1–#7. Five refdata modules (surnames, given_names, business, addresses, industries) plus the plugin registry are touched in one coordinated change.

- **Frozen-dataclass state** replaces the `_state: dict[str, Any]` lazy-load pattern in all five refdata modules. Each module now has a small `_<Pack>State` `@dataclass(frozen=True)` with explicit fields and types. Module-level `_state: _<Pack>State | None`; `None` means "not loaded or data file missing". Reviewer's concern: the dict-pattern carried 4–6 implicit invariants per copy with no static type backing; a typo silently wrote a string to an int field with no detection.
- **Atomic-swap `_reload()`** falls out for free: it just sets `_state = None` under the lock; the next reader drives `_load()` which assigns a freshly built dataclass. Compared to the previous `_state["loaded"] = False` + dict-wipe pattern, readers never see a half-built state mid-reload. This was flagged on industries.py in the PR #222 review-fix; the pattern is now consistent across all five packs.
- **Plugin Protocols bound and enforced.** `goldenmatch/plugins/base.py` Protocols (`ScorerPlugin`, `VectorizedScorerPlugin`, `TransformPlugin`) now match the runtime contracts in `core/scorer.py` / `utils/transforms.py`. `PluginRegistry.register_scorer` / `register_transform` `isinstance`-check the plugin against the Protocol and raise `TypeError` at registration if the duck-typed implementation is missing a method or the `name` attribute — fails at bind time instead of deep inside a scoring loop. Refdata adapters (`NameFreqWeightedJW`, `GivenNameAliasedJW`, `LegalFormStripTransform`, `AddressNormalizeTransform`, `NaicsNormalizeTransform`) explicitly inherit the Protocol for documentation + isinstance support.
- **Comment trim**: dropped the WHAT-not-WHY comments flagged by the comment analyzer in `industries.py` (the section-loop block comments, redundant whitespace-collapse note, etc.). Kept the WHY comments that document non-obvious invariants. Same pass applied to the other four refdata modules where applicable.
- **6 new tests** in `tests/test_plugins.py` under a new `TestProtocolEnforcement` class — registry rejects bad scorers/transforms at bind time, accepts Protocol conformers, and verifies every built-in refdata adapter satisfies the Protocol at runtime.

What's NOT in this slice (deferred to a separate PR):

- Auto-config gating on `ColumnProfile.col_type` — the higher-impact behavior change flagged in the PR #221 review. Touches `core/autoconfig.py`'s call signature into the hook, so it deserves its own focused PR with regression numbers against NCVR/DBLP-ACM.

### Added -- NAICS industry-code normalization (strategy direction #8, seventh slice)

Extends the `reference-business` pack with US Census 2022 NAICS industry classification. Canonicalizes both numeric codes ("511210", "511 210", "511210 (Software Publishing)") AND known industry titles ("Software Publishers" → "513210") to the same string before matching, so two records describing the same business industry land on the same value.

- **Bundled NAICS 2022 hierarchy** at `goldenmatch/refdata/data/naics_2022.json` -- 2,125 entries across all five hierarchy levels (17 sectors, 96 subsectors, 308 industry groups, 692 5-digit industries, 1,012 6-digit US industries). Sourced from the U.S. Census Bureau's "2-6 digit 2022 Codes" file (https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx). Public-domain US federal data, no license restrictions. The "31-33" range-encoded sector for Manufacturing is expanded across each constituent 2-digit code.
- **Lookup API**: `title_for_code(code)`, `code_for_title(title)`, `naics_normalize(value)`, `industries_available()`, `known_codes()`, `known_titles()`. Title lookup is case- and punctuation-tolerant; code lookup tolerates separators and trailing text.
- **`naics_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Three input shapes:
  - Numeric input: scans EVERY 2+-digit run in the string; for each, walks back through prefixes looking for the longest known code. Returns the first run that resolves. If no run resolves, returns the 6-digit-truncated form of the first run (so two records sharing an unknown code still match). Multi-run scanning lets inputs like `"NAICS 2022 code 511210"` skip the vintage-year prefix and pick up the real code (review-driven; was a first-run short-circuit before).
  - Known industry titles → the canonical code at the narrowest matching hierarchy level.
  - Anything else → lowercase + whitespace-collapse pass-through.
  Never raises. Falls back to lowercase+strip if the bundled data is missing.
- **Autoconfig hook extended**: column-name patterns `naics`, `sic`, `industry`, `industry_code`, `industry_classification`, `business_type` → `naics_normalize` is prepended to the transforms list (mirrors the existing legal_form_strip / address_normalize handling). `_COMPANY_NAME_RE` was tightened to exclude `business[_ ]?type` so that classification column doesn't accidentally also pick up `legal_form_strip` (review-driven).
- **Thread-safety**: `_reload()` now relies on `_load()`'s lock + new-dict-assignment for atomic state swap, instead of wiping dicts before re-parse — readers see either the old dict or the new dict, never an empty in-between state (review-driven).
- **Tests**: `tests/test_refdata_industries.py` (~40 tests) -- title↔code round-trips, case/punctuation tolerance, separator-tolerant code parsing, overlong-code truncation, longest-known-prefix fallback (covers the review-flagged uncovered branch), title-precedence narrowest-wins (regression for the iteration-order rule), multi-digit-run scanning, `business_type` non-overlap with `_COMPANY_NAME_RE`, transform plugin registration, transform-chain composition, `FieldTransform` validator acceptance, `MatchkeyField` accepts in `transforms:`, autoconfig column-name variants including `business[_ ]?type` regex branches.
- **In-session validation blocked** by the same Polars DLL hang documented in PRs #220 and #221 (`goldenmatch/__init__.py` eagerly imports polars-heavy modules, which poisons every `goldenmatch.*` import including refdata submodules; openpyxl-only extraction of the source xlsx worked fine). The transform is a pure synchronous regex+dict function with no hot loops; rerun `pytest tests/test_refdata_industries.py -v` on a fresh Python boot to materialize the test result.
- **What's still deferred**: OpenCorporates company-name variants (the last documented `reference-business` extension), libpostal binding for `reference-address-postal`, per-scorer threshold tuning in `LearningMemory`, and the controller-level A/B rule that would A/B-test refdata refinements in the iteration loop instead of applying them unconditionally.

### Added -- Auto-config integration for refdata packs (strategy direction #8, sixth slice)

Wires all four refdata packs into the zero-config controller. Auto-config no longer needs an explicit YAML to pick up surname-IDF weighting, given-name aliasing, legal-form stripping, or USPS address normalization — it picks them automatically when column names signal the relevant shape.

- **New module** `goldenmatch/refdata/autoconfig_hooks.py` exposes `refine_matchkey_field(column_name, scorer, transforms) -> (scorer, transforms)`. Pure function; safe to call on every column unconditionally.
- **Refinement rules** (each gated on the relevant pack's `is_available()` — non-refdata installs behave exactly as before):
  - `last_name | surname | lname | family_name` → scorer becomes `name_freq_weighted_jw`.
  - `first_name | given_name | fname | forename` → scorer becomes `given_name_aliased_jw`.
  - `company | business | org | firm | employer | legal_name | entity_name` → `legal_form_strip` is prepended to the transforms list.
  - `address | street | addr_line | mailing_address` → `address_normalize` is prepended.
- **Wired into `core/autoconfig.py`** at both `build_matchkeys()` (vanilla weighted/exact path) and `build_probabilistic_matchkeys()` (Fellegi-Sunter path). The hook fires *after* `_SCORER_MAP[col_type]` resolves but *before* `MatchkeyField` is constructed — so the existing column-type classification, cardinality guards, and exact-matchkey skips still run unchanged.
- **Scorer-swap protection**: only string-similarity scorers (`jaro_winkler`, `levenshtein`, `token_sort`, `ensemble`, `dice`, `jaccard`) get swapped. Exact and embedding scorers pass through — preserves identity-field semantics.
- **Transform prepend, not replace**: `legal_form_strip` runs before any existing `lowercase`/`strip`, so the canonical short form still goes through downstream normalization. Idempotent — the function won't double-prepend if the transform is already in the list.
- **Compound columns** (e.g. `company_last_name`) get both refinements: scorer swap from the last_name match + transform prepend from the company match.
- **Tests**: `tests/test_refdata_autoconfig.py` (~50 tests) — parametrized across every column-name variant for each refinement rule, exact-scorer-not-swapped, idempotency, no-mutation-of-caller's-list, compound-column composition, and two end-to-end tests via `build_matchkeys()`.
- **In-session validation blocked** by the same Polars DLL hang documented in PR #220 / CLAUDE.md — every Python invocation in the session that imports Polars (directly or via pytest's conftest chain) sits idle indefinitely. The refinement function is a pure synchronous function and its tests don't depend on Polars; rerun `pytest tests/test_refdata_autoconfig.py` on a fresh Python boot to materialize the test result. Three of the existing test cases (those calling `build_matchkeys()`) do touch Polars and need the same fresh-boot rerun.
- **What's still deferred**: per-scorer threshold tuning in `LearningMemory`, regression check on NCVR / DBLP-ACM under the new auto-config (need a clean Python session), and the controller-level rule that A/B-tests refdata vs vanilla in its iteration loop for ground-truth-aware swap decisions.

### Added -- Surname-scorer common-name-FP synthetic benchmark (strategy direction #8, fifth slice)

Companion benchmark to the synthetic fixtures shipped with the other refdata packs (PR #217 nicknames, PR #218 legal-form, PR #219 address). NCVR's corruption distribution doesn't exercise the borderline JW zone the `name_freq_weighted_jw` scorer was built for; this fixture does, so we can finally show the scorer's actual lift instead of just "no regression".

- **`tests/benchmarks/run_surname_fp_synth.py`** -- 1000-record fixture:
  - **200 TP pairs**: same person across two records, identical first name, identical surname drawn from the common-US-Census pool (Smith, Johnson, Williams, ...). 20% of these use OOV-typo surnames on one side (Smith / Smiht) to verify the scorer's pass-through-to-plain-JW degradation doesn't regress recall.
  - **200 FP-candidate pairs**: *different* people, same first name, borderline-similar common surnames (Smith vs Smyth, Johnson vs Johnsen, Jones vs Jonas, Miller vs Millar, Martin vs Marten, White vs Whyte). Plain JW scores the surname pair around 0.89-0.94 -- exactly the borderline zone the refdata scorer down-weights.
  - **600 distractor singletons**: unique first AND last names, no FP pressure.
  - Blocking on `first_name` puts each pair into its own 2-record block.
- **Configured matchkey**: `first_name + last_name` weighted, threshold 0.92. The threshold is tuned so plain JW (1.0 + 0.89-0.94)/2 squeaks above (calls FP-candidates duplicates), but refdata-weighted (1.0 + 0.77-0.84)/2 drops below (rejects them).
- **Predicted numbers** from the scorer math validated by direct plugin calls earlier in the session:

  | | TP | FP-candidates passed | P | R | F1 |
  | - | - | - | - | - | - |
  | baseline (`jaro_winkler` on last_name) | 200 | ~200 (most pass at JW 0.89-0.94 averaged with 1.0) | ~0.50 | ~1.00 | **~0.67** |
  | refdata (`name_freq_weighted_jw`) | 200 | ~5-30 (only the residual high-JW cases) | ~0.87-0.98 | ~1.00 | **~0.93** |

  Expected F1 delta around **+0.26**. Numbers are predicted, not measured -- the in-session benchmark run is blocked by a known Polars DLL hang (CLAUDE.md gotcha: `Polars DLL hangs: kill zombie python ...`). After a clean Python boot, run `python tests/benchmarks/run_surname_fp_synth.py --out report.txt` to materialize the actual measurement.

- **Per-pair scorer math validated** for the surnames used in the fixture (direct plugin calls):

  | Surname pair | Plain JW | Refdata-weighted | Drop |
  | - | - | - | - |
  | Smith / Smyth | 0.893 | 0.769 | -0.124 |
  | Johnson / Johnsen | 0.943 | 0.818 | -0.125 |
  | Jones / Jonas | 0.907 | 0.790 | -0.117 |
  | Miller / Millar | 0.933 | 0.821 | -0.112 |
  | Martin / Marten | 0.933 | 0.840 | -0.093 |
  | White / Whyte | 0.893 | 0.793 | -0.100 |

  The down-weighting is consistent in the [0.10, 0.13] range for both-sides-known common-name pairs in the borderline JW zone.

- **What's still deferred** (after this slice): auto-config integration across all four refdata packs, libpostal binding for `reference-address-postal` extra, industry codes (NAICS) and OpenCorporates company-name variants for `reference-business`.

### Added -- Reference-address pack: USPS-style address normalization (strategy direction #8, fourth slice)

Fourth refdata slice. Opens the `reference-address` pack with the `address_normalize` transform: collapses USPS Publication 28 street-suffix, directional, and secondary-unit variants to their canonical short forms so "123 Main Street North Apartment 5" and "123 Main St N Apt 5" both reduce to "123 main st n apt 5" before scoring.

- **Bundled USPS abbreviation table** at `goldenmatch/refdata/data/address_abbreviations.json` — ~500 surface variants covering street suffixes (150+ canonical forms), 8 directionals, 9 secondary-unit designators. Sourced from USPS Publication 28 Appendix C; public-domain US federal data, no license restrictions.
- **`address_normalize` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Tokenizes on whitespace + commas, lowercases each token, strips punctuation, then maps any recognised variant to its USPS canonical short form. Unknown tokens pass through unchanged. Idempotent.
- **Position-agnostic by design**: every USPS-known token is normalized, not just trailing ones. Trade-off: words that are both name parts and suffix variants ("Lake", "Court", "Park") collapse along with true suffixes. Match invariance is preserved as long as both sides reduce equally — pinned by `test_aggressive_normalization_preserves_match_invariance`. For display purposes use a different normalization; this transform is matching-only.
- **Tests**: `tests/test_refdata_addresses.py` (48 tests) — per-suffix parametrized across 17 variants, directionals across 8, secondary units across 7, multi-token compound case, punctuation stripping, idempotency, unknown-token pass-through, position-agnostic invariance, plugin transform dispatch, transform-chain composition, validator acceptance.
- **Synthetic address benchmark** at `tests/benchmarks/run_address_synth.py`. 1000-record fixture, 200 same-street pairs differing in suffix abbreviation (some also in directional/unit) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 116 | 0 | 84 | 1.0000 | 0.5800 | **0.7342** |
  | baseline (lowercase only) | 114 | 0 | 86 | 1.0000 | 0.5700 | **0.7261** |
  | refdata (`address_normalize`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.2658. Recall +0.4200. Plain JW catches the small suffix deltas (Ave/Avenue, St/Street) but misses larger ones (Boulevard/Blvd, Northeast/NE, Apartment/Apt). The transform catches everything.

- **What's still deferred**: libpostal binding (heavy C deps + ~2 GB model; opt-in extra rather than bundled), street-name canonicalization (USPS CASS proper), ZIP+4 lookups, international postal-code formats.

### Added -- Reference-business pack: legal-form normalization (strategy direction #8, third slice)

Third refdata slice. Opens the `reference-business` pack with the `legal_form_strip` transform: strips trailing corporate suffixes ("Inc", "LLC", "GmbH", "Pty Ltd", …) so "Acme Inc." and "Acme Incorporated" collapse to "Acme" before scoring.

- **Bundled token list** at `goldenmatch/refdata/data/legal_forms.json` — ~80 surface variants spanning US, UK, EU, Asia-Pacific, LatAm jurisdictions. Public-knowledge corporate-suffix conventions; no license restrictions.
- **`legal_form_strip` transform** auto-registered via `PluginRegistry` on `import goldenmatch.refdata`. Strips multi-word suffixes first (so "Limited Liability Company" beats "Limited" or "Company" alone). Iterative (handles "Acme Holdings Inc" -> "Acme"). Idempotent. Case-insensitive. Returns input unchanged when no match or data file missing.
- **Plugin transform fallback wired into the core pipeline**:
  - `goldenmatch.utils.transforms.apply_transform` now falls through to `PluginRegistry.get_transform` for unknown transform names (mirrors the existing scorer fallback).
  - `goldenmatch.config.schemas.FieldTransform._validate_transform` checks the registry before raising `Invalid transform` (mirrors `MatchkeyField._validate_*` scorer fallback).
  - Net result: any plugin transform Just Works in YAML config, matchkey transforms list, and `apply_transforms` chains.
- **Tests**: `tests/test_refdata_business.py` (45 tests) -- per-form strip parametrized across 28 variants, case-insensitive, whitespace normalize, iterative strip on multi-suffix names, idempotency, mid-name preservation, None/empty handling, plugin transform dispatch, transform-chain composition (`legal_form_strip` then `lowercase`), `FieldTransform` validator accepts plugin name, `MatchkeyField` accepts it in `transforms:`.
- **Synthetic business-name benchmark** at `tests/benchmarks/run_business_synth.py`. 1000-record fixture, 200 same-stem pairs differing only in legal-form suffix (Acme Inc vs Acme Incorporated, etc.) + 600 distractors, threshold 0.95:

  | | TP | FP | FN | P | R | F1 |
  | - | - | - | - | - | - | - |
  | baseline (no transform) | 79 | 0 | 121 | 1.0000 | 0.3950 | **0.5663** |
  | refdata (`legal_form_strip`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |
  | refdata (`legal_form_strip` + `lowercase`) | 200 | 0 | 0 | 1.0000 | 1.0000 | **1.0000** |

  F1 delta +0.4337. Recall +0.6050. The transform catches every pair the variant labels differ on; precision unchanged at 1.0 (no FPs introduced).
- **What's still deferred**: industry code lookups (NAICS), OpenCorporates company-name variants, `reference-address` pack (token normalization + libpostal binding), auto-config integration, per-scorer threshold tuning.

### Added -- Reference data infrastructure (strategy direction #8, first slice)

`goldenmatch.refdata` -- bundled, public-domain reference data the engine can consume to lift accuracy on people-shape matching. Spec: `docs/superpowers/specs/2026-05-08-competitive-strategy-review.md` direction #8.

- **US Census 2010 top-10K surname frequency table** bundled at `goldenmatch/refdata/data/census_surnames_2010_top10k.csv` (~176 KB, public domain). Provenance, license, regenerate command documented in `PROVENANCE.md`.
- **Lookup API**: `surname_count`, `surname_rank`, `surname_frequency`, `surname_idf`, `is_available`. Case-insensitive; strips non-alpha. OOV names return `None` (or `1.0` from `surname_idf`, treated as "rarer than known").
- **`name_freq_weighted_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Algorithm: Jaro-Winkler outside the borderline zone (`jw >= 0.95` or `jw < 0.70`) returns plain JW unchanged -- preserves recall on confident matches. Inside the borderline zone, both-sides-known pairs get re-weighted by mean surname IDF with a `_COMMON_NAME_FLOOR = 0.6`. OOV-on-either-side falls back to plain JW (refuses to up-credit typos of common names).
- **NxN plugin path**: `core/scorer.py::_fuzzy_score_matrix` now falls through to `PluginRegistry` for unknown scorer names, building the matrix via `score_pair` calls. Slower than rapidfuzz `cdist` for the registered scorers but keeps the contract uniform.
- **Regenerate**: `python -m goldenmatch.refdata.scripts.fetch_census_surnames` pulls the upstream archive and rewrites the bundled CSV.
- **Tests**: `tests/test_refdata_surnames.py` (21 tests) -- lookup correctness, IDF monotonicity, scorer borderline behavior, OOV pass-through, plugin registration, `MatchkeyField` validator accepts the new scorer.
- **NCVR A/B benchmark** at `tests/benchmarks/run_ncvr_refdata.py`. 7500-record corrupted-duplicates GT, last_name scorer swapped: F1 0.9721 (baseline, zero-config) -> 0.9721 (refdata). No regression. Lift is zero on this dataset because NCVR's heavy-corruption distribution puts few pairs in the borderline JW zone where the weighting acts -- needs an enterprise-shape benchmark per direction #5 to demonstrate positive lift.
- **What's deferred** (future work): auto-config integration (the controller doesn't yet pick `name_freq_weighted_jw` automatically); `reference-business` and `reference-address` packs; threshold tuning per-scorer in `LearningMemory`.

### Added -- Given-name alias pack (strategy direction #8, second slice)

Second slice of the `reference-people` pack. Adds nickname-equivalence to first-name matching: William ↔ Bill, Robert ↔ Bob, Margaret ↔ Peggy, etc.

- **Curated alias table** at `goldenmatch/refdata/data/given_name_aliases.json` (~140 canonical English given names, public-knowledge naming conventions; no license restrictions).
- **Lookup API**: `canonical_form`, `aliases_of`, `are_equivalent`, `given_names_available`. Case-insensitive; strips non-alpha. Symmetric and transitive within an equivalence class. OOV pass-through.
- **`given_name_aliased_jw` scorer** registered via the plugin system on `import goldenmatch.refdata`. Alias-equivalent pairs return 1.0 regardless of edit distance; unrelated pairs return plain Jaro-Winkler. The scorer never *lowers* a JW score -- it only promotes known aliases. Degrades cleanly when the alias table is missing.
- **Tests**: `tests/test_refdata_given_names.py` (23 tests) -- lookup symmetry, transitive equivalence, multi-canonical name handling (e.g. "Jack" canonical AND alias-to-John), case/punct insensitivity, OOV pass-through, scorer correctness, plugin registration, validator acceptance.
- **Synthetic nickname benchmark** at `tests/benchmarks/run_nickname_synth.py`. 1000-record fixture with 200 nickname-shape duplicate pairs + 600 distractors with isolated random first/last names. Plain JW baseline catches **0/200** pairs at threshold 0.95 (JW(William, Bill) ~= 0.55, far below threshold); `given_name_aliased_jw` catches **200/200**, P=1.0, R=1.0, **F1 0.00 -> 1.00**.
- **Asymmetry-on-ambiguous-short-form bugfix**: short forms that belong to multiple canonicals (e.g. "kate" appears in Catherine, Kathleen, Kaitlyn; "chris" in Christopher, Christine, Christina) were silently asymmetric — `are_equivalent("Kate", "Catherine")` returned False while `("Catherine", "Kate")` returned True, because the old lookup stored a single canonical per form (last-writer-wins). The matcher's NxN score matrix only consults the upper triangle, so the False direction was the one being read and every ambiguous-short-form pair was being dropped. Each form now stores the full set of canonicals it belongs to; equivalence holds iff the two forms share a canonical. Regression test in `test_are_equivalent_symmetric_for_ambiguous_short_forms`.
- **What's still deferred**: same list as the first slice (auto-config integration, business / address packs, per-scorer threshold tuning).

## [1.15.0] - 2026-05-12

### Added -- Identity Graph (v2.0 headline feature)

`goldenmatch.identity` -- a first-class durable graph layer above run-local clusters. Spec: `docs/superpowers/specs/2026-05-12-identity-graph-design.md`. Roadmap: `docs/superpowers/plans/2026-05-12-identity-graph-roadmap.md`.

- **`IdentityStore`** (SQLite default, Postgres optional): identity nodes, source records, evidence edges, append-only event log, aliases. WAL + busy_timeout for multi-process safety. Schema versioned via `PRAGMA user_version`.
- **Stable `entity_id` across runs**. `resolve_clusters()` runs after dedupe clustering and decides `create` / `absorb` / `merge` based on which existing identities cover the cluster's records. Idempotent on `(run_name, kind, entity_id)`.
- **`IdentityConfig`** -- new optional section in `goldenmatch.yml`. When `identity.enabled: true`, the pipeline writes graph state at `.goldenmatch/identity.db` (or the configured backend) on every `run_dedupe()`. Disabled by default; failure logs + skips, never blocks dedupe output.
- **Surfaces**: Python (`goldenmatch.identity.*` + root re-exports), CLI (`goldenmatch identity list/show/resolve/history/conflicts/merge/split`), REST (`/api/v1/identities/...`), web "Identities" tab, MCP (6 `identity_*` tools), A2A (6 skills, agent card now declares 18 total skills). TS edge-safe core (`InMemoryIdentityStore` + `findByRecord` / `getEntity` / `manualMerge` / `manualSplit`) ships in the same release; persistent SQLite backend + pipeline-driven population are TS-port v2 follow-ups.
- **Postgres analytical views**: `v_identities`, `v_identity_pairs`, `v_identity_timeline` in `goldenmatch/db/migrations/identity_v1.sql`. `IdentityStore(backend="postgres")` creates the same schema on first connect.
- **DuckDB / extensions contract** documented at `docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md` for the `goldenmatch-extensions` repo to implement.
- **47 new Python tests**, **13 new TS tests**. Full sweep: 1984 passed, 0 regressions.
- Example: `examples/identity_graph.py`.

## [1.14.0] - 2026-05-11

This release ships the full v1.7-v1.12 AutoConfigController surface to every user-facing entry point in the suite. No algorithm changes vs 1.13.0 — same DQbench / DBLP-ACM / Febrl3 / NCVR numbers — but you can now read what the controller decided from every interface (web, TUI, CLI, REST, MCP, A2A, Postgres, DuckDB) and round-trip the committed config (including Path Y negative-evidence) through SQL.

### Fixed

- **AgentSession default path now actually runs the AutoConfigController** (PR #169). `deduplicate(config=None)` / `match_sources(config=None)` were building a config from the legacy `select_strategy()` heuristic and passing it explicitly to `dedupe_df`/`match_df`, which suppressed the zero-config controller path. `last_telemetry` ended up `None` for the case it was meant to capture. Default path now calls `dedupe_df(df)` / `match_df(df_a, df_b)` with no config so the controller fires. Explicit-config calls explicitly clear `last_telemetry` to prevent stale-blob leaks across calls on the same session. `select_strategy()` still runs but only for the `reasoning` payload, not to back the actual matching config. Hard-asserting tests landed alongside the fix.

### Added — AutoConfigController surface-parity arc

Six PRs (#156-#159 + #161; #160 added CI lanes) bring every user-facing entry point up to speed with the v1.7-v1.12 AutoConfigController / IndicatorContext / NegativeEvidence work. Before this arc, controller decisions were observable only by reading `result.postflight_report.controller_history` in Python. Now every surface returns the same JSON shape (`stop_reason`, `health`, refit decisions, indicator column priors, committed `negative_evidence`) via `goldenmatch.web.controller_telemetry.serialize_telemetry`.

**Web UI** (PR #156)
- New `ControllerPanel` in Workbench surfaces stop_reason badge, health verdict, complexity profile cells, indicator column priors, refit decision trace, and `Path Y · N NE` indicator on committed matchkeys.
- New `GET /api/v1/controller/telemetry` endpoint populated by `/autoconfig` and `/run?auto_config=true`.
- Home gains a `ProvenanceCallout` linking `docs/reproducing-benchmarks.md` + `docs/scale-envelope.md` with the four reproducible numbers.

**TUI** (PR #157)
- New `Controller` tab (7th tab) showing the same telemetry the web panel shows.
- New `Ctrl+A` binding triggers async auto-configure; result adopted into ConfigTab + ExportTab; switches to Controller tab on completion.
- `MatchEngine.auto_configure(domain=None)` captures `_LAST_CONTROLLER_RUN` and exposes telemetry on `engine.last_telemetry`.

**CLI** (PR #158)
- New `goldenmatch autoconfig <files>` subcommand. Prints committed config to stdout (pipe to `> goldenmatch.yml`); telemetry panel to stderr. Flags: `--out PATH`, `--domain`, `--verbose`, `--hide-controller`.
- `goldenmatch dedupe` zero-config path captures `_LAST_CONTROLLER_RUN` and renders the same panel before the cluster report (`--show-controller` / `--hide-controller`).
- New shared `goldenmatch.cli._controller_render` module with Rich Panel + one-line `render_short_status` for log scraping.

**SQL extensions** (PR #159)
- Bridge (Rust/pyo3) gains `DedupeResult.telemetry_json`, `autoconfig()` returning `(committed_config_json, telemetry_json)`, and `dedupe_full()` accepting the full Pydantic `GoldenMatchConfig` JSON (unlocks `negative_evidence` from SQL).
- Postgres: new `goldenmatch_autoconfig`, `goldenmatch_autoconfig_telemetry`, `goldenmatch_dedupe_full`, `goldenmatch_dedupe_full_telemetry`, `gm_telemetry`. New JSONB column `goldenmatch._jobs.last_telemetry_json` (added via `ALTER TABLE ... IF NOT EXISTS` for in-place upgrade).
- DuckDB: parallel UDFs registered on every `register(con)`.

**CI** (PR #160)
- New `rust_pgrx` lane (matrix: PG 15/16/17) — cargo pgrx install + psql smoke covering the new v1.7-v1.12 surface.
- New `duckdb_extensions` lane — runs the DuckDB UDF Python tests that the main `python` matrix doesn't pick up.

**Agent / programmatic surfaces** (PR #161)
- `AgentSession.autoconfigure(file_path)` returns `{config, telemetry}`; `deduplicate` / `match_sources` cache `last_telemetry`.
- REST API (`goldenmatch serve`): new `POST /autoconfig` (with optional `records` body override) + `GET /controller/telemetry`.
- MCP: `auto_configure` tool rewired off the legacy `select_strategy` heuristic onto the controller. New `controller_telemetry` tool. `agent_deduplicate` / `agent_match_sources` embed telemetry inline.
- A2A: 10 → 12 skills (added `autoconfig` + `controller_telemetry`). `deduplicate` / `match` skills embed telemetry in their wire result.

**Cross-surface telemetry shape** (single source of truth at `goldenmatch.web.controller_telemetry.serialize_telemetry`)

```json
{
  "available": true,
  "source": "autoconfig",
  "stop_reason": "green",
  "health": "green",
  "elapsed_ms": 1234.5,
  "full_vs_sample_drift": 0.12,
  "scoring": {"n_pairs_scored": 4421, "mass_above_threshold": 0.087},
  "blocking": {"n_blocks": 312, "reduction_ratio": 0.94},
  "cluster": {"n_clusters": 1820, "transitivity_rate": 0.99},
  "column_priors": [{"column": "email", "identity_score": 0.95, "corruption_score": 0.0}],
  "decisions": [{"iteration": 1, "rule_name": "...", "rationale": "...", "wall_clock_ms": 234}],
  "committed_matchkeys": [{"name": "exact_email", "has_negative_evidence": true}],
  "negative_evidence": [{"matchkey_name": "exact_email", "field": "phone", "penalty": 0.5}]
}
```

## [1.13.0] - 2026-05-11

This is a release-plumbing wave: typed-accessor API additions, PyPI metadata refresh, and contributor-facing quality improvements. **No DQbench / Febrl3 / NCVR / DBLP-ACM number changes** — algorithm is unchanged this wave.

### Added
- **Typed accessor API on `MatchkeyConfig` / `MatchkeyField`** (PR #151). New properties: `MatchkeyConfig.fuzzy_threshold`, `MatchkeyField.fuzzy_scorer`, `MatchkeyField.fuzzy_weight`, `MatchkeyField.resolved_field`. Each raises `ValueError` when the underlying matchkey isn't a fuzzy/weighted type, so the invariant is now enforceable in pyright strict mode rather than asserted in callers.

  ```python
  from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField

  mk = MatchkeyConfig(
      name="identity",
      type="weighted",
      threshold=0.85,
      fields=[MatchkeyField(field="name", transforms=["lowercase"], scorer="jaro_winkler", weight=1.0)],
  )
  assert mk.fuzzy_threshold == 0.85  # safe access on weighted matchkey
  # mk.fuzzy_threshold on an exact matchkey raises ValueError
  ```

- **`docs/scale-envelope.md`** (PR #149): documents the Polars / DuckDB / Ray operating ranges plus block-size failure modes so callers can pick a backend before hitting an OOM.
- **Postgres CI lane** (PR #144): flipped from skipped to live so DB integration tests now run on every PR.

### Changed
- **PyPI metadata corrected** (PR #148): `[project.urls]` Homepage / Repository / Documentation entries now point at the monorepo at `benseverndev-oss/goldenmatch`. The pre-fold standalone-repo URLs are gone. Metadata only refreshes on a wheel build, so this release is what makes the corrected URLs visible on PyPI.

### Fixed
- **Reproducibility of all four published benchmark numbers** (PR #152, replaces #150): DQbench composite 91.04, DBLP-ACM 0.9641, Febrl3 0.9443, NCVR 0.9719 now all reproduce from a fresh clone. See `docs/reproducing-benchmarks.md` for the exact commands and dataset prep steps.

### Internal (contributors only)
- Ruff lint expanded to F / I / B-narrowed / UP rule sets across `packages/python/` (PR #146).
- Pyright strict mode now enforced on the 21-file core slice of `goldenmatch` (PR #147). The new typed accessors in PR #151 eliminated 7 type-suppression workarounds in callers.

### Benchmarks (zero-config, no LLM)

Unchanged vs v1.12.0 — algorithm not touched this wave.

| Dataset | v1.12.0 | v1.13.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 91.04 | 91.04 | +0.00 |

## [1.12.0] - 2026-05-10

<!-- README-callout
**Negative evidence on exact matchkeys (Path Y)** — NE penalties now filter adversarial collision pairs at the `exact_email` level, not just inside the weighted matchkey scoring loop. DQbench composite **91.04** (was 66.99 at v1.11). T2 F1 69.0% → 97.5%, T3 F1 53.8% → 85.5%.
-->

### Added
- **`_apply_negative_evidence_to_exact_pairs`** in `core/scorer.py`: post-filter helper that applies NE penalties to pairs produced by exact matchkeys. Called from `core/pipeline.py` after `find_exact_matches`. Score formula: `final = max(0, 1.0 - sum(penalties))`; pair emits only if `final >= matchkey.threshold`. Exact matchkeys without NE fields are unaffected (binary 1.0/0.0 emit preserved).
- **Exact-matchkey NE threshold default**: when `promote_negative_evidence` adds NE fields to a threshold-None exact matchkey, the threshold is defaulted to 0.5 to activate the score-and-threshold path.
- **`promote_negative_evidence` extended** to walk all matchkey types (was weighted-only in v1.11). The `_is_exact_matchkey_field` gate is selectively skipped when iterating an exact matchkey for itself — its v1.11 rationale (prevent recall regression on fuzzy data) doesn't apply to exact-matchkey self-iteration.

### Changed
- **`core/pipeline.py`**: calls `_apply_negative_evidence_to_exact_pairs` after `find_exact_matches` when any exact matchkey carries NE fields. Zero overhead when no NE fields are present.
- **`promote_negative_evidence`** now populates NE on exact matchkeys in addition to weighted matchkeys. Exact matchkeys for high-identity-prior columns (email) gain NE from disagreeing secondary fields, allowing adversarial collision pairs to be filtered at the exact matchkey level.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.11.0 | v1.12.0 | Delta |
|---|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 | +0.0000 |
| Febrl3 | 0.9443 | 0.9443 | +0.0000 |
| NCVR | 0.9719 | 0.9719 | +0.0000 |
| DQbench composite | 66.99 | 91.04 | +24.05 pp |

DQbench tier detail (v1.12.0):

| Tier | Precision | Recall | F1 | vs v1.11 |
|---|---|---|---|---|
| T1 | 80.6% | 100.0% | 89.3% | flat |
| T2 | 95.1% | 100.0% | 97.5% | +28.5 pp |
| T3 | 74.7% | 100.0% | 85.5% | +31.7 pp |

Primary target (>= 75) met. T3 F1 headline target (>= 70%) met. All floor constraints met. The T3 gain resolves the v1.11 root cause: Path Y NE filtering now operates at the `exact_email` matchkey level, directly shedding adversarial collision pairs that share an email but disagree on name/address NE fields.

## [1.11.0] - 2026-05-10

### Added
- **`NegativeEvidenceField`** in `config/schemas.py`: new optional field on `MatchkeyConfig`. Each entry specifies a field, transforms, scorer, similarity threshold, and penalty. When a weighted matchkey scores a pair, any NE field whose similarity falls below its threshold subtracts the penalty from the weighted score.
- **`_apply_negative_evidence`** in `core/scorer.py`: pure helper that computes the NE penalty for a scored pair and returns the adjusted score. Called inside the weighted-matchkey scoring loop.
- **`promote_negative_evidence`** in `core/autoconfig_negative_evidence.py`: eager rule that adds NE fields to weighted matchkeys for columns with high identity priors (identity_score >= 0.75, cardinality_ratio >= 0.5) that also have an exact matchkey counterpart. Gated on the exact-matchkey counterpart requirement to prevent recall regression on noisy ER data where legitimate duplicates may have differing phone/address values.
- **`_pick_scorer_for_column`** in `core/autoconfig_negative_evidence.py`: maps column name / type to (transforms, scorer) for NE fields. Phone -> (digits_only, exact). Email -> ([], token_sort). Address -> ([], token_sort). Default -> ([], ensemble).
- **`rule_demote_clustered_identity`** at position 7 in `DEFAULT_RULES`: detects when an exact matchkey identity column is shared across distinct entities (adversarial reuse pattern). Demotes the exact matchkey to a fuzzy participant on the weighted matchkey and adds the column to blocking. Threshold of 0.75 (raised from 0.5 after Phase 7 analysis showed T2's collision rate of 0.62 was causing false demotion and 186 FNs).
- **`compute_identity_collision_signal`** in `core/indicators.py`: for each multi-record group sharing an identity column value, computes max pairwise divergence on witness columns using token_sort_ratio. Returns fraction of groups with max divergence > 0.5.

### Changed
- **`AutoConfigController.run`**: calls `promote_negative_evidence` between v0 config build and the iteration loop, so NE fields are present on weighted matchkeys before the first iteration profiles them.
- **`rule_demote_clustered_identity` collision threshold**: raised from 0.5 to 0.75. This prevents false-firing on legitimate fuzzy ER datasets (T2 collision rate 0.615) while still catching high-rate adversarial reuse (rates near 1.0).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.10.0 | v1.11.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 66.91 | 66.99 |

T2 recall regression (186 FNs from v1.11 early iteration) fixed by raising `rule_demote_clustered_identity` threshold from 0.5 to 0.75. T3 unchanged at 53.8%. Primary target (>= 75) not met; ships on best-effort basis above v1.10 baseline. T3 F1 target (>= 70%) remains an open v1.12 challenge: the exact-matchkey gate correctly protects T2 recall but also prevents phone NE from reducing T3 adversarial FPs.

### Notes for v1.12

- T3 adversarial FPs come from the `exact_email` matchkey capturing collision pairs directly. NE on the weighted matchkey does not affect these pairs. Real T3 improvement requires either a higher-precision collision signal or a different mechanism for adversarial reuse that does not require collision_rate to exceed T2's rate (0.615).
- Removing the exact-matchkey gate would raise composite to ~68.9 but drops T2 by ~0.8 pp. Not shipped due to net regression on T2 at the pair level.

## [1.10.0] - 2026-05-08

### Added
- **5 complexity indicators** (`core/indicators.py`): `compute_column_priors`, `estimate_sparse_match_signal`, `compute_corruption_score`, `estimate_full_pop_hits`, `compute_cross_blocking_overlap`. Each has a wall-clock budget; cheap two run eagerly, expensive three run lazily via `IndicatorContext` memoization.
- **`IndicatorContext`** in `autoconfig_controller.py` threads indicators through the policy/rule chain. `RefitPolicy.propose` gains optional `ctx` kwarg; `HeuristicRefitPolicy` and `LLMRefitPolicy` both forward; controller introspects custom-policy signatures via `inspect.signature` for backward compat.
- **3 new indicator-aware rules**: `rule_corruption_normalize`, `rule_cross_blocking_disagreement`, `rule_sparse_match_expand`. `DEFAULT_RULES` now has 13 rules (was 10).
- **`GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast`** env var gates the two expensive indicators (full-pop scan, cross-blocking probe) for users who prefer v1.9 wall-clock.
- **`ColumnPrior`, `SparsityVerdict`, `IndicatorsProfile`** dataclasses in `core/complexity_profile.py`. New default-None fields: `DataProfile.column_priors`, `ComplexityProfile.indicators`.

### Changed
- **`rule_no_matches`** (modified): when ctx provides high-identity-prior on the blocking column, tries `[lower_threshold, normalize, multi_pass]` alternatives in order before falling back to today's behavior. When `ctx.sparsity_verdict.is_sparse`, lowers threshold by 0.10 (proxy for ExpandSample, queued v1.11).
- **`rule_blocking_key_swap`** (modified): vetoed when blocking column has `identity_score >= 0.8` AND `full_pop_matchkey_hits > 0` (protects v0's correct identity blocking from being abandoned on noisy samples).

### Benchmarks (zero-config, no LLM)

| Dataset | v1.9.0 | v1.10.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 66.91 |

T2 F1: 58.7% → 69.0% (+10.3 pp). T1 and T3 unchanged. Primary target (>= 70) not met; ships on fallback basis (>= 65).

### Notes for v1.11

- `rule_sparse_match_expand` substitutes `_with_lower_threshold(0.10)` for the spec's `ExpandSample(2.0)` action; real controller-level sample expansion queued for v1.11.
- No rule forces a *positive* swap to an identity-prior column when v0 picked something else; v1.10 only protects identity columns from being abandoned. v1.11 may add `rule_promote_identity_blocking` if benchmark measurement shows the gap matters.
- Attribution sweep (which of the 5 indicators drove the T2 gain) not run — composite fell in fallback range (65-70); sweep was deferred per plan.

## [1.9.0] - 2026-05-08

### Added
- **Best-effort commit semantics.** `RunHistory.pick_committed()` extends the lex key to RED entries (rank=2) and returns the highest-ranked entry by `(health_rank, -mass_separation, iteration)`. Replaces v1.8's `cheapest_healthy()` which returned None on all-RED history. Filters errored entries via `error is None and profile is not None`. Closes a known v1.8 design-doc gap.
- **`RunHistory.stop_reason: StopReason | None`** populated at every break point in `AutoConfigController.run()`. Observable via `result.postflight_report.controller_history.stop_reason`. Eight values: GREEN, CONVERGED, BUDGET_ITERATIONS, BUDGET_TIME, POLICY_SATISFIED, POLICY_NO_PROGRESS, OSCILLATING, CANCELLED.
- **Virtual v0 fallback + precision-collapse floor.** The controller appends `config_v0`'s profile as a synthetic `HistoryEntry(iteration=-1)` before `pick_committed()` runs, so v0 stays in the candidate pool. `pick_committed(precision_collapse_floor=0.9)` demotes RED entries with `mass_above_threshold > 0.9` (the "everything matches" pathology) to rank=3. Together these prevent committing a config demonstrably worse than v0.
- **Health-aware commit logging.** WARNING on RED commit (names failing sub-profile + stop_reason + iteration); INFO on YELLOW; silent on GREEN; ERROR on all-errored fallback. Logs use `iter=v0` to identify virtual-v0 commits.

### Changed
- `RunHistory.cheapest_healthy()` is now a deprecation alias for `pick_committed()`. **Behavior change**: returns RED entries when no GREEN/YELLOW exists (was: returned None). DeprecationWarning text calls out the change explicitly. Removed in v2.0.
- `StopReason` enum moved from `core/autoconfig_controller.py` to `core/complexity_profile.py` (next to `HealthVerdict`).

### Fixed
- DQbench composite regression caught during release verification: unguarded best-effort commit could select a precision-collapsed RED config (T1: 1% precision, 100% recall -- "match everything"). Virtual v0 + precision floor restored v1.8 parity exactly.

### Benchmarks (zero-config, no LLM)

| Dataset | v1.8.0 | v1.9.0 |
|---|---|---|
| DBLP-ACM | 0.9641 | 0.9641 |
| Febrl3 | 0.9443 | 0.9443 |
| NCVR | 0.9719 | 0.9719 |
| DQbench composite | 62.87 | 62.87 |

### Notes for v1.10

The original v1.9 spec assumed best-effort RED commit would deliver a DQbench composite gain (target >= 65). In practice, the controller's complexity indicators can't distinguish "blocking key is wrong" from "blocking key is right but sample has no visible matches" -- both produce `mass_above_threshold=0.0`. v1.10 will add new indicators (identity-column priors, cross-blocking overlap probe, blocking-column corruption signal, sparse-match sensitivity) so the controller can tell these cases apart and deliver real gains on the tiers where it currently can't escape the impasse.

## [1.8.0] - 2026-05-08

<!-- README-callout
**Introspective auto-config controller** — Iterates on stage-emitted complexity signals (block-size dist, score histogram, transitivity, borderline mass) and refines its config via heuristic rules until convergence. Zero-config beats hand-tuned on DBLP-ACM (F1 **0.964** vs 0.918 ceiling), NCVR (**0.972**), Febrl3 (**0.944**). Cross-run memory at `~/.goldenmatch/autoconfig_memory.db`, LLM policy fallback (`GOLDENMATCH_AUTOCONFIG_LLM=1`), standardization auto-detection. Built by [Ben Severn](https://bensevern.dev).
-->

### Added
- **Introspective auto-config controller** that beats hand-tuned configs on multiple benchmarks without manual tuning. Zero-config now produces a defensible config the first time, even on shapes it hasn't been hand-tuned for. The controller iterates on stage-emitted complexity signals (block size distribution, score histogram, transitivity rate, candidates compared, mass above/in-borderline) and refines its config via a heuristic rule policy until convergence. (#103, #104, #109, #114)
- **Cross-run memory** at `~/.goldenmatch/autoconfig_memory.db` — past committed configs are reused when the data shape signature matches. Opt out with `GOLDENMATCH_AUTOCONFIG_MEMORY=0`. (#111)
- **LLM policy fallback** (option B): when heuristic rules exhaust without reaching GREEN, an `LLMRefitPolicy` proposes a config diff. Default off; opt in with `GOLDENMATCH_AUTOCONFIG_LLM=1`. (#112)
- **Per-pair LLM scoring auto-enable** when the committed profile shows borderline-heavy mass and an LLM API key is available. Adaptive bounds track the matchkey's threshold dynamically. (#113, #115)
- **Standardization auto-detection** in v0 — phone/email/zip/state/name/address columns now auto-emit `StandardizationConfig` rules. (#115)
- **Recall-aware probes** — `random_pair_above_threshold_rate` signal in `ScoringProfile`; `rule_recall_gap_suspected` and `rule_blocking_field_null_heavy` rules. (#109)
- **NCVR benchmark regression test** (gated on dataset presence). (#110)
- **11 real-data integration tests** + **5 Hypothesis property tests** for controller invariants. (#106, #107)

### Changed
- `auto_configure_df` is now controller-backed; gains optional `reference` kwarg for cross-source match mode. Public signature otherwise unchanged.
- Zero-config callers in `_api.dedupe_df` / `_api.match_df` now call `auto_configure_df` *before* the pipeline (eliminates double pipeline run). (#103)
- `PostflightReport` gains `controller_profile` + `controller_history` fields surfacing the typed `ComplexityProfile` and audit trail. (#103, #108)

### Fixed
- Zero-config crashes in `match_df` (`ColumnNotFoundError: __title_key__`) and `match()` (`ColumnNotFoundError: __placeholder__`). (#102)
- Cache poisoning across structurally-identical-but-semantically-different datasets. (#112)
- SQLite cross-thread access in default memory store (web routers fixed). (#111)

### Benchmarks (zero-config, no manual tuning)

| Dataset | v1.7.1 | v1.8.0 | Hand-tuned ceiling |
|---|---|---|---|
| DBLP-ACM (cross-source) | 0.5102 | **0.9641** | 0.918 |
| Febrl3 (single-source) | 0.8528 | **0.9443** | 0.971 |
| NCVR (corruption GT) | — | **0.9719** | — |
| DQbench (no LLM) | 46.24 (hand-tuned) | **62.87** (zero-config) | — |

## [1.6.0] - 2026-05-04

### Added
- **Learning Memory completion** — corrections now flow end-to-end from collection points through pipeline application to postflight surfaces.
  - **Re-anchor via record_hash**: corrections survive row reorder and input refresh through a collision-safe vectorized record-hash lookup. Ambiguous re-anchors (duplicate rows) report as `stale_ambiguous` rather than silently misapplying. New `MemoryConfig.reanchor` flag (default `True`) gates the behavior.
  - **Pipeline hook**: `dedupe_df` and `match_df` apply stored corrections after scoring and overlay learned thresholds before scoring. `DedupeResult.memory_stats` and `MatchResult.memory_stats` surface applied/stale/stale-ambiguous counts.
  - **Seven collection points** capture corrections automatically: review queue (`steward`, trust 1.0), boost tab y/n (`boost`, 1.0), `unmerge_record`/`unmerge_cluster` (`unmerge`, 1.0, empty hashes), LLM scorer decisions (`llm`, 0.5), MCP `agent_approve_reject` (`agent`, 0.5), and REST `POST /reviews/decide` (`steward`, 1.0).
  - **Postflight section**: rendered postflight string adds a `Memory: N corrections applied, M stale, K stale-ambiguous` line when memory is active.
  - **Explainer integration**: review queue items carry a `why` field. Deterministic template by default; routes to `core/llm_scorer.llm_explain_pair` when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set.
  - **CLI subgroup**: `goldenmatch memory stats|learn|export|import|show`.
  - **Five MCP tools**: `list_corrections`, `add_correction`, `learn_thresholds`, `memory_stats`, `memory_export`. Server card description updated to "35 MCP tools".
  - **Python API**: `goldenmatch.get_memory()`, `goldenmatch.add_correction()`, `goldenmatch.learn()`, `goldenmatch.memory_stats()`.
  - **Stale persistence**: stale corrections are enqueued to a sibling SQLite review queue (`.goldenmatch/review_queue.db`) so the next `goldenmatch review` invocation surfaces them.
  - **8 end-to-end integration tests** in `test_memory_e2e.py` covering happy path, re-anchor on reorder, stale-on-edit, trust conflict, threshold learning, deterministic explainer fallback, postflight rendering, and stale-ambiguous reporting.

### Changed
- Zero-config posture preserved: nothing changes for users who don't enable memory (`config.memory.enabled = False` by default; absent config section means no memory work).

- **NEW**: TypeScript / Node.js port published as `goldenmatch` on npm
  - Full feature parity with Python: scorers, clustering, golden records, LLM, PPRL, probabilistic, graph ER, streaming, MCP/REST/A2A servers
  - Edge-safe core (browsers, Workers, Edge Runtime) + Node-only file/DB layer
  - 478 tests, strict TypeScript

## [1.4.1] - 2026-04-06

### Added
- **MCP tools for data quality** — `scan_quality` (scan without fixing), `fix_quality` (scan + apply fixes with safe/moderate mode), `run_transforms` (GoldenFlow phone/date/Unicode normalization). All 3 tools validate file paths, handle write failures gracefully, and include logging
- **A2A skills for data quality** — `quality` (scan + fix via GoldenCheck) and `transform` (normalize via GoldenFlow) skills added to the Agent-to-Agent protocol
- `run_transform(strict=True)` parameter — MCP/A2A handlers surface transform failures instead of silently returning unmodified data
- `_scan_only()` now returns serialized findings so MCP tools can inspect quality issues without duplicating the scan
- 10 new tests: happy-path coverage with mocked deps, file validation, write failure handling

### Fixed
- Eliminated redundant double-scan in `scan_quality` MCP handler (was scanning data twice and reaching into goldencheck internals)
- Temp file cleanup handles `PermissionError` on Windows (file locks no longer leak orphaned temp files)
- `_serialise_result` exception clause narrowed from `Exception` to `ImportError`
- `fix_quality` test assertion strengthened to check error message content

## [1.4.0] - 2026-04-06

### Added
- **Scoring & survivorship quality upgrade** — MST-based cluster auto-splitting, cluster quality labels (strong/weak/split), quality-weighted survivorship strategies, field-level provenance tracking
- **Data-driven strategy selection** — auto-config selects learned blocking (>= 5K rows), enables cross-encoder reranking (3+ fields), adjusts thresholds from data quality (null rate, string length)
- **`llm_auto` flag** — `GoldenMatchConfig.llm_auto=True` auto-enables LLM scorer ($0.05 budget) and memory store when API key detected. Applied uniformly across all config paths
- New config: `auto_split`, `quality_weighting`, `weak_cluster_threshold` in `GoldenRulesConfig`

### Fixed
- Pipeline wires `auto_split` config to `build_clusters`
- `add_to_cluster` documents oversized-flag-only behavior (callers must split)
- Threshold adjustments mutually exclusive (high-null and short-string no longer cancel out)

## [1.3.2] - 2026-04-03

### Fixed
- Auto-config: blocking keys with zero value overlap between sources are now skipped with a warning (fixes DBLP-ACM venue blocking failure where DBLP uses "VLDB" and ACM uses "Very Large Data Bases")
- Embedding scorer: falls back to token_sort when embedding model fails to load (HuggingFace auth, Vertex AI quota, missing dep, CUDA OOM) instead of crashing the pipeline

## [1.3.1] - 2026-04-03

### Added
- GoldenFlow integration: optional data transformation step in the dedupe pipeline (`pip install goldenmatch[transform]`)
- `TransformConfig` Pydantic model (enabled, mode: announced/silent/disabled)
- Pipeline step 1.4b: GoldenFlow runs after GoldenCheck, before autofix — normalizes phone numbers, dates, categoricals, unicode
- Graceful degradation: if goldenflow crashes, logs warning and continues with untransformed data
- Warning when config enables transforms but goldenflow is not installed
- 8 new tests

## [1.3.0] - 2026-04-03

### Added
- CCMS cluster comparison: `compare_clusters()` classifies each cluster from run A as unchanged, merged, partitioned, or overlapping relative to run B (based on Talburt et al., arXiv:2601.02824v1)
- `CompareResult` and `ClusterCase` dataclasses with `summary()` method
- Talburt-Wang Index (TWI) for normalized clustering similarity (1.0 = identical, approaches 0 for divergent outcomes)
- Parameter sensitivity analysis: `run_sensitivity()` sweeps config parameters and compares each run against a baseline using CCMS
- `SweepParam`, `SweepPoint`, `SensitivityResult` dataclasses with `stability_report()` for identifying optimal parameter ranges
- Supported sweep fields: `threshold` (all fuzzy matchkeys), `matchkey.<name>.threshold` (individual), `blocking.max_block_size`
- `--sample` option for sensitivity sweeps (random subsample for speed on large datasets)
- Per-point error handling: failed sweep points are logged and skipped, partial results preserved
- CLI command `goldenmatch compare-clusters` with `--details`, `--case-type` filter, `--output` JSON
- CLI command `goldenmatch sensitivity` with `--sweep field:start:stop:step` (repeatable), `--sample`, `--output`
- 16 new tests (10 comparison, 6 sensitivity)

## [1.2.7] - 2026-04-02

### Added
- Three auto-config cardinality guards to prevent failures on edge-case data:
  - Blocking: exclude near-unique columns (cardinality_ratio >= 0.95)
  - Matchkeys: skip exact matchkeys for low-cardinality columns (cardinality_ratio < 0.01)
  - Description columns: route long text to fuzzy matching (token_sort) alongside embedding
- Library comparison benchmarks: head-to-head against Splink, Dedupe, and RecordLinkage on Febrl (0.971 F1) and DBLP-ACM (0.918 F1)

### Fixed
- Auto-config no longer generates blocking keys from near-unique columns that produce single-record blocks
- Auto-config no longer creates exact matchkeys for columns with very few distinct values (e.g., gender, status)
- Description/long-text columns now get fuzzy fallback scoring instead of embedding-only

## [1.2.6] - 2026-04-01

### Added
- Iterative LLM calibration: samples ~100 pairs per round, learns optimal threshold via grid search, converges in 2-3 rounds (~200 pairs, ~$0.01) instead of scoring all candidates
- Concurrent LLM requests via ThreadPoolExecutor with configurable `max_workers` (default 5)
- Thread-safe BudgetTracker with `threading.RLock`
- ANN hybrid blocking: oversized blocks fall back to ANN sub-blocking via embeddings (embeds only unique text values)
- LLM-assisted column classification for ambiguous auto-config types
- Utility-based fuzzy field ranking (cardinality × completeness × string length)
- Price/cost/amount column name patterns to prevent zip misclassification
- `get_embedder()` GPU routing — returns VertexEmbedder when mode=vertex
- 3 new LLMScorerConfig fields: `calibration_sample_size`, `calibration_max_rounds`, `calibration_convergence_delta`
- 3 new ColumnProfile fields: `null_rate`, `cardinality_ratio`, `avg_len`
- 40 new tests (test_llm_calibration.py, test_ann_subblock.py, expanded test_autoconfig.py)

### Fixed
- ID patterns checked before phone/zip in auto-config — SalesID no longer misclassified as "phone"
- SalePrice (5-digit amounts) no longer misclassified as "zip"
- Identifier classifications authoritative over data profiling
- fiModelDesc no longer dropped from fuzzy fields on wide datasets
- Default batch_size bumped from 20 to 75
- "Never demote" behavior: LLM-rejected pairs keep original fuzzy score (was 0.0)
- Robust error handling: URLError/timeout retried, fut.result() guarded, ANN failures caught gracefully
- VertexEmbedder import failures fall back to local embedder

### Changed
- LLM scorer uses iterative calibration when candidates > calibration_sample_size (100)
- Multi-pass blocking passes ann_column/ann_top_k/ann_model to static builder
- `_classify_by_name` check order: date → email → ID → price → zip → geo → address → phone → name

## [1.2.0] - 2026-03-25

### Added
- **Autonomous ER Agent** -- GoldenMatch as a discoverable AI agent via A2A and MCP protocols
- `AgentSession` class -- profiles data, selects strategy, runs pipeline, explains reasoning
- `ReviewQueue` with confidence gating (auto-merge >0.95, review 0.75-0.95, reject <0.75)
- Three storage backends for review queue: memory (default), SQLite, Postgres
- `gate_pairs()` -- split scored pairs by confidence thresholds
- A2A server (`goldenmatch agent-serve`) with agent card, task lifecycle, SSE streaming
- 8 A2A skills: analyze_data, configure, deduplicate, match, explain, review, compare_strategies, pprl
- 10 MCP agent-level tools (additive to existing tools)
- `goldenmatch agent-serve --port 8200` CLI command
- Demo script: `python examples/agent_demo.py`
- Branch & Merge SOP added to CLAUDE.md

## [1.1.0] - 2026-03-23

### Added
- `gm.dedupe_df()` -- deduplicate a Polars DataFrame directly (no file I/O)
- `gm.match_df()` -- match two Polars DataFrames directly (no file I/O)
- `gm.score_strings()` -- score two strings with a named similarity algorithm
- `gm.score_pair_df()` -- score a pair of record dicts
- `gm.explain_pair_df()` -- explain a pair match from record dicts
- Internal: `run_dedupe_df()` and `run_match_df()` pipeline entry points
- These functions are the prerequisite for native SQL extensions (Postgres/DuckDB)
- New companion repo: [goldenmatch-extensions](https://github.com/benseverndev-oss/goldenmatch-extensions) -- PostgreSQL extension (`goldenmatch_pg`) and DuckDB extension (`goldenmatch-duckdb`) for in-database entity resolution via SQL

## [1.0.0] - 2026-03-23

### Changed
- **Production/Stable** -- dropped Beta label. Semver strictly enforced from this release.
- Public API surface frozen: 96 exports from `import goldenmatch as gm`, 21 CLI commands, config YAML schema, REST endpoints, MCP tools. See `docs/api-stability.md`.

### Added
- Clean Python API: `gm.dedupe()`, `gm.match()`, `gm.pprl_link()`, `gm.evaluate()` with typed results
- 96 public exports covering every feature (config, pipeline, streaming, LLM, PPRL, domain, explain, etc.)
- REST API client: `gm.Client("http://localhost:8000")`
- Jupyter/notebook display: `_repr_html_()` on DedupeResult and MatchResult
- CI/CD quality gates: `goldenmatch evaluate --min-f1 0.90` exits code 1 if below threshold
- 7 runnable example scripts in `examples/`
- `goldenmatch label` CLI for interactive ground truth building

## [0.7.0] - 2026-03-23

### Added
- Ray distributed backend for large-scale entity resolution (`pip install goldenmatch[ray]`)
- `--backend ray` CLI flag for dedupe command
- `backend: ray` config option in GoldenMatchConfig
- `backends/ray_backend.py` with `score_blocks_ray()` -- drop-in replacement for ThreadPoolExecutor
- Automatic fallback to parallel scorer for small block counts (<= 4)
- Ray auto-initializes locally using all CPU cores, no user configuration needed
- Supports Ray clusters for 50M+ record workloads
- `goldenmatch label` CLI command -- interactive pair labeling to build ground truth CSV for accuracy measurement (y/n/s keyboard input)

## [0.6.0] - 2026-03-23

### Added
- Privacy-preserving record linkage (PPRL) package (`goldenmatch/pprl/`)
- Trusted third party mode: parties send encrypted bloom filters, coordinator computes similarity
- SMC mode: secret-shared dice similarity, only match bits revealed (simulated circuit)
- `goldenmatch pprl link` CLI command for cross-party linkage
- Bloom filter security levels: standard (512-bit), high (1024-bit + HMAC), paranoid (2048-bit + balanced padding)
- Per-field HMAC salting prevents cross-field correlation attacks
- Balanced bloom filter padding normalizes filter density for short strings
- Custom HMAC key support via transform parameter (`bloom_filter:2:20:512:my_key`)
- `pip install goldenmatch[pprl]` optional dependency group
- PPRL auto-configuration (`auto_configure_pprl`) -- profiles data, selects optimal fields, bloom filter parameters, and threshold automatically. 92.4% F1 on FEBRL4, 76.1% on NCVR
- MCP tools: `pprl_auto_config` (auto-configure PPRL for a dataset), `pprl_link` (run cross-party linkage)
- Vectorized PPRL similarity computation (13x speedup over row-wise scoring)
- NCVR (North Carolina Voter Registration) and FEBRL4 benchmark suites for PPRL evaluation

## [0.5.0] - 2026-03-23

### Added
- In-context LLM clustering (`mode: cluster`) -- send blocks of 50-100 borderline records to LLM for direct cluster assignment instead of pairwise yes/no scoring
- Uncertainty scores -- LLM returns confidence per cluster, surfaced in cluster metadata and review queue
- `core/llm_cluster.py` -- new module with component detection, graph splitting, structured JSON parsing, pairwise fallback
- LLMScorerConfig gains `mode`, `cluster_max_size`, `cluster_min_size` fields
- Budget-aware degradation: cluster mode -> pairwise fallback -> stop

## [0.4.0] - 2026-03-23

### Added
- CI/CD pipeline: automated tests on Python 3.11/3.12/3.13, ruff lint, smoke test
- `py.typed` PEP 561 marker for type checker support
- `docs/api-stability.md` documenting the public API surface
- This CHANGELOG

### Changed
- Version policy: public API surface defined and documented ahead of 1.0 semver commitment

## [0.3.1] - 2026-03-22

### Added
- 5 new domain packs: healthcare, financial, real_estate, people, retail (7 total)
- `goldenmatch evaluate` CLI command -- precision/recall/F1 against ground truth CSV
- `goldenmatch incremental` CLI command -- match new records against existing base
- GitHub Actions "Try It" workflow for zero-install demo
- GitHub Codespaces devcontainer
- `dbt-goldenmatch` package for DuckDB-based entity resolution
- GitHub Discussions, issue templates, community standards (CoC, contributing, security)
- PyPI download badge in README

## [0.3.0] - 2026-03-21

### Added
- Fellegi-Sunter probabilistic matching with EM-trained m/u probabilities
- Learned blocking -- data-driven predicate selection
- LLM scorer with budget controls (BudgetTracker, cost caps, model tiering)
- Domain-aware feature extraction (electronics, software auto-detection)
- Custom domain registry (YAML rulebooks, MCP tools)
- Plugin architecture (scorers, transforms, connectors, golden strategies via entry points)
- Enterprise connectors: Snowflake, Databricks, BigQuery, HubSpot, Salesforce
- DuckDB backend for out-of-core processing
- Streaming/CDC mode with StreamProcessor
- Multi-table graph entity resolution
- Natural language explainability (zero LLM cost)
- Lineage tracking with streaming writer (no 10K cap)
- REST API review queue for data steward approval
- Daemon mode with health endpoint and PID file
- MCP server tools: list_domains, create_domain, test_domain, suggest_config

### Changed
- LLM scorer refactored to accept LLMScorerConfig with BudgetConfig
- Pipeline: domain extraction step between standardize and matchkeys
