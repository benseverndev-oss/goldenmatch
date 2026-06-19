# Document/text near-dup blocking path Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `dedupe_df(text_corpus)` auto-block on near-dup shingles/LSH with no manual config (lexical, the done bar), and add a new pyo3-free SimHash kernel over embeddings as a semantic `strategy="simhash"` escalation.

**Architecture:** Phase A (Python only) adds a text-corpus detector + routing to `build_blocking` that emits `strategy="lsh"` (reusing the shipped #1081 kernel), and guards the controller's blocking-refit rules from swapping it. Phase B adds a SimHash (Rademacher ±1 hyperplane) kernel to `sketch-core` — byte-identical across Rust/Python/TS via golden vectors — exposed as `strategy="simhash"` + `SimHashLSHBlocker`, auto-selected when an embedder is reachable.

**Tech Stack:** Python (polars autoconfig + pytest), Rust (`sketch-core` pyo3-free + `native` pyo3, `rayon`), TypeScript (pure-TS), GitHub Actions bench.

**Spec:** `docs/superpowers/specs/2026-06-19-text-near-dup-blocking-path-design.md` — read it first. The SimHash parity contract ("Phase B — Kernel") is normative; code below matches it.

**Golden constants** (precomputed from the verified reference; tests assert these exact values). `base_hash`/`splitmix64` are the #1081 kernel (already shipped, reused).

- `V = [0.5, -0.3, 0.8, 0.1, -0.9, 0.4, -0.2, 0.7]` (a fixed dim-8 input vector).
- `simhash_signature(V, num_planes=8, seed=42)` = `[1, 1, 1, 1, 1, 0, 1, 1]`
- `simhash_band_hashes(that_sig, num_bands=4)` = `[8326405673782927272, 10087387020540333614, 407431194778926956, 13491348438230804516]`
- `simhash_signature(V, num_planes=16, seed=7)` = `[1, 1, 0, 0, 1, 1, 1, 0, 0, 1, 0, 1, 1, 0, 1, 1]`
- `simhash_signature([0.0]*8, 8, 42)` = `[1]*8` (zero vector → all-ones; `dot==0` tie → 1).

**Measured recall gate config** (semantic SimHash): `num_planes=256, num_bands=32` (r=8) → recall **1.000** / candidate-reduction **0.86** on Gaussian variants at cosine ≥ 0.89. `num_bands=64` (r=4) collapses reduction to ~0.03 — do not use.

---

## Environment notes (read once)

- **Worktree:** `.worktrees/1082-text-near-dup` (branch `feat/1082-text-near-dup-path`, off fresh `origin/main` which has #1081). `docs/superpowers/**` is gitignored — `git add -f`.
- **Python tests:** `cd packages/python/goldenmatch`; prefix `PYTHONPATH=<worktree pkg> POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 python -m pytest tests/test_<file>.py -q`. Target specific files (full collection is slow). Never run the whole suite locally (OOM).
- **Rust:** `CG="C:/Users/bsevern/.cargo/bin/cargo.exe"`; set `CARGO_HOME=C:/Users/bsevern/.cargo RUSTUP_HOME=C:/Users/bsevern/.rustup RUSTUP_TOOLCHAIN=1.94.1`. Test the crate: `$CG test --manifest-path packages/rust/extensions/sketch-core/Cargo.toml`. Clippy: `$CG clippy --manifest-path .../Cargo.toml --all-targets -- -D warnings`. fmt only touches files you changed.
- **native crate:** `cargo check --no-default-features` (set `PYO3_PYTHON=<venv python>`) to verify the shim compiles; the full wheel + parity test run in CI's `native` lane. After `cargo fmt` on native, REVERT any reformatting of files you didn't edit (CI doesn't fmt-check the `native` crate).
- **TS:** do NOT run `tsc`/vitest locally (OOM) — CI gates it. Author against `src/core/` patterns; edge-safe (no `node:` imports).
- **Native gate:** `simhash` is NOT added to `_GATED_ON` (same as `sketch`); reachable via `GOLDENMATCH_NATIVE=1`.
- **Commit cadence:** one commit per task. Two PRs: **Phase A** (lexical auto-enable — the done bar, shippable on its own) then **Phase B** (SimHash). Arm `gh pr merge --auto --squash` on green; stop.

---

## File Structure

**Phase A — new:** `tests/test_text_corpus_autoconfig.py`.
**Phase A — modified:** `core/autoconfig.py` (detector + routing), `core/autoconfig_rules.py` (controller guard).

**Phase B — new:** `packages/rust/extensions/sketch-core/src/simhash.rs`; `core/simhash_blocker.py`; `scripts/gen_simhash_golden.py`; `tests/fixtures/sketch_simhash_golden.json`; `tests/test_simhash_reference.py`, `tests/test_native_simhash_parity.py`, `tests/test_simhash_blocker.py`, `tests/test_simhash_recall.py`; `packages/typescript/goldenmatch/src/core/simhash.ts`, `tests/unit/simhash.test.ts`.
**Phase B — modified:** `sketch-core/src/lib.rs`, `native/src/sketch.rs`, `native/src/lib.rs`; `core/sketch.py` (+ SimHash reference); `config/schemas.py` (`SimHashKeyConfig`); `core/blocker.py` (dispatch); `core/autoconfig.py` (semantic routing); `scripts/bench_lsh_recall_qqp.py` (`--method`).

---

# PHASE A — lexical auto-enable (the done bar; shippable PR on its own)

## Task A1: `_is_text_corpus` detector

**Files:** Modify `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`; Test `packages/python/goldenmatch/tests/test_text_corpus_autoconfig.py`.

- [ ] **Step 1: failing test.** Build `ColumnProfile` lists by hand and assert detection:

```python
from goldenmatch.core.autoconfig import ColumnProfile, _is_text_corpus

def _p(name, col_type, card=0.9, avg_len=120.0):
    return ColumnProfile(name=name, dtype="str", col_type=col_type, confidence=0.9,
                         null_rate=0.0, cardinality_ratio=card, avg_len=avg_len)

def test_single_text_column_is_corpus():
    assert _is_text_corpus([_p("body", "description")]) is True

def test_structured_with_name_is_not_corpus():
    assert _is_text_corpus([_p("name", "name", card=0.8, avg_len=12.0),
                            _p("bio", "description")]) is False

def test_low_card_name_does_not_block_corpus():
    # a low-cardinality (<0.1) name-classified column is not "blockable" -> still a corpus
    assert _is_text_corpus([_p("category", "name", card=0.02, avg_len=8.0),
                            _p("body", "description")]) is True

def test_no_description_is_not_corpus():
    assert _is_text_corpus([_p("name", "name", card=0.8, avg_len=12.0)]) is False
```

- [ ] **Step 2: run, verify fail.**
- [ ] **Step 3: implement** in autoconfig.py:

```python
def _is_text_corpus(profiles: list[ColumnProfile]) -> bool:
    """True when the data is a text corpus: a description column dominates and
    there is no blockable name column (the text is the primary identity)."""
    has_description = any(p.col_type == "description" for p in profiles)
    if not has_description:
        return False
    has_blockable_name = any(
        p.col_type in ("name", "multi_name") and p.cardinality_ratio >= 0.1
        for p in profiles
    )
    return not has_blockable_name
```

- [ ] **Step 4: run, verify pass.** **Step 5: commit** `feat(autoconfig): _is_text_corpus detector (#1082)`.

## Task A2: route text corpora to `strategy="lsh"`

**Files:** Modify `core/autoconfig.py` (the `build_blocking` `_ann_eligible` block ~2224–2236, and add helpers); extend `test_text_corpus_autoconfig.py`.

- [ ] **Step 1: failing test** — a text-corpus df gets `strategy="lsh"` on the longest description column:

```python
import polars as pl
from goldenmatch.core.autoconfig import profile_columns, build_blocking

def test_build_blocking_text_corpus_emits_lsh():
    df = pl.DataFrame({"body": [
        "the quick brown fox jumps over the lazy dog near the river bank today",
        "the quick brown fox jumps over the lazy dog beside the river bank today",
        "completely unrelated sentence about astrophysics and quantum mechanics here",
    ] * 20})
    profiles = profile_columns(df)
    cfg = build_blocking(profiles, df)
    assert cfg.strategy == "lsh"
    assert cfg.lsh is not None and cfg.lsh.column == "body"
    assert cfg.lsh.mode == "word" and cfg.lsh.num_perms == 128
```

- [ ] **Step 2: run, verify fail** (currently emits `ann`/name fallback).
- [ ] **Step 3: implement.** Add helpers + replace the `_ann_eligible` return block:

```python
def _embedder_available(config=None) -> bool:
    from goldenmatch.core.embedder import inhouse_embedding_available
    if inhouse_embedding_available():
        return True
    # a configured embedding provider on the resolved config also counts
    return bool(getattr(getattr(config, "embedding", None), "provider", None))

def _auto_build_lsh_config(profiles: list[ColumnProfile]) -> "BlockingConfig":
    from goldenmatch.config.schemas import BlockingConfig, LSHKeyConfig
    descs = [p for p in profiles if p.col_type == "description"]
    col = max(descs, key=lambda p: p.avg_len).name
    return BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(
        column=col, mode="word", k=2, num_perms=128, threshold=0.5, seed=0))

def _text_corpus_blocking(profiles, df, config=None) -> "BlockingConfig":
    # Phase B fills in the semantic branch; Phase A is lexical only.
    return _auto_build_lsh_config(profiles)
```

Then, where the `_ann_eligible` block currently returns `BlockingConfig(strategy="ann", ...)`, replace it with:

```python
if _is_text_corpus(profiles):
    return _text_corpus_blocking(profiles, df)
```

(Delete the `_ann_eligible` / `ann_min_rows` auto-selection — ANN is no longer auto-picked for description columns; explicit `strategy="ann"` still works. Keep `_embedding_cols` only if used elsewhere; otherwise remove dead code.)

- [ ] **Step 4: run, verify pass.** Also run `tests/test_autoconfig.py -q` and `tests/test_autoconfig_regressions.py -q`. **Expected behavior change:** if any existing test asserts `strategy=="ann"` is *auto-selected* for a description-only / large-text df, UPDATE that assertion (to `lsh`, or `simhash` under an embedder) — dropping ANN auto-selection is the intended change per the spec, not a regression to "fix back". A genuine regression is the exact/name/structured paths changing; those must stay identical.
- [ ] **Step 5: commit** `feat(autoconfig): route text corpora to lsh blocking (#1082)`.

## Task A3: controller guard — don't swap a near-dup strategy

**Files:** Modify `core/autoconfig_rules.py`; Test `tests/test_text_corpus_autoconfig.py`.

- [ ] **Step 1: failing test** — drive the controller on a text-corpus shape; the committed `lsh` strategy must survive (point the profile at the singleton-trap + key-swap shapes). Use the existing controller test harness pattern (see `tests/test_autoconfig_regressions.py` for how `AutoConfigController` is driven). Minimal version:

```python
from goldenmatch.config.schemas import BlockingConfig, LSHKeyConfig
from goldenmatch.core import autoconfig_rules as R

def test_blocking_rules_skip_lsh_strategy():
    cfg = _config_with_blocking(BlockingConfig(strategy="lsh",
        lsh=LSHKeyConfig(column="body", threshold=0.5)))
    # a profile that would normally trip key-swap (compared, nothing matched)
    ctx, profile = _profile(candidates_compared=10, mass_above_threshold=0.0)
    for rule in (R.rule_blocking_key_swap, R.rule_blocking_singleton_trap,
                 R.rule_uniform_heavy_blocking):
        assert rule(profile, cfg, history) is None  # guarded, no swap
```

(Adapt `_config_with_blocking`/`_profile` to the real rule signature — **read
`autoconfig_rules.py` for the exact shape**: it is `(profile, current, history,
ctx=None)`, NOT `(config, profile, ctx)`. Note `rule_cross_blocking_disagreement`
also early-returns `None` when `ctx is None` for a separate reason, so to prove
the *guard* specifically, either pass a non-None `ctx` or just assert the
end-state "strategy survives" rather than per-rule `None`.)

- [ ] **Step 2: run, verify fail** (rules currently return a swap proposal).
- [ ] **Step 3: implement.** Add the shared helper and guard every blocking-strategy/key rule:

```python
def _near_dup_locked(config) -> bool:
    b = getattr(config, "blocking", None)
    return b is not None and b.strategy in ("lsh", "simhash")
```

Add `if _near_dup_locked(current): return None` as the FIRST line of each of these nine rules: `rule_blocking_singleton_trap`, `rule_blocking_too_coarse`, `rule_blocking_key_swap`, `rule_uniform_heavy_blocking`, `rule_blocking_field_null_heavy`, `rule_low_reduction_ratio`, `rule_recall_gap_suspected`, `rule_cross_blocking_disagreement`, `rule_blocking_adaptive_on_p99_outlier`. (Do NOT guard threshold/matchkey rules.)

- [ ] **Step 4: run, verify pass.** **Step 5: commit** `fix(autoconfig): controller must not swap lsh/simhash blocking (#1082)`.

## Task A4: end-to-end zero-config text dedupe

**Files:** Test `tests/test_text_corpus_autoconfig.py`.

- [ ] **Step 1: failing test** — `dedupe_df` on a tiny text corpus (near-dup pairs + distinct) clusters the near-dups together without any config. Build a ~30-row corpus (some near-dup paraphrase-free lexical variants), call `dedupe_df(df)`, assert the known near-dup rows land in the same cluster and distinct rows don't. Follow `tests/test_autoconfig_regressions.py` patterns (set `rerank=False`, `confidence_required=False` if needed for the offline/borderline path).
- [ ] **Step 2–4: run → implement nothing new (exercises A1–A3) → pass.** If it fails because clustering/scoring needs tuning, adjust the corpus, not the pipeline.
- [ ] **Step 5: commit** `test(autoconfig): zero-config text-corpus dedupe e2e (#1082)`.

**→ PHASE A is a shippable milestone. Open PR "feat: auto-enable lexical near-dup blocking for text corpora (#1082)", land it, then continue to Phase B.**

---

# PHASE B — semantic SimHash

## Task B1: Rust `simhash.rs` kernel

**Files:** Create `packages/rust/extensions/sketch-core/src/simhash.rs`; modify `src/lib.rs`.

- [ ] **Step 1: implement** `simhash.rs` (reuse `crate::hash::{base_hash, splitmix64}`):

```rust
//! SimHash (random ±1 hyperplane) LSH over f64 vectors. Mirrors the Python
//! reference (core/sketch.py) and TS port byte-for-byte; see the #1082 spec.
use crate::hash::{base_hash, splitmix64};

/// LSB-first ±1 draw from a splitmix64 stream, refilling 64 bits at a time.
struct BitStream { state: u64, buf: u64, left: u32 }
impl BitStream {
    fn new(seed: u64) -> Self { Self { state: seed, buf: 0, left: 0 } }
    #[inline]
    fn draw_pm1(&mut self) -> f64 {
        if self.left == 0 { let (v, s) = splitmix64(self.state); self.buf = v; self.state = s; self.left = 64; }
        let bit = self.buf & 1; self.buf >>= 1; self.left -= 1;
        if bit == 1 { 1.0 } else { -1.0 }
    }
}

/// Projection matrix (num_planes x dim), Rademacher ±1, row-major from the stream.
fn projection_matrix(num_planes: usize, dim: usize, seed: u64) -> Vec<Vec<f64>> {
    let mut bs = BitStream::new(seed);
    (0..num_planes).map(|_| (0..dim).map(|_| bs.draw_pm1()).collect()).collect()
}

/// SimHash signature: one byte (0/1) per plane. Empty/zero vector -> all ones.
pub fn simhash_signature(vector: &[f64], num_planes: usize, seed: u64) -> Vec<u8> {
    let planes = projection_matrix(num_planes, vector.len(), seed);
    planes.iter().map(|row| {
        let mut dot = 0.0_f64;
        for j in 0..vector.len() { dot += row[j] * vector[j]; }
        if dot >= 0.0 { 1u8 } else { 0u8 }
    }).collect()
}

/// Banded LSH over the 0/1 signature bytes. num_planes must be divisible by num_bands.
pub fn simhash_band_hashes(sig: &[u8], num_bands: usize) -> Vec<u64> {
    let n = sig.len();
    assert!(num_bands > 0 && n.is_multiple_of(num_bands),
        "num_planes {n} not divisible by num_bands {num_bands}");
    let r = n / num_bands;
    (0..num_bands).map(|b| {
        let mut buf = Vec::with_capacity(8 + r);
        buf.extend_from_slice(&(b as u64).to_le_bytes());
        buf.extend_from_slice(&sig[b * r..(b + 1) * r]);
        base_hash(&buf)
    }).collect()
}
```

`#[cfg(test)]`: assert the golden constants (sig for `V` at planes=8 seed=42 == `[1,1,1,1,1,0,1,1]`; band hashes == the 4 values; zero vector → all ones; non-divisible panics). Add a `simhash_band_hashes_batch(vectors: &[Vec<f64>], num_planes, num_bands, seed)` that builds the matrix ONCE and projects all rows (rayon-guarded like the MinHash batch). `lib.rs`: `pub mod simhash; pub use simhash::*;`.

- [ ] **Step 2:** `$CG test --manifest-path packages/rust/extensions/sketch-core/Cargo.toml simhash` → PASS; `$CG clippy ... --all-targets -- -D warnings`; `$CG fmt`.
- [ ] **Step 3: commit** `feat(sketch-core): SimHash kernel (#1082)`.

## Task B2: Python SimHash reference + golden fixture

**Files:** Modify `core/sketch.py`; create `scripts/gen_simhash_golden.py`, `tests/fixtures/sketch_simhash_golden.json`, `tests/test_simhash_reference.py`.

- [ ] **Step 1: failing test** (`test_simhash_reference.py`) asserting the golden constants via `sketch.simhash_signature` / `sketch.simhash_band_hashes`.
- [ ] **Step 2: run, verify fail.** **Step 3: implement** in `sketch.py` (mirror the Rust exactly: `_SimBitStream`, `simhash_signature(vector, num_planes, seed)`, `simhash_band_hashes(sig, num_bands)`, `simhash_band_hashes_batch(vectors, num_planes, num_bands, seed)` native-gated on `"simhash"`). Use floats; `dot >= 0.0 → 1`.
- [ ] **Step 4: pass.** Write `gen_simhash_golden.py` (imports sketch.py; emits cases over fixed vectors incl. zero/negative/varied dims + params; u64 band hashes as decimal strings, sig as 0/1 int arrays). Run it → fixture. Add `test_simhash_golden.py` locking the reference to the fixture.
- [ ] **Step 5: commit** `feat(sketch): SimHash Python reference + golden vectors (#1082)`.

## Task B3: Rust golden-vector test

**Files:** Create/extend `packages/rust/extensions/sketch-core/tests/` golden test to read `sketch_simhash_golden.json` and assert Rust reproduces every case (mirror the existing `tests/golden.rs` MinHash pattern; sig compares as `Vec<u8>`, band hashes as `u64`).

- [ ] Run `$CG test --manifest-path .../sketch-core/Cargo.toml` → PASS. Commit `test(sketch-core): SimHash golden-vector parity (#1082)`.

## Task B4: native pyo3 binding + parity

**Files:** Modify `native/src/sketch.rs` (+ `simhash_band_hashes_batch` pyfunction taking `Vec<Vec<f64>>`), `native/src/lib.rs` (register); create `tests/test_native_simhash_parity.py`.

- [ ] **Step 1:** add the shim (validate `num_planes % num_bands == 0`); register. `cargo check --no-default-features` (with `PYO3_PYTHON`) → compiles; clippy clean; REVERT any fmt changes to unrelated native files.
- [ ] **Step 2:** parity test (skipif native unbuilt / missing symbol) sweeping random vectors, native vs `sketch._simhash_band_hashes_batch_python`. Commit `feat(native): SimHash pyo3 binding + parity (#1082)`.

## Task B5: `SimHashKeyConfig` + `SimHashLSHBlocker` + dispatch

**Files:** Modify `config/schemas.py` (`SimHashKeyConfig`, `strategy="simhash"` + validator, re-export), `core/blocker.py` (dispatch); create `core/simhash_blocker.py`, `tests/test_simhash_blocker.py`.

- [ ] **Step 1: failing tests** — config validation (mirror `LSHKeyConfig`: `num_planes>=1`, `threshold` XOR `num_bands`, divisibility); `strategy="simhash"` requires `simhash` block; the blocker over a small synthetic embedding matrix clusters high-cosine rows (inject a fake embedder via a test seam or pass embeddings directly).
- [ ] **Step 2–4:** implement `SimHashKeyConfig(column, num_planes=256, num_bands|threshold, seed=0, model=None)`; `SimHashLSHBlocker` (`from_config`; `blocks(df, embeddings)` over an `(n, dim)` float64 array → `simhash_band_hashes_batch` → `BlockResult` per non-singleton `(band, bucket)`, dropping all-zero rows; `build_simhash_blocks(lf, config)` embeds `config.simhash.column` via `get_embedder(model)`). Wire `strategy=="simhash"` into `build_blocks`. Run → pass.
- [ ] **Step 5: commit** `feat(sketch): SimHashLSHBlocker + simhash strategy (#1082)`.

## Task B6: auto-config semantic routing

**Files:** Modify `core/autoconfig.py` (`_text_corpus_blocking` semantic branch); extend `test_text_corpus_autoconfig.py`.

- [ ] **Step 1: failing test** — with `_embedder_available` monkeypatched True, a text-corpus df → `strategy=="simhash"` (column = longest description); patched False → `strategy=="lsh"` (regression of A2).
- [ ] **Step 2–4:** fill the semantic branch:

```python
def _text_corpus_blocking(profiles, df, config=None):
    if _embedder_available(config):
        from goldenmatch.config.schemas import BlockingConfig, SimHashKeyConfig
        col = max((p for p in profiles if p.col_type == "description"),
                  key=lambda p: p.avg_len).name
        return BlockingConfig(strategy="simhash",
            simhash=SimHashKeyConfig(column=col, num_planes=256, num_bands=32, seed=0))
    return _auto_build_lsh_config(profiles)
```

Run → pass. **Step 5: commit** `feat(autoconfig): semantic SimHash routing when embedder available (#1082)`.

## Task B7: SimHash recall gate

**Files:** Create `scripts/bench_simhash_recall.py` (importable `measure_simhash_recall`: synthetic Gaussian base vectors + noisy variants, known dup pairs, measure recall + reduction via `SimHashLSHBlocker` over the vectors); `tests/test_simhash_recall.py` pins `num_planes=256, num_bands=32, noise=0.3, seed=1` and asserts `recall >= 0.95` and `reduction >= 0.7` (measured 1.0 / 0.86). Commit `feat(bench): SimHash recall harness + gate (#1082)`.

## Task B8: TS SimHash port

**Files:** Create `packages/typescript/goldenmatch/src/core/simhash.ts` (+ barrel export), `tests/unit/simhash.test.ts` (copy the simhash golden fixture into the TS test tree; assert every case). Pure-TS, `number` math; NO TS blocker (no embedder). Do NOT run tsc/vitest locally — CI gates. Commit `feat(ts/sketch): SimHash kernel port + golden parity (#1082)`.

## Task B9: QQP lexical-vs-semantic A/B

**Files:** Modify `scripts/bench_lsh_recall_qqp.py` (add `--method {lexical,semantic}`; `semantic` embeds the unique questions via `get_embedder` and runs `SimHashLSHBlocker`), `.github/workflows/bench-lsh-recall.yml` (a `method` input or run both). The honest payoff: semantic recovers more QQP paraphrases than lexical's 0.21. Commit `feat(bench): QQP lexical-vs-semantic A/B (#1082)`.

## Task B10: docs

- [ ] rollout-docs-sweep: `blocking.mdx` (new `simhash` strategy + "auto-enabled for text corpora" on `lsh`), `configuration.mdx` (`simhash` config), `tuning.mdx` (`simhash` native component), CHANGELOGs (py + ts), context-network ADR + nav + log, a zero-config text-corpus example. Commit `docs: SimHash semantic near-dup rollout (#1082)`.

**→ Open PR "feat: semantic SimHash near-dup blocking (#1082)", land it.**

---

## Finalization

- [ ] Targeted local test pass (Phase A python; Phase B python + `cargo test sketch-core`). TS via CI.
- [ ] Both PRs green + auto-merge armed; record any bench numbers on #1082.

## Risks / watch-items

- **Auto-config regression:** run `tests/test_autoconfig.py` after A2 (the `_ann_eligible` removal must not break the structured paths).
- **Controller guard completeness:** all nine blocking rules guarded (grep `_near_dup_locked`); a tenth must adopt it.
- **SimHash f64 parity:** golden vectors + native sweep; TS is kernel-parity-only.
- **Embedder in tests:** B5/B6 must not require a real model download — inject embeddings / monkeypatch `_embedder_available`.
