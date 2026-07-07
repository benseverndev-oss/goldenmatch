# "Arrow everywhere" in GoldenFlow (Pillar-1) — measurement spike + NO-GO

**Date:** 2026-07-07 • **Status:** Decided — **do not** thread Arrow through the
whole transform engine. Companion to the frame-container spike
([`2026-07-06-frame-container-eviction-spike.md`](2026-07-06-frame-container-eviction-spike.md)).

## Question

Fusion made the *chainable owned-kernel* path Arrow-native (a run of owned
string/numeric/nullable kernels executes in one native Arrow pass). The proposed
"natural extension" is to make the WHOLE engine Arrow-native: hold each column as
an Arrow array and thread it through *every* transform (fused and non-fused),
converting Polars↔Arrow only once at the I/O boundary instead of once per
transform.

The ceiling on what that can buy is the fraction of a realistic pipeline's wall
spent in the per-transform Polars↔Arrow orchestration that threading would remove.
Measure it before rewriting the engine.

## Method

`scratchpad/arrow_everywhere_spike.py` — a realistic 2M-row mixed pipeline (4
fused string runs across `first_name`/`last_name`/`email`/`city` **plus** a
series-mode native `phone_e164`), with `pl.Series.to_arrow` / `pl.from_arrow` /
`pl.DataFrame.with_columns` wrapped to accumulate their wall under the real
`transform_df`. Plus a direct micro-measurement of `with_columns` cost.

## Result (2M rows)

| component | wall | note |
|---|---:|---:|
| total `transform_df` | 2520.6 ms | |
| `to_arrow` (export) | 32.7 ms | |
| `from_arrow` (import) | 40.5 ms | |
| **Arrow conversions total** | **73.2 ms** | **2.9%** ← the real ceiling |
| `with_columns` (in the run) | 1059 ms | but see below |

The `with_columns` total *looks* huge, but a direct micro-measurement shows why
it isn't removable overhead:

| operation (2M×5 frame) | wall |
|---|---:|
| `with_columns` **column replace** (the engine's per-transform re-insert) | **0.02 ms** |
| `with_columns` **with an expr** (`str.strip_chars()` — real vectorized work) | **29 ms** |

So the per-transform column re-insertion is genuinely zero-copy (0.02 ms). The
1059 ms attributed to `with_columns` is almost entirely **Polars executing
SIMD-vectorized expression work** (the expr-mode transforms + the phone
fast-path's internal Polars ops) — the actual transform computation, not
orchestration.

## Verdict — NO-GO

The only overhead threading Arrow through the engine could remove is the **~3%**
of Polars↔Arrow conversions, and even that is optimistic:

1. **Conversions are ~3%** (73 ms / 2520 ms) — near-zero-copy Arrow C-data
   export/import, exactly as the frame-container spike found. The per-transform
   column re-insert (`with_columns`) is 0.02 ms — not overhead.
2. **It would REGRESS the fast path.** The expr-mode transforms (strip, lowercase,
   collapse_whitespace as Polars expressions, and phone's vectorized fast tier)
   run on Polars' SIMD-vectorized `str.*` kernels. Replacing those with a
   hand-rolled per-value Arrow loop is *slower* — the 29 ms `str.strip_chars()`
   is Polars doing work a manual Arrow pass can't beat.
3. **The chainable case is already Arrow-native.** Runs of owned kernels already
   fuse into one Arrow pass (ADR 0034). Non-owned transforms (phone/date) are
   one-per-column and need Series/Polars anyway (they lean on vectorized Polars +
   the `phonenumbers`/`dateutil` residual).

Same shape as the frame-container NO-GO and the perf-audit lesson: the "evict/
replace Polars" intuition over-predicts; measured, Polars isn't the cost — it's
doing legitimate fast vectorized work, and the kernels are the rest.

## Where the real GoldenFlow headroom is

Not the Arrow substrate (~3%, and regressive). The measured levers:

- **Owned kernel speed** (done this cycle: 3–8× on the hot name/text/number
  kernels; `benches/kernel_rank.rs`).
- **The series-mode residual** — `phone_e164`/`date_iso8601` fall back to per-row
  `phonenumbers`/`dateutil` for the rows the vectorized fast tier + native kernel
  can't resolve (corrupt / international). That per-row Python-library tail is the
  remaining slow spot on messy data; widening the native residual coverage
  (already the shape of `apply_with_residual`) is the real win, not the substrate.
- More owned-kernel coverage so fewer transforms hit the Python fallback.

"Arrow everywhere" is closed as **not worth it**; revisit only if a future profile
shows the Polars↔Arrow conversions (not the vectorized work) dominating a real
workload — they do not today (~3%).
