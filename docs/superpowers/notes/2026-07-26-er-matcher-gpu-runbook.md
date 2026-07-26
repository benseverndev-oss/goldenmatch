# ER-matcher GPU + publish runbook (P3/P4)

The in-sandbox pieces are built + tested (P1/P2/P5/P6 merged; P3 trainer + P3a gate
+ P4 workflow in this PR). This runbook is the out-of-band sequence YOU run — the
GPU train + the publish — because they need infra the dev sandbox can't reach
(a Modal GPU + a GitHub Release). Every step is gated: nothing multi-hour or
irreversible fires without a passing check first.

Spec: `docs/superpowers/specs/2026-07-26-oss-er-matcher-llm-boost-design.md`
Plan: `docs/superpowers/plans/2026-07-26-oss-er-matcher-llm-boost-plan.md`

## 0. One-time setup

```bash
pip install modal && modal token new          # authenticate the Modal CLI
# store a ROTATED HF token as a Modal secret (NOT pasted into any file — spec §1):
modal secret create er-matcher-hf HF_TOKEN=<your-rotated-hf-token>
```

If a token was ever pasted into a chat/PR, **rotate it first** — treat it as public.

## 1. Generate the training data (CPU, local)

```bash
python scripts/er_matcher/gen_pairs.py --out-dir data/er_matcher   # writes train/val/test.jsonl + manifest.json
```

The JSONL is gitignored; the generator + seed are the reproducible source of truth.

## 2. P3a — smoke run + GO/NO-GO gate (cheap GPU)

```bash
modal run scripts/er_matcher/modal_train.py --smoke          # A10G, ~minutes, few-hundred steps
modal volume get er-matcher-out smoke_metrics.json .
python scripts/er_matcher/perf_report.py --metrics smoke_metrics.json \
    --total-steps <full-run-steps> --gpu-cost-per-hour-usd <A100-rate> --budget-usd 50
```

The gate checks: GPU util ≥ 90% (else the data/pack path is the bottleneck — fix
before spending), extrapolated $ ≤ budget, learning curve still climbing, peak
mem ≤ GPU capacity. **Only proceed to §3 on `GO`.** A `NO-GO` names the failing
check; fix it (e.g. more dataloader workers, bump GPU tier, more/less data) and
re-smoke.

## 3. P3b — full run (only after §2 says GO)

```bash
modal run scripts/er_matcher/modal_train.py                   # A100, right-sized from the smoke
modal volume get er-matcher-out model/merged ./er-matcher-merged
```

Reproducible: fixed seed + pinned base revision (set `base_revision` in
`scripts/er_matcher/config.yaml` before the run) + the committed config.

## 4. P5 — eval gate (CPU, local; the ship decision)

Serve the merged model locally (or a quick quantize) and run the eval harness:

```bash
python -m goldenmatch.core.er_matcher.serve_local --tier 3b --port 8080 &   # after §5 pins exist
python scripts/er_matcher/eval.py --data-dir data/er_matcher \
    --hosted-boost-f1 <hosted-baseline> --fs-baseline-f1 <fs-zeroconfig>
```

Ship **only if it wins**: pair-F1 clears the absolute floor, beats the hosted-LLM
boost, adds over the pure-FS baseline, and is calibrated. A model that doesn't win
does not ship — iterate corruption realism / data volume and retrain.

## 5. P4 — quantize + publish (CPU workflow)

Upload the merged dir somewhere the workflow can fetch (a private HF repo, or a
tarball URL), then:

```
Actions → "Publish ER-matcher GGUF" → Run workflow:
  model_source = <hf-repo-id or https tarball URL of the merged dir>
  tier         = 3b
  release_tag  = er-matcher-3b-v1
```

It converts → GGUF (`q4_k_m` + `q8_0`), sha256s them, uploads to the Release, and
**prints the exact `filename`/`url`/`sha256` pin**. Paste that into
`core/er_matcher/registry.py` `MODELS["3b"]` (replacing the `None` PENDING pins),
commit, and the loader (`resolve_model`) will download + verify on first use.

## 6. Flip Path A on

With the pins filled, `python -m goldenmatch.core.er_matcher.serve_local --tier 3b`
serves the OpenAI-compatible endpoint; point the boost at it via
`GOLDENMATCH_LLM_BASE_URL=http://localhost:8080/v1` (no core change — the existing
override). Offline, no API key.

## Gate summary (nothing fires cold)

| Gate | Where | Blocks |
|---|---|---|
| P3a perf GO/NO-GO | `perf_report.py` | the multi-hour full run |
| P5 eval win | `eval.py::evaluate_gate` | publishing / shipping |
| sha256 verify | `registry.py::resolve_model` | loading tampered/unpinned bytes |
| serializer drift | `registry.py` | loading a model trained on a different rendering |
