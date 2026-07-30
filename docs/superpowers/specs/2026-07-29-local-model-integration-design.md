# Design Spec: Local-model integration — self-hosted boost + closed-loop refit

- **Date:** 2026-07-29
- **Status:** DRAFT (design only)
- **Author:** Claude (with benzsevern)
- **Companion spec:** `2026-07-26-oss-er-matcher-llm-boost-design.md` (the *model* — training, sizing, distribution, eval gates). This spec is the *integration* half: how a self-hosted model plugs into GoldenMatch's existing seams and how its verdicts feed back into the config. Read them together.
- **Touches:** `goldenmatch/core/llm_scorer.py`, `goldenmatch/core/pipeline.py`, `goldenmatch/config/schemas.py` (`LLMScorerConfig`), `goldenmatch/core/review_queue.py`, `goldenmatch/core/probabilistic.py` (`estimate_m_from_labels`/`EMResult`), `goldenmatch/core/memory/store.py` (`Correction`).

## 1. Summary

Wire a **self-hosted local model** into GoldenMatch as a first-class option for the
borderline-band adjudication tier, and **close the loop** so the tier's verdicts
recalibrate the config instead of being thrown away after a single run.

The headline finding from mapping the codebase: **this is wiring, not a new
subsystem.** GoldenMatch already has all three seams:

1. **A borderline-band → adjudicator tier already exists.** The optional
   LLM-scorer stage (`LLMScorerConfig.enabled`, default off) already carves the
   uncertain band (score in `[candidate_lo=0.75, candidate_hi=0.95]` → send to the
   adjudicator; `> auto_threshold=0.95` → auto-accept) and already fans out with
   `batch_size`/`max_workers`. Today it is backed by hosted API providers
   (`openai`/`anthropic`). A self-hosted model is a **new provider for the
   existing tier**, not a new stage.
2. **The review queue already exists.** `review_queue.gate_pairs(...)` uses the
   same 0.75–0.95 band and there is a `ReviewQueue` (memory / SQLite) whose
   verdicts flow to a `MemoryStore` (SQLite / Postgres) via the `Correction`
   sink. Opt-in human triage is wiring, not new infra.
3. **The refit primitive already exists.** `estimate_m_from_labels(df, mk, labels)
   → EMResult`, then `compute_thresholds()` for the recalibrated boundary.
   `EMResult` is exactly the "suggested refined config" object to hand back
   (persist via `EMResult.save_json`, reuse via `MatchkeyConfig.model_path`).

So the feature collapses to three concrete deliverables:

- **(D1) A self-hosted provider** for the existing `llm_scorer` tier, packaged as
  an optional extra, with an `_llm_loader.py` mirroring the `_native_loader.py`
  pattern (download-on-first-use + local-path override + pinned/verified artifact).
- **(D2) Close the loop:** route the tier's verdicts into `estimate_m_from_labels`
  → return a refined `EMResult`/`GoldenMatchConfig` as the **suggested** config
  (default), with `auto_refit=True` to apply it in-run.
- **(D3) Internal parallelism** for local throughput — extend the existing
  `max_workers` fan-out to a multiprocessing path for the self-hosted model (the
  hosted path is network-bound and stays threaded).

**Default posture: full-auto.** The boost runs and auto-applies its verdicts by
default (D1). The review queue (D2's human variant) and closed-loop *auto*-refit
are both **opt-in** — full-auto returns the refined config as a *suggestion*
without mutating the live run unless asked.

## 2. On-mission check (North Star + architecture frame)

- **zero-config:** a self-hosted boost that needs no API key and no network makes
  the one remaining paid-dependency capability work out of the box.
- **never-black-box:** the loop is inspectable — the model's verdicts become
  labels, the labels become an `EMResult` you can print, diff, and persist.
- **approach-the-expert:** the closed loop is the mechanism by which zero-config
  *improves itself* on a dataset instead of running one static config forever.
- **One authoritative semantic owner per capability:** we are **not** adding a
  second adjudication tier or a second refit path. D1 adds a provider under the
  existing `llm_scorer` owner; D2 reuses `estimate_m_from_labels` (the existing
  supervised-m owner) verbatim. No new source of truth.
- **Arrow at bulk boundaries, not the universal calling convention:** the
  adjudicator sees a small `list` of borderline pairs (a fraction of candidates),
  not an Arrow batch — the existing pair-list contract is correct; do not
  Arrow-ify it.
- **Compute vs. control stay distinct:** the boost is compute; the *refit
  decision* (suggest vs. apply, and persisting a corrected `EMResult`) is control
  and rides the existing memory/`Correction` + `model_path` machinery.

## 3. The three seams (verified against source)

### 3.1 The tier — `LLMScorerConfig` + the pipeline stage

`config/schemas.py::LLMScorerConfig` (verified): `enabled: bool=False`,
`provider: str|None=None` (currently `openai`/`anthropic`, auto-detected),
`model`, `auto_threshold=0.95`, `candidate_lo=0.75`, `candidate_hi=0.95`,
`batch_size=75`, `max_workers=5`, plus a `budget` and `mode` (`pairwise`/`cluster`).

`core/pipeline.py` (verified): every backend gates the stage on
`config.llm_scorer and config.llm_scorer.enabled and all_pairs` and calls
`llm_score_pairs(all_pairs, df, config=config.llm_scorer, ...)`.

`core/llm_scorer.py` (verified): `_openai_base_url()` already reads
`GOLDENMATCH_LLM_BASE_URL` / `OPENAI_BASE_URL`, and `_detect_provider()` already
returns `("openai", "sk-local")` when a base URL is set with no key — i.e. the
**local OpenAI-compatible-server path (companion spec "Path A") is already wired**
and needs only docs + a convenience launcher.

### 3.2 The review queue — `review_queue.gate_pairs` + `Correction`

`core/review_queue.py::gate_pairs(pairs, merge_threshold=0.95,
review_threshold=0.75, ...)` (verified): splits scored pairs into
`(auto_merged, review, auto_rejected)` on the same band; `ReviewQueue`
(`backend="memory"` or `"sqlite"` only — verified; it raises on anything else)
holds `ReviewItem`s; verdicts flow to `MemoryStore` (SQLite / Postgres — the
durable backend lives here, **not** on `ReviewQueue`) via `Correction` /
`add_correction`.

### 3.3 The refit primitive — `estimate_m_from_labels` → `EMResult`

`core/probabilistic.py::estimate_m_from_labels(df, mk, labels, *,
n_sample_pairs=10000, blocking_fields=None, ...) → EMResult` (verified): the
supervised analogue of `train_em` — given known true-match pairs, m is the
observed comparison-level frequency (no EM iteration, no convergence risk). Its
docstring already states the persistence contract: "Persist it with
`EMResult.save_json` and reuse via `MatchkeyConfig.model_path`." `compute_thresholds()`
turns the recalibrated weights into the operating boundary.

## 4. Deliverable D1 — self-hosted provider + `_llm_loader.py`

Two wiring paths, same prompt/IO contract as the companion spec (§8 there).

- **Path A (ships first, ~0 core code):** a self-hosted OpenAI-compatible server
  (llama.cpp / Ollama / vLLM). Set `provider="openai"` +
  `GOLDENMATCH_LLM_BASE_URL=http://localhost:...`; the existing `_detect_provider`
  supplies the stub key. **Repo work: docs + a `goldenmatch llm serve-local`
  convenience command** that launches the pinned model. No scorer changes.
- **Path B (in-process, no server):** a new optional extra
  `goldenmatch[local-llm]` (`llama-cpp-python`; the GGUF is pulled from a GitHub
  Release asset via stdlib `urllib` — no `huggingface_hub` dependency) and a new
  `provider="local"` branch. A `LocalLlamaAdapter` implements the same
  prompt→`{match, confidence, reason}` contract in-process. The model artifact is
  published to a GitHub Release by the `publish-er-matcher` workflow
  (Modal → GH), so users pull it from GitHub.

**`_llm_loader.py` mirrors `core/_native_loader.py`:**

| `_native_loader.py` idiom | `_llm_loader.py` analogue |
|---|---|
| `GOLDENMATCH_NATIVE=auto/0/1` gate | `GOLDENMATCH_LOCAL_LLM=auto/0/1` gate |
| discover order: in-tree → wheel → pure-Python fallback | discover order: local path override → HF cache → download-on-first-use → abstain |
| pinned + verified (companion spec: sha256 after download) | same pin `(repo_id, revision, filename, sha256)`, verified post-download |
| graceful degradation (fallback path) | **graceful abstain** — a load/parse failure contributes nothing to the boost, never crashes the pipeline (mirrors the current hosted fallback: no key → unscored) |

Provider validation: `provider` is a free `str` today (runtime-dispatched, not a
Pydantic enum), so adding `"local"` is a new dispatch branch in `llm_scorer.py`,
not a schema migration. Keep the openai/anthropic branches untouched (byte-identical when `provider != "local"`).

## 5. Deliverable D2 — close the loop (the genuinely new piece)

This is the part the companion spec does **not** cover. Today the tier's verdicts
adjudicate the current run's borderline pairs and are then discarded. Closing the
loop turns those verdicts into **labels** that recalibrate the config.

**Not to be confused with the controller `RefitPolicy` family.** The auto-config
controller already has a `RefitPolicy` line (`HeuristicRefitPolicy`,
`LLMRefitPolicy`, … in `core/autoconfig_policy.py`) that *refits the config* by
re-proposing rules from complexity signals during auto-config iteration — a
signal-driven, label-free mechanism. D2 is a distinct thing: a **label-driven m
re-estimation** that reuses `estimate_m_from_labels` (the supervised-m owner)
with verdicts as labels. It does not add a new refit *owner* — it feeds the
existing supervised primitive. Keep the two vocabularies separate in code (e.g.
name D2's surface `refit_from_labels` / `auto_refit`, never `RefitPolicy`).

**Flow:**

1. The boost adjudicates the borderline band → per-pair `{match: bool,
   confidence}` verdicts (D1). Confident matches (and, symmetrically, confident
   non-matches) are the label set.
2. Feed the confident-match pairs to `estimate_m_from_labels(df, mk, labels)` →
   a refined `EMResult` (supervised m, no EM convergence risk).
3. `compute_thresholds()` on the refined result → the recalibrated operating
   boundary.
4. Package the refined `EMResult` (+ threshold) as the **suggested**
   `GoldenMatchConfig`, returned to the caller (surfaced on the result object).

**Default = suggest, not apply ("Both, gated").** The `auto_refit` kwarg on
`dedupe_df` is tri-state: `False` (default) is off; `True` / `"suggest"` attaches
the refined `EMResult` to `DedupeResult.refit_suggestion` and leaves the run
unchanged; `"apply"` is the stronger opt-in that persists the refined `EMResult`
(`save_json` → `model_path`) and re-runs scoring+clustering **once** with it in
the same call, returning that second pass (suggestion still attached). Suggest
reads the corrections THIS run persisted to the configured `MemoryStore` (via
`refit_from_memory`); a run with no probabilistic matchkey / no memory / no
confident labels is a no-op (`refit_suggestion=None`).

**Reuse, don't reinvent:**
- Confidence gate on which verdicts become labels: reuse the tier's
  `auto_threshold` semantics (a verdict is a label only when the model is
  confident), so the label set is precision-first — a wrong label poisons m.
- Persistence: the refined `EMResult` persists via `EMResult.save_json` and is
  reused across runs via `MatchkeyConfig.model_path` (the existing contract), and
  the underlying verdicts can also flow to `MemoryStore` as `Correction`s
  (`source` = the boost) so the learning-memory layer sees them like any other
  correction.
- **Label hygiene (the correctness gate):** only *confident* verdicts become
  labels; `estimate_m_from_labels` already Laplace-smooths (`smoothing=1.0`) so a
  level unseen among few labels can't force `m=0` (an infinite weight). Blocking
  fields stay excluded from m-estimation (`blocking_fields=`), matching `train_em`.

**Human variant (opt-in):** instead of the model's verdicts, route the borderline
band through `gate_pairs` → `ReviewQueue`; steward verdicts become the label set
for the same `estimate_m_from_labels` refit. Same loop, human-in-the-middle. The
review queue and the model boost are two sources of the *same* label stream, not
two loops.

## 6. Deliverable D3 — internal parallelism

The hosted path is network-bound; `max_workers` threads already give real
concurrency (I/O wait). A self-hosted CPU model is **compute**-bound, so threads
contend on the GIL / a single llama.cpp context. Extend the existing `max_workers`
fan-out with a **multiprocessing** path selected when `provider="local"` (a pool
of worker processes each holding a model context, or a batched server with
`n_threads`). The hosted path stays threaded (byte-identical). This is a boost on
a *fraction* of pairs (the borderline band), not every pair — so throughput needs
to be adequate, not maximal; measure the whole-path wall (companion spec §10
latency floor), not a per-call microbench.

## 7. Rollout phases

- **P0 (this spec).** Design + defaults for review.
- **P1.** D1 Path A: docs + `goldenmatch llm serve-local`; confirm the existing
  `GOLDENMATCH_LLM_BASE_URL` path end-to-end against a local server. Zero core risk.
- **P2.** D2 the closed loop in **suggest** mode (return refined `EMResult` as a
  suggestion). No behavior change to a run; purely additive on the result object.
- **P3 (shipped).** D2 `auto_refit` tri-state on `dedupe_df` — `True`/`"suggest"`
  attaches `DedupeResult.refit_suggestion`; `"apply"` persists via `model_path`
  and re-runs once. Label source = this run's `MemoryStore` corrections.
- **P4.** D1 Path B: `goldenmatch[local-llm]` in-process `LocalLlamaAdapter` +
  `_llm_loader.py`, skip-guarded test (mock the adapter or a tiny fixture model).
- **P5.** D3 multiprocessing for the local provider.
- **P6 (opt-in surface).** Wire the human-review variant of the loop (gate_pairs →
  ReviewQueue → refit) as an explicit mode.

Each phase is independently shippable and off-by-default until enabled.

## 8. Defaults for review (open decisions)

Local Claude's proposed defaults, called out for sign-off — these are the choices
that shape the implementation:

1. **Full-auto by default; review queue + auto-refit opt-in.** The boost runs and
   auto-applies verdicts; the closed loop *suggests* a refined config without
   mutating the run unless `auto_refit=True`. Confirm.
2. **`auto_refit` default = off (suggest).** A refit silently mutating the
   operating point on every run is surprising; suggest-by-default keeps the run
   reproducible and lets the caller opt into applying it. Confirm.
3. **Label set = confident verdicts only** (gated on `auto_threshold`), and both
   confident-match and confident-non-match verdicts feed m-estimation (not just
   matches). Confirm — or restrict to confident matches only for v1.
4. **Base model / artifact:** inherited from the companion spec (lean
   Qwen2.5-3B, Apache-2.0). No new decision here.
5. **Cross-repo boundary (resolved):** the model artifact is published as a
   **GitHub Release asset** (trained on Modal → uploaded by the
   `publish-er-matcher` workflow); this repo holds only the loader, prompt
   template, pinned `(url, filename, sha256)`, and the wiring — the GGUF bytes are
   never in the git tree.

## 9. Risks & mitigations

| Risk | Mitigation |
|---|---|
| A wrong model verdict poisons the refit (bad m) | Label hygiene: only confident verdicts become labels; Laplace smoothing guards unseen levels; suggest-by-default keeps a bad refit out of the live run until reviewed |
| `auto_refit` makes runs non-reproducible | Default off; when on, the refined `EMResult` is persisted (`save_json`) + pinned via `model_path` so a run is replayable |
| Local CPU model too slow | It's a boost on the borderline band (a fraction of pairs), batched; D3 multiprocessing; companion spec latency floor gates "too slow to be default" |
| Second adjudication/refit path drifts from the owner | No new owner: D1 is a provider under `llm_scorer`; D2 reuses `estimate_m_from_labels` verbatim |
| Adding `provider="local"` breaks openai/anthropic | New dispatch branch only; `provider != "local"` is byte-identical; guarded by tests |
| Loader/model download failure crashes a run | Graceful abstain (mirrors the no-key hosted fallback): the boost contributes nothing, never raises |

## 10. Open questions

1. Sign-off on the §8 defaults (esp. #1–#3).
2. Should the refined `EMResult` from D2 auto-persist to `model_path` in
   *suggest* mode, or only when `auto_refit=True`? (Lean: persist only on apply,
   to keep suggest side-effect-free.)
3. Does the human-review variant (P6) share one `auto_refit` switch with the model
   loop, or get its own explicit "refit from steward labels" command?
4. D3: worker-process pool vs. a single batched local server — pick after the D1
   throughput measurement, not before.
