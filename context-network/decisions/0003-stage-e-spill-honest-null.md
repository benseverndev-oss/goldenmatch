# 0003 — Stage E spill verdict: HONEST-NULL on one-box survival

**Status:** accepted (2026-06-03, PRs #705 harness + #706 verdict)
**Evidence:** bench run `26911207018` on `large-new-64GB`

## Context
Stage E asked: does the spine's relational spill let it SURVIVE where the in-memory
pipeline OOMs? Measured 3 variants (in-memory `bucket` / `spine_nospill` / `spine_spill`)
via `scripts/bench_datafusion_spine_spill.py` + `bench-datafusion-spine-spill.yml`.

## What was measured (200K rows, soundex-on-last_name, jw≥0.85, pool 2GB)
| variant | wall | peak RSS | pairs/dupes | clusters |
|---|---|---|---|---|
| bucket | 17.6s | 3572 MB | 199,979 dupes | 5628 |
| spine_nospill | 108.1s | 4765 MB | 5,203,861 raw pairs | 5606 |
| spine_spill | 111.0s | 4820 MB | 5,203,861 raw pairs | 5606 |

At 1M rows the spine was OOM-killed (job exit 143, runner-level OOM).

## Decision / verdict
- **The relational spill path is CORRECT:** `spine_spill` == `spine_nospill` output,
  byte-identical, at bounded ~4.8GB RSS. The fair-spill pool works.
- **One-box survival does NOT bind.** The spine emits ~5.2M raw above-threshold pairs at
  200K (~26/row), collected driver-side to feed the UF break — the in-memory island the
  spill pool doesn't cover. With soundex blocking that island grows ~O(N²) and OOMs the
  64GB box at ~1M rows, BEFORE the in-memory `bucket` comparand (3.5GB at 200K) would.
- **Architecturally precluded on one box:** `bucket` OOMs only on large blocks (its
  O(block²) score matrix), but large blocks also explode the spine's pair set → the UF
  island OOMs first. No reachable sub-50M-pair one-box scale shows "in-memory dies, spine
  survives" for this workload. This is exactly the spec's anticipated honest-null.

## Consequences
- **Do NOT flip the `mode` default** on a one-box survival claim. The relational-spill +
  determinism gates are met; the one-box-survival gate is not (and can't be here).
- The spine's value is **engine portability / Sail**, not one-box survival — consistent
  with [0001-gate-reframe-engine-portability.md](0001-gate-reframe-engine-portability.md).
- Process gotcha learned: a full-box OOM SIGTERMs the WHOLE GitHub job (exit 143); it is
  NOT a catchable per-child SIGKILL. Robust per-variant OOM testing needs a per-child
  cgroup `MemoryMax` cap (a scoped follow-up).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-03
