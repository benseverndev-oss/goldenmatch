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
# or a single suite: --suite accuracy | perf | robustness
# scale: --n-image-bases 30 --n-audio-bases 12
```

## Layout (reusable by design)

| File | Role |
|---|---|
| `metrics.py` | **Generic ER eval core** — `prf_at_threshold`, `threshold_sweep`, `discrimination`, `blocking_eval`, `per_group_recall`. No goldenmatch import; other ER stages (blocking, fuzzy/FS) can reuse it. |
| `datasets.py` | Deterministic synthetic media-variant datasets (no committed assets). Each *base* is one entity; labelled *variants* under named perturbations. Stdlib-only. |
| `perf.py` | Throughput under `GOLDENMATCH_NATIVE=0/1` + speedup. |
| `run.py` | Orchestrates the suites; emits JSON + a markdown summary. |

## Metrics — what each one tells you

- **Best operating point** — the threshold maximising F1 over *all* pairs. The
  scorer's achievable precision/recall.
- **Per-transform recall** — recall split by perturbation. The most useful view:
  it shows *which* transforms the hash survives and which break it, instead of
  hiding that in an aggregate.
- **Blocking band sweep** — recall vs candidate-reduction as `num_bands` varies.
  This is the knob behind `PerceptualKeyConfig.num_bands`; the sweep is how we
  pick its default.
- **Discrimination** — separation between the matched and non-matched score
  distributions (the signal the threshold then splits). `overlap` counts
  non-match pairs scoring above the lowest match score.
- **Performance** — images/sec and audio-items/sec for the Python vs native path,
  and the speedup. The number that says whether a kernel optimisation moved the
  wall.
- **Robustness** — recompute determinism + native == Python over the suite.

## Baseline findings (first run, 30 image / 12 audio entities) — the backlog

- **pHash is photometric, not geometric** (measured): recall ≈ brightness 0.93,
  contrast 0.87, recompress 0.93, blur 0.70 — but **noise 0.33, crop 0.0,
  rotate 0.0**. Geometric transforms are pHash's intrinsic blind spot; the
  aggregate F1 looks modest only because the dataset deliberately includes those
  hard cases. *Open question:* keep crop/rotate as a documented "out of envelope"
  bucket, or add a geometry-tolerant primitive (e.g. ORB/keypoint) as a separate
  capability.
- **Blocker default `num_bands=8` is reduction-biased**: recall ≈ 0.72 at 8 bands
  vs ≈ 0.97 at 16 (reduction 0.77 → 0.28). *Open question:* is 8 the right
  zero-config default, or should auto-config pick from the recall target?
- **Audio survives amplitude + time-shift, not additive noise** (recall amplitude
  1.0, trim 1.0, noise 0.0 at threshold 1.0). *Open question:* lower the
  `audio_fp` default threshold to recover noisy matches, and measure the precision
  cost.

Re-run after any change to `perceptual.py` / `perceptual_blocker.py` /
`perceptual_autoconfig.py` and diff the JSON to see the metric move.
