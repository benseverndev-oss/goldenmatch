# ER-Matcher Training Run + Cost Benchmark (SP2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Produce a trained LoRA ER-matcher on the real 25k corpus via a data-driven, cost-gated run: benchmark bf16-LoRA vs QLoRA-4bit (cost + quality), let the human pick from an A/B scorecard, then run the winner to a merged model + basic held-out eval.

**Architecture:** Extend the existing box-safe/GPU-split trainer. The PURE decision logic (learning-curve sweep aggregation, cheapest-GPU-tier selection, A/B scorecard, config-matrix expansion) lives in `perf_report.py` + a new pure module and is unit-tested with no GPU/network. The GPU code (`_train_runtime.py` sweep loop + resume; `modal_train.py` sweep/matrix entrypoints + tier param) is exercised by the actual Modal runs, matching the trainer's existing test boundary.

**Tech Stack:** Python 3.11 stdlib + PyYAML for pure logic (box-safe); torch/transformers/trl/peft/bitsandbytes on the Modal GPU path (unchanged deps). pytest.

**Spec:** `docs/superpowers/specs/2026-07-27-er-matcher-training-run-cost-benchmark-design.md`

**Depends on:** #2178 + #2200 (MERGED). Branch off current `main`.

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `scripts/er_matcher/gpu_tiers.py` | **Create** | Pure GPU-tier table + `select_cheapest_tier(peak_mem_gb)` |
| `scripts/er_matcher/perf_report.py` | **Modify** | Add tier-aware extrapolation + `build_ab_scorecard(...)` (pure) |
| `scripts/er_matcher/config_matrix.py` | **Create** | Pure `expand_configs(base) -> [bf16, qlora]` variants |
| `scripts/er_matcher/sweep.py` | **Create** | Pure sweep aggregation: `data_fractions()`, `build_learning_curve(points)` |
| `scripts/er_matcher/_train_runtime.py` | **Modify** | Sweep loop (train N% slices, collect eval_loss) + `resume_from_checkpoint` wiring |
| `scripts/er_matcher/modal_train.py` | **Modify** | `train_sweep` entrypoint + config-matrix driver + `gpu` tier param |
| `scripts/er_matcher/config.yaml` | **Modify** | Right-size for the 25k corpus |
| `scripts/er_matcher/run_benchmark.py` | **Create** | Local orchestrator: trigger both-config sweeps, pull metrics, print A/B scorecard |
| `scripts/er_matcher/test_gpu_tiers.py` | **Create** | Tier-selection tests |
| `scripts/er_matcher/test_config_matrix.py` | **Create** | Config-matrix tests |
| `scripts/er_matcher/test_sweep.py` | **Create** | Sweep-aggregation tests |
| `scripts/er_matcher/test_perf_report.py` | **Modify** | A/B scorecard + tier-aware extrapolation tests |

**Boundary:** `gpu_tiers.py`, `config_matrix.py`, `sweep.py`, and the new `perf_report.py` functions are PURE (stdlib only, no torch/network) — fully unit-tested. `_train_runtime.py`/`modal_train.py` GPU changes are exercised by the real Modal runs (Task 9), not CI. `run_benchmark.py`'s pure flow logic is tested; its Modal calls are the execute step.

---

## Phase A — Pure decision logic (box-safe, TDD)

### Task 1: GPU-tier table + cheapest-fit selection

**Files:** Create `scripts/er_matcher/gpu_tiers.py`, `test_gpu_tiers.py`

- [ ] **Step 1: failing test** (`test_gpu_tiers.py`, opens with `sys.path.insert(0, os.path.dirname(__file__))`):

```python
from gpu_tiers import TIERS, select_cheapest_tier

def test_tiers_sorted_and_have_capacity_and_rate():
    for t in TIERS:
        assert t.capacity_gb > 0 and t.usd_per_hour > 0 and t.name

def test_selects_cheapest_that_fits():
    t = select_cheapest_tier(19.5)          # fits L4 (24GB) and up
    assert t.name in {"L4", "A10G"}          # cheapest fitting
def test_escalates_when_large():
    assert select_cheapest_tier(30.0).name.startswith("A100")
def test_raises_when_nothing_fits():
    import pytest
    with pytest.raises(ValueError):
        select_cheapest_tier(999.0)
```

- [ ] **Step 2:** run -> FAIL.
- [ ] **Step 3:** implement `gpu_tiers.py`: a frozen dataclass `Tier(name, capacity_gb, usd_per_hour)`, a `TIERS` list sorted ascending by `usd_per_hour` (L4 24GB/0.80, A10G 22GB/1.10, A100-40GB 40GB/2.10, A100-80GB 80GB/2.50 — rates are DEFAULTS, documented as "confirm current Modal rates"), and `select_cheapest_tier(peak_mem_gb, *, headroom=0.9) -> Tier` returning the first tier whose `capacity_gb * headroom >= peak_mem_gb`, else `ValueError`. Stdlib only.
- [ ] **Step 4:** run -> PASS.
- [ ] **Step 5:** commit `feat(er-matcher): GPU-tier table + cheapest-fit selection`.

### Task 2: tier-aware full-run extrapolation in perf_report

**Files:** Modify `perf_report.py`, `test_perf_report.py`

- [ ] **Step 1: failing test** — `extrapolate_on_tier(smoke_steps, smoke_wall_s, total_steps, tier)` returns `{s_per_step, full_wall_h, full_cost_usd}` using the tier's `usd_per_hour`; a wrapper `extrapolate_cheapest(metrics, total_steps)` selects the tier from `metrics["peak_mem_gb"]` and extrapolates. Assert cost = wall_h * tier.rate.
- [ ] **Step 2-4:** implement on top of the existing `extrapolate_full_run` (reuse its step-rate math; add the tier rate); run tests green (existing perf_report tests must stay green).
- [ ] **Step 5:** commit `feat(er-matcher): tier-aware full-run cost extrapolation`.

### Task 3: A/B scorecard (pure)

**Files:** Modify `perf_report.py`, `test_perf_report.py`

- [ ] **Step 1: failing test** — `build_ab_scorecard(entries)` where each entry is `{config_name, metrics, total_steps}` returns, per config: `{config, fits_tier, full_cost_usd, full_wall_h, curve_slope, final_eval_loss, gpu_util, peak_mem_gb}`. Assert both configs present, cost/tier computed via Task 2, `curve_slope` via existing `learning_curve_slope`, `final_eval_loss` = last point of the learning_curve. NO auto-pick (human decides) — assert it returns data, not a winner.
- [ ] **Step 2-4:** implement (pure, composes Task 2 + `learning_curve_slope`); green.
- [ ] **Step 5 (benchmark-cost guard, pure + tested):** add `estimate_benchmark_cost(*, smoke_step_s, n_configs, n_slices, steps_per_slice, tier_usd_per_hour) -> float` = `smoke_step_s * n_configs * n_slices * steps_per_slice / 3600 * tier_usd_per_hour`, and a `within_ceiling(cost, ceiling)` check. Test the arithmetic + a below/above-ceiling case. Task 9's $8 guard calls this instead of an ad-hoc calc.
- [ ] **Step 6:** commit `feat(er-matcher): A/B scorecard + benchmark-cost guard (pure)`.

### Task 4: config-matrix expansion

**Files:** Create `config_matrix.py`, `test_config_matrix.py`

- [ ] **Step 1: failing test** — `expand_configs(base_cfg_dict) -> list[(name, cfg_dict)]` yields exactly `[("bf16-lora", {..., qlora_4bit: False}), ("qlora-4bit", {..., qlora_4bit: True})]`, each a full copy of base with ONLY `qlora_4bit` toggled. **`bf16` stays `True` in BOTH variants** — the 4-bit path uses `bnb_4bit_compute_dtype=torch.bfloat16` and passes `bf16=cfg.bf16` to `SFTConfig`; flipping `bf16` off would wrongly route to the fp16 branch. base unchanged (no mutation). Assert names, `qlora_4bit` per variant, `bf16 is True` in both, and base not mutated.
- [ ] **Step 2-4:** implement (pure dict copies); green.
- [ ] **Note (connects to Task 7):** the Modal container loads `config.yaml` via `load_config` — it does NOT receive these dicts. So the matrix's job is to define the variant NAMES + the `qlora_4bit` value; Task 6 adds a `--qlora-4bit` CLI override so the driver can push each variant's flag into the container. `expand_configs` may return the `(name, qlora_bool)` the driver needs directly.
- [ ] **Step 5:** commit `feat(er-matcher): config-matrix expansion (bf16 vs qlora)`.

### Task 5: sweep aggregation (pure)

**Files:** Create `sweep.py`, `test_sweep.py`

- [ ] **Step 1: failing test** — `data_fractions() == [0.1, 0.25, 0.5, 1.0]`; `slice_len(total, frac)` = `max(1, round(total*frac))`; `build_learning_curve(points)` where points are `[(frac, eval_loss)]` returns them sorted ascending by frac as `[{"frac":..,"eval_loss":..}]` (the shape `learning_curve_slope` consumes). Assert ordering + shape + that it round-trips through `perf_report.learning_curve_slope` (import it, positive slope on a descending-loss curve).
- [ ] **Step 2-4:** implement (pure); green.
- [ ] **Step 5:** commit `feat(er-matcher): learning-curve sweep aggregation (pure)`.

---

## Phase B — GPU code paths (exercised by real runs, not CI)

### Task 6: sweep loop + resume + config-override + measured total_steps

**Files:** Modify `_train_runtime.py`, **`train.py`** (argparse + dispatch)

- [ ] **`train.py` argparse/dispatch:** add `--sweep` (store_true) and `--qlora-4bit` as an override (`argparse.BooleanOptionalAction` → `--qlora-4bit` / `--no-qlora-4bit`, default `None` = use config.yaml). In `main`, after `load_config`, if `args.qlora_4bit is not None` set `cfg.qlora_4bit = args.qlora_4bit` then re-`validate()`. Dispatch: `run_sweep(cfg, args)` if `args.sweep` else `run_training(cfg, args)` (currently an unconditional `run_training` call). This override is the ONLY way the container gets the bf16-vs-qlora variant (the container loads config.yaml; it never sees Task 4's dicts).
- [ ] **`run_sweep(cfg, args)` in `_train_runtime.py`:** in ONE process (single base-model + tokenizer load), loop `sweep.data_fractions()`: for each frac, take the first `sweep.slice_len(len(train_rows), frac)` train rows, train `args.smoke_steps` steps with eval enabled, capture `trainer.evaluate()["eval_loss"]`, accumulate `(frac, eval_loss)`. Emit metrics with `learning_curve = sweep.build_learning_curve(points)` + the existing gpu_util/peak_mem/tokens_per_s (from the largest slice) + `full_total_steps` (see below).
- [ ] **Measured `full_total_steps` (fixes the packing problem):** do NOT compute steps as `rows/batch` — packing makes the step count depend on packed-sequence count, unknown until packing runs. After the trainer builds the packed dataset, derive the FULL-run step count from the packed dataloader: `full_total_steps = int(len(trainer.get_train_dataloader()) * cfg.epochs)` (the dataloader length already reflects packing + effective batch for the FULL 100% slice; for sub-slices scale accordingly, but emit the 100%-slice-derived full-run count). Emit it in both smoke and sweep metrics so `run_benchmark`/`perf_report` extrapolate on a MEASURED step count, not a guess.
- [ ] **Resume:** in the full-run path, if a checkpoint exists under the out-dir on the volume, pass `resume_from_checkpoint=True` to `trainer.train()`.
- [ ] Keep the QLoRA branch (`if cfg.qlora_4bit:`) working in both sweep + full paths (it already exists at `_train_runtime.py:118`).
- [ ] Self-check: pure helpers (`sweep`) imported, not reimplemented. No box-safe test here (GPU path); verified by Task 9's real run.
- [ ] Commit `feat(er-matcher): sweep loop + resume + --qlora-4bit override + measured total_steps`.

### Task 7: modal_train sweep entrypoint + matrix driver + tier param

**Files:** Modify `modal_train.py`

- [ ] **GPU tier at RUN time (not decoration):** `@app.function(gpu=...)` is fixed at definition. To target the human-picked tier for the full run, use Modal's `.with_options(gpu=tier).remote(...)` on the full-run function (do NOT try to "pass a gpu arg" into a fixed-gpu function). The sweep/smoke stay on A10G (cheapest adequate for the benchmark).
- [ ] Add `train_sweep(qlora: bool)` (A10G) that runs the trainer with `--sweep` and `--qlora-4bit`/`--no-qlora-4bit` (per the Task 6 override), writing `sweep_metrics_{bf16-lora|qlora-4bit}.json` to the volume.
- [ ] Add a `local_entrypoint` `benchmark` that invokes `train_sweep` for BOTH variants from `config_matrix.expand_configs` (each variant's `qlora_4bit` → the `--qlora-4bit` flag).
- [ ] Add a `full(config_name, gpu)` local_entrypoint that runs the chosen-config full run via `train_full.with_options(gpu=gpu).remote(qlora=<from config_name>)`.
- [ ] Commit `feat(er-matcher): modal sweep entrypoint + bf16/qlora matrix + with_options tier`.

### Task 8: config right-size + local orchestrator

**Files:** Modify `config.yaml`; Create `run_benchmark.py`

- [ ] `config.yaml`: keep `seq_len` MEASURED (unchanged policy); confirm `epochs: 2`, effective batch, dataloader workers are sane for 17,690 rows (document the reasoning in a comment). No blind changes.
- [ ] `run_benchmark.py` (local): trigger the Modal `benchmark` (both configs), pull `sweep_metrics_*.json` via `modal volume get`, then run `perf_report.build_ab_scorecard` and PRINT the scorecard (per config: fits-tier, full-run $/wall, curve slope, final eval-loss). **Use the MEASURED `full_total_steps` emitted in each config's metrics (Task 6)** — do NOT recompute `rows/effective_batch` (wrong under packing). The scorecard assembly reuses the Phase A tested modules; the Modal trigger/pull is the execute step.
- [ ] Commit `feat(er-matcher): config right-size + benchmark orchestrator`.

---

## Phase C — Execute (the actual runs; gated)

### Task 9: run the benchmark, bring the A/B scorecard, run the full model

> This is the execute step (real Modal GPU, on-disk `benzsevern` token). NOT CI.

- [ ] Estimate the benchmark-phase cost up front via `perf_report.estimate_benchmark_cost(...)` (Task 3): smoke step-time x 2 configs x 4 slices x steps-per-slice x A10G rate. If `not within_ceiling(cost, 8.0)`, STOP and surface to the human before launching.
- [ ] Run `modal run modal_train.py::benchmark` (both configs, A10G) -> `sweep_metrics_bf16.json`, `sweep_metrics_qlora.json` on the volume.
- [ ] Run `run_benchmark.py` -> the A/B scorecard. **Bring it to the human; the human picks the config** (spec §6 — the full-run spend always has a human in the loop).
- [ ] On the human's pick: `modal run modal_train.py::full --config-name <pick> --gpu <selected tier>` -> merged LoRA model on the volume.
- [ ] Basic held-out eval: run `eval.py` on the test split -> overall match-F1 (per-benchmark parity is SP3). Report the number.
- [ ] Commit any config/lockfile updates from the run; open the SP2 PR (code from Phases A/B). Data/models stay on the volume (not committed).

---

## Testing & conventions

- Box-safe: Phase A + the pure bits of Phase B/C are unit-tested with NO GPU/network (fixture-free where possible; `sys.path.insert(0, os.path.dirname(__file__))` header; register/compute inside tests). Determinism where applicable.
- GPU paths (`_train_runtime` sweep/resume, `modal_train` entrypoints) are verified by the real run (Task 9), matching the existing trainer's boundary.
- `ruff check --fix` on touched files; no `ruff format` (repo convention).
- Commits per task; PR after Phase A/B code is green (Phase C is the run, reported separately).

## Out of scope (later sub-projects)
- Sub-project 3 — cross-benchmark eval harness (per-benchmark match-F1/calibration; consumes Magellan/NCVR).
- Sub-project 4 — release (quantize GGUF, publish, model card from the manifest).
- Phase 1b — rich synthetic generator.
