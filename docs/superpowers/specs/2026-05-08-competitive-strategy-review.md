# Golden Suite — Engine Strategy Review

**Date:** 2026-05-08
**Author:** review pass on `claude/review-competitive-strategy-zy5de`
**Scope:** the **engine**. Everything in this monorepo: the Python core, TS port, Rust extension, scorers, blockers, auto-config controller, Learning Memory, PPRL, graph_er, streaming. Postgres extension counts; web UI auth does not.
**Out of scope (lives in `golden-showcase`):** pricing, hosted SaaS, marketplace listings, governance UI, SOC 2 posture, sales motion. Those are the product wrapper. This doc is about the part you `import`.

The question this doc answers: **how do we become the best entity-resolution engine?**

---

## TL;DR

GoldenMatch is already the most consistent-across-data-shapes ER engine in OSS — top-2 F1 on Febrl, DBLP-ACM, NCVR, with the only self-verifying introspective auto-config controller in the field, and byte-level Python↔TS parity at the scorer layer. The engine gap to the next tier (Senzing, Quantexa research stack, Ditto-class fine-tuned transformers) is in five places:

1. **In-memory scale ceiling** at ~500K rows. Senzing handles 10M+ on a single node by design; Splink rides DuckDB to 1B. Today's `--backend duckdb` opt-in pays the credibility tax in every demo.
2. **Identity graph as a first-class engine output**, not a clustering byproduct. Quantexa and Senzing won this category because their engines emit a graph; ours emits a clusters dict.
3. **Auto-config controller v2** — the v1.8 controller is the strongest moat we have. It needs a predicted-F1-without-ground-truth signal, robustness to long-tail data shapes, and a memory that generalizes across runs. If we don't extend it, competitors will copy the v1.8 design within a year.
4. **Hybrid LLM ↔ distilled-classifier scorer.** LLM scoring (`core/llm_scorer.py`) works and is budget-bounded but the engine is *dependent* on the LLM at the borderline. Distill the LLM votes into a local cross-encoder per dataset and the engine becomes accuracy-equivalent without an internet round-trip — a moat against Senzing-style offline shops and against vendors who can't get LLM keys past procurement.
5. **Inner-loop speed.** The 2026-05-04 audit found the matchkey-transform hoist gave ~1.22× wall, well below the static-count framing implied. The next bottleneck is unmeasured. The audit's lesson — measure-first — applies here. Likely target: the scorer pair-emission loop. A Rust hot-path for that loop is the highest-leverage speed bet.

The full direction list (12 items, ROI-ordered) follows. None of these are about distribution, billing, or sales — all are about making the engine measurably better than every alternative.

---

## What "best engine" means, concretely

A buyer (or another tool that embeds us) compares ER engines on:

| Axis | Today | Best in field | Gap |
|---|---|---|---|
| **Accuracy, structured/PII** | F1 0.971 (Febrl) | Splink 0.998 | -2.7 pts |
| **Accuracy, bibliographic** | F1 0.972 (DBLP-ACM zero-config) | tied | none |
| **Accuracy, product matching** | F1 0.722 (Abt-Buy +LLM) | Ditto 0.893 (fine-tuned) | -17 pts |
| **Accuracy, zero-config** | DBLP-ACM 0.964, Febrl3 0.944, NCVR 0.972 | nobody else publishes zero-config numbers | leader |
| **Throughput, single node** | 500K in-memory ceiling, 10M with `--backend duckdb` | Splink/DuckDB 1B; Senzing 10M+ default | 20×-2000× |
| **Latency, match-one** | not benchmarked | Tilores < 50ms p95 | unknown |
| **Runtime breadth** | Python, TS (parity), Postgres (partial), DuckDB (partial) | Splink: Spark + DuckDB. Senzing: C/Java + bindings. | wide on languages, narrow on databases |
| **Privacy** | PPRL F1 0.924 (FEBRL4) | tied with research, ahead of vendors | leader |
| **Provenance** | Per-field lineage + Learning Memory | Tamr golden-record provenance | parity |
| **Adaptivity** | Introspective auto-config controller (v1.8) | nobody | leader |
| **Determinism / offline** | LLM-required for borderline accuracy | Senzing fully offline | regression |
| **Composability** | MIT, MCP/A2A surface, plugin SDK | Splink Python-only; Senzing closed core | leader |

Five "leader" cells, two genuine deficits (throughput, product-matching SOTA), one sneaky deficit (LLM-dependence at the borderline). The directions below close the deficits without giving up the leader cells.

---

## Where the engine wins today (don't regress these)

- **Zero-config controller (`core/autoconfig_controller.py`).** v1.8 introspective controller iterates on block-size distribution, score histogram, transitivity rate, and borderline mass; converges with cross-run memory. DBLP-ACM 0.51→0.964. *Nobody else publishes zero-config numbers because they don't have a story for it.*
- **Polyglot byte-level parity.** `tests/parity/` locks scorer output at 4-decimal tolerance Py↔TS. Learning Memory is byte-identical SHA-256 across runtimes. Splink, Dedupe, Senzing, Tamr — none have this.
- **PPRL in production shape, not research shape.** Auto-config 92.4% F1 FEBRL4. Per-field HMAC. Most ER engines treat privacy-preserving linkage as a research demo.
- **AI-native scorer with budget caps.** $0.04 LLM cost on Abt-Buy with graceful degradation; cached votes survive re-runs via Learning Memory. Most "AI ER" tools wrap a single LLM call with no budget control.
- **One pipeline, eleven scorers, eight blockers.** `core/scorer.py` and `core/blocker.py` are the broadest scorer/blocker zoo in any single OSS engine. Each is composable in YAML.

Every direction below has to answer: *does it weaken any of these?*

---

## The competitor map (engine-only)

### Tier-1 OSS engines (we benchmark against these)
- **Splink** — Fellegi-Sunter + Spark/DuckDB. SOTA on PII. Weak on non-PII (DBLP-ACM 0.728). Python-only. Active learning is manual labelling. **Our edge:** zero-config, polyglot, non-PII.
- **dedupe** — Active-learning forward, ML-based. Slowest of the field. **Our edge:** out-of-the-box accuracy, no labels needed.
- **RecordLinkage Toolkit** — Classical. Strong on DBLP-ACM specifically. **Our edge:** every other dimension.
- **Zingg** — Spark + ML. Java/Scala. **Our edge:** single-machine, polyglot, LLM, MCP.
- **JedAI** — Java toolkit. Academic. **Our edge:** modern stack, zero-config.

### Tier-1 closed engines (we don't benchmark against these — we should)
- **Senzing G2** — Identity-graph engine, deterministic + probabilistic, 25 years of reference data, fully offline. Semi-OSS but the brain is closed. **Their edge:** identity graph + reference data + offline. **Our shot:** AI-native, composable, MCP, zero-config.
- **Quantexa engine** — Graph contextual decisioning. Closed. **Their edge:** network reasoning. **Our shot:** open core lets researchers and toolmakers build on us.
- **Tilores** — Identity-graph-as-a-service. **Their edge:** real-time latency. **Our shot:** identical engine in batch + streaming.

### Research SOTA (the accuracy ceiling)
- **Ditto** — Fine-tuned transformer, F1 0.893 on Abt-Buy (1000+ labels). **Their edge:** product matching. **Our gap:** -17 pts F1 with no labels. Hybrid scorer + reference data could close half.
- **HierGAT, JointBERT, EMTransformer** — Research papers, not products. We should run their reference impl on our leaderboard.
- **GPT-4 / Claude few-shot** — F1 0.92+ on Abt-Buy with zero training. We use this in `core/llm_scorer.py`. The next move is distillation (direction 4).

### Cloud-managed engines
- **AWS Entity Resolution, Google Cloud Entity Reconciliation, Snowflake/Databricks-native ER UDFs.** All shipped 2024-2026. Closed. Tied to one warehouse. **Their edge:** integration. **Our shot:** the same engine ports across; their results don't.

---

## Strategic directions, ROI-ordered

Each item: **thesis · evidence · ship · effort · risk**.

### Direction 1 — Lift the in-memory throughput ceiling

**Thesis.** The 500K row OOM cliff is the engine's single biggest weakness in any side-by-side. Lift the default ceiling to ~10M on a 64GB box without flags. This is *engine* work — not infra.

**Evidence.**
- `goldenmatch/CLAUDE.md` — "1M records: OOM in-memory — use DuckDB backend or chunked processing for >500K records".
- 2026-05-04 audit (`docs/superpowers/specs/2026-05-02-performance-audit-checklist.md`) lesson: measure 5-run wall before designing. The matchkey-transform hoist gave 1.22×; the static count implied 5×.
- `backends/duckdb_backend.py` and `core/chunked.py` exist; the engine just doesn't choose them.

**Ship.**
1. **Re-bench** at 100K, 500K, 1M, 5M with RSS tracking. 5-run median. Identify the *measured* bottleneck — pair-list growth in the scorer is the prime suspect.
2. **Streaming pair emission** in `core/scorer.py` — never hold N² pairs in memory; cluster-as-you-go via `core/cluster.py`'s union-find.
3. **DuckDB-as-default above a measured row threshold**, gated to avoid regressing small-data wall time. Today's opt-in becomes opt-out.
4. **Backpressure on the LLM scorer** — the queue can't grow unbounded under streaming pair emission.

Acceptance: 10M rows on a 64GB box, no flags, no OOM, end-to-end (ingest → match → cluster → golden record). Wall time documented; not "fast enough", a number.

**Effort.** 3–5 weeks engineering, 1 week benchmarking. Risk concentrated in the scorer refactor; the rest is plumbing.

**Risk.** Medium. DuckDB-as-default could regress small-data wall; gate on row count. Streaming pair emission interacts with `golden_rules.auto_split` (cluster-as-you-go vs split-after-cluster) — needs design.

---

### Direction 2 — Identity graph as a primary engine output

**Thesis.** Today the engine emits `dict[int, dict]` (clusters with members + pair_scores). Tomorrow it emits a `GoldenGraph` with nodes (entities), edges (evidence), time, and source provenance. Senzing and Quantexa won the identity-graph category by emitting a graph from their engine. We have most of the pieces (`core/graph_er.py`, `core/lineage.py`, Learning Memory) and they don't compose into a graph output.

**Evidence.**
- `core/graph_er.py` and `core/graph.py` exist but are post-clustering renderers, not primary outputs.
- `core/lineage.py` already records per-field provenance.
- `core/memory/store.py` has the SQLite schema for persistent decisions.
- Customer pattern across pilots: every "person ↔ account ↔ device ↔ transaction" question is graph-shaped, not pair-shaped. The engine doesn't have the right output for it.

**Ship.**
1. **`gm.graph.upsert(records, source=...)` → `GoldenGraph`** as a new top-level engine API. Nodes are entities, edges are scored pairs with scorer-name + transform-chain + timestamp + source.
2. **Re-resolution on append.** New records re-score only against the affected entity neighborhood (uses the existing ANN index + `core/streaming.py`).
3. **Edge provenance carried through.** `edge.scorer = "jaro_winkler"`, `edge.transforms = ["lower", "strip"]`, `edge.timestamp`, `edge.source`. Defensible against Senzing because each edge is auditable in a way their closed graph isn't.
4. **Graph-aware clustering.** Replace the post-hoc auto-split heuristic with graph-native components (modularity-based or F-S-weighted cuts).
5. **Storage backends.** SQLite + DuckDB v1; Postgres via the Rust extension v2; Neo4j adapter v3.
6. **Cross-time queries.** `graph.find(name="...", at="2024-01")`, `graph.timeline(entity_id)`. This is the wedge against Senzing-static-snapshot.

**Effort.** 8–12 weeks for v1. Hardest part is the data-model spec; spend a week on it before code.

**Risk.** High-reward, high-effort. The data model decision is sticky; design first. Don't reimplement a graph database — be a graph application.

---

### Direction 3 — Auto-config controller v2

**Thesis.** v1.8's introspective controller is the strongest *engine* moat we have. It will be copied within a year; we should be at v2 by then. v2 means (a) predicted F1 without ground truth, (b) robustness to long-tail data shapes, (c) cross-run memory that generalizes (transfer learning, not exact-match lookup), (d) explanations the user can trust.

**Evidence.**
- v1.8 ships `core/autoconfig_controller.py` + `autoconfig_history.py` + `autoconfig_memory.py` + `autoconfig_policy.py` + `autoconfig_rules.py` + `autoconfig_verify.py`. Five-file architecture, well-documented.
- DBLP-ACM 0.51→0.964 zero-config beats hand-tuned ceiling 0.918.
- Cross-run memory (`~/.goldenmatch/autoconfig_memory.db`) currently uses an exact data-shape signature. Generalization gap is wide open.
- LLM fallback (`LLMRefitPolicy`) is opt-in; underutilized for the diagnostic role.

**Ship.**
1. **Predicted F1 without ground truth.** Train a meta-model on `complexity_profile.py` signals → measured F1 across all benchmarks we own. The controller emits "this run will land near F1 0.93±0.04" alongside the config. Honest uncertainty bounds — a number nobody else can show.
2. **Long-tail robustness.** Build a stress benchmark: malformed data, mostly-nulls, all-identical, near-empty fields, non-Latin scripts, unicode confusables. Controller must converge or honestly report "I don't know" — never silently ship a bad config.
3. **Generalizing memory.** Move the cross-run memory from exact-signature lookup to nearest-neighbor over normalized profile vectors. Past committed configs become *priors*, not *replays*.
4. **LLM as diagnostic, not last-resort.** When heuristic rules disagree, the LLM judges the disagreement (cheap, structured, single call). Currently it only fires when rules exhaust.
5. **Explanation surface.** Each controller iteration emits a one-line "I picked X because Y". Already partly there; promote to first-class output.

**Effort.** 6–10 weeks. The meta-model (item 1) is the hard part — needs ~50 benchmark runs across diverse data to train.

**Risk.** Medium. Predicted-F1 with bad uncertainty is worse than no prediction; calibrate aggressively or don't ship that piece.

---

### Direction 4 — Hybrid LLM ↔ distilled-classifier scorer

**Thesis.** LLM-augmented scoring is great when the LLM is available. It's a vulnerability when it's not (offline shops, procurement-blocked teams, latency-sensitive workloads). Distill the LLM votes a customer accumulates into a local cross-encoder per dataset; the engine becomes LLM-equivalent at the borderline *without* the LLM after a warm-up run. Closes the loop with Learning Memory.

**Evidence.**
- `core/llm_scorer.py` + `core/llm_budget.py` already cache votes via Learning Memory.
- `core/cross_encoder.py` already loads HuggingFace cross-encoders for borderline rerank.
- Distillation is a two-step training pipeline that already-cached votes make trivial.
- Senzing's offline posture is a buying gate for finance/defense/healthcare we currently can't pass.

**Ship.**
1. **`gm distill`** CLI: read all cached LLM votes from Learning Memory, train a small local cross-encoder (DistilBERT-class), save to `~/.goldenmatch/distilled/{dataset_hash}.onnx`.
2. **Engine auto-prefers** the distilled model for borderline pairs once it exists for the matching profile signature.
3. **LLM fallback** only when the distilled model is uncertain (low-margin output) or when the data shape changes enough to invalidate the model.
4. **Air-gap mode** (`--offline`): refuse to call the LLM, never. Hard guarantee.
5. **Distillation benchmarks** on the leaderboard (direction 5): "with LLM 0.722, distilled 0.71x, no LLM 0.65x" on Abt-Buy. Honest numbers; the value prop is *good-enough offline*, not *better than LLM*.

**Effort.** 4–6 weeks. Distillation tooling exists; integration into the borderline gate is the work.

**Risk.** Medium. Distillation requires enough cached votes to be useful (~500+); document the warm-up requirement honestly.

---

### Direction 5 — Public benchmark leaderboard

**Thesis.** Whoever runs the leaderboard owns the conversation. DQBench is referenced in our README but not promoted. A hosted, reproducible, submission-PR leaderboard with per-segment podiums (PII, bibliographic, product, business, healthcare, PPRL) anchors every future comparison on our turf. *The engine work is the benchmark harness, not the website* — that part lives in golden-showcase.

**Evidence.**
- DQBench is real (`benzsevern/dqbench`), referenced with score 95.30 in our README.
- Comparison benchmark scripts already exist at `D:\show_case\golden-showcase\comparison_bench\` (Splink, Dedupe, RecordLinkage on Febrl/DBLP-ACM/NC Voter).
- No OSS competitor hosts a leaderboard. Splink/Dedupe link to academic papers; nobody runs the comparison themselves.
- The discipline of running competitors monthly catches our own regressions.

**Ship.**
1. **Reproducible run scripts** for every published number: ours + Splink + Dedupe + RecordLinkage + Zingg (Java/Spark) + research SOTA where reference impls exist.
2. **Per-segment scoring**: PII, bibliographic, product, business, healthcare, PPRL, **adversarial** (the stress benchmark from direction 3).
3. **Scheduled CI run** weekly against pinned competitor versions; commit the JSON, render in golden-showcase.
4. **Stress benchmark on the leaderboard.** Adversarial / dirty-data scores. Nobody publishes these because most engines do badly. We use it as a moat.

**Effort.** 3–5 weeks for the engine-side harness. Site lives in golden-showcase.

**Risk.** Low. Worst case the discipline of weekly competitor runs catches our regressions before customers do.

---

### Direction 6 — Postgres extension to engine-API parity

**Thesis.** The engine has two runtimes today (Python, TS) at parity, and a third (Postgres via pgrx) at ~30% parity. Lift the Postgres extension to the same surface — `dedupe`, `match`, `match_one`, PPRL, Learning Memory replay, review queue, identity graph (direction 2). The engine's reach grows by the number of teams whose first install is `CREATE EXTENSION goldenmatch;`.

**Evidence.**
- `packages/rust/extensions/` ships pgrx + DuckDB UDFs.
- README shows ~7 SQL functions; full Python API has ~15 entry points.
- Splink-on-DuckDB is the closest competitor in this lane; their UDFs are narrower.
- Postgres-native ER is a buying gate for analytics teams who won't add a Python sidecar.

**Ship.**
1. **`goldenmatch_run(config jsonb, source regclass)`** end-to-end orchestrator UDF mirroring `gm.dedupe()`.
2. **Incremental matching as a Postgres trigger.** Insert → ANN-block → score-against-cluster → write back. Mirrors `core/streaming.py`.
3. **Stored configs** in `goldenmatch_configs` table with versioning; the live ER config is reviewable in SQL.
4. **ANN index sidecar table + maintenance triggers.** v1; pgvector-native v2.
5. **Identity graph nodes/edges as Postgres tables** (compounds with direction 2).
6. **DuckDB MotherDuck publishing** of the same UDFs — two-week shippable.

**Effort.** 6–10 weeks. pgrx local-build issues documented (`needs libclang/LLVM, use CI for builds`); dev loop is slow, budget for it.

**Risk.** Medium. Compounds well with direction 2 (the graph nodes/edges live as Postgres tables); sequence after #2 lands the data model.

---

### Direction 7 — Inner-loop speed via measured Rust hot-paths

**Thesis.** The 2026-05-02 audit closed the matchkey-transform hoist for ~1.22× wall and noted "no Rust core for the Python path" — Rust calls Python via PyO3 in the SQL extension, not the other way. The next 2× wall isn't going to come from another Polars rewrite; it's going to come from a Rust hot-path for the scorer's pair-emission inner loop. *Only do this with measurement first.*

**Evidence.**
- Audit lesson literally on file: "measure wall-clock with the workload of interest before designing".
- Matchkey hoist measured 1.22× wall vs implied 5×; the framing was wrong.
- `core/scorer.py` parallel block scoring is in Python; `rapidfuzz.cdist` is Rust under the hood but the per-block dispatch and pair filtering is Python.
- Splink's DuckDB UDFs run native; we run Polars + Python wrappers.

**Ship.**
1. **Profile** the 5M-row scorer pipeline (direction 1's bench). Identify where the wall is — pair filter? scorer dispatch? cluster build?
2. **Iff the bottleneck warrants** (>2× projected speedup measured on a Rust prototype), implement the hot loop in the existing `packages/rust/extensions/bridge/` crate, expose via PyO3.
3. **TS parity.** Whatever Rust path lands, TS gets a WASM-built equivalent or a documented "Node-only acceleration via napi binding" path. Don't fork the engine.
4. **Cargo workspace** stays as-is per `CLAUDE.md` ("Cargo doesn't allow nested workspaces sharing members"); the bridge crate already accommodates.

**Effort.** 2 weeks profiling + 4–6 weeks Rust hot-path if the measurement supports it. **If the measurement doesn't support a 2× speedup, kill the direction and reinvest the time elsewhere.**

**Risk.** Medium. The audit's lesson is the lesson here too: don't refactor for speed without a measured target.

---

### Direction 8 — Reference data integration (engine accuracy)

**Thesis.** Senzing's accuracy moat on people/business matching isn't a smarter algorithm; it's 25 years of reference dictionaries. We can't out-curate them, but we can match them on the 80% case using bundled OSS reference data — US Census surnames, libpostal, OpenCorporates, NAICS, SSA name frequencies — wired into the scorer's normalization pipeline. *This is engine work, not a marketing pack.*

**Evidence.**
- `goldenmatch/domains/` has 7 packs but no reference data behind them — they're rule sets, not lookups.
- libpostal (MIT), US Census (public domain), OpenCorporates (CC-BY), NAICS (public) — all incorporable.
- Auto-config controller (direction 3) gets a stronger signal when names are normalized against a frequency table.

**Ship.**
1. **`goldenmatch[reference-people]`** extra: bundled name-frequency lookups, given-name aliases (William↔Bill), nickname tables, soundex variants per locale.
2. **`goldenmatch[reference-business]`** extra: legal-form normalization (Inc/LLC/GmbH), industry code lookups, OpenCorporates company-name variants.
3. **`goldenmatch[reference-address]`** extra: libpostal binding (we already have it as opt-in for `pyap`/`usaddress`), CASS-style normalization.
4. **Scorer integration**: `name_freq_weighted_jw` becomes a first-class scorer; `address_libpostal_norm` becomes a transform.
5. **Benchmarks**: NCVR with reference-people pack, OpenCorporates merge with reference-business pack. Publish on the leaderboard (direction 5).

**Effort.** 4–6 weeks. Half is data-licensing diligence; half is integration.

**Risk.** Medium. Each reference dataset has its own license terms; some won't permit redistribution. Start with US-Census + libpostal.

---

### Direction 9 — Active learning, as algorithm not as UX

**Thesis.** `core/active_sampling.py` exists. The algorithm's quality — which 20 pairs to label — is the engine differentiator, not the UI that shows them. Make the sampling provably optimal (uncertainty + diversity + cluster coverage) and the engine becomes the one Dedupe and Splink both have to copy. UI lives in golden-showcase.

**Evidence.**
- `core/active_sampling.py` already wired into Learning Memory.
- Threshold learner already triggers at 10 corrections.
- Dedupe's reputation rests on its active learning; the algorithm is publicly documented.

**Ship.**
1. **Confidence-calibrated uncertainty sampling.** Today the sampler is heuristic; replace with margin-from-decision-boundary + conformal prediction intervals.
2. **Diversity via cluster coverage.** No two consecutive label asks come from the same cluster bucket.
3. **Adversarial-pair injection.** Sometimes show a clearly-positive or clearly-negative pair to detect labeler drift.
4. **Cold-start regime.** First 5 labels follow a different policy (broad coverage); next 5 narrow on the boundary.
5. **Benchmark.** On NCVR, 20 active-learned labels should beat 200 random-sampled labels at threshold tuning. Publish.

**Effort.** 3–4 weeks.

**Risk.** Low. Worst case it doesn't beat the existing heuristic; we kept the heuristic.

---

### Direction 10 — Streaming/CDC parity with batch

**Thesis.** "The same scorer code that ran your batch overnight scores the next inserted row in <100ms" is a positioning we can almost claim. `core/streaming.py` exists. `core/match_one.py` exists. The Postgres extension (direction 6) makes it trigger-driven. Position the engine as the only one where batch and streaming use the same scorer code.

**Evidence.**
- `core/streaming.py` and `core/match_one.py` shipped.
- `goldenmatch/CLAUDE.md`: `match_one()` returns empty list for exact matchkeys (broken edge case — fixable).
- No OSS competitor has byte-identical batch/streaming results.

**Ship.**
1. **`gm.match_one_async()`** with sub-100ms p95 on a Postgres-co-located deployment.
2. **CDC reference deploy.** Debezium → Kafka → `match_one_async`. Document the wiring; ship the docker-compose.
3. **Streaming Learning Memory replay.** Corrections apply to streaming events identically to batch. Already mostly true; close the gaps.
4. **Latency benchmark on the leaderboard** (direction 5).

**Effort.** 3–5 weeks v1.

**Risk.** Medium. Latency claims are easy to make and hard to defend; benchmark publicly.

---

### Direction 11 — TS parity catch-up on the asymmetric features

**Thesis.** `tests/parity/` locks scorer parity at 4-decimal Py↔TS. Beyond scorers, the TS port is asymmetric — autoconfig controller, PPRL auto-config, learned blocking, plugin SDK, reference data (direction 8) all live in Python. Closing the asymmetry keeps the polyglot story honest.

**Evidence.**
- TS version is 0.4.x; Python is 1.9.x. The version gap reflects feature gap.
- TS package shipped 478 tests; Python ships ~1319.
- Edge runtimes (Vercel Edge, Cloudflare Workers) are an engine reach we own only in TS.
- `core/autoconfig_controller.py` has no TS analog; `_signals_view` parity only at the legacy-signals layer.

**Ship.**
1. **Auto-config controller v2 ports to TS** alongside the Python build (direction 3) — design with parity from the start.
2. **PPRL auto-config to TS.** Privacy-preserving in the browser is a category nobody else has.
3. **Learned blocking to TS.** Currently Python-only; needed for parity claims to be honest.
4. **Plugin SDK to TS.** TS has the plugin protocol but lacks the registry/discovery surface Python has.
5. **Parity harness extended** to cover autoconfig outputs at JSON-equality level.

**Effort.** 6–8 weeks staged across releases. Don't try to land it in one PR.

**Risk.** Low-medium. Bigger Python releases without TS parity gradually erode the polyglot pitch.

---

### Direction 12 — Engine-side explainability + counterfactuals

**Thesis.** Every match the engine emits should answer "what tipped this pair, and what would have flipped it?". Today we ship per-pair NL prose and per-field scores; add counterfactual minimal-edits ("if `address` had been `123 Main` instead of `123 Maine`, score would have dropped from 0.91 to 0.78") and the engine becomes the only one whose outputs are auditable in the way regulated industries need.

**Evidence.**
- `core/explain.py` + `core/explainer.py` + `core/diff.py` + `core/lineage.py` are already shipped.
- One-line NL prose explanations per pair already in the web UI.
- No competitor publishes counterfactual explanations as engine output. Tamr / Reltio describe their decisions; they don't show what would have flipped them.
- Regulated industries (healthcare, finance, KYC) need this for compliance, not for nice-to-have.

**Ship.**
1. **`gm.explain_pair_counterfactual(a, b, config)`** returns the minimal field-edit set that would flip the decision.
2. **Per-cluster explanations** that name the bottleneck pair and what it would take to split.
3. **Lineage as an engine output**, not a debug log. JSON schema, queryable.
4. **Confidence calibration plot** as part of the standard report — "your decisions at threshold 0.85 are right 94% of the time on this dataset".

**Effort.** 3–5 weeks. Counterfactual generation is well-trodden; integration into the existing explainer is the work.

**Risk.** Low.

---

## What NOT to do (engine traps)

1. **Don't add a new scorer just because it's in a paper.** The 2026-05-04 audit lesson applies: measure first. Today's ensemble is broad; another scorer adds maintenance, often loses accuracy.
2. **Don't try to beat Ditto on Abt-Buy with a hand-tuned recipe.** That's a fine-tuned-transformer race. Win it with hybrid distillation (direction 4) and reference data (direction 8); lose the F1 vanity battle gracefully.
3. **Don't fragment the polyglot story.** Every Python feature lands without a TS plan is a bill paid later. Direction 11 exists for a reason.
4. **Don't ship more domain packs without benchmarks** — three benchmarked packs beat seven unbenchmarked.
5. **Don't promise scale we haven't measured.** Today's "10M+ records with Ray backend" is technically true and operationally weak. Direction 1 fixes the *measured* claim; until then, soften the README.
6. **Don't add SQL UDFs in `packages/rust/extensions/` ahead of the Python API parity.** The extension should never lead the engine; it should track it. Otherwise we're maintaining two engines.
7. **Don't make the auto-config controller smarter without making it more honest.** Predicted-F1 with bad uncertainty (direction 3 item 1) is worse than no prediction. Calibrate or skip.
8. **Don't deepen LLM-dependence in the borderline before distillation lands** (direction 4). Currently the engine is "LLM-augmented"; without distillation, it slides toward "LLM-dependent". That's a moat for OpenAI, not for us.

---

## Suggested 90-day cuts (engine-only)

If you can fund **one** direction in the next quarter, fund **direction 1 (throughput)**. The 500K cliff is the engine's weakest claim. Lift it, every other story gets more credible.

If **three**:

1. **Weeks 1–4: direction 1** (throughput) + spec direction 2 (identity graph data model) in parallel.
2. **Weeks 5–8: direction 3** (auto-config v2) + direction 5 (leaderboard harness). The accuracy story tightens around the same week.
3. **Weeks 9–12: direction 4** (LLM distillation) + direction 9 (active-learning algorithm). The engine becomes accuracy-equivalent without LLM dependence; corrections become more sample-efficient.

If **five**, add:

- **Direction 2 (identity graph) v1 ships** — long-cycle bet; the others compound around it.
- **Direction 7 (Rust hot-path)** *only if direction 1's profiling supports it*. If the bottleneck is elsewhere, swap for direction 8 (reference data).

Defer to 2026-Q4:

- **Direction 6 (Postgres extension parity)** — sequenced after direction 2 lands the identity graph data model, since the extension is the SQL-native shape of the same graph.
- **Direction 10 (streaming parity)** — depends on direction 1's pair-emission refactor and direction 6's trigger model.
- **Direction 11 (TS catch-up)** — co-design with direction 3, then ship as a Q4 release pulse.
- **Direction 12 (counterfactuals)** — regulated-industry pull; nice-to-have until a buyer asks.

---

## Open questions for the next iteration

1. **What's the measured wall on 5M rows today?** Direction 1 hinges on this number. We don't have it.
2. **Does the auto-config controller's cross-run memory generalize at all today?** Direction 3 item 3 assumes it doesn't; verify before designing.
3. **How many cached LLM votes does an average user accumulate?** Direction 4's distillation viability depends on it. If <50, the warm-up is too long.
4. **Which Rust hot-path candidate is biggest?** Direction 7 doesn't pick one without measurement.
5. **What's the right adversarial benchmark?** Direction 3 + 5 both need it; nobody has shipped a canonical adversarial ER benchmark. Designing one is its own contribution.

---

## Closing

The shortest version: **the engine is already the most consistent ER engine in OSS. The next year's bets are about (a) lifting the scale ceiling, (b) emitting a graph instead of a clusters dict, (c) keeping the auto-config moat from being copied, (d) closing the LLM-dependence gap, (e) running competitors weekly so we know who's catching up.** Five engine moves, all measurable, none requiring us to invent new science.

Pick three. Sequence them. Measure them.
