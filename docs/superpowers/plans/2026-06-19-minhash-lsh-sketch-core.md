# MinHash / LSH sketch kernel (`sketch-core`) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pyo3-free `goldenmatch-sketch-core` Rust crate (shingling → MinHash → banded LSH) exposed on Python (native + pure-Python fallback) and TypeScript, plus a `MinHashLSHBlocker` conforming to the existing blocker contract, with measured recall (synthetic CI gate + Quora-QQP bench job).

**Architecture:** A single hand-rolled, dependency-free hash family is the cross-language parity contract. The **Python reference (`sketch.py`) is the source of truth**; golden vectors are generated from it and every implementation (Rust, Python, TS) is checked against them. The kernel does per-record sketching; the host language groups `(band, bucket)` into blocks using existing infra (Approach A).

**Tech Stack:** Rust (pyo3-free core + pyo3 `native` wrapper, `rayon`), Python (polars blocker, pytest), TypeScript (pure-TS + `BigInt`, vitest), GitHub Actions bench.

**Normative algorithm:** the exact arithmetic lives in the spec — read it before starting:
`docs/superpowers/specs/2026-06-19-minhash-lsh-sketch-core-design.md` → "Canonical algorithm (parity contract)". All `u64` arithmetic is wrapping unless a modulus is given. The code blocks below match that spec exactly; if they ever disagree, the spec wins and the plan is the bug.

**Golden constants** (precomputed from the reference; tests assert these exact values):

| input | function | expected `u64` |
|---|---|---|
| `""` | `base_hash` | `17665956581633026203` |
| `"a"` | `base_hash` | `198367012849983736` |
| `"ab"` | `base_hash` | `11528740771484442951` |
| `"hello world"` | `base_hash` | `417524495691944273` |
| seed `0`, draws 1..4 | `splitmix64` | `16294208416658607535`, `7960286522194355700`, `487617019471545679`, `17909611376780542444` |
| `(128, 0.5)` | `optimal_bands` | `(32, 4)` |
| `(128, 0.8)` | `optimal_bands` | `(8, 16)` |
| `(128, 0.9)` | `optimal_bands` | `(4, 32)` |

With `sh = shingle("hello world", char, k=3)`, `signature(sh, num_perms=8, seed=42)`
(note: `signature` takes the **shingle list**, not text) =
`[17041167395646177, 77277049784527919, 186077308732231195, 564709922545612565, 113913446168519210, 82732991858855180, 16713511289126713, 83663724776489692]`
and `band_hashes(that_sig, num_bands=4)` =
`[12901963457859849374, 4306753959614852008, 8435817867480225113, 7834504510243305493]`.

---

## Environment notes (read once)

- **Work in this worktree only:** `.worktrees/1081-minhash-lsh` (branch `feat/1081-minhash-lsh-sketch-core`, off fresh `origin/main`). `docs/superpowers/**` is gitignored — `git add -f` to commit plan/spec.
- **Python local runs:** prefix with `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8` to dodge the Windows polars WMI hang. Run **targeted test files only** — never the full suite locally (it OOMs this box; full suite runs in CI). `sketch.py` itself imports only stdlib + (optionally) the native loader, so its unit tests don't touch polars; the blocker tests do.
- **Native build:** `python scripts/build_native.py` builds `goldenmatch._native` in-tree (Windows dev picks up new symbols immediately). Set `PYO3_PYTHON` to the venv python if the build can't find it.
- **`GOLDENMATCH_NATIVE`**: `0` = force pure Python, `1` = require native (raise if absent), unset/`auto` = native iff available and component gated on.
- **Rust:** the crate is a standalone workspace; build/test it directly with `--manifest-path packages/rust/extensions/sketch-core/Cargo.toml`. The `native` crate is also a standalone workspace.
- **TS:** building/typechecking TS locally OOMs this box — push and let CI (`typecheck` + vitest) gate it. Author against the existing `src/core` patterns; keep it edge-safe (no Node imports).
- **Commit cadence:** one commit per task (after its tests pass). Branch already exists; do NOT open the PR until the whole plan lands and CI is green. Use `--no-verify` only if a pre-commit hook is unrelated-flaky.

---

## File Structure

**New (Rust):** `packages/rust/extensions/sketch-core/{Cargo.toml, src/lib.rs, src/hash.rs, src/shingle.rs, src/minhash.rs, src/lsh.rs}`
**New (Python):** `goldenmatch/core/sketch.py`, `goldenmatch/core/lsh_blocker.py`, `scripts/gen_sketch_golden.py`, `scripts/bench_lsh_recall.py`, `scripts/bench_lsh_recall_qqp.py`; tests `test_sketch_reference.py`, `test_sketch_golden.py`, `test_native_sketch_parity.py`, `test_lsh_blocker.py`, `test_lsh_recall.py`; fixtures `tests/fixtures/sketch_golden.json`.
**New (TS):** `src/core/sketch.ts`, `src/core/lshBlocker.ts`, `tests/unit/sketch.test.ts`.
**New (CI):** `.github/workflows/bench-lsh-recall.yml`.
**Modified:** `native/Cargo.toml`, `native/src/lib.rs`, `goldenmatch/core/_native_loader.py`, `goldenmatch/config/schemas.py`, the blocking dispatch (`goldenmatch/core/blocker.py`), TS barrel exports, `tuning.mdx`, CHANGELOGs, context-network ADR.

The Python reference is small and stdlib-only by design (no polars import) so it can be the parity source and run in fast, isolated tests.

---

## Phase 1 — Python reference (`sketch.py`), the source of truth

### Task 1.1: Hash primitives (`base_hash`, `splitmix64`)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/sketch.py`
- Test: `packages/python/goldenmatch/tests/test_sketch_reference.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sketch_reference.py
from goldenmatch.core import sketch

def test_base_hash_golden():
    assert sketch.base_hash(b"") == 17665956581633026203
    assert sketch.base_hash(b"a") == 198367012849983736
    assert sketch.base_hash(b"ab") == 11528740771484442951
    assert sketch.base_hash("hello world".encode("utf-8")) == 417524495691944273

def test_splitmix64_stream_from_zero():
    state, out = 0, []
    for _ in range(4):
        v, state = sketch.splitmix64(state)
        out.append(v)
    assert out == [16294208416658607535, 7960286522194355700,
                   487617019471545679, 17909611376780542444]
```

- [ ] **Step 2: Run, verify fail** — `POLARS_SKIP_CPU_CHECK=1 pytest tests/test_sketch_reference.py -q` → ImportError / no `base_hash`.

- [ ] **Step 3: Implement** (in `sketch.py`):

```python
"""Pure-Python reference + fallback for the sketch-core MinHash/LSH kernel.

This module is the authoritative reference for the cross-language parity
contract (see docs/superpowers/specs/2026-06-19-minhash-lsh-sketch-core-design.md).
Rust and TypeScript reproduce these outputs byte-for-byte. Stdlib only (plus the
optional native loader, imported lazily) — do not add heavy imports here.
"""
from __future__ import annotations

_MASK64 = (1 << 64) - 1
_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x00000100000001B3
_SM_C1 = 0xBF58476D1CE4E5B9
_SM_C2 = 0x94D049BB133111EB
_SM_GAMMA = 0x9E3779B97F4A7C15
_MERSENNE_P = (1 << 61) - 1


def base_hash(data: bytes) -> int:
    h = _FNV_OFFSET
    for byte in data:
        h = ((h ^ byte) * _FNV_PRIME) & _MASK64
    h = ((h ^ (h >> 30)) * _SM_C1) & _MASK64
    h = ((h ^ (h >> 27)) * _SM_C2) & _MASK64
    return (h ^ (h >> 31)) & _MASK64


def splitmix64(state: int) -> tuple[int, int]:
    state = (state + _SM_GAMMA) & _MASK64
    z = state
    z = ((z ^ (z >> 30)) * _SM_C1) & _MASK64
    z = ((z ^ (z >> 27)) * _SM_C2) & _MASK64
    z = (z ^ (z >> 31)) & _MASK64
    return z, state
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `git add -f` the two files; `git commit -m "feat(sketch): hash primitives reference (#1081)"`.

### Task 1.2: `shingle` (char/word, edge cases)

**Files:** Modify `sketch.py`; extend `test_sketch_reference.py`.

- [ ] **Step 1: Failing test** — cover char, word, short-input, empty/whitespace precedence, and the exact ASCII-whitespace tokenization:

```python
def test_shingle_char_basic():
    sh = sketch.shingle("hello world", "char", 3)
    assert len(sh) == 9 and sh == sorted(sh) and len(set(sh)) == len(sh)

def test_shingle_word_ascii_whitespace_only():
    # U+00A0 (non-breaking space) is NOT a separator -> one token
    assert len(sketch.shingle("a b", "word", 1)) == 1
    # ASCII spaces/tabs/newlines ARE separators -> two tokens, k=1 -> 2 shingles
    assert len(sketch.shingle("a\tb", "word", 1)) == 2
    assert len(sketch.shingle("a\nb", "word", 1)) == 2

def test_shingle_short_input_single_shingle():
    assert sketch.shingle("ab", "char", 5) == [sketch.base_hash(b"ab")]
    assert sketch.shingle("x", "word", 3) == [sketch.base_hash(b"x")]

def test_shingle_empty_and_whitespace_only_is_empty_set():
    assert sketch.shingle("", "char", 3) == []
    assert sketch.shingle("   \t\n", "word", 2) == []  # zero tokens precedence
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** in `sketch.py`:

```python
_ASCII_WS = frozenset({0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x20})


def _word_tokens(text: str) -> list[str]:
    out: list[str] = []
    cur: list[str] = []
    for ch in text:
        if ord(ch) in _ASCII_WS:
            if cur:
                out.append("".join(cur)); cur = []
        else:
            cur.append(ch)
    if cur:
        out.append("".join(cur))
    return out


def shingle(text: str, mode: str = "char", k: int = 3) -> list[int]:
    if mode == "char":
        units: list[str] = list(text)
        sep = ""
    elif mode == "word":
        units = _word_tokens(text)
        sep = " "
    else:
        raise ValueError(f"unknown shingle mode: {mode!r}")
    n = len(units)
    if n == 0:
        return []
    hs: set[int] = set()
    if n < k:
        hs.add(base_hash(sep.join(units).encode("utf-8")))
    else:
        for i in range(n - k + 1):
            hs.add(base_hash(sep.join(units[i:i + k]).encode("utf-8")))
    return sorted(hs)
```

- [ ] **Step 4: Run, verify pass.** **Step 5: Commit** `feat(sketch): shingling reference (#1081)`.

### Task 1.3: `signature` + `estimate_jaccard`

**Files:** Modify `sketch.py`; extend test.

- [ ] **Step 1: Failing test** (uses the golden signature):

```python
def test_signature_golden():
    sh = sketch.shingle("hello world", "char", 3)
    assert sketch.signature(sh, 8, 42) == [
        17041167395646177, 77277049784527919, 186077308732231195,
        564709922545612565, 113913446168519210, 82732991858855180,
        16713511289126713, 83663724776489692]

def test_signature_empty_is_all_max():
    assert sketch.signature([], 8, 42) == [(1 << 64) - 1] * 8

def test_estimate_jaccard_matches_true_within_tolerance():
    import random
    rng = random.Random(1)
    words = [str(rng.randint(0, 500)) for _ in range(60)]
    a = " ".join(words)
    b = " ".join(w for w in words if rng.random() > 0.3)
    sa, sb = sketch.shingle(a, "word", 2), sketch.shingle(b, "word", 2)
    est = sketch.estimate_jaccard(sketch.signature(sa, 128, 7), sketch.signature(sb, 128, 7))
    true = len(set(sa) & set(sb)) / len(set(sa) | set(sb))
    assert abs(est - true) < 0.15
```

- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement:**

```python
def _coefficients(num_perms: int, seed: int) -> tuple[list[int], list[int]]:
    a: list[int] = []
    b: list[int] = []
    state = seed
    for _ in range(num_perms):
        v, state = splitmix64(state)
        a.append((v % (_MERSENNE_P - 1)) + 1)
        v, state = splitmix64(state)
        b.append(v % _MERSENNE_P)
    return a, b


def signature(shingles: list[int], num_perms: int, seed: int) -> list[int]:
    a, b = _coefficients(num_perms, seed)
    sig = [_MASK64] * num_perms
    for i in range(num_perms):
        ai, bi, m = a[i], b[i], _MASK64
        for x in shingles:
            p = (ai * (x % _MERSENNE_P) + bi) % _MERSENNE_P
            if p < m:
                m = p
        sig[i] = m
    return sig


def estimate_jaccard(sig_a: list[int], sig_b: list[int]) -> float:
    if not sig_a:
        return 0.0
    return sum(1 for x, y in zip(sig_a, sig_b) if x == y) / len(sig_a)
```

- [ ] **Step 4: pass. Step 5: Commit** `feat(sketch): minhash signature reference (#1081)`.

### Task 1.4: `band_hashes` + `optimal_bands`

**Files:** Modify `sketch.py`; extend test.

- [ ] **Step 1: Failing test:**

```python
def test_band_hashes_golden():
    sig = sketch.signature(sketch.shingle("hello world", "char", 3), 8, 42)
    assert sketch.band_hashes(sig, 4) == [
        12901963457859849374, 4306753959614852008,
        8435817867480225113, 7834504510243305493]

def test_band_hashes_requires_divisible():
    import pytest
    with pytest.raises(ValueError):
        sketch.band_hashes([0] * 8, 3)

def test_optimal_bands_golden():
    assert sketch.optimal_bands(128, 0.5) == (32, 4)
    assert sketch.optimal_bands(128, 0.8) == (8, 16)
    assert sketch.optimal_bands(128, 0.9) == (4, 32)
```

- [ ] **Step 2: fail. Step 3: Implement:**

```python
def band_hashes(sig: list[int], num_bands: int) -> list[int]:  # param 'sig' avoids shadowing signature()
    n = len(sig)
    if num_bands <= 0 or n % num_bands != 0:
        raise ValueError(f"num_perms {n} not divisible by num_bands {num_bands}")
    r = n // num_bands
    out: list[int] = []
    for band in range(num_bands):
        buf = band.to_bytes(8, "little")
        for j in range(r):
            buf += sig[band * r + j].to_bytes(8, "little")
        out.append(base_hash(buf))
    return out


def optimal_bands(num_perms: int, threshold: float, steps: int = 1000) -> tuple[int, int]:
    def integral(lo: float, hi: float, f) -> float:
        h = (hi - lo) / steps
        s = 0.5 * (f(lo) + f(hi))
        for i in range(1, steps):
            s += f(lo + i * h)
        return s * h
    best: tuple[int, int, float] | None = None
    for b in range(1, num_perms + 1):
        if num_perms % b:
            continue
        r = num_perms // b
        pc = lambda s, _r=r, _b=b: 1.0 - (1.0 - s ** _r) ** _b
        err = 0.5 * integral(0.0, threshold, pc) + 0.5 * integral(threshold, 1.0, lambda s: 1.0 - pc(s))
        if best is None or err < best[2] - 1e-12:
            best = (b, r, err)
    assert best is not None
    return best[0], best[1]
```

- [ ] **Step 4: pass. Step 5: Commit** `feat(sketch): banded LSH + optimal_bands reference (#1081)`.

### Task 1.5: top-level `sketch_band_hashes` convenience + exports

**Files:** Modify `sketch.py`; extend test.

- [ ] **Step 1: Failing test** for the one-call path the blocker uses:

```python
def test_sketch_band_hashes_end_to_end():
    bh = sketch.sketch_band_hashes("hello world", mode="char", k=3,
                                   num_perms=8, num_bands=4, seed=42)
    assert bh == [12901963457859849374, 4306753959614852008,
                  8435817867480225113, 7834504510243305493]

def test_band_hashes_batch_matches_singles():
    texts = ["hello world", "", "foo bar baz"]
    batch = sketch.band_hashes_batch(texts, mode="word", k=2, num_perms=16,
                                     num_bands=8, seed=3)
    singles = [sketch.sketch_band_hashes(t, mode="word", k=2, num_perms=16,
                                         num_bands=8, seed=3) for t in texts]
    assert batch == singles
```

- [ ] **Step 2: fail. Step 3: Implement** `sketch_band_hashes` (compose shingle→signature→band_hashes) and `band_hashes_batch` (list comprehension; the native path overrides this later). Add `__all__`.
- [ ] **Step 4: pass. Step 5: Commit** `feat(sketch): end-to-end + batch reference (#1081)`.

---

## Phase 2 — Golden vectors

### Task 2.1: generator script + committed fixture

**Files:** Create `scripts/gen_sketch_golden.py`, `tests/fixtures/sketch_golden.json`.

- [ ] **Step 1:** Write `scripts/gen_sketch_golden.py` that **imports `goldenmatch.core.sketch`** (single source) and emits a JSON list of cases. Cover: empty, whitespace-only (word), unicode/multibyte (`"héllo wörld"`, CJK), len<k, repeated tokens, long text, both modes, a couple `(num_perms, num_bands, seed)` triples. Each case: `{text, mode, k, num_perms, num_bands, seed, shingles, signature, band_hashes}` with ints serialized as **decimal strings** (JSON can't hold u64 safely).
- [ ] **Step 2:** Run it: `POLARS_SKIP_CPU_CHECK=1 python scripts/gen_sketch_golden.py > packages/python/goldenmatch/tests/fixtures/sketch_golden.json`. Eyeball the file.
- [ ] **Step 3: Commit** `test(sketch): golden-vector fixture + generator (#1081)`.

### Task 2.2: Python golden-vector test (locks the contract)

**Files:** Create `tests/test_sketch_golden.py`.

- [ ] **Step 1: Failing test** — load the fixture, for each case assert `sketch.shingle/signature/band_hashes` reproduce the stored values (parse the decimal strings back to int).
- [ ] **Step 2:** run → should PASS immediately (fixture came from `sketch.py`); this is a characterization lock, not red-green. If it fails, the generator and module diverged — fix before continuing.
- [ ] **Step 3: Commit** `test(sketch): golden-vector lock for python reference (#1081)`.

---

## Phase 3 — Rust crate `sketch-core`

### Task 3.1: scaffold + `hash.rs`

**Files:** Create `Cargo.toml`, `src/lib.rs`, `src/hash.rs`.

- [ ] **Step 1:** `Cargo.toml` — standalone workspace, mirror `score-core`:

```toml
[workspace]

[package]
name = "goldenmatch-sketch-core"
version = "0.1.0"   # match the sibling -core version; check score-core/Cargo.toml
edition = "2021"
license = "MIT"
description = "MinHash + LSH sketching kernels (pyo3-free), shared across native/postgres/datafusion"

[lib]
name = "goldenmatch_sketch_core"

[dependencies]
rayon = "1"   # match the version score-core/native already pin (check Cargo.lock)
```

- [ ] **Step 2:** `src/hash.rs` with `base_hash(&[u8]) -> u64` and `splitmix64(u64) -> (u64,u64)` using `wrapping_*`, plus `#[cfg(test)]` asserting the golden constants (`base_hash(b"")==17665956581633026203`, the 4 splitmix draws from 0). `src/lib.rs`: `pub mod hash;` (+ the other mods as added).
- [ ] **Step 3:** `cargo test --manifest-path packages/rust/extensions/sketch-core/Cargo.toml hash` → PASS.
- [ ] **Step 4: Commit** `feat(sketch-core): crate scaffold + hash kernel (#1081)`.

### Task 3.2: `shingle.rs`

- [ ] **Step 1:** implement `shingle(text: &str, mode: ShingleMode, k: usize) -> Vec<u64>` (sorted, deduped via `sort_unstable` + `dedup`). `ShingleMode { Char, Word }`. Char iterates `text.chars()`; window of `k` chars re-collected to a `String` then `.as_bytes()`. Word splits on the exact ASCII set `matches!(c, '\t'|'\n'|'\u{0B}'|'\u{0C}'|'\r'|' ')` (NOT `split_whitespace`), joins window with `' '`. `n==0` → empty; `n<k` → single whole-sequence shingle.
- [ ] **Step 2:** `#[cfg(test)]`: count for `"hello world"` char k=3 == 9; `"a\u{A0}b"` word k=1 has 1 token; `"a\tb"` word k=1 has 2; empty/whitespace-only → empty.
- [ ] **Step 3:** `cargo test ... shingle` → PASS. **Step 4: Commit** `feat(sketch-core): shingling kernel (#1081)`.

### Task 3.3: `minhash.rs`

- [ ] **Step 1:** `signature(shingles: &[u64], num_perms: usize, seed: u64) -> Vec<u64>` with the `u128` multiply: `((a as u128 * (x % P) as u128 + b as u128) % P as u128) as u64`, `P = (1<<61)-1`. Coefficients from `splitmix64` exactly as the reference. `estimate_jaccard(&[u64],&[u64]) -> f64`.
- [ ] **Step 2:** `#[cfg(test)]`: the golden 8-perm signature for `"hello world"`; empty shingles → all `u64::MAX`.
- [ ] **Step 3:** `cargo test ... minhash` → PASS. **Step 4: Commit** `feat(sketch-core): minhash signature kernel (#1081)`.

### Task 3.4: `lsh.rs`

- [ ] **Step 1:** `band_hashes(sig: &[u64], num_bands: usize) -> Vec<u64>` (`to_le_bytes`, band_idx as `u64` prefix; return `Err`/panic-free via `assert!` on divisibility — choose `Result` and document). `optimal_bands(num_perms: usize, threshold: f64) -> (usize, usize)` mirroring the reference's 1000-step trapezoid + ascending tie-break.
- [ ] **Step 2:** `#[cfg(test)]`: golden band hashes; `optimal_bands(128, 0.5)==(32,4)`, `(128,0.8)==(8,16)`, `(128,0.9)==(4,32)`.
- [ ] **Step 3:** `cargo test ... lsh` → PASS. **Step 4: Commit** `feat(sketch-core): banded LSH kernel (#1081)`.

### Task 3.5: public API, batch entry points, golden-vector test

**Files:** Modify `src/lib.rs`; add a Rust integration test reading the JSON fixture.

- [ ] **Step 1:** `lib.rs` re-exports + `sketch_band_hashes(text, mode, k, num_perms, num_bands, seed) -> Vec<u64>` and `band_hashes_batch(&[&str], ...) -> Vec<Vec<u64>>`. The batch fn rayon-parallelizes only above a row threshold (env `GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS`, default 10_000) and runs on the calling thread below — the #688 `LockLatch` lesson; cite it in a comment.
- [ ] **Step 2:** add `tests/golden.rs` reading `../../../python/goldenmatch/tests/fixtures/sketch_golden.json` (path via `CARGO_MANIFEST_DIR`), asserting Rust reproduces every case. Use a tiny hand-rolled JSON parse or add `serde_json` as a **dev-dependency** only (keep the lib dep-light).
- [ ] **Step 3:** `cargo test --manifest-path packages/rust/extensions/sketch-core/Cargo.toml` → all PASS.
- [ ] **Step 4: Commit** `feat(sketch-core): public API + batch + golden-vector parity (#1081)`.

---

## Phase 4 — Python native binding + parity

### Task 4.1: pyo3 wrappers in `native`

**Files:** Modify `packages/rust/extensions/native/Cargo.toml`, `native/src/lib.rs`.

- [ ] **Step 1:** add `goldenmatch-sketch-core = { path = "../sketch-core" }` to `native/Cargo.toml`.
- [ ] **Step 2:** add `#[pyfunction] sketch_band_hashes_batch(texts: Vec<String>, mode: &str, k: usize, num_perms: usize, num_bands: usize, seed: u64) -> PyResult<Vec<Vec<u64>>>` (and `sketch_signature_batch`) delegating to the core; register in the module init alongside existing functions. Map mode string → `ShingleMode`, error on unknown.
- [ ] **Step 3:** build: `python scripts/build_native.py` → succeeds; `python -c "import goldenmatch._native as n; print(n.sketch_band_hashes_batch(['hello world'],'char',3,8,4,42))"` prints the golden band hashes.
- [ ] **Step 4: Commit** `feat(native): sketch_band_hashes/signature pyo3 bindings (#1081)`.

### Task 4.2: wire the loader + native fast path in `sketch.py`

**Files:** Modify `goldenmatch/core/_native_loader.py`, `goldenmatch/core/sketch.py`.

- [ ] **Step 1:** add `"sketch"` to `_GATED_ON` in `_native_loader.py` (default-on once parity is green). 
- [ ] **Step 2:** in `sketch.py`, make `band_hashes_batch` (and `signature` batch) call `native_module().sketch_band_hashes_batch(...)` when `native_enabled("sketch")`, else the pure-Python path. Lazy-import the loader at call time (keep module import stdlib-only).
- [ ] **Step 3:** quick check both paths agree: `GOLDENMATCH_NATIVE=0 python -c "..."` vs `GOLDENMATCH_NATIVE=1 python -c "..."` identical.
- [ ] **Step 4: Commit** `feat(sketch): native fast path + loader gate (#1081)`.

### Task 4.3: native↔python parity test

**Files:** Create `tests/test_native_sketch_parity.py`.

- [ ] **Step 1: Failing test** — `pytest.mark.skipif(not native_available())`. A property-style sweep: random texts/params, assert `band_hashes_batch` identical under `GOLDENMATCH_NATIVE=0` and `=1` (toggle via `monkeypatch.setenv` + reload, or call the native module and `sketch.py` reference directly and compare). Include empty/unicode/short edge cases.
- [ ] **Step 2:** run (native built) → PASS. **Step 3: Commit** `test(sketch): native↔python parity (#1081)`.

---

## Phase 5 — Python `MinHashLSHBlocker`

### Task 5.1: config (`LSHKeyConfig`, `strategy="lsh"`, validator)

**Files:** Modify `goldenmatch/config/schemas.py`.

- [ ] **Step 1: Failing test** in `tests/test_lsh_blocker.py`: constructing `BlockingConfig(strategy="lsh", lsh=LSHKeyConfig(column="text", mode="word", k=2, num_perms=128, threshold=0.5))` validates; `strategy="lsh"` without an `lsh` block raises; `lsh` with neither `threshold` nor `num_bands` raises; both set is allowed (explicit `num_bands` wins).
- [ ] **Step 2:** add `"lsh"` to the `strategy` `Literal`; add `LSHKeyConfig` (fields: `column: str`, `mode: Literal["char","word"]="char"`, `k: int=3`, `num_perms: int=128`, `seed: int=0`, `threshold: float|None=None`, `num_bands: int|None=None`); add an `lsh: LSHKeyConfig|None=None` field; extend `_validate_keys_or_passes` with a **positive** branch for `"lsh"` that *requires* the `lsh` block to be present and *rejects* `keys`/`passes`. (Note: `"ann"` is handled by mere omission from the `needs_keys`/`needs_passes` sets — it has no validator code. `"lsh"` is the opposite: add an explicit check, don't copy a no-op.) Validate `num_perms % num_bands == 0` when `num_bands` set.
- [ ] **Step 3:** run config tests → PASS. **Step 4: Commit** `feat(config): LSH blocking config schema (#1081)`.

### Task 5.2: the blocker

**Files:** Create `goldenmatch/core/lsh_blocker.py`.

- [ ] **Step 1: Failing test** (`test_lsh_blocker.py`): a small polars frame of near-duplicate texts + distinct texts; running the blocker yields `BlockResult`s whose candidate pairs include the known near-dup pairs and exclude obviously-distinct ones; every block is non-singleton; a pair colliding in multiple bands appears once across the returned pairs.
- [ ] **Step 2:** implement `MinHashLSHBlocker` (resolve `num_bands` via `optimal_bands` when only `threshold` given), computing per-record band hashes through `sketch.band_hashes_batch` (native-gated), exploding to `(band_idx, bucket)`, grouping with polars, emitting one `BlockResult(strategy="minhash_lsh", block_key=f"lsh_b{band}_{bucket}", df=...)` per non-singleton bucket. Mirror the `BlockResult` shape used by `blocker.py`/`ann_blocker.py` (read those first). Provide a helper to dedup candidate pairs across bands for callers that want pairs directly.
- [ ] **Step 3:** run → PASS. **Step 4: Commit** `feat(sketch): MinHashLSHBlocker (#1081)`.

### Task 5.3: dispatch wiring

**Files:** Modify the blocking dispatch (`goldenmatch/core/blocker.py` — find where `strategy` routes to static/multi_pass/ann).

- [ ] **Step 1: Failing test:** calling the public blocking entry with `strategy="lsh"` routes to `MinHashLSHBlocker` and returns blocks (test via the high-level `build_blocks`/equivalent, not the class directly).
- [ ] **Step 2:** add the `"lsh"` branch. **Step 3:** run → PASS. **Step 4: Commit** `feat(sketch): wire lsh strategy into blocking dispatch (#1081)`.

---

## Phase 6 — Synthetic recall gate (always-on)

### Task 6.1: recall bench script

**Files:** Create `scripts/bench_lsh_recall.py`.

- [ ] **Step 1:** a self-contained generator (seed docs of 20–40 tokens; `variants` near-dup copies via insert/delete/substitute at `edit_rate`; known dup-pair set) + measure: build LSH buckets via `sketch`, compute **recall** (true pairs sharing ≥1 bucket) and **reduction** (`1 - candidates/all_pairs`). CLI flags for `k, num_perms, threshold, edit_rate, num_seed, variants, seed`. Print a small table. Reuse stdlib-only logic (no polars needed in the script).
- [ ] **Step 2:** run it across a couple edit rates; confirm it reproduces ~0.97 recall / ~0.99 reduction at the pinned config. **Step 3: Commit** `feat(bench): synthetic LSH recall harness (#1081)`.

### Task 6.2: the gate test

**Files:** Create `tests/test_lsh_recall.py`.

- [ ] **Step 1: Failing test** — pinned config `mode="word", k=2, num_perms=128, threshold=0.5, edit_rate=0.1, num_seed=60, variants=3, seed=0`; assert `recall >= 0.90` and `reduction >= 0.95`. (Measured 0.972 / 0.989; thresholds carry margin. Deterministic via fixed seed.) Import the measurement fn from `bench_lsh_recall.py` (make it importable, not just `__main__`).
- [ ] **Step 2:** run → PASS. **Step 3: Commit** `test(sketch): always-on synthetic recall gate (#1081)`.

> Note for the executor: the empty-set `u64::MAX` signature arises mechanically (the inner per-shingle loop never runs), not via a special case — keep it that way in all impls.

---

## Phase 7 — TypeScript port

### Task 7.1: `sketch.ts` + golden-vector test

**Files:** Create `src/core/sketch.ts`, `tests/unit/sketch.test.ts`.

- [ ] **Step 1:** port the reference to TS using `BigInt` for the 64-bit FNV/splitmix and the `mod (2n**61n - 1n)` multiply; mask with `& 0xFFFFFFFFFFFFFFFFn`; code-point shingling via `Array.from`; the exact 6-char ASCII whitespace set for word mode; `to_le_bytes` via a `DataView`/`BigUint64Array`. Export `baseHash`, `splitmix64`, `shingle`, `signature`, `bandHashes`, `optimalBands`, `sketchBandHashes`. Keep edge-safe (no Node imports). Document the `BigInt` perf caveat in a header comment; WASM speed is a later slice.
- [ ] **Step 2:** `tests/unit/sketch.test.ts` loads the golden fixture and asserts every case matches (compare as decimal strings to avoid `BigInt`/number issues); also assert the headline golden constants directly. **Fixture path:** from `packages/typescript/goldenmatch/tests/unit/` the Python fixture is **four** levels up: `../../../../python/goldenmatch/tests/fixtures/sketch_golden.json`. Prefer copying the fixture into the TS test tree (e.g. `tests/fixtures/sketch_golden.json`) in this step and reading the local copy, so the TS package doesn't reach across packages — if you copy, add a one-line note in `gen_sketch_golden.py` that it writes both locations, or a CI check that the two are byte-identical.
- [ ] **Step 3:** push to CI (local TS build OOMs). Gate on the CI `typecheck` + vitest. **Step 4: Commit** `feat(ts/sketch): pure-TS MinHash/LSH port + golden parity (#1081)`.

### Task 7.2: TS `MinHashLSHBlocker` + exports

**Files:** Create `src/core/lshBlocker.ts`; modify the TS barrel/exports.

- [ ] **Step 1:** mirror the Python blocker over the TS record/blocking types (read the existing TS blocker for the contract). Group `(band, bucket)` via a `Map`, emit blocks/candidate pairs in the TS shape; dedup pairs across bands.
- [ ] **Step 2:** unit test: near-dup recall on a small in-memory set; export from the package barrel.
- [ ] **Step 3:** push; CI gates. **Step 4: Commit** `feat(ts/sketch): MinHashLSHBlocker + exports (#1081)`.

---

## Phase 8 — Real-corpus bench job (Quora Question Pairs)

> **Licensing first:** before committing any QQP rows, verify redistribution terms. **Do NOT commit raw Quora data.** The committed `qqp_sample.csv` must be either (a) a tiny hand-written synthetic stand-in shaped like QQP (`id,q1,q2,is_duplicate`), or (b) omitted in favor of download-at-runtime. The full corpus is downloaded only inside the bench job. Confirm the chosen path in the PR description.

### Task 8.1: QQP recall script

**Files:** Create `scripts/bench_lsh_recall_qqp.py`; optional `tests/fixtures/qqp_sample.csv` (synthetic stand-in) + a smoke test.

- [ ] **Step 1:** script downloads QQP via **HuggingFace `datasets` (`load_dataset("quora")`)** as the pinned acquisition path (retry+backoff; the bench job installs `datasets`). It runs the `MinHashLSHBlocker` over the unique questions, and reports recall (fraction of `is_duplicate==1` pairs that share ≥1 bucket), precision proxy, and reduction vs labels; writes a markdown report. Parameterize config; default to the gate config.
- [ ] **Step 2:** add a CI-fast smoke test over the tiny synthetic sample asserting the script's measurement fn runs end-to-end and returns sane (recall in [0,1]) numbers — NOT a recall threshold (the sample is too small to be meaningful).
- [ ] **Step 3: Commit** `feat(bench): QQP real-corpus LSH recall (#1081)`.

### Task 8.2: bench workflow

**Files:** Create `.github/workflows/bench-lsh-recall.yml`.

- [ ] **Step 1:** `workflow_dispatch` (inputs: `k`, `num_perms`, `threshold`, `runner` default `large-new-64GB`), install the package (+ `[native]`), run `scripts/bench_lsh_recall_qqp.py`, upload the markdown report as an artifact. Mirror `bench-issue-688.yml`’s shape. Do not gate `ci-required` on it.
- [ ] **Step 2:** validate YAML (`python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-lsh-recall.yml'))"`). **Step 3: Commit** `ci(bench): LSH recall workflow (#1081)`. (Dispatch the run after merge; capture numbers in the PR/issue.)

---

## Phase 9 — Docs & rollout

### Task 9.1: doc sweep

- [ ] **Step 1:** run the **rollout-docs-sweep** skill against the repo's `.claude/doc-surfaces.md` inventory. At minimum update: `docs-site/goldenmatch/tuning.mdx` (the `GOLDENMATCH_NATIVE` `sketch` component + the `lsh` blocking strategy / `LSHKeyConfig` fields + `GOLDENMATCH_NATIVE_SKETCH_RAYON_MIN_ROWS`), the Python + TS CHANGELOGs (under "Unreleased"), and a context-network ADR recording Approach A + the parity contract.
- [ ] **Step 2:** Commit `docs: sketch-core MinHash/LSH rollout (#1081)`.

---

## Finalization

- [ ] Full local **targeted** test pass (do NOT run the whole suite): `POLARS_SKIP_CPU_CHECK=1 pytest tests/test_sketch_reference.py tests/test_sketch_golden.py tests/test_native_sketch_parity.py tests/test_lsh_blocker.py tests/test_lsh_recall.py -q` and `cargo test --manifest-path packages/rust/extensions/sketch-core/Cargo.toml`.
- [ ] Push branch; open PR (title `feat: MinHash/LSH sketch-core kernel (#1081)`, body links #1081/#1080, notes the QQP licensing decision and bench-dispatch follow-up). Use the GitHub PR template.
- [ ] Arm `gh pr merge <N> --auto --squash` once CI is green; **stop** (merge queue handles the rest — do not poll). Per repo SOP, self-merge on green.
- [ ] After merge: dispatch `bench-lsh-recall.yml` on `main`, record the real-corpus recall numbers on #1081, then close the issue (or tick the epic #1080 checkbox).

## Risks / watch-items

- **Parity drift:** any change to a hash constant or ordering must regenerate golden vectors AND pass Rust + TS + native parity. Never edit golden values by hand.
- **`u128`/`BigInt` overflow:** covered by golden cases with large coefficients; keep the `% P` reduction of `x` before the multiply.
- **Native wheel symbol skew (#688 lesson):** in-tree build picks up `sketch_band_hashes_batch` immediately; if/when this rides the `goldenmatch-native` wheel, republish in the same change (out of scope here — gate stays in-tree).
- **CI cost:** the recall gate is small/deterministic; the QQP job is `workflow_dispatch` only.
```
