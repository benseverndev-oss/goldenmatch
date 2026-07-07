# Frame-container eviction (Pillar-1 #3) — measurement spike + NO-GO

**Date:** 2026-07-06 • **Status:** Decided — **do not evict `pl.DataFrame`**

## Question

Pillar-1 of the "Rust is the reference" thesis is the *Great Polars Eviction* —
pull transform execution off Polars. The fused columnar apply (ADR 0034) already
evicted the per-transform orchestration (N Arrow round-trips + N `with_columns` +
N affected-scans → one native pass). The remaining, much larger step is evicting
the **frame container itself** — replacing `pl.DataFrame` as the substrate.

Before committing to that (a multi-week rewrite of Polars' container + I/O +
expression + join/dedup/filter machinery), measure the ceiling: **on the
already-fused path, what fraction of wall is Polars plumbing vs the native
kernel?** The plumbing fraction is the *most* evicting the container could ever
buy; the kernel work is irreducible and exists in any container.

## Method

`scratchpad/frame_container_spike.py` — a 2M-row messy string column, a realistic
fused cleanup chain (`strip → lowercase → collapse_whitespace → remove_html_tags
→ remove_punctuation → normalize_unicode`), 5-run **min** wall per component,
native fused kernel on. Components isolated: `to_arrow` (export), the native
`apply_chain_ops_arrow` kernel, `from_arrow` (import), `with_columns` (write-back),
and the full `transform_df` end-to-end.

## Result (2M rows)

| Component | min wall | share |
|---|---:|---:|
| `to_arrow` (export) | 7.6 ms | |
| **native KERNEL** | **540.6 ms** | **96.9%** (irreducible) |
| `from_arrow` (import) | 9.7 ms | |
| `with_columns` (write-back) | ~0.0 ms | (Arrow-backed, zero-copy) |
| **Polars plumbing total** | **17.4 ms** | **3.1%** ← eviction ROI ceiling |

Full `transform_df` end-to-end (engine + manifest + samples) = 521.7 ms; the
kernel is ~100% of it (the manifest/sample overhead is in the noise).

**Worst case** (short 2-op runs of the *cheapest* kernels, where plumbing is the
largest relative share):

| chain | kernel | plumbing | plumbing share |
|---|---:|---:|---:|
| `strip → lowercase` | 108.6 ms | 14.3 ms | 11.7% |
| `strip → collapse_whitespace` | 92.7 ms | 14.0 ms | 13.1% |

## Verdict — NO-GO

Evicting the frame container has a **~3% wall ceiling** on realistic chains
(~12–13% only for trivial 2-op runs of the cheapest kernels), for an enormous,
high-risk rewrite. The measurement is decisive in three ways:

1. **The kernel already dominates (~97%).** That is the *goal state* of Pillar-1:
   Rust owns execution. Polars is no longer the bottleneck on the fused path — the
   owned kernels are. There is no Polars overhead left to evict that matters.
2. **The plumbing that remains is Arrow C-data export/import**, which is
   near-zero-copy and which *any* native frame would still pay at the I/O and
   interop boundary. Replacing the container doesn't remove it.
3. **RSS is already captured by fusion** (ADR 0034: −22% at 5M by not
   materializing intermediate columns). The container's own bookkeeping is
   negligible next to the column data itself, which a native frame must also hold.

This is the same shape as the banked perf-audit lesson (`CLAUDE.md`: static
counts over-predicted; every measured item came in under the framing). The
"evict Polars" intuition over-predicted; measured, Polars isn't the cost.

## Where the remaining Pillar-1 headroom actually is

Not the container — the **kernel**. The 97% is owned Rust we control:

- **SIMD / vectorized kernels** for the hot char-class scans (`collapse_whitespace`,
  `remove_punctuation`, `normalize_unicode`) — the per-byte loops are the wall now.
- **Algorithmic**: fold multiple char-class passes into one scan where the kernels
  compose (e.g. strip+collapse+lowercase in a single pass over the bytes).
- Extend fusion **coverage** (done: string, numeric f64, nullable URL/company/email;
  ADR 0034) so more of a real pipeline rides the one-pass path.

Polars stays the container, I/O layer, and expression engine for
join/dedup/filter/rename — all of which it does well and none of which the fusion
work needs to own. The container eviction is closed as **not worth it**; revisit
only if a future profile shows Polars plumbing dominating a real workload (it does
not today).
