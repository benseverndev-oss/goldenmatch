# GoldenPipe arrow-canonical: decision

**Status:** decision requested (measurement done). **Created:** 2026-08-10.
**Workstream:** W3 of `docs/superpowers/plans/2026-08-10-arrow-seam-hardening.md`.
**Probe:** `packages/python/goldenpipe/benchmarks/adapter_conversion_probe.py`.

## Recommendation

**Option A — do nothing, and document the boundary as intentional.**

The arrow seam is arrow-native inside goldenmatch / goldencheck / goldenflow and
polars-canonical in goldenpipe. That is a real inconsistency. It is also, on the
numbers below, the **faster** arrangement — flipping goldenpipe to arrow-canonical
would make its one substantive conversion **~2.5x slower**, not faster.

The consistency argument survives, but it now carries a measured price tag rather
than being free tidying. That makes it a judgment call about what the suite is
optimising for, which is why this document stops here instead of opening a PR.

## What was already known

Stage 0 (`2026-07-06-goldenpipe-stage0-findings.md`) measured the frame handoff:

| | 2176 ms run | 4806 ms run |
|---|---|---|
| handoff: CSV re-read | 3.5 ms · 0.2% | 5.7 ms · 0.1% |
| handoff: full-df Utf8 cast | 1.6 ms · 0.1% | 2.2 ms · 0.0% |
| **handoff total** | **5.1 ms · 0.2%** | **7.9 ms · 0.2%** |

Wall is ~99% per-stage kernel compute; `goldenmatch.dedupe` alone is ~75%.

## What was NOT known, and now is

Stage 0 measured the handoff, not the two conversion sites in
`adapters/match.py`. Measuring those turned up a structural point that matters
more than the timings.

**The two sites are in DIFFERENT stages, and only one is on the default path:**

| site | stage | scope |
|---|---|---|
| `ctx.df.cast({col: pl.Utf8 ...})` (`match.py:40`) | `DedupeStage` — **default** | ALL columns |
| `{c: ctx.df[c].to_arrow() ...}` (`match.py:172`) | `FusedDedupeStage` — **opt-in** | key + score columns only |

The plan framed both as "the adapter's per-column conversion". They are not one
thing, and the arrow-facing one is the opt-in stage.

### Measurements (median of 5, mixed-dtype frame: 3 string + 2 numeric)

| | 100K | 1M |
|---|---|---|
| **A** per-column `.to_arrow()` (fused stage, 2 cols) | 0.76 ms | 7.56 ms |
| **B** whole-frame `pl.Utf8` cast (classic stage) | 3.82 ms | 32.26 ms |
| **C** arrow-native equivalent of B | 8.17 ms | 87.19 ms |
| **B − C** (what flipping would save) | **−4.35 ms** | **−54.93 ms** |

Negative means flipping **costs** wall.

### Fairness check

A whole-frame number could be an artifact of one library short-circuiting the
already-`Utf8` columns. It is not:

| | 100K | 1M |
|---|---|---|
| numeric→string cast, polars | 3.04 ms | 32.55 ms |
| numeric→string cast, arrow | 7.66 ms | 83.18 ms |
| **arrow slowdown on real work** | **2.52x** | **2.56x** |
| no-op string cast, polars | 0.15 ms | 0.23 ms |
| no-op string cast, arrow | 0.01 ms | 0.01 ms |

Both libraries short-circuit the no-ops. The gap is real work: **pyarrow's
numeric→string cast is ~2.5x slower than polars'**, consistently across sizes.

### The point the numbers make

The `pl.Utf8` cast is **not a polars tax**. It is a dtype normalization the
pipeline needs regardless of substrate — an arrow-canonical goldenpipe still has
to do it, just via `pc.cast`, ~2.5x slower. "Remove the conversion" was the wrong
mental model: there is no conversion to remove, only a choice of which library
performs an intrinsic cast.

And A — the actual polars→arrow crossing — is **7.6 ms at 1M on the opt-in
stage**, against a wall that is ~75% dedupe kernel. Both adapter docstrings claim
polars→arrow shares column buffers; the measurement is consistent with that.

### 10M

Not run. The plan called for it on CI, but 100K→1M is linear and the slowdown
ratio is stable (2.52x → 2.56x), so 10M would burn a `large-new-64GB` slot to
confirm a sign that two sizes already agree on — for a change the numbers say not
to make. Say the word if you want it anyway before deciding.

## Options

### A — Do nothing; document the boundary as intentional (RECOMMENDED)

Add a note to `models/frame.py` recording that polars-canonical is deliberate,
cross-referencing Stage 0 and this document, so the next person to notice the
inconsistency finds the reasoning instead of re-litigating it.

- **Cost:** ~0.
- **Keeps:** two substrates in the suite; per-column `.to_arrow()` on the opt-in
  fused stage (7.6 ms/1M).
- **Why recommended:** the flip has no wall upside (Stage 0) and now a measured
  wall *downside* (2.5x on the cast). Consistency alone does not justify making
  the default path slower.

### B — Additive `ArrowFrame` impl; adapters prefer arrow when the source is arrow

The `Frame` protocol already has two backends (`LocalFrame`, `DuckDBFrame`), so a
third is additive and `LocalFrame` stays polars-backed — Stage 0 untouched.

- **Cost:** moderate, contained to the adapters.
- **Buys:** removes the A crossing (7.6 ms/1M) on the opt-in stage.
- **Does NOT remove** the B cast — that becomes C, i.e. ~2.5x slower.
- **Verdict:** the plan pre-committed to recommending B if conversion exceeded
  ~2% of wall. It does not, and the dominant term moves the wrong way. **Not
  recommended on these numbers.**

### C — Full flip: arrow-canonical `PipeContext`, `polars()` a compat shim

- **Cost:** 41 call sites across 7 adapters, plus goldenanalysis and infermap,
  which also hard-dep polars and sit in the same tier.
- **Buys:** one substrate; drops the last hard polars dep in the orchestration
  tier (install footprint, supply chain).
- **Costs:** ~2.5x on every numeric→string cast on the default path.
- **Verdict:** only coherent as a **product decision about the polars footprint
  across goldenpipe + goldenanalysis + infermap together.** Flipping goldenpipe
  alone leaves two of three tier-2 packages polars-canonical — inconsistency at
  full price. Not an engineering call, and not one to make off this document.

## If the answer is "consistency still matters"

That is a legitimate position — the honest framing is that you would be buying
a single substrate for roughly **+55 ms per 1M rows** on the default path, plus
the 41-call-site change. Two things would make it a better trade:

1. **Fix the cast, not the substrate.** The `pl.Utf8` whole-frame cast exists to
   dodge schema-mismatch errors when mixed-dtype columns reach GoldenMatch. If
   goldenmatch's ingest normalized dtypes itself, the adapter would not need the
   cast at all, and B/C would stop mattering — which changes the arithmetic for
   option C entirely.
2. **Take the tier together.** goldenpipe + goldenanalysis + infermap in one
   programme, so the footprint win is real rather than partial.

Neither is in scope here.

## Reproducing

```bash
cd packages/python/goldenpipe/benchmarks
python adapter_conversion_probe.py --rows 1000000 --repeat 5
```

The probe greps `adapters/match.py` and **refuses to run** if the expressions it
claims to measure have drifted from the shipped call sites — a probe that
silently measures a paraphrase is worse than no probe. Verified: renaming the
comprehension variable trips it and names the drifted snippet. It matches the
expression as a substring, so it tolerates a trailing comment change and would
not catch a semantically-equivalent rewrite that kept the same text — the
granularity is "this expression still exists", not "this expression is still
reached". Good enough to stop silent rot; not a substitute for reading the
adapter.

### Caveat on small inputs

The 2.5x arrow slowdown holds at 100K and 1M. At ~1K rows both paths are
sub-millisecond and the ratio inverts (arrow slightly faster) — noise-dominated
and irrelevant to a decision about pipeline-scale work, but worth stating so the
claim is not read as "arrow casts are always slower".
