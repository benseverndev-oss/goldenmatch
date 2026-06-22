# Auto-config smarter levers S1–S3 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route the auto-config "smarter" decision logic (corrected pair-count extrapolation + three adaptive thresholds) through the shared pyo3-free `goldenmatch-autoconfig-core` Rust kernel so Python, TS (wasm), and SQL inherit each fix once, proven by golden vectors.

**Architecture:** Each lever splits into a **decision kernel** (pure deterministic formula/threshold → the Rust core) and **measurement** (block/collision counting → per-surface). Surfaces measure, then call the core for the decision. The pure-Python implementation is both the runtime fallback and the golden-vector oracle; Rust ≡ Python ≡ TS is asserted byte-for-byte on generated vectors. Ships as one bench-gated PR per lever, S1 first (S2b consumes S1's output).

**Tech Stack:** Rust (`autoconfig-core` crate, `serde`/`serde_json`; `autoconfig-wasm` wasm-bindgen; `native` pyo3), Python 3 (`goldenmatch.core`, Polars), TypeScript (`autoconfigWasm.ts`), pytest + `cargo test` + vitest, DQbench/F1 benches.

**Spec:** `docs/superpowers/specs/2026-06-22-autoconfig-smarter-faster-s1-s3-design.md`

---

## Scope & decomposition

Four sequenced PRs. **S1 is specified in full bite-sized TDD detail below** (it is foundational — S2b depends on its corrected pair count — and it establishes the core-kernel + measurement + golden + binding plumbing the other three reuse). **S2a, S2b, S3 are task-level outlines** at the end; each gets bite-sized expansion when it is picked up, following the S1 pattern, once S1's shipped kernel API is concrete.

### Two implementation decisions locked in (refinements over the spec's prose)

1. **Integer-exact kernel arithmetic.** The spec says scale pairs by `ratio²`. Implement it as pure integer floor arithmetic — `pairs_out = total_comparisons * n_full * n_full // (n_sample * n_sample)` (Python arbitrary-precision; Rust `u128` intermediate cast back to `u64`) — NOT float `ratio²`. Float `ratio²` then `int()` would drift between Python and Rust and break golden parity. Integer floor division is identical across both.
2. **`chao1_f1`/`chao1_f2` are `Optional` and populated on the fast static path only.** `None` = not measured → kernel uses **linear** `n_blocks * ratio` (the spec-sanctioned fallback). A present value (including `0`) = measured → kernel uses Chao1 richness. This mirrors the existing `FieldStats.estimated_full_cardinality` precedent (`complexity_profile.py:185-207`) and disambiguates "no singletons in this sample" (Chao1 saturation) from "singletons not counted" (fallback). The exact `build_blocks` fallback path cannot recover pre-singleton-drop counts, so it leaves them `None` → linear; only `_fast_static_block_sizes` (the common static case) populates them. This is a contained, correct narrowing of the spec's "both paths" suggestion.

---

## File structure (S1)

| File | Change | Responsibility |
|---|---|---|
| `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` | Modify | `BlockingProfile` gains `chao1_f1`/`chao1_f2` Optional fields; `extrapolate_to` becomes the corrected kernel (oracle) + native dispatch |
| `packages/python/goldenmatch/goldenmatch/core/blocker.py` | Modify | `_fast_static_block_sizes` computes F1/F2 from the pre-filter agg; `measure_blocking_profile` threads them into the profile |
| `packages/python/goldenmatch/goldenmatch/core/autoconfig_native.py` | Modify | Add `extrapolation_input_to_json` + `extrapolation_from_json` JSON helpers |
| `packages/rust/extensions/autoconfig-core/src/extrapolate.rs` | Create | `extrapolate_pair_count` kernel + `ExtrapolationInput`/`ExtrapolationOutput` serde structs + unit tests |
| `packages/rust/extensions/autoconfig-core/src/lib.rs` | Modify | Re-export the new fn + structs from the crate root |
| `packages/rust/extensions/autoconfig-core/tests/golden.rs` | Modify | Add `extrapolation_golden_parity` test |
| `packages/rust/extensions/autoconfig-core/golden/extrapolation_vectors.json` | Create (generated) | The cross-surface parity fixture |
| `scripts/gen_autoconfig_golden.py` | Modify | Add `gen_extrapolation_vectors()` from the pure-Python oracle |
| `packages/rust/extensions/autoconfig-wasm/src/lib.rs` | Modify | Add `autoconfig_extrapolate_pair_count` wasm shim |
| `packages/rust/extensions/native/src/autoconfig.rs` | Modify | Add `autoconfig_extrapolate_pair_count` pyo3 shim |
| `packages/rust/extensions/native/src/lib.rs` | Modify | Register the new pyfunction |
| `packages/rust/extensions/native/Cargo.toml` + `pyproject.toml` | Modify | Bump `0.1.7 → 0.1.8` (lockstep) |
| `packages/typescript/goldenmatch/src/core/autoconfigWasm.ts` | Modify | Expose `extrapolatePairCount` + snake↔camel adapter |
| `packages/typescript/goldenmatch/tests/parity/autoconfig-core.parity.test.ts` | Modify | Add the extrapolation vectors to the TS parity test |
| `scripts/bench_autoconfig_sample_quality.py` | Modify | Add a programmatic post-fix assertion (`extrap/true → ~1.0`) |
| `tests/.../test_autoconfig_native_parity.py` (native lane) | Modify | Add extrapolation parity (native ON ≡ pure Python) |

### The kernel (reference — both implementations must match this exactly)

```
extrapolate_pair_count(total_comparisons, n_blocks, singleton_block_count,
                       chao1_f1, chao1_f2, n_rows_sample, n_rows_full):
    if n_rows_sample <= 0 or n_rows_full <= 0:
        return (n_blocks, total_comparisons, singleton_block_count)   # unchanged

    # Pairs: integer-exact ratio^2 scaling, capped at the all-pairs maximum.
    pairs = total_comparisons * n_rows_full * n_rows_full // (n_rows_sample * n_rows_sample)
    pairs_cap = n_rows_full * (n_rows_full - 1) // 2
    pairs = min(pairs, pairs_cap)

    # n_blocks: Chao1 richness when F1/F2 measured, else linear fallback.
    # Integer-floor (// not float) so Python and Rust agree bit-for-bit.
    if chao1_f1 is None or chao1_f2 is None:
        blocks = n_blocks * n_rows_full // n_rows_sample              # linear
    else:
        observed = n_blocks + chao1_f1                               # re-add dropped singletons
        blocks = observed + chao1_f1 * chao1_f1 // (2 * (chao1_f2 + 1))   # Chao1
    blocks = min(blocks, n_rows_full)                                # cap (REACHABLE via Chao1)

    # singleton_block_count: linear (not load-bearing; feeds health()). Integer-floor.
    singletons = singleton_block_count * n_rows_full // n_rows_sample

    return (blocks, pairs, singletons)
```

Notes that matter for parity and tests:
- `n_blocks` as measured counts only size≥2 blocks (singletons dropped), so `observed = n_blocks + chao1_f1` reconstructs the true observed distinct-block count before applying Chao1.
- **Every arithmetic path is integer-floor (`//`), not float** — the linear and singleton paths use `x * n_full // n_sample`, not the old `int(x * (n_full/n_sample))`. This removes float entirely so the Python oracle and the Rust kernel are bit-identical (Rust uses `u128` intermediates to avoid `u64` overflow on `x * n_full`). Behaviorally identical to the old float form for all realistic N; only differs at ULP-extreme N that never occurs.
- **The all-pairs *pairs* cap is a defensive rail, structurally inert for legitimate inputs.** A measured `total_comparisons ≤ C(n_sample, 2)`, which makes `raw_pairs = tc·n_full²//n_sample² ≤ cap` always (`raw/cap ≤ (n_sample−1)/n_sample < 1`). It only clamps pathological inputs where `tc` exceeds the sample's all-pairs maximum. Keep it (cheap hard invariant: estimate never exceeds physical max), but the cap-trigger test must use a deliberately out-of-range `tc`. The **n_blocks cap IS reachable** (Chao1 with many singletons can exceed `n_full`).

---

## Task 1: `BlockingProfile` gains `chao1_f1` / `chao1_f2` fields

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py:253-265` (dataclass fields)
- Test: `packages/python/goldenmatch/tests/test_complexity_profile.py` (or the existing profile test module — grep for `BlockingProfile(` in tests; create `test_complexity_profile.py` if none)

- [ ] **Step 1: Write the failing test**

```python
def test_blocking_profile_chao1_fields_default_none():
    from goldenmatch.core.complexity_profile import BlockingProfile
    bp = BlockingProfile(n_blocks=5, total_comparisons=10)
    assert bp.chao1_f1 is None
    assert bp.chao1_f2 is None

def test_blocking_profile_chao1_fields_settable():
    from goldenmatch.core.complexity_profile import BlockingProfile
    bp = BlockingProfile(n_blocks=5, total_comparisons=10, chao1_f1=3, chao1_f2=1)
    assert bp.chao1_f1 == 3
    assert bp.chao1_f2 == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages/python/goldenmatch/tests/test_complexity_profile.py -k chao1 -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'chao1_f1'`
(Set `POLARS_SKIP_CPU_CHECK=1` and `GOLDENMATCH_NATIVE=0` in the env per the local-run notes.)

- [ ] **Step 3: Add the fields**

In the `BlockingProfile` dataclass (after `oversized_block_count: int = 0`):

```python
    # Chao1 mark-recapture inputs for n_blocks richness extrapolation (S1).
    # Optional: None => not measured (extrapolate_to uses linear n_blocks
    # fallback); a present value (incl. 0) => measured (Chao1 richness).
    # Populated only by the fast static measurement path (blocker.py).
    chao1_f1: int | None = None   # blocks with exactly 1 sampled row (singletons)
    chao1_f2: int | None = None   # blocks with exactly 2 sampled rows (doubletons)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages/python/goldenmatch/tests/test_complexity_profile.py -k chao1 -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/tests/test_complexity_profile.py
git commit -m "feat(autoconfig): BlockingProfile chao1_f1/chao1_f2 fields (S1)"
```

---

## Task 2: Measurement — count F1/F2 in the fast static path

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py:88-151` (`_fast_static_block_sizes` returns F1/F2)
- Modify: `packages/python/goldenmatch/goldenmatch/core/blocker.py:154-222` (`measure_blocking_profile` threads them in)
- Test: `packages/python/goldenmatch/tests/test_blocker_measure.py` (grep for an existing `measure_blocking_profile` test; create if none)

The agg in `_fast_static_block_sizes` has every per-key size **before** the `s >= 2` drop. Count F1/F2 there (after the existing null/sentinel-key filter, before the size filter).

- [ ] **Step 1: Write the failing test**

Construct a static-blocking frame whose block-size distribution is known. Example: a `last_name` column with values producing blocks of sizes {1, 1, 1, 2, 3} (three singletons, one doubleton, one triple). Assert the measured profile reports `chao1_f1 == 3`, `chao1_f2 == 1`, and (unchanged) `n_blocks == 2` (only size≥2 blocks), `total_comparisons == 1 + 3 == 4`.

```python
def test_measure_blocking_profile_counts_f1_f2():
    import polars as pl
    from types import SimpleNamespace
    from goldenmatch.core.blocker import measure_blocking_profile
    # sizes: A x1, B x1, C x1, D x2, E x3  -> 3 singletons, 1 doubleton, 1 triple
    names = ["A", "B", "C", "D", "D", "E", "E", "E"]
    df = pl.DataFrame({"last_name": names})
    blocking = SimpleNamespace(
        strategy="static", keys=[SimpleNamespace(fields=["last_name"])],
        passes=[], auto_select=False, max_block_size=1000, skip_oversized=False,
    )
    cfg = SimpleNamespace(blocking=blocking)
    bp = measure_blocking_profile(df, cfg)
    assert bp is not None
    assert bp.chao1_f1 == 3
    assert bp.chao1_f2 == 1
    assert bp.n_blocks == 2          # only D(2) and E(3) survive the size>=2 drop
    assert bp.total_comparisons == 1 + 3   # C(2,2)=1 + C(3,2)=3
```

(Confirm the exact `keys`/`key_config` shape `_build_block_key_expr` expects by reading `_build_block_key_expr` + how `config.keys[i]` is consumed; adjust the `SimpleNamespace` to match the real `BlockingKey`/config model — prefer constructing the real config objects over `SimpleNamespace` if the expr builder introspects typed attributes.)

- [ ] **Step 2: Run test to verify it fails**

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_blocker_measure.py -k f1_f2 -v`
Expected: FAIL — `AttributeError`/`AssertionError` (`chao1_f1` is `None`, not measured yet).

- [ ] **Step 3: Implement F1/F2 counting**

Change `_fast_static_block_sizes` to return `tuple[list[int], int, int] | None` = `(sizes, f1, f2)`. Count F1/F2 from the **reassigned (filtered) `agg`** — i.e. AFTER the eager null/sentinel-key `agg = agg.filter(...)` reassignment (so null/sentinel groups don't inflate F1, per spec design lines 142-143) and BEFORE the `s >= 2` comprehension:

```python
        all_key_sizes = agg.get_column("__sz__").to_list()
        f1 += sum(1 for s in all_key_sizes if s == 1)
        f2 += sum(1 for s in all_key_sizes if s == 2)
        sizes = [s for s in all_key_sizes if s >= 2]
```

Initialize `f1 = 0`, `f2 = 0` before the `for key_config in keys` loop; accumulate across keys; return `(all_sizes, f1, f2)` instead of `all_sizes`. Keep the `return None` bail-outs returning `None`.

In `measure_blocking_profile`, update the caller:

```python
        fast = _fast_static_block_sizes(lf, blocking_cfg)
        if fast is None:
            sizes = []
            for b in build_blocks(lf, blocking_cfg):
                try:
                    sizes.append(b.df.select(pl.len()).collect().item())
                except Exception:
                    sizes.append(0)
            chao1_f1 = None    # exact path can't recover pre-drop counts -> linear fallback
            chao1_f2 = None
        else:
            sizes, chao1_f1, chao1_f2 = fast
```

Then pass `chao1_f1=chao1_f1, chao1_f2=chao1_f2` into the `BlockingProfile(...)` constructor.

- [ ] **Step 4: Run test to verify it passes**

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_blocker_measure.py -k f1_f2 -v`
Expected: PASS

- [ ] **Step 5: Regression — existing measure tests still green**

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_blocker_measure.py -v`
Expected: PASS (the new return tuple did not break existing callers — confirm `_fast_static_block_sizes` has no other caller via grep before relying on this).

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/blocker.py packages/python/goldenmatch/tests/test_blocker_measure.py
git commit -m "feat(autoconfig): measure chao1 F1/F2 in fast static blocking path (S1)"
```

---

## Task 3: Pure-Python kernel — corrected `extrapolate_to` (the oracle)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py:277-296` (`extrapolate_to`)
- Test: `packages/python/goldenmatch/tests/test_complexity_profile.py`

- [ ] **Step 1: Write the failing tests** (these pin the exact numbers the Rust core must also produce)

```python
import math
from goldenmatch.core.complexity_profile import BlockingProfile

def test_extrapolate_pairs_quadratic():
    # ratio=100; pairs scale by ratio^2; well under the all-pairs cap.
    bp = BlockingProfile(n_blocks=10, total_comparisons=100,
                         chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(1_000, 100_000)
    assert out.total_comparisons == 100 * 100_000 * 100_000 // (1_000 * 1_000)  # 1_000_000
    assert out.n_blocks == 10 * 100_000 // 1_000   # linear fallback (integer-floor): 1000

def test_extrapolate_pairs_cap_inert_for_legit_input():
    # All-pairs cap does NOT trigger for legitimate measured inputs
    # (total_comparisons <= C(n_sample,2)): raw stays below the cap.
    # tc=10, ns=10, nf=20 -> raw = 10*20*20//(10*10) = 40; cap = 20*19//2 = 190.
    bp = BlockingProfile(n_blocks=2, total_comparisons=10, chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(10, 20)
    assert out.total_comparisons == 40   # min(40, 190) -> 40, cap inert

def test_extrapolate_pairs_cap_clamps_pathological_input():
    # Defensive rail: a tc that EXCEEDS the sample's all-pairs max (C(10,2)=45)
    # is pathological; the cap clamps it to the physical maximum.
    # tc=50, ns=10, nf=20 -> raw = 50*400//100 = 200 > cap 190 -> clamp to 190.
    bp = BlockingProfile(n_blocks=2, total_comparisons=50, chao1_f1=None, chao1_f2=None)
    out = bp.extrapolate_to(10, 20)
    assert out.total_comparisons == 20 * 19 // 2   # 190, the all-pairs cap

def test_extrapolate_nblocks_chao1():
    # F1/F2 present -> Chao1 richness: observed=(n_blocks+F1), + F1^2//(2*(F2+1)).
    bp = BlockingProfile(n_blocks=50, total_comparisons=100, chao1_f1=10, chao1_f2=5)
    out = bp.extrapolate_to(1_000, 100_000)
    observed = 50 + 10
    assert out.n_blocks == observed + 10 * 10 // (2 * (5 + 1))   # 60 + 8 = 68

def test_extrapolate_noop_on_bad_args():
    bp = BlockingProfile(n_blocks=5, total_comparisons=10)
    assert bp.extrapolate_to(0, 100) is bp
    assert bp.extrapolate_to(100, 0) is bp
```

- [ ] **Step 2: Run to verify they fail**

Run: `GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_complexity_profile.py -k extrapolate -v`
Expected: FAIL (current `extrapolate_to` scales linearly → `total_comparisons == 10_000`, not `1_000_000`).

- [ ] **Step 3: Rewrite `extrapolate_to`**

```python
    def extrapolate_to(self, n_rows_sample: int, n_rows_full: int) -> BlockingProfile:
        """Project sample's pair-count signal to a full-data row count (S1).

        Pair count grows quadratically with the row-count ratio, so candidate
        pairs scale by ratio**2 (integer-exact), capped at the all-pairs
        maximum. n_blocks uses a Chao1 richness estimate when F1/F2 were
        measured (the fast static path), else linear fallback. Block-size
        percentiles are not scaled (shape ~invariant to N).
        """
        import dataclasses

        if n_rows_sample <= 0 or n_rows_full <= 0:
            return self

        pairs = (
            self.total_comparisons * n_rows_full * n_rows_full
            // (n_rows_sample * n_rows_sample)
        )
        pairs = min(pairs, n_rows_full * (n_rows_full - 1) // 2)

        if self.chao1_f1 is None or self.chao1_f2 is None:
            blocks = self.n_blocks * n_rows_full // n_rows_sample
        else:
            observed = self.n_blocks + self.chao1_f1
            blocks = observed + self.chao1_f1 * self.chao1_f1 // (2 * (self.chao1_f2 + 1))
        blocks = min(blocks, n_rows_full)

        return dataclasses.replace(
            self,
            n_blocks=blocks,
            total_comparisons=pairs,
            singleton_block_count=self.singleton_block_count * n_rows_full // n_rows_sample,
        )
```

- [ ] **Step 4: Run to verify they pass**

Run: `GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_complexity_profile.py -k extrapolate -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/tests/test_complexity_profile.py
git commit -m "feat(autoconfig): corrected pair-count extrapolation oracle (ratio^2 + Chao1 + cap) (S1)"
```

---

## Task 4: Rust core `extrapolate_pair_count` kernel

**Files:**
- Create: `packages/rust/extensions/autoconfig-core/src/extrapolate.rs`
- Modify: `packages/rust/extensions/autoconfig-core/src/lib.rs` (add `mod extrapolate;` + re-exports)
- Test: inline `#[cfg(test)] mod tests` in `extrapolate.rs`

The Python kernel is the oracle; this must match it bit-for-bit. Use `u128` for the pair intermediate (Python ints are arbitrary precision; `u64` would overflow at `total_comparisons * n_full²`).

- [ ] **Step 1: Write the failing Rust unit tests** (mirror the Python pin-tests)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    fn input(tc: u64, nb: u64, f1: Option<u64>, f2: Option<u64>, ns: u64, nf: u64) -> ExtrapolationInput {
        ExtrapolationInput { total_comparisons: tc, n_blocks: nb, singleton_block_count: 0,
                             chao1_f1: f1, chao1_f2: f2, n_rows_sample: ns, n_rows_full: nf }
    }

    #[test]
    fn pairs_quadratic() {
        let o = extrapolate_pair_count(&input(100, 10, None, None, 1_000, 100_000));
        assert_eq!(o.total_comparisons, 1_000_000);
        assert_eq!(o.n_blocks, 1_000); // linear fallback
    }

    #[test]
    fn pairs_cap_inert_for_legit_input() {
        // legit tc (<= C(10,2)=45): raw=10*400/100=40 < cap 190 -> 40
        let o = extrapolate_pair_count(&input(10, 2, None, None, 10, 20));
        assert_eq!(o.total_comparisons, 40);
    }

    #[test]
    fn pairs_cap_clamps_pathological() {
        // pathological tc (> C(10,2)): raw=50*400/100=200 > cap 190 -> 190
        let o = extrapolate_pair_count(&input(50, 2, None, None, 10, 20));
        assert_eq!(o.total_comparisons, 190);
    }

    #[test]
    fn nblocks_chao1() {
        let o = extrapolate_pair_count(&input(100, 50, Some(10), Some(5), 1_000, 100_000));
        assert_eq!(o.n_blocks, 68); // (50+10) + 100/(2*6)=8
    }

    #[test]
    fn noop_bad_args() {
        let o = extrapolate_pair_count(&input(10, 5, None, None, 0, 100));
        assert_eq!(o.total_comparisons, 10);
        assert_eq!(o.n_blocks, 5);
    }
}
```

- [ ] **Step 2: Run to verify they fail (compile error — fn doesn't exist)**

Run (via the direct toolchain cargo per `reference_rustup_proxy_exfat_direct_binary`, or plain `cargo` in CI):
`cargo test --manifest-path packages/rust/extensions/autoconfig-core/Cargo.toml extrapolate`
Expected: FAIL to compile (`cannot find function extrapolate_pair_count`).

- [ ] **Step 3: Implement the kernel + structs**

```rust
//! S1 pair-count extrapolation kernel: corrects the sample->full projection
//! (ratio^2 for pairs + Chao1 block richness + all-pairs cap). Integer-exact
//! to stay byte-parity with the Python oracle (`complexity_profile.extrapolate_to`).
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ExtrapolationInput {
    pub total_comparisons: u64,
    pub n_blocks: u64,
    pub singleton_block_count: u64,
    pub chao1_f1: Option<u64>,   // None => linear n_blocks fallback
    pub chao1_f2: Option<u64>,
    pub n_rows_sample: u64,
    pub n_rows_full: u64,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ExtrapolationOutput {
    pub n_blocks: u64,
    pub total_comparisons: u64,
    pub singleton_block_count: u64,
}

pub fn extrapolate_pair_count(input: &ExtrapolationInput) -> ExtrapolationOutput {
    let ns = input.n_rows_sample;
    let nf = input.n_rows_full;
    if ns == 0 || nf == 0 {
        return ExtrapolationOutput {
            n_blocks: input.n_blocks,
            total_comparisons: input.total_comparisons,
            singleton_block_count: input.singleton_block_count,
        };
    }
    // Pairs: integer-exact ratio^2, capped at all-pairs maximum. u128 intermediate.
    let pairs_raw = (input.total_comparisons as u128) * (nf as u128) * (nf as u128)
        / ((ns as u128) * (ns as u128));
    let cap = (nf as u128) * ((nf - 1) as u128) / 2;
    let pairs = pairs_raw.min(cap) as u64;

    // n_blocks: Chao1 richness when F1/F2 measured, else linear (float * ratio,
    // truncating cast — matches Python int(n_blocks * nf / ns)).
    let blocks = match (input.chao1_f1, input.chao1_f2) {
        (Some(f1), Some(f2)) => {
            let observed = input.n_blocks + f1;
            (observed + f1 * f1 / (2 * (f2 + 1))).min(nf)
        }
        _ => {
            // linear fallback, integer-floor via u128 (avoids u64 overflow on n_blocks*nf)
            let linear = ((input.n_blocks as u128) * (nf as u128) / (ns as u128)) as u64;
            linear.min(nf)
        }
    };

    let singletons =
        ((input.singleton_block_count as u128) * (nf as u128) / (ns as u128)) as u64;

    ExtrapolationOutput { n_blocks: blocks, total_comparisons: pairs, singleton_block_count: singletons }
}
```

In `lib.rs`: add `mod extrapolate;` and `pub use extrapolate::{extrapolate_pair_count, ExtrapolationInput, ExtrapolationOutput};` (match the existing re-export style — read `lib.rs` first to confirm the `pub use` block).

- [ ] **Step 4: Run to verify they pass + clippy clean**

Run: `cargo test --manifest-path packages/rust/extensions/autoconfig-core/Cargo.toml extrapolate`
Expected: PASS
Run (CI only — clippy cannot run locally per `reference_rustup_proxy_exfat_direct_binary`): `cargo clippy --manifest-path packages/rust/extensions/autoconfig-core/Cargo.toml --all-targets -- -D warnings`

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/autoconfig-core/src/extrapolate.rs packages/rust/extensions/autoconfig-core/src/lib.rs
git commit -m "feat(autoconfig-core): extrapolate_pair_count kernel (S1)"
```

---

## Task 5: Golden vectors — cross-surface parity for the kernel

**Files:**
- Modify: `scripts/gen_autoconfig_golden.py` (add `gen_extrapolation_vectors()` + write `extrapolation_vectors.json`)
- Create (generated): `packages/rust/extensions/autoconfig-core/golden/extrapolation_vectors.json`
- Modify: `packages/rust/extensions/autoconfig-core/tests/golden.rs` (add the parity test)

- [ ] **Step 1: Add the generator** (drives the REAL pure-Python `extrapolate_to` oracle)

In `gen_autoconfig_golden.py`, add a function that builds `BlockingProfile`s across a grid (vary `total_comparisons`, `n_blocks`, `chao1_f1`/`f2` ∈ {None, 0, small, large}, `n_rows_sample`, `n_rows_full`), calls `bp.extrapolate_to(ns, nf)`, and emits `{"input": {...}, "expected": {"n_blocks":..., "total_comparisons":..., "singleton_block_count":...}}`. Include ≥ 30 vectors and make sure the grid explicitly hits all five branches: (a) realistic quadratic (cap inert), (b) **pathological** `total_comparisons > C(n_sample,2)` that triggers the *pairs* cap, (c) Chao1-saturation (`chao1_f1` large, `chao1_f2` small) that triggers the *n_blocks* `min(n_full)` cap, (d) linear fallback (`chao1_f1`/`f2 = None`), (e) noop `n_sample<=0` / `n_full<=0`. `None` for `chao1_f1`/`f2` serializes to JSON `null` (matches the `Option<u64>` deserialize). All kernel arithmetic is integer-floor (incl. `u128` Rust intermediates), so no magnitude-bounding is required for parity — vectors reproduce bit-for-bit at any N. Add `gen_extrapolation_vectors()` to `main()` with a `>= 30` count assert and write `OUT_DIR / "extrapolation_vectors.json"`.

- [ ] **Step 2: Generate the fixture**

Run: `GOLDENMATCH_NATIVE=0 python scripts/gen_autoconfig_golden.py`
Expected: writes `extrapolation_vectors.json` (and re-writes planner/classifier as a no-op diff — confirm `git diff --stat` shows only the new file changed materially).

- [ ] **Step 3: Add the Rust golden test**

In `golden.rs`, mirror `planner_golden_parity`:

```rust
const EXTRAPOLATION_JSON: &str = include_str!("../golden/extrapolation_vectors.json");

#[test]
fn extrapolation_golden_parity() {
    use goldenmatch_autoconfig_core::{extrapolate_pair_count, ExtrapolationInput};
    let vectors: Vec<Value> = serde_json::from_str(EXTRAPOLATION_JSON)
        .expect("failed to parse extrapolation_vectors.json");
    assert!(vectors.len() >= 30, "expected >= 30 vectors, got {}", vectors.len());
    let mut failures = Vec::new();
    for (idx, vec) in vectors.iter().enumerate() {
        let input: ExtrapolationInput = serde_json::from_value(vec["input"].clone()).unwrap();
        let got = serde_json::to_value(extrapolate_pair_count(&input)).unwrap();
        for f in ["n_blocks", "total_comparisons", "singleton_block_count"] {
            if got[f] != vec["expected"][f] {
                failures.push(format!("vec {idx} field {f}: got {} exp {}", got[f], vec["expected"][f]));
            }
        }
    }
    assert!(failures.is_empty(), "{} mismatches:\n{}", failures.len(), failures.join("\n"));
}
```

- [ ] **Step 4: Run the golden test**

Run: `cargo test --manifest-path packages/rust/extensions/autoconfig-core/Cargo.toml extrapolation_golden_parity`
Expected: PASS (Rust ≡ Python oracle on all vectors).

- [ ] **Step 5: Commit**

```bash
git add scripts/gen_autoconfig_golden.py packages/rust/extensions/autoconfig-core/golden/extrapolation_vectors.json packages/rust/extensions/autoconfig-core/tests/golden.rs
git commit -m "test(autoconfig-core): extrapolation golden-vector parity (S1)"
```

---

## Task 6: Bindings — wasm + pyo3 shims + version bump

**Files:**
- Modify: `packages/rust/extensions/autoconfig-wasm/src/lib.rs`
- Modify: `packages/rust/extensions/native/src/autoconfig.rs`
- Modify: `packages/rust/extensions/native/src/lib.rs` (register pyfunction)
- Modify: `packages/rust/extensions/native/Cargo.toml` + `packages/rust/extensions/native/pyproject.toml` (`0.1.7 → 0.1.8`, lockstep)

- [ ] **Step 1: wasm shim** — add to `autoconfig-wasm/src/lib.rs` (extend the import line with `extrapolate_pair_count, ExtrapolationInput`):

```rust
/// S1: a JSON `ExtrapolationInput` -> a JSON `ExtrapolationOutput`.
#[wasm_bindgen]
pub fn autoconfig_extrapolate_pair_count(input_json: &str) -> Result<String, JsError> {
    let input: ExtrapolationInput = serde_json::from_str(input_json)
        .map_err(|e| JsError::new(&format!("bad ExtrapolationInput json: {e}")))?;
    let out = extrapolate_pair_count(&input);
    serde_json::to_string(&out).map_err(|e| JsError::new(&e.to_string()))
}
```

- [ ] **Step 2: pyo3 shim** — add to `native/src/autoconfig.rs` (extend the import line):

```rust
#[pyfunction]
pub fn autoconfig_extrapolate_pair_count(input_json: &str) -> PyResult<String> {
    let input: ExtrapolationInput = serde_json::from_str(input_json)
        .map_err(|e| PyValueError::new_err(format!("bad ExtrapolationInput json: {e}")))?;
    let out = extrapolate_pair_count(&input);
    serde_json::to_string(&out).map_err(|e| PyValueError::new_err(e.to_string()))
}
```

- [ ] **Step 3: Register the pyfunction** in `native/src/lib.rs` next to the existing `autoconfig_decide_plan` registration: `m.add_function(wrap_pyfunction!(autoconfig::autoconfig_extrapolate_pair_count, m)?)?;` (match the existing wiring exactly — grep `autoconfig_decide_plan` in `lib.rs`).

- [ ] **Step 4: Version bump** — `0.1.7 → 0.1.8` in BOTH `native/Cargo.toml` `[package].version` and `native/pyproject.toml` `[project].version` (republish reads from pyproject; see the stale-wheel footgun in CLAUDE.md).

- [ ] **Step 5: Build + verify symbols (CI / cloud — local build is blocked on this box)**

Run: `python scripts/build_native.py` then a smoke check that `goldenmatch._native` exposes `autoconfig_extrapolate_pair_count`.
Run: `cargo check` on autoconfig-wasm.

- [ ] **Step 6: Commit**

```bash
git add packages/rust/extensions/autoconfig-wasm/src/lib.rs packages/rust/extensions/native/src/autoconfig.rs packages/rust/extensions/native/src/lib.rs packages/rust/extensions/native/Cargo.toml packages/rust/extensions/native/pyproject.toml
git commit -m "feat(native,wasm): extrapolate_pair_count shims + bump 0.1.8 (S1)"
```

---

## Task 7: Python native dispatch + native-parity test

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_native.py` (JSON helpers)
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` (`extrapolate_to` dispatches to native when enabled)
- Test: the native-lane parity test module (`test_autoconfig_native_parity.py`)

- [ ] **Step 1: JSON helpers** in `autoconfig_native.py` (mirror `plan_input_to_json` / `plan_from_json`):

```python
def extrapolation_input_to_json(bp, n_rows_sample, n_rows_full) -> str:
    return json.dumps({
        "total_comparisons": int(bp.total_comparisons),
        "n_blocks": int(bp.n_blocks),
        "singleton_block_count": int(bp.singleton_block_count),
        "chao1_f1": bp.chao1_f1,   # None -> null
        "chao1_f2": bp.chao1_f2,
        "n_rows_sample": int(n_rows_sample),
        "n_rows_full": int(n_rows_full),
    })

def extrapolation_from_json(s: str) -> dict:
    d = json.loads(s)
    return {"n_blocks": int(d["n_blocks"]),
            "total_comparisons": int(d["total_comparisons"]),
            "singleton_block_count": int(d["singleton_block_count"])}
```

- [ ] **Step 2: Dispatch** — at the top of `extrapolate_to`, after the `<=0` guard, add the native fast path (mirror the `autoconfig_planner.py:110-129` guard pattern: `native_enabled("autoconfig")` + `hasattr(native_module(), "autoconfig_extrapolate_pair_count")`). On hit, build JSON, call the shim, `dataclasses.replace(self, **extrapolation_from_json(out))`. On miss, fall through to the pure-Python body. Keep the pure-Python body intact as oracle + fallback.

- [ ] **Step 3: Native-parity test** — in the native lane test module, add a test that builds several `BlockingProfile`s and asserts `native ON extrapolate_to == pure-Python extrapolate_to` (skips when the ext lacks the symbol, like the existing native-parity tests).

- [ ] **Step 4: Run** — pure-Python regression (NATIVE=0) green; native lane runs in CI/cloud (built ext).

Run: `GOLDENMATCH_NATIVE=0 python -m pytest packages/python/goldenmatch/tests/test_complexity_profile.py -v`
Expected: PASS (dispatch is inert without the ext; oracle path unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_native.py packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/tests/
git commit -m "feat(autoconfig): native dispatch for extrapolate_pair_count + parity test (S1)"
```

---

## Task 8: TS surface — loader + parity (cloud / CI lane)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/core/autoconfigWasm.ts`
- Modify: `packages/typescript/goldenmatch/tests/parity/autoconfig-core.parity.test.ts`
- Copy the `extrapolation_vectors.json` into the TS parity fixtures (follow the existing emitter/copy step the TS build uses for the planner/classifier vectors).

- [ ] **Step 1:** Expose `extrapolatePairCount(input)` in `autoconfigWasm.ts` — call `autoconfig_extrapolate_pair_count`, `JSON.stringify` in / `JSON.parse` out, with the snake↔camel adapter (`total_comparisons`↔`totalComparisons`, `n_blocks`↔`nBlocks`, `chao1_f1`↔`chao1F1`, `n_rows_sample`↔`nRowsSample`, etc.).
- [ ] **Step 2:** Add the extrapolation vectors to `autoconfig-core.parity.test.ts`: load the JSON, run the TS wasm path, assert `== expected` (mind the snake/camel adapter). This is the Rust ≡ Python ≡ TS proof for the kernel.
- [ ] **Step 3:** Rebuild the embedded wasm (`scripts/build_autoconfig_wasm.mjs`), then `npx tsc --noEmit && npx vitest run`. (Cloud/CI — local TS build OOMs per `feedback_box_memory_oom_ts`.)
- [ ] **Step 4: Commit.**

> Note: the TS *measurement* (the TS profiler's own block-stat collection, incl. a `chao1_f1`/`f2` analogue) is NOT in this PR — TS inherits the `ratio²` pair fix immediately via the kernel; the n_blocks Chao1 refinement lands when the TS profiler is next touched (it passes `null` F1/F2 → linear fallback until then, exactly like the exact Python path). Tracked, not done here.

---

## Task 9: Gates — sample-quality bench assertion + planner-routing test

**Files:**
- Modify: `scripts/bench_autoconfig_sample_quality.py`
- Test: a new planner-routing regression test (Python, in the autoconfig test suite)

- [ ] **Step 1:** In `bench_autoconfig_sample_quality.py`, after the table, add a programmatic assertion that the corrected `med_ratio` is within a tolerance band of 1.0 (e.g. `0.5 ≤ med_ratio ≤ 2.0`) for sample fractions ≥ some floor — replacing the current print-only `return 0`. This converts the bench into a CI gate proving the under-estimation is fixed. (Keep the table print for humans.)
- [ ] **Step 2:** Add a planner-routing regression test: a `BlockingProfile` measured on a sample whose *true* full pair count crosses `SIMPLE_PLAN_MAX_PAIRS` (50M). Assert `apply_planner_rules(profile_after_extrapolation, ...)` now returns the `chunked` rung where the pre-fix linear extrapolation returned `simple`. This pins the user-facing under-provisioning bug.
- [ ] **Step 3:** Run both locally where possible (the bench is pure-Python, local-doable; the planner-routing test is pure-Python).

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python scripts/bench_autoconfig_sample_quality.py`
Expected: prints the table AND exits 0 with `extrap/true` near 1.0 (was < 1.0 / strongly under).

- [ ] **Step 4: Commit.**

---

## Task 10: PR wrap

- [ ] CHANGELOG entry (Python pkg + native pkg) for S1 + the 0.1.8 bump.
- [ ] Confirm `git diff origin/main --stat` is scoped to the S1 files; no stray planner/classifier golden churn.
- [ ] Open the PR; **CI must run** the full matrix (rust clippy on autoconfig-core, the native lane parity test, the TS parity lane, the bench gate, DQbench/F1 no-regress). This box does not get CI on a human branch — land via a `claude/*` cloud branch / the merge queue, same posture as the native-core arc.
- [ ] Republish `goldenmatch-native` 0.1.8 in the same change that adds the depended-on symbol (stale-wheel footgun: a new symbol behind the `hasattr` fallback silently no-ops on every `pip install goldenmatch[native]` env until the wheel ships).

---

## Follow-on PRs (S2a / S2b / S3) — task outlines

Each reuses the S1 plumbing pattern (pure-Python oracle + Rust core kernel + golden vectors + wasm/pyo3 shims + native dispatch + DQbench/F1 gate) and is its own PR. Bite-sized expansion happens when each is picked up, against S1's now-concrete API.

### S2b — adaptive sparse-match floor (do right after S1; depends on S1's pair count)
- **Core:** `sparse_match_floor(estimated_pairs: u64) -> u64 = min(50, estimated_pairs / 100)` (new fn in autoconfig-core, re-exported; wasm + pyo3 shims; golden vectors).
- **Python:** `core/indicators.py:142` `estimate_sparse_match_signal` — replace the fixed `sparse_threshold=50` with the core floor computed from the controller's `BlockingProfile.estimated_pair_count` (the S1-corrected value); thread `estimated_pairs` from the call site. Keep the per-surface collision counting.
- **Tests/gates:** unit test the floor; golden parity; DQbench/F1 no-regress.

### S3 — per-type exact-matchkey floor (closes TODO #715)
- **Core:** `exact_matchkey_floor(col_type) -> f64` table — email 0.70, phone 0.30, name/string 0.50, default 0.50 (starting values; calibrate on DQbench). New fn keyed on the core `ColType` enum; wasm + pyo3 shims; golden vectors.
- **Python:** `core/autoconfig.py:875-877` — replace the blanket `< 0.5` check with `< exact_matchkey_floor(p.col_type)` via native dispatch; zip/geo stay fully skipped (the guard above, unchanged); selection loop otherwise unchanged.
- **Tests/gates:** unit test each type's floor; a fixture where a phone column at 0.4 cardinality now backs an exact matchkey (was rejected) and an email at 0.6 now rejected (was accepted); golden parity; DQbench/F1.

### S2a — adaptive identifier floor (core classify; cross-surface for free; heaviest gate)
- **Core:** edit `classify.rs:406` `classify_by_data` — replace `cardinality_ratio >= 0.95` with `>= (1.0 - 1.0/(values.len() as f64).sqrt())`. Mirror in the Python oracle `core/autoconfig.py:216`.
- **Gate:** this regenerates the **classifier** golden vectors and re-proves Python/TS byte-parity, plus DQbench/F1 — the full classifier parity gate. Land alone, last, so any DQbench movement is attributable to the floor change.
- **Calibration:** if `1-1/√n` regresses DQbench, the spec sanctions tuning the form (`1-c/√n`) behind the bench delta before merging.

### Deferred (not in this arc)
S2c `SIMPLE_PLAN_MAX_PAIRS` (revisit once S1 lands — a fixed pair ceiling is meaningless while the input was biased); Chao1-cardinality-into-classification; the TS profiler `chao1_f1`/`f2` measurement field; native measurement kernel (Stage-D perf wash); S4/S5.

---

## Local-vs-CI execution notes

- **Local-doable** (this box): all Python unit tests (`POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0`, targeted file runs only — never the full xdist suite, it OOMs the box), the Rust `cargo test` (via the direct toolchain binary per `reference_rustup_proxy_exfat_direct_binary`), the sample-quality bench, the planner-routing test, golden-vector regeneration.
- **CI / cloud only:** `cargo clippy -D warnings` (the rustup-proxy/exFAT wall blocks it locally), the native ext build + native-parity lane, the wasm build + TS `tsc`/`vitest` (OOMs locally), DQbench/F1. Land each PR via a `claude/*` cloud branch or the merge queue — human-pushed branches get no CI in this repo.
