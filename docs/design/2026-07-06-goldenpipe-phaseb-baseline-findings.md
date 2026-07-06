# GoldenPipe Phase B baseline — out-of-core streaming is the wrong target for ER

**Status:** done (measurement). **Created:** 2026-07-06.
**Gates:** Phase B of `2026-07-06-goldenpipe-relocatable-stage-contract.md`.

## The question

The relocatable-stage contract's **Phase B** is an out-of-core streaming `Frame`
(yield/consume Arrow record batches instead of holding the whole DataFrame). Per
the design's own gate, it must first be shown that **holding the whole frame is
the bottleneck** at scale. This is that baseline.

## Method

`benchmarks/phaseb_outofcore_probe.py`, one **fresh process per size** (peak RSS
is a process-lifetime max, so isolation is required for a clean number). Two
measurements: peak RSS vs the input-frame size, and which stage dominates.

## Results

| rows | input frame | peak RSS | peak / frame | dedupe share |
|--:|--:|--:|--:|--:|
| 20,000 | 1.09 MB | 273 MB | **249×** | 77% |
| 50,000 | 2.73 MB | 610 MB | **223×** | 79% |
| 100,000 | 5.47 MB | 1697 MB | **310×** | 89% |

Two facts jump out:
1. **The input frame is a rounding error.** Peak process memory is **223–310× the
   frame**. Streaming the frame out-of-core would relieve well under 0.5% of peak
   RSS.
2. **Memory grows superlinearly with rows** (273 → 610 → 1697 MB; doubling
   50k→100k nearly *tripled* memory), and `goldenmatch.dedupe` climbs to **89%** of
   stage time. That growth is dedupe's candidate/scored-pair structures, not the
   frame.

## Verdict — skip Phase B (streaming Frame); it can't help ER

The memory (and compute) bottleneck is **dedupe's internal, whole-dataset
structures**, not the input frame. And dedupe is **inherently non-streamable** —
deduplication must see *all* records to find cross-record matches; blocking
reduces comparisons but still indexes the whole dataset. A streaming `Frame` at
the orchestrator would stream a 5 MB input while dedupe balloons to 1.7 GB
internally. It optimizes the wrong thing, again — the same shape as Stage 0.

**Stage streamability (why the dominant stage can't stream):**
- `goldenflow.transform` — row-wise → **streamable** (but it's ~11% of the wall).
- `goldencheck.scan` — needs whole columns (n_unique / FD / composite); already
  sample-capped at 100k → bounded, not the target.
- `goldenmatch.dedupe` — **whole-dataset, non-streamable, superlinear** → the
  dominant stage, and the one a streaming Frame cannot touch.

**Where out-of-core ER actually lives (if ever needed):** it is a *dedupe
algorithm* project inside `goldenmatch` — external / disk-backed blocking +
streaming candidate generation — **not** a goldenpipe orchestration / `Frame`
concern. Tracked there, separately, gated on a real >memory workload.

## Recommendation — go to Phase C (cross-process / cross-language)

- **Phase A stands** (the relocatable-stage seam is merged/ready) — it is the
  groundwork for Phase C, not Phase B.
- **Phase B (streaming Frame) is deprioritized** on this evidence — not blocked,
  just not the ER bottleneck's tool.
- **Phase C** — a stage relocated to another process / language / engine (DuckDB /
  Postgres / a TS worker), where the handoff is *real* Arrow serialization — is the
  right pillar-2 target and the stated goal. It gets its own Stage-0-style baseline
  first: a workload where a remote stage's compute + Arrow IPC beats running it
  locally (e.g. pushing a transform or a blocking pass into DuckDB/Postgres, which
  already have Arrow-native surfaces from the goldencheck/goldenflow SQL work).

## Repro

`python packages/python/goldenpipe/benchmarks/phaseb_outofcore_probe.py --rows 100000`
(run per size in a fresh process).
