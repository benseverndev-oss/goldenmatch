# Perceptual crawl-tier bench harness

A **dispatch-only** benchmark for the multimodal-ER perceptual crawl tier
(ADR 0022) — the standing way to measure whether the image/audio perceptual
hashing is actually *good*, and to iterate on the metrics that matter: the scorer
operating point, the blocker recall-vs-reduction tradeoff, per-transform
robustness, the native speedup, and determinism.

It is **not** a per-PR CI gate (the perceptual algorithm is parity-locked by the
golden fixture; accuracy/perf are judgement calls we iterate on, not pass/fail).
Run it locally or via the `bench-perceptual` workflow (`workflow_dispatch`).

## Run it

```bash
cd packages/python/goldenmatch
uv run python scripts/bench_perceptual/run.py --suite all --out report.json
# or a single suite: --suite accuracy | perf | robustness | e2e | hotspot
# scale: --n-image-bases 30 --n-audio-bases 12 --n-radial-bases 12 --e2e-bases 30
```

## Layout (reusable by design)

| File | Role |
|---|---|
| `metrics.py` | **Generic ER eval core** — `prf_at_threshold`, `threshold_sweep`, `discrimination`, `blocking_eval`, `per_group_recall`. No goldenmatch import; other ER stages (blocking, fuzzy/FS) can reuse it. |
| `datasets.py` | Deterministic synthetic media-variant datasets (no committed assets). Each *base* is one entity; labelled *variants* under named perturbations. Stdlib-only. |
| `perf.py` | Kernel throughput under `GOLDENMATCH_NATIVE=0/1` + speedup (image pHash, audio fingerprint, radial). |
| `hotspot.py` | cProfile self-time hotspots per kernel (Python path; native is opaque to cProfile). |
| `pipeline_bench.py` | **End-to-end**: runs the real `dedupe_df` pipeline on a pHash column and reports F1 vs ground truth + wall + per-stage timings. |
| `run.py` | Orchestrates the suites; emits JSON + a markdown summary. |

## Metrics — what each one tells you

- **Best operating point** — the threshold maximising F1 over *all* pairs. The
  scorer's achievable precision/recall. Reported for **image pHash, the radial
  (rotation/crop-aware) feature, and audio**.
- **Per-transform recall** — recall split by perturbation. The most useful view:
  it shows *which* transforms the hash survives and which break it, instead of
  hiding that in an aggregate. (The radial lane is where rotate/crop — pHash's
  ~0.0 — get recalled.)
- **Blocking band sweep** — recall vs candidate-reduction as `num_bands` varies.
  This is the knob behind `PerceptualKeyConfig.num_bands`; the sweep is how we
  pick its default.
- **Discrimination** — separation between the matched and non-matched score
  distributions (the signal the threshold then splits). `overlap` counts
  non-match pairs scoring above the lowest match score.
- **Performance** — images/sec, audio-items/sec, and radial-profiles/sec for the
  Python vs native path, and the speedup. The number that says whether a kernel
  optimisation moved the wall.
- **End-to-end (`e2e`)** — runs the *real* `dedupe_df` pipeline on a synthetic
  pHash column (explicit `phash` matchkey + `perceptual` blocking) and reports
  **cluster-pair F1 / precision / recall vs ground truth**, **wall**, throughput,
  and **per-stage timings** (`core.bench.bench_capture`: blocking, scoring,
  clustering, golden). The component lanes measure pieces; this is the whole pipe.
- **Hotspots (`hotspot`)** — top functions by **self-time** (`tottime`) per kernel
  on the Python path, so a slow path is localised to a function, not a stage.
  cProfile sees only Python frames (the native kernel is opaque — that's measured
  by `perf`), and `cumtime` is *not* wall, so `tottime` is the sort key.
- **Robustness** — recompute determinism + native == Python over the suite.

## The first-run findings — all three resolved (the harness earned its keep)

The harness's first iteration produced three findings; acting on them (with
*validate-before-build* discipline) **refuted two cheap fixes and shipped the
real ones**:

- **Finding 1 — pHash is photometric, not geometric** (rotate/crop recall 0.0).
  A dihedral-canonical hash and rotation-*invariant* descriptors were both
  measured net-negative; the **radial-variance feature** (orientation profile +
  angular-aligned compare) closes it — rotate/crop **0.0 → ~1.0** (see the
  `accuracy_radial` lane). *Shipped: ADR 0022 finding 1.*
- **Finding 2 — blocker `num_bands=8` was reduction-biased** (0.72 recall vs 0.97
  at 16). Now **recall-target-driven** (`recommend_num_bands`); default 16.
  *Shipped: finding 2.*
- **Finding 3 — "audio dies under additive noise"** was a **dataset artifact**:
  pure tones leave the Haitsma-Kalker bands near-empty. On broadband audio the
  fingerprint *is* noise-robust; fixed by a broadband suite + the canonical
  threshold (`audio_fp` 0.80 → 0.65). *Shipped: finding 3.*

The lesson the harness keeps teaching: **measure on a realistic workload before
designing a fix.** Re-run after any change to `perceptual.py` /
`perceptual_blocker.py` / `perceptual_autoconfig.py` and diff the JSON to see the
metric move — and watch the `e2e` F1/wall + `hotspot` self-time when tuning perf.
