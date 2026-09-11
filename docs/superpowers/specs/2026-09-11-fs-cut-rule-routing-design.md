# FS link-cut routing: pick the cut rule per dataset

**Status:** Design approved in brainstorming, 2026-09-11. Spec awaiting review.
**Builds on:** decision 0024 (auto-config probabilistic routing) and PR #2939 (50,000-pair `u`
sample and the `prior_mid` linear cut).

## Why

Every Fellegi-Sunter link-cut method tried on the `fs-lever-gate` panel helps some datasets and
hurts others. A single global default keeps getting re-litigated, while each method is a clear
win somewhere.

| method | helps | hurts |
|---|---|---|
| Otsu calibrator (`GOLDENMATCH_FS_CALIBRATE_THRESHOLD`) | dblp_acm +0.49, febrl3 +0.005, febrl4 +0.002 | dblp_scholar −0.14, ncvr_synthetic −0.05, historical_50k −0.01 |
| Posterior calibration | dblp_acm +0.49, amazon_google +0.04 | historical_50k −0.32, dblp_scholar −0.17, household / cotenant −0.02 |
| Fixed evidence cut, best `c` per dataset | febrl3 at 3; febrl4, ncvr_synthetic, amazon_google at 9; dblp_acm at 12 | historical_50k and dblp_scholar do best at the default |
| `prior_mid` (PR #2939) | ncvr_synthetic, dblp_acm, amazon_google | none measured, but fixed rather than tailored |

Decision 0024 already routes one choice this way, deterministic vs probabilistic:
1. measure both strategies per dataset;
2. write a label-free trigger;
3. accept it only if it never routes a dataset below the default.

This design applies the same loop to how the link cut is placed.

## Decisions

- **Signals.** The router reads the column profile plus trained-model diagnostics. It does not decide on the static profile alone, and it is not a learned meta-model.
- **Gate.** A routed rule must never score below the shipped default's F1 on any corpus dataset, with the harness's 0.01 tolerance. This is the decision-0024 rule.
- **Corpus.** Rows are written against a *design set* and must also pass a *held-out set* that was never used to write them.
- **v1 scope.** Link-cut placement only. Blocking-pass routing is deferred: a pass changes EM's training population and shifts λ, which moves prior-based cuts.
- **Approach.** A post-EM decision table.
  - Rejected: controller observe-act rules, which fire only on failed health checks and miss the non-iterative zero-config path.
  - Rejected: a sample tournament on a label-free quality proxy. Three such selectors have already failed on this code (histogram anti-mode, λ-quantile, Otsu as a default).

## Architecture

### Cut rules are evidence cutoffs in bits

`W` is a pair's summed match weight, including negative evidence and TF adjustment. `lo` and `hi` are the summed per-field minimum and maximum weights, plus the negative-evidence range. They are the same envelope the linear score normalizes against:

    score = (W - lo) / (hi - lo)

A new module, `goldenmatch/core/fs_cut_rules.py`, defines each rule as a function of the trained model (and the matchkey) that returns a cutoff in bits:

| rule | links when | note |
|---|---|---|
| `midpoint` | `W >= (lo + hi) / 2` | today's fixed 0.50 linear cut |
| `prior_mid` | `W >= max(log2((1-λ)/λ), (lo+hi)/2, 0)` | the #2939 default |
| `posterior_099` | `W >= log2(99) + log2((1-λ)/λ)` | where `σ(prior + W) = 0.99`, the posterior default |
| `evidence_3`, `evidence_5`, `evidence_9`, `evidence_12` | `W >= c` | fixed evidence bars |
| `otsu` | `W >= lo + t·(hi - lo)` | `t` is the existing training-pair Otsu split, clamp included; needs the P2 training histogram, so until P2 a pinned `otsu` falls back to the default with reason `no training histogram` |

All of them are monotone in `W`. So each converts to the equivalent linear normalized cutoff,
`t = clamp((bits - lo) / (hi - lo), 0, 1)`, and every scorer applies it unchanged, the native
kernel included. **No scorer or kernel changes.** Scores stay on the linear scale. The existing
positive-evidence guard (`W > 0`, default on) still applies.

### Resolution and precedence

`_fs_link_threshold` and `resolve_thresholds` resolve the link cut through one shared helper, in
this order:

1. explicit `mk.link_threshold`;
2. an EM-calibrated cutoff (`calibrated_link_threshold`);
3. a pinned `mk.link_cut_rule`;
4. the router's choice, when the router is enabled;
5. the shipped default rule: `prior_mid` if #2939 has landed, else `midpoint`.

Explicit user choices that change the **score scale** bypass the router entirely and behave as
today: `GOLDENMATCH_FS_CALIBRATED=posterior` and `GOLDENMATCH_FS_EVIDENCE_CUT`.
`GOLDENMATCH_FS_CALIBRATE_THRESHOLD=1` keeps producing a calibrated cutoff (step 2).
`GOLDENMATCH_FS_LINEAR_CUT` becomes a process-wide way to set the default rule (`off` → `midpoint`).

### Matchkey field

`MatchkeyConfig.link_cut_rule` is a `Literal` over the rule names, or `None`.

- `None` means the router decides, or the default when the router is off.
- A value pins the rule; an unknown value is a config validation error.

This lets the choice be reported, persisted, stamped by the controller when appropriate, and differ between matchkeys in one run.

### Router

`choose_cut_rule(diagnostics) -> (rule, reason)` is a pure function over an ordered table of rows.

- Each row is a condition on diagnostics, the rule it picks, and a human-readable reason.
- The first matching row wins.
- The last row is the shipped default.

It is unit-testable without data and gated by the harness.

### Reporting

The per-matchkey cutoff report (`fs_link_thresholds`) gains `cut_rule` and `cut_reason` next to
`link_threshold`, `source` and `refit`.

- Source values stay as they are (`configured`, `calibrated`, `evidence_rule`, `fallback`).
- A routed or pinned rule reports `evidence_rule`.
- The controller still stamps only `calibrated` cutoffs (#2637), and the #2483 warning still fires only for `fallback`.

## Router inputs

Every input is available right after EM, with no extra scoring pass:

- **Model statistics:** λ, `lo`, `hi`, the midpoint and prior cutoffs in bits, per-field weight spans, and whether negative-evidence fields are present.
- **Training-sample weight histogram:** a compact histogram of `W` over EM's blocked training pairs, computed in `train_em` (the same population the Otsu calibrator already reads) and stored on `EMResult`. It gives each candidate rule's estimated admitted fraction without scoring.
- **Profile:** column type mix and `DomainProfile.detected_domain`.

Not used in v1:

- `ZeroLabelConfidenceProfile` needs a full score-and-cluster pass, so it is not available when the cut is resolved.
- Lowering the scoring floor to measure the real candidate distribution would widen the review band, which is held in driver memory.

The router runs where the link cut is resolved. The valley-gated threshold refit (default on) still runs afterwards and may still raise the cut; the gate measures the combination end to end.

## Writing table rows: evidence first

1. **Measure.** For every dataset in the design and held-out sets, the harness records F1, precision and recall for each candidate rule (labelled), next to the diagnostics above (label-free).
2. **Propose a row.** Pick a condition on diagnostics and the rule it selects, from the design-set matrix only. Row authors read only the design-set results. Held-out results are produced and stored separately, as a gate-only artifact, and are read only when a proposed row is gated.
3. **Gate the row.** On every design dataset the routed rule must score at least `default − 0.01`; then the same on every held-out dataset. A row that fails either set is dropped, not tuned against the held-out set.
4. **Order the rows.** The first match wins, and the last row is the shipped default.

Starting hypotheses from the current panel. They are candidates to test, not decisions:

| condition | rule | evidence |
|---|---|---|
| λ < 0.005 and a wide envelope | `evidence_12` | dblp_acm: λ 0.002, best at cut 12 (0.9077 at 50k `u`) |
| λ > 0.5 | `midpoint` | historical_50k: λ 0.661, prior cut −0.96 bits, midpoint +10.8 binds |
| midpoint < 0 and small λ | `prior_mid` | ncvr_synthetic, household_hardneg, cotenant_hardneg |

A dataset whose winning rule no label-free signal separates gets the default, by design.

## Corpus and harness

- **Design set:** the 13-dataset `scripts/autoconfig_quality` registry: person, sparse_zip, shared_email, household_hardneg, cotenant_hardneg, febrl3, febrl4, ncvr_synthetic, historical_50k, dblp_acm, dblp_scholar, amazon_google, plus ncvr_real where available locally.
- **Held-out set**, frozen before any row is written:
  - more public labelled ER benchmarks (candidates: Abt-Buy, Walmart-Amazon, Fodors-Zagat, febrl1/2), with licence and availability checked per dataset before anything is fetched or vendored;
  - synthetic person and biblio variants from `scripts/bench_er_headtohead/generate_fixture.py`, sweeping corruption rate and duplicates per entity.

  Changing the held-out set after rows exist means re-running the full gate.
- **Per-rule block:** for each dataset, train EM once (cached via `mk.model_path`), then run the pipeline once per candidate rule with the rule pinned through `link_cut_rule`. Record F1, precision, recall and the diagnostics in a separate per-rule scorecard, in the same format as the harness scorecard. They do not go into the committed baseline: that baseline is diffed by the required `quality_gate` job, and its time budget cannot hold eleven runs per dataset.
- **Where it runs:** large datasets run in CI or on the homelab, never on a laptop.

## Gate

A table row, and later the router default flip, is accepted only when **all** hold:

- **Design set:** never below the default, within 0.01.
- **Held-out set:** never below the default, within 0.01.
- **Existing gates:** `quality_gate`, `bench-suggest-quality` (gate) and `bench-quality-scale` (QIS) pass.

## Error handling

All failures degrade to the shipped default and say why:

- **Degenerate model:** λ ≤ 0 or ≥ 1, or `hi <= lo`, gives the default with reason `degenerate model`.
- **Older saved model:** an `EMResult` loaded from JSON without the training histogram gives the default with reason `no training histogram`. Older models stay valid.
- **Stub model:** a model object without `match_weights` falls through to the fixed default.
- **Invalid pinned rule:** an invalid `link_cut_rule` raises at config validation.
- **Unknown env value:** an unrecognised router env value warns and uses the default.

## Testing

- **Rule arithmetic:** each rule's bit cutoff on hand-built models, and the bits-to-normalized conversion with clamping.
- **Precedence:** `_fs_link_threshold` and `resolve_thresholds` agree at every precedence step.
- **Router rows:** tests on synthetic diagnostics, each with a known-positive case that fails if the row is removed.
- **Model histogram:** it round-trips through `EMResult.save_json` / `load_json`, and older JSON without it loads.
- **Cutoff report:** it carries `cut_rule` and `cut_reason`.
- **Pinning:** tests that pin routing, shedding or guard behaviour set the rule explicitly.
- **TS parity:** the emitter pins the rule, as it does for the positive-evidence filter today.

## Rollout

| phase | adds | behaviour |
|---|---|---|
| P1 | `fs_cut_rules.py`, `link_cut_rule`, resolver integration, reporting | router off; default rule unchanged |
| P2 | training-sample histogram on `EMResult`, diagnostics assembly | router off |
| P3 | held-out corpus and the per-rule harness block | measurement only |
| P4 | table rows written from evidence, each gated | router behind `GOLDENMATCH_FS_CUT_ROUTER`, default off |
| P5 | router default on once the gate holds on both sets | env kept as a kill switch |

- P1 to P3 are independently useful even if no row passes: pinnable rules and a per-rule measurement matrix.
- P1 lands after #2939 so that `prior_mid` is the default rule it preserves.
- P1 also checks which parity gates a new `MatchkeyConfig` field and a new `EMResult` field require: the Python↔TypeScript API gate, the config matrix, and Spark EM's `EMResult`.

## Risks and open questions

- **Biased training sample.** The histogram comes from EM's blocked training pairs, not the scored candidates. The refuted λ-quantile selector read the same population. Rows built on it are only as good as the gate says.
- **λ depends on the route.** On the bucket-vs-legacy e2e fixture (8 rows) the two routes' EM estimate λ at 0.99 and 0.05. Rules that read λ inherit that sensitivity. No panel dataset moved because of it, but the gate is the check.
- **Refit interaction.** The valley refit may raise any routed cut. Rows are judged end to end, so a rule that only helps before the refit will not pass.
- **Small corpus.** Even with a held-out set, a few dozen datasets is a small base. Prefer few, simple rows with a clear mechanism over many narrow ones.
- **Out of scope:** routing the score scale (posterior vs linear), routing blocking passes, and a TypeScript port of the rules. Each is a later, separate design.

## References

- **Prior routing design:** `context-network/decisions/0024-autoconfig-probabilistic-routing.md` and `docs/superpowers/specs/2026-06-23-autoconfig-probabilistic-routing-design.md`.
- **PR #2939**, 50k `u` sample and `prior_mid`, gate results:
  - `quality_gate`: ncvr_synthetic f1 0.9901 → 0.999; historical_50k f1 0.8129 → 0.8339;
  - `bench-suggest-quality`: pass;
  - QIS: pass, every rung at F1 1.0.
- **`prior_mid` A/B:** `fs-lever-gate` run 34551694684.
- **Per-cut sweeps at 50k `u`:** runs 34547728440, 34547824612, 34547826392, 34547828074, 34547829753.
- **Otsu calibrator on the widened panel:** run 34529465555.
