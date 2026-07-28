# ER-Matcher Zero-Shot Eval + Calibration (SP3) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Produce a citable zero-shot generalization number (F1 on unseen benchmarks the model never trained on) + a post-hoc calibration fix (logit-derived P(match) + temperature scaling, no retrain).

**Architecture:** Pure decision logic (temperature scaling, ECE, SOTA table, gate wiring) in new/existing modules, box-safe unit-tested. The GPU logit-eval (`zeroshot_eval`) and the network fetch (`MagellanSource.fetch`) are verified by the real Modal run — same test boundary as SP2.

**Tech Stack:** Python 3.11 stdlib (math for temp-scaling); torch/transformers on the Modal GPU path; pytest.

**Spec:** `docs/superpowers/specs/2026-07-27-er-matcher-sp3-zeroshot-eval-calibration-design.md`

---

## File Structure

| Path | Change | Responsibility |
|---|---|---|
| `scripts/er_matcher/calibration.py` | **Create** | Pure `fit_temperature`, `apply_temperature`, `logit`/`sigmoid`, NLL |
| `scripts/er_matcher/sota_baselines.py` | **Create** | Published DeepMatcher/Ditto F1 per benchmark + lookup |
| `scripts/er_matcher/eval.py` | **Modify** | Zero-shot scorecard builder wiring `run_eval` + `evaluate_gate` (informational floor, SOTA display-only) |
| `scripts/er_matcher/sources/magellan.py` | **Modify** | Implement `fetch()` (Walmart-Amazon, Beer) — the existing `TODO(#magellan-fetch)` stub |
| `scripts/er_matcher/modal_train.py` | **Modify** | New `zeroshot_eval` entrypoint (forward-pass logit path) |
| `scripts/er_matcher/test_calibration.py` | **Create** | Temp-scaling + ECE tests |
| `scripts/er_matcher/test_sota_baselines.py` | **Create** | SOTA table tests |
| `scripts/er_matcher/test_eval.py` | **Modify** | Zero-shot scorecard + P(pred-class) semantics tests |

**Boundary:** `calibration.py`, `sota_baselines.py`, the `eval.py` scorecard builder are PURE (stdlib, box-safe tested). `MagellanSource.fetch()` (network) and `zeroshot_eval` (GPU forward-pass) are verified by the real run.

---

## Phase A — Pure logic (box-safe, TDD)

### Task 1: calibration.py (temperature scaling)

**Files:** Create `scripts/er_matcher/calibration.py`, `test_calibration.py`

- [ ] **Step 1: failing test** (`sys.path.insert(0, os.path.dirname(__file__))` header):
```python
from calibration import sigmoid, logit, fit_temperature, apply_temperature, ece_from_probs
import math

def test_sigmoid_logit_roundtrip():
    for p in (0.1, 0.5, 0.9):
        assert abs(sigmoid(logit(p)) - p) < 1e-9

def test_fit_temperature_softens_overconfident():
    # overconfident logits (large magnitude), labels mostly agree -> T > 1 softens
    logits = [4.0, 4.0, -4.0, -4.0, 4.0]   # P(match) ~0.98 / 0.02
    labels = [True, False, False, True, True]  # some wrong -> overconfident
    T = fit_temperature(logits, labels)
    assert T > 1.0            # softening

def test_apply_temperature():
    assert abs(apply_temperature(2.0, T=2.0) - sigmoid(1.0)) < 1e-9  # sigmoid(z/T)

def test_well_calibrated_T_near_one():
    # NON-degenerate + already-calibrated: rows at P(match)=0.7 that are 70% True,
    # and rows at 0.3 that are 30% True -> a genuine NLL minimum at T~1.
    # (Do NOT use logits=[0.0]*N: sigmoid(0/T)=0.5 for ALL T -> flat objective,
    # underdetermined, minimizer can't return a meaningful T.)
    logits = [logit(0.7)]*100 + [logit(0.3)]*100
    labels = [i < 70 for i in range(100)] + [i < 30 for i in range(100)]
    assert 0.7 < fit_temperature(logits, labels) < 1.4   # near 1
```
- [ ] **Step 2:** run -> FAIL.
- [ ] **Step 3:** implement `calibration.py` (stdlib `math` only):
  - `sigmoid(z)`, `logit(p)` (clamp p to (eps, 1-eps)).
  - `fit_temperature(logits, labels, *, lo=0.05, hi=10.0, iters=50) -> float`: minimize NLL `sum(-y*log(sigmoid(z/T)) - (1-y)*log(1-sigmoid(z/T)))` over T by a bounded 1-D search (golden-section or a coarse grid + refine — pure Python, no scipy). Return the T minimizing NLL. **Tie-break toward T=1.0** when the objective is flat/near-flat (degenerate/underdetermined data) so it never returns a boundary value spuriously.
  - `apply_temperature(z, *, T) -> float` = `sigmoid(z / T)`.
  - `ece_from_probs(probs, labels, *, bins=10) -> float`: standard binned ECE of `P(positive)` vs the positive `label` (this is the P(match)-vs-label ECE the spec §2 reports separately; distinct from eval.py's correctness-based ECE).
- [ ] **Step 4:** run -> PASS.
- [ ] **Step 5:** commit `feat(er-matcher): temperature-scaling calibration (pure)`.

### Task 2: sota_baselines.py

**Files:** Create `scripts/er_matcher/sota_baselines.py`, `test_sota_baselines.py`

- [ ] **Step 1: failing test** — `SOTA` dict maps benchmark key -> `{"deepmatcher_f1": float, "ditto_f1": float, "source": str}`; `sota_for(name) -> dict | None`. Assert `walmart_amazon` + `beer` present with plausible values, unknown -> None.
- [ ] **Step 2-4:** implement with published numbers (document the source paper per entry, e.g. Ditto: Walmart-Amazon F1 ~0.86, Beer ~0.94; DeepMatcher: Walmart-Amazon ~0.67, Beer ~0.72 — cite the papers in comments; a planner/implementer confirms exact figures from the Ditto/DeepMatcher papers). `sota_for` returns None for unknown. Pure data module.
- [ ] **Step 5:** commit `feat(er-matcher): published SOTA F1 baseline table`.

### Task 3: zero-shot scorecard builder in eval.py

**Files:** Modify `scripts/er_matcher/eval.py`, `test_eval.py`

- [ ] **Step 1: failing test** — `build_zeroshot_scorecard(per_benchmark: dict) -> dict` where each entry is `{"f1", "raw_ece", "calibrated_ece", "n"}`; returns per-benchmark rows with the SOTA comparison column (via `sota_baselines.sota_for`) and an `evaluate_gate` verdict using an INFORMATIONAL zero-shot floor (`abs_floor=0.65`, the SOTA-baseline slots UNSET). Assert: SOTA is a display field (not a gate input — the gate passes/fails only on abs_floor + calibration, independent of SOTA); calibrated_ece is what the gate's calibration check reads.
- [ ] **Step 2-4:** implement `build_zeroshot_scorecard` composing `sota_baselines` + `evaluate_gate`. **Shape mapping:** `evaluate_gate` reads `scorecard["splits"][split]["overall"]["f1"/"ece"]`, so per benchmark synthesize that nested shape from the flat entry and map `calibrated_ece -> overall["ece"]` (the gate's calibration check reads the calibrated value; `f1 -> overall["f1"]`). Pass `abs_floor=0.65`, leave `hosted_boost_f1`/`fs_baseline_f1`/`baseline_f1` = None. Keep `run_eval`/`evaluate_gate`/`confusion`/`prf1` UNCHANGED (pure). Green; existing `test_eval.py` stays green.
- [ ] **Step 5:** commit `feat(er-matcher): zero-shot scorecard (F1 + SOTA display + informational gate)`.

---

## Phase B — Fetch + GPU logit-eval (verified by real run)

### Task 4: MagellanSource.fetch() (unseen datasets)

**Files:** Modify `scripts/er_matcher/sources/magellan.py`

- [ ] Implement the existing `fetch()` stub (below the `GOLDENMATCH_ALLOW_FETCH` guard, the `TODO(#magellan-fetch)`): download the DeepMatcher `Structured/{Walmart-Amazon,Beer}` archives, sha256-verify, unpack into the source root so `load_from_dir()` reads `tableA/tableB/train/valid/test.csv`. Keep the guard + eval_only. `urllib`/`zipfile` inside `fetch()` (not the import path). **NOTE the existing `_DEEPMATCHER_BASE_URL` is likely `.../~anhai/data/deepmatcher_data/` (missing the `1`) — the correct anhaidgroup host is `.../~anhai/data1/deepmatcher_data/`; fix the constant, don't just append. Confirm exact archive URLs + pin sha256, and confirm per-dataset zip vs exploded-CSV dir before wiring `zipfile`.**
- [ ] The PURE `load_from_dir` parse is already tested; add a small test only if a new pure helper is introduced. The download is exercised by Task 6's real run (network — box-safe suite must not fetch).
- [ ] Commit `feat(er-matcher): implement MagellanSource.fetch for unseen eval datasets`.

### Task 5: zeroshot_eval Modal entrypoint (logit path)

**Files:** Modify `scripts/er_matcher/modal_train.py`

- [ ] New `@app.function(gpu=GPU_FULL, ...)` `zeroshot_eval(dataset: str, allow_fetch: bool = True)` + a local_entrypoint. Loads `/out/model/merged`. For each pair: apply the chat template, then teacher-force the EXACT verdict prefix the SFT emits — `{"match":` with **NO trailing space**. NOTE: `render_target` (prompt.py) uses `json.dumps(..., separators=(",", ":"))` — COMPACT JSON, so the emitted string is `{"match":true,...}` with **NO space after the colon** (verified by `test_render_target_is_compact_json`). So the value token that follows `{"match":` is `true` / `false` with **NO leading space** — resolve both ids ONCE via `tok.encode("true")`/`"false"` (NO leading space) and verify they're single tokens. Run a FORWARD pass to get the next-token logits; read the logits at the `true` / `false` token ids, softmax over just those two -> `P(match)`, `z = logit(P(match))`. Verdict `match = P(match) > 0.5` (= the greedy argmax, consistent with the generated verdict); `confidence = P(match) if match else 1-P(match)` (P(predicted class), per spec §2).
- [ ] Fit temperature on the `valid` split's `(z, label)`; score `test` via `eval.run_eval` with the (calibrated) matcher; compute raw vs calibrated ECE; call `eval.build_zeroshot_scorecard`. Write `zeroshot_eval_results.json` to the volume.
- [ ] Watch-item for the real run: `P(match)` must SEPARATE matches from non-matches (mean P(match) higher on true matches) — else the token-id resolution is wrong. Print a quick separation stat.
- [ ] Commit `feat(er-matcher): zeroshot_eval entrypoint (logit-derived P(match) + temp scaling)`.

---

## Phase C — Execute (the real run)

### Task 6: run the zero-shot eval, report citable numbers

> Execute step (real Modal GPU + fetch; on-disk `benzsevern` token). NOT CI.

- [ ] `GOLDENMATCH_ALLOW_FETCH=1`; run `zeroshot_eval` for Walmart-Amazon (+ Beer) — fetches the data, extracts logits, fits T, scores test.
- [ ] Confirm the P(match) separation watch-item on the logs before trusting numbers.
- [ ] Pull `zeroshot_eval_results.json`; report the citable scorecard: per-benchmark zero-shot **F1 vs published SOTA** + raw/calibrated **ECE** + gate verdict.
- [ ] Open the SP3 PR (Phase A/B code); mark ready once the real run validates the GPU path.

---

## Testing & conventions
- Box-safe: Phase A (calibration, sota_baselines, scorecard) fully unit-tested, no GPU/network. `MagellanSource.fetch()` network + `zeroshot_eval` GPU verified by the real run.
- `ruff check --fix` on touched files; no `ruff format`. `sys.path.insert(0, os.path.dirname(__file__))` test header. Commits per task; PR after Phase A/B green.

## Out of scope
- Retraining (soft confidence targets) — post-hoc only here.
- Phase 1b (data diversity) — measured against SP3's baseline, separately.
- Sub-project 4 (release / quantized GGUF / model card).
