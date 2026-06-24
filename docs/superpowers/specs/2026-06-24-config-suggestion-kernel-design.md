# Config-Suggestion Kernel — Design

**Date:** 2026-06-24
**Status:** Approved (brainstorm); pending plan
**Author:** Ben Severn (with Claude)

## Problem & vision

We want to push GoldenMatch users onto a clear path: **zero-config for the
first run, then iterate.** The user runs zero-config, sees the results, and the
system reviews those results against the config that produced them and offers
**ranked, explainable suggestions** for config edits that would make the next
run more accurate. The user accepts or rejects each suggestion; accepted ones
mutate the config, rejected ones train the suggester to stop offering them.

Today this surface is shallow and Python-only: the MCP `suggest_config` tool
requires the user to hand it bad-merge examples, runs ad-hoc heuristics inline
in the MCP server, and has no result-driven review, no accept/reject loop, and
no learning. The auto-config *decision* logic that does exist
(`autoconfig_rules.py`, `autoconfig_planner_rules.py`) is pure-Python and runs
only during sample iteration inside the controller; the Rust `bridge::autoconfig`
delegates back into Python rather than owning any decision logic.

The goal: a **native source-of-truth kernel** for config suggestions so every
language surface (Python, SQL/DataFusion, TS) benefits from one implementation,
and a **benchmark harness** that makes the *intelligence* of the suggestions a
measurable, iterable number.

### Scope

In scope (v1): the **config-suggestion engine** — review run results, emit
ranked suggestions, accept/reject with learning, plus the benchmark harness.

Out of scope (filed as follow-up issues):
- Pair/cluster verdicts as the feedback signal (infer the guilty knob from
  approve/reject of specific match results).
- Direct config edits as feedback (user edits config; kernel predicts impact).
- Natural-language intent ("too many false merges on company names") → delta.
- Unifying the kernel with the live auto-config refit rule table (Approach B
  below) — the right *eventual* end state, but only after the suggestion rules
  are proven by the benchmark.

## Approach

Three approaches were considered:

- **A — New pyo3-free `suggest-core` kernel + thin bindings.** Follows the
  established `score-core` / `analysis-core` pattern: Rust is the canonical
  source of truth, parity is structural. Never touches the shipped, gated
  auto-config controller path. **Chosen.**
- **B — Promote the existing Python refit rule table into the kernel and reuse
  it for both internal refit and user-facing suggestions.** DRYest, but the
  biggest blast radius — rewrites logic on the live benchmark-gated controller,
  and those rules are shaped for *sample* signals during iteration, not
  *full-result* diagnostics. Rejected for v1; the right eventual end state.
- **C — Python-first prototype, harden to native later.** Lowest risk on
  suggestion quality, but defers (and risks never doing) the native ask and
  writes the decision logic twice. Rejected; its measurement discipline is
  folded into A instead.

**Decision: Approach A**, with C's measurement discipline folded in — build the
native kernel from the start, but make the benchmark harness a first-class
deliverable so we can iterate on suggestion quality, and gate the feature's
default-on flip behind that benchmark proving non-regression on real F1.

## Architecture

### The kernel: `suggest-core` (Arrow-in, pyo3-free)

New crate `packages/rust/extensions/suggest-core/` (`goldenmatch-suggest-core`),
pyo3-free, following the `score-core` model: Rust owns the logic; parity is
structural, not asserted after the fact.

The kernel **ingests the run's artifacts as Arrow** and does everything inward
of that — extraction, reduction, decision, and rationale generation. Nothing of
substance is duplicated per language. The repo already runs Arrow-in-Rust for
its native-direct kernels (the #509 graph/embed UDFs on DuckDB+DataFusion; the
whole pyarrow stack is on arrow 59), so this is a trodden lane.

Entry point (shape, not final signature):

```
suggest(
    scored_pairs:    RecordBatch,   // (id_a, id_b, score) — run already has this
    clusters:        RecordBatch,   // (cluster_id, size, confidence, quality, oversized)
    column_signals:  RecordBatch,   // one row per column: (field, col_type, scorer,
                                    //  identity_score, corruption_score, collision_rate,
                                    //  cardinality_ratio, null_rate, variant_rate)
                                    //  cardinality is a RATIO in [0,1], matching the
                                    //  existing cardinality_ratio used by autoconfig guards
    config:          ConfigSummary, // small struct (JSON)
    priors:          AcceptancePriors,
) -> RankedSuggestions              // structs incl. the rendered display string
```

Inside, in Rust: histogram the scores, compute mass-above / mass-just-below
bands, percentile the block sizes, read the collision/corruption signals, run
the decision rules, **generate the rationale text**, and rank. The reductions
reuse the `analysis-core` `histogram` / `quantile` kernels — no second
implementation of those.

**Rationale text is generated in the kernel, not per-language.** The explanation
string ("raise `name` threshold 0.82→0.88 because bad-merge mass clusters just
below it") must be identical on every surface, so the kernel emits it. Surfaces
only wrap it in their own UI chrome (a TUI panel, a JSON field, an MCP content
block).

### Consumers

1. **Python** — a thin `suggest.rs` `#[pyfunction]` module added to the existing
   `goldenmatch-native` crate (same way `score.rs` delegates to `score-core`).
   Ships in the `goldenmatch[native]` wheel. Python zero-copies its polars
   frames out via `.to_arrow()`.
2. **SQL / DataFusion** — hands the kernel its Arrow batches directly,
   native-direct, like the graph/embed UDFs. Promoted from "staged" to nearly
   free by the Arrow-in contract. *Staged for v1* (contract ready, wiring later.)
3. **TS** — a WASM build of `suggest-core` over Arrow IPC. *Staged for v1.*

### No-native behaviour

**Config suggestions are a `goldenmatch[native]` feature.** Pulling extraction
into the Arrow kernel means there is exactly one implementation; we deliberately
do **not** maintain a pure-Python extraction mirror (that would re-duplicate the
thing the Arrow kernel exists to unify). Without the native wheel, every surface
emits a clear "install `goldenmatch[native]` for config suggestions" message and
degrades to today's shallow `suggest_config`. "Parity" therefore means the kernel
is deterministic against fixtures, not Rust-vs-Python.

### Boundary

The shipped, benchmark-gated auto-config controller path is **never touched** —
the suggestion engine reads run *outputs*; it does not reach into the controller.
Learning re-enters only as the `priors` input, keeping the kernel a stateless
pure function of `(diagnostics, config, priors)`.

## v1 suggestion rules

Three high-signal, ground-truth-free rules. Each reads diagnostics the run
already produces and emits a
`Suggestion { kind, target, current_value, proposed_value, rationale, evidence,
predicted_effect, confidence, config_patch, fingerprint }`.

**1. Threshold raise / lower** — driven by the score histogram over
`scored_pairs`:
- *Bimodality dip:* if the score distribution has a clear valley and the current
  threshold isn't in it → suggest moving to the dip.
- *Mass bands:* high `mass_above_threshold` (the "everything matches" pathology
  the controller already guards) → suggest **raise**; significant mass just
  below threshold on **weak/oversized** clusters → recall risk → suggest
  **lower**.

**2. Scorer swap** — the shipped noise-aware (#662) logic, surfaced as a
suggestion instead of a silent auto-config default. A free-text / address / name
column with high `corruption_score` / `variant_rate` still scored by
`token_sort` → suggest `jaro_winkler`. This is the NCVR `res_street_address`
lever (F1 0.871→0.981), the single biggest known win, now offered with its
evidence.

**3. Add negative evidence** — the collision signal: an identity-ish column
(`identity_score ≥ 0.75`, `cardinality ≥ 0.5`) not already negative evidence,
showing a high in-cluster `collision_rate` → suggest adding it as NE. Mirrors
`compute_identity_collision_signal` / `promote_negative_evidence`, surfaced with
evidence.

**Staged (designed, not built in v1):**
- *Add / drop blocking pass* (recall via block-size percentiles + orthogonal
  keys).
- *Field-weight adjustment* — needs per-field pair scores materialized, which
  the pipeline does not emit today (it is why `MemoryLearner._compute_weights`
  is stubbed). Filed as the bridge issue; it also unblocks the weight-learning
  stub.

The unifying thread: every v1 rule is a signal the codebase *already computes*
but currently either applies silently (scorer swap), only during sample
iteration (collision), or not at all post-run (threshold). The kernel turns
those latent signals into explicit, explainable, user-approvable suggestions.

## Learning loop (accept / reject → priors)

Each `Suggestion` carries a declarative **`ConfigPatch`** (e.g.
`set matchkey["name"].threshold = 0.88`; `set field["addr"].scorer = jaro_winkler`;
`add negative_evidence "email"`). The kernel defines *what* to change once; each
language applies the patch to its own native config object (Python: Pydantic
`model_copy`; YAML save is atomic via `os.replace`, mirroring `web/rules.py`).

**Persistence reuses `MemoryStore`** — a new `suggestion_feedback` row
(suggestion fingerprint, `kind`, target signature, decision, dataset,
timestamp), alongside corrections and learned adjustments. The **fingerprint** is
a stable hash of `(kind, target-signature, patch shape)` so accept/reject
re-anchors across runs — the same durability idea as `record_hash` for
corrections.

**Priors close the loop without ML in v1:** before each `suggest()`, the adapter
loads accept/reject counts and passes `AcceptancePriors` in; the kernel
(a) suppresses a `(kind, target)` the user keeps rejecting, and (b) nudges
ranking by net acceptance. Counts → multiplier + suppression threshold. The
kernel stays a pure function — fully golden-vector testable. ML ranking is a
future lever.

## Benchmark & iteration harness

A first-class deliverable, not just an end gate — the value of the kernel is the
*quality* of its suggestions, so suggestion intelligence must be measurable and
iterable.

`scripts/suggest_quality/` mirrors the proven `scripts/autoconfig_quality`
harness: `report` / `gate` / `bless` ergonomics, deterministic posture
(`GOLDENMATCH_AUTOCONFIG_MEMORY=0`, fixed seeds). Datasets are the labeled ER
sets with ground truth: Febrl3/4, DBLP-ACM, NCVR sample, `historical_50k`,
synthetic.

### The measured loop, per dataset

1. Zero-config → run dedupe → record baseline F1.
2. Assemble diagnostics → kernel emits ranked suggestions.
3. **Oracle enumeration:** actually apply *every candidate edit the kernel could
   propose* (threshold sweep, each scorer swap, each NE candidate), re-run
   dedupe, measure the true F1 lift of each. This gives ground truth for "which
   suggestions are good" and the oracle-best edit.
4. Score the suggester against that oracle.

### Metrics

- **North star — oracle-ranking correlation:** rank correlation between the
  suggester's order and the measured F1 lift of each candidate. A smart
  suggester ranks the highest-impact edit first.
- **Suggester precision:** fraction of emitted suggestions that, when applied,
  do **not** regress F1. A suggestion that drops F1 is a bug.
- **Convergence-to-ceiling:** accept top suggestion, re-run, repeat until no
  positive suggestion remains; report final F1 vs the known hand-tuned /
  zero-config ceiling and the step count.

### Regression anchors

The harness pins specific known wins as hard anchors. Above all: on NCVR, rule
#2 **must** emit the `res_street_address` token_sort→jaro_winkler swap as its #1
suggestion (F1 0.871→0.981), or the bench fails. Same posture for the DBLP-ACM
and `historical_50k` gaps.

### Ergonomics & CI

`report` runs the loop and prints the per-dataset table + a headline
suggester-score. `bless` snapshots current scores as the baseline. `gate` (CI,
`workflow_dispatch` + on `suggest-core` changes, `large-new-64GB` runner) fails
on regression vs blessed baseline. Dev loop for improving the kernel: tweak a
rule → `report` → watch rank-correlation / convergence move → `bless` when
better. The feature's default-on flip is gated behind this bench proving
non-regression on real F1.

## Surfaces (v1 wiring)

- **Python API:** `review_config(result, config) → RankedSuggestions`,
  `apply_suggestion(config, suggestion) → config`,
  `record_suggestion_feedback(suggestion, decision)`.
- **CLI:** `goldenmatch suggest` (review last/named run → table) with an
  interactive accept/reject loop that writes the updated `goldenmatch.yml`
  atomically.
- **MCP:** upgrade `suggest_config` to the result-driven engine (keep
  `bad_merges` as one back-compat evidence source), add `accept_suggestion` /
  `reject_suggestion`; bump the server-card tool count. The plan must make the
  `server.json` → MCP-registry sync an explicit task (lockstep footgun per
  CLAUDE.md), not an afterthought.
- **TUI:** a Suggestions panel in the Config tab post-run, reusing the Ctrl+T
  triage accept/reject pattern.
- **Staged (cheap later, by design):** SQL/DataFusion (Arrow-direct), TS (WASM),
  Web/REST/A2A.

## Data contract & testing

- **Contract** lives in `suggest-core` (Rust structs + serde JSON); Python typed
  wrappers mirror it; the boundary is JSON/Arrow.
- **Tests:** per-rule kernel unit tests; **golden-vector fixtures** (JSON inputs
  → expected suggestions) pinning determinism; the suggester-quality bench;
  Python-binding tests; CLI/MCP integration; and a native-absent degradation
  test asserting the clean "install `[native]`" message.

## Open questions for planning

- Confirm whether any pyo3-free autoconfig decision crate already exists. The
  `project_autoconfig_native_core` memory note claims one shipped ("PyPI native
  0.1.11"), but `bridge::autoconfig` delegates to Python and no autoconfig crate
  was found under `packages/rust/extensions/`, so the note may be stale or refer
  to specific small levers. Resolve in planning before assuming `suggest-core` is
  the first decision crate — it affects whether we extend an existing crate or
  go greenfield.
- Exact column set for the `column_signals` batch and which already-computed
  artifact each field comes from (`ComplexityProfile`, indicators, clusters).
- Whether `scored_pairs` is materialized at the size needed post-run for all
  backends, or whether the histogram should be read from `ComplexityProfile`
  where the run already computed it.
```
