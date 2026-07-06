# InferMap Rust cutover — Wave 2 (pure name-scorers) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add three name-scorer kernels (`exact_score`, `fuzzy_name_score`, `initialism_score`) to the existing `infermap-core`, dispatch them from the Python scorer classes (kernel returns the score, reasoning stays host), parity-gate `native == _pure`. `fuzzy_name` reuses `goldenmatch-score-core`'s Jaro-Winkler. Native + Python (WASM/TS later).

**Architecture:** Per-pair kernels. Each scorer's `_*_pure` = the current logic extracted verbatim (with its ORIGINAL per-scorer input: `exact` = raw `name`; `fuzzy`/`initialism` = `canonical_name or name`). `infermap-core` gains a pure-Rust dep on `goldenmatch-score-core`.

**Tech Stack:** Rust (pyo3/abi3, CI-built — box can't `cargo build`), Python 3.11+ (box-testable pure path), rapidfuzz (installed).

**Branch:** `feat/infermap-core-wave2-scorers` (created off Wave 1's `feat/infermap-core-wave1-detect`, stacked — infermap-core isn't on main until #1490 merges). Rebase onto main if #1490 lands first.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-core-wave2-scorers-design.md`

**Env notes:** Rust CI-only (write + `#[cfg(test)]`, don't run cargo). Python pure path box-runnable: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`, prefix `PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0`. `ruff check` touched Python. gh benzsevern; merge-queue (no `--delete-branch`); REST for PR if GraphQL limited.

---

### Task 1: `infermap-core` — the 3 scorer kernels

**Files:**
- Modify `packages/rust/extensions/infermap-core/Cargo.toml` (add score-core dep)
- Modify `packages/rust/extensions/infermap-core/src/lib.rs` (add 3 kernels + tokenizer + DP + tests)

- [ ] **Step 1: Cargo dep** — add to `[dependencies]`:
```toml
goldenmatch-score-core = { path = "../score-core" }
```
(score-core has its own empty `[workspace]` so it self-isolates; it's pyo3-free — the invariant holds. It's already in the extensions-workspace `exclude`.)

- [ ] **Step 2: Add the kernels** (in `src/lib.rs`, after `detect_domain`)

```rust
use goldenmatch_score_core::jaro_winkler_similarity;

/// ExactScorer: 1.0 iff trimmed-lowercased names are equal, else 0.0.
pub fn exact_score(a: &str, b: &str) -> f64 {
    if a.trim().to_lowercase() == b.trim().to_lowercase() { 1.0 } else { 0.0 }
}

/// normalize = strip + lower + remove `_`, `-`, ` ` (mirrors fuzzy_name._normalize).
fn normalize(s: &str) -> String {
    s.trim().to_lowercase().chars().filter(|&c| c != '_' && c != '-' && c != ' ').collect()
}

/// FuzzyNameScorer: Jaro-Winkler on normalized names (reuses score-core).
pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
    jaro_winkler_similarity(&normalize(a), &normalize(b))
}

/// initialism tokenizer -- hand-scanner mirroring the INLINE regex at
/// initialism.py:40 `[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+` (NOT the dead
/// `_TOKEN_RE`). Pre-split on `_ - . space` (collapse runs); per chunk emit lowercased
/// tokens by the alternation. VERIFIED targets (live Python): HTTPSConnection ->
/// [https, connection]; providerID -> [provider, id]; order_id -> [order, id];
/// ABC -> [abc]; v2Name -> [v, 2, name].
fn tokenize(name: &str) -> Vec<String> {
    // 1) split on _ - . and whitespace, drop empties -> chunks.
    // 2) per chunk, scan left-to-right; at each position take the FIRST regex
    //    alternative that matches (this is what re.findall does). PRECISE rules that
    //    reproduce `[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+` with its backtracking:
    //    - Let U = the maximal uppercase run starting here (length L).
    //        * If L >= 2 AND the char right after U is a LOWERCASE letter: emit U[..L-1]
    //          as an acronym token; the LAST uppercase char (U[L-1]) starts the FOLLOWING
    //          word -> continue at U[L-1] as an `[A-Z]?[a-z]+` word.
    //          (This is the lookahead peeling ONE upper at a time until [A-Z][a-z] follows.)
    //          e.g. HTTPSConnection: U=HTTPSC(L=6), next 'o' lower -> [HTTPS], then Connection.
    //               providerIDs -> provider, then U=ID(L=2) next 's' lower -> [I], then Ds.
    //        * Else if L == 1 and it is followed by a lowercase run: it's a WORD
    //          (`[A-Z]?[a-z]+`) -> e.g. Name -> name.
    //        * Else (L==0, i.e. lowercase word; OR U followed by a non-lowercase like a
    //          digit or end-of-chunk): emit the whole U as an acronym token (`[A-Z]+`),
    //          or the lowercase run as a word (`[A-Z]?[a-z]+`), or a `\d+` digit run.
    //    - lowercase every emitted token.
    // VERIFIED targets (live Python -- fixture-pin ALL, incl. the boundary cases):
    //   HTTPSConnection->[https,connection]  providerID->[provider,id]  order_id->[order,id]
    //   ABC->[abc]  v2Name->[v,2,name]  providerIDs->[provider,i,ds]  URLs->[ur,ls]
    //   iOS->[i,os]  macOS->[mac,os]  Name->[name]
    // IMPLEMENT to reproduce these EXACTLY (the providerIDs/URLs cases are the
    // load-bearing boundary a naive "split last upper" impl gets wrong -- spec §6.2).
    todo!() // must be implemented; verify against the fixtures below
}

/// DP: can `target` be formed by concatenating >=1-char prefixes of `source_tokens`
/// in order, using each exactly once? Mirrors `_is_prefix_concat`.
/// Work on chars (Python slices by char) -- collect target + tokens to Vec<char> or,
/// since inputs are ASCII field names, bytes are equivalent (documented ASCII scope).
fn is_prefix_concat(target: &str, source_tokens: &[String]) -> bool {
    let t: Vec<char> = target.to_lowercase().chars().collect();
    let toks: Vec<Vec<char>> = source_tokens.iter().map(|s| s.chars().collect()).collect();
    let (n_src, n_tgt) = (toks.len(), t.len());
    if n_src == 0 || n_tgt == 0 { return false; }
    let mut dp = vec![vec![false; n_tgt + 1]; n_src + 1];
    dp[0][0] = true;
    for i in 1..=n_src {
        let tok = &toks[i - 1];
        for j in 1..=n_tgt {
            let kmax = tok.len().min(j);
            for k in 1..=kmax {
                if t[j - k..j] == tok[..k] && dp[i - 1][j - k] {
                    dp[i][j] = true;
                    break;
                }
            }
        }
    }
    dp[n_src][n_tgt]
}

/// InitialismScorer: `0.6 + 0.35*(len_short/len_long)` when one side abbreviates the
/// other; None (abstain) otherwise. Mirrors `_score_pair`.
pub fn initialism_score(a: &str, b: &str) -> Option<f64> {
    let tok_a = tokenize(a);
    let tok_b = tokenize(b);
    let joined_a: String = tok_a.concat();
    let joined_b: String = tok_b.concat();
    if joined_a.is_empty() || joined_b.is_empty() { return None; }
    if joined_a == joined_b { return None; }
    let (long, short) = if is_prefix_concat(&joined_b, &tok_a) {
        (&joined_a, &joined_b)
    } else if is_prefix_concat(&joined_a, &tok_b) {
        (&joined_b, &joined_a)
    } else {
        return None;
    };
    // CHAR count (Python len()), not byte len() -- ASCII-equal but be exact.
    let ratio = short.chars().count() as f64 / long.chars().count() as f64;
    Some(0.6 + 0.35 * ratio)
}
```

- [ ] **Step 3: Tests** (in the existing `mod tests`)
```rust
    #[test] fn exact_match_and_mismatch() {
        assert_eq!(exact_score("City", " city "), 1.0);
        assert_eq!(exact_score("a", "b"), 0.0);
    }
    #[test] fn fuzzy_identical_and_disjoint() {
        assert_eq!(fuzzy_name_score("city", "city"), 1.0);
        assert_eq!(fuzzy_name_score("abc", "xyz"), 0.0);
    }
    #[test] fn tokenize_camelcase_examples() {
        assert_eq!(tokenize("HTTPSConnection"), vec!["https", "connection"]);
        assert_eq!(tokenize("providerID"), vec!["provider", "id"]);
        assert_eq!(tokenize("order_id"), vec!["order", "id"]);
        assert_eq!(tokenize("ABC"), vec!["abc"]);
        assert_eq!(tokenize("v2Name"), vec!["v", "2", "name"]);
        // Load-bearing boundary: N-upper run + single trailing lowercase (a naive
        // "split last upper only when [A-Z][a-z] follows" impl gets these wrong).
        assert_eq!(tokenize("providerIDs"), vec!["provider", "i", "ds"]);
        assert_eq!(tokenize("URLs"), vec!["ur", "ls"]);
        assert_eq!(tokenize("iOS"), vec!["i", "os"]);
        assert_eq!(tokenize("macOS"), vec!["mac", "os"]);
        assert_eq!(tokenize("Name"), vec!["name"]);
    }
    #[test] fn initialism_abbrev_and_abstain() {
        // assay_id <-> ASSI : tok_a=[assay,id] joined "assayid"; ASSI joined "assi";
        // is_prefix_concat("assi",[assay,id]) -> ASS+I -> true; ratio 4/7 -> 0.6+0.35*4/7
        let s = initialism_score("assay_id", "ASSI").unwrap();
        assert!((s - (0.6 + 0.35 * (4.0 / 7.0))).abs() < 1e-12);
        assert_eq!(initialism_score("city", "town"), None); // not an abbreviation
        assert_eq!(initialism_score("city", "city"), None); // joined_a == joined_b
    }
```

- [ ] **Step 4: CI path-filter — infermap_native must re-run on score-core changes.** infermap-core now depends on `score-core`, so a change to `score-core::jaro_winkler_similarity` must re-trigger InferMap's fuzzy parity gate (the repo convention: `native_flow` lists `goldenflow-core/**` for exactly this). In `.github/workflows/ci.yml`, the `infermap_native` filter block (added in Wave 1, ~line 581) — add:
```yaml
              - 'packages/rust/extensions/score-core/**'
              - 'packages/python/infermap/infermap/scorers/exact.py'
              - 'packages/python/infermap/infermap/scorers/fuzzy_name.py'
              - 'packages/python/infermap/infermap/scorers/initialism.py'
```
(the scorer files now hold the `_*_pure` references `test_native_parity.py` imports). Validate `yaml.safe_load(ci.yml)`.

- [ ] **Step 5: Verify (read-only)** — the `tokenize` `todo!()` is IMPLEMENTED (not left as todo); kernels match spec §3; tests reference them incl. the boundary cases. Note: the DP `for i in 1..=n_src { for j in 1..=n_tgt {...} }` cross-indexes `dp[i-1][j-k]`, which clippy accepts, but the lane runs `cargo clippy -- -D warnings` — if `clippy::needless_range_loop` fires, add `#[allow(clippy::needless_range_loop)]` on the fn. No local build.
- [ ] **Step 6: Commit** — `git add packages/rust/extensions/infermap-core .github/workflows/ci.yml && git commit -m "feat(infermap-core): exact/fuzzy_name/initialism scorer kernels + CI filter (Wave 2)"`

---

### Task 2: `infermap-native` — 3 pyfunction shims

**Files:** Modify `packages/rust/extensions/infermap-native/src/lib.rs`.

- [ ] **Step 1: Add the pyfunctions** (next to `detect_domain`)
```rust
#[pyfunction]
fn exact_score(a: &str, b: &str) -> PyResult<f64> { Ok(infermap_core::exact_score(a, b)) }

#[pyfunction]
fn fuzzy_name_score(a: &str, b: &str) -> PyResult<f64> { Ok(infermap_core::fuzzy_name_score(a, b)) }

#[pyfunction]
fn initialism_score(a: &str, b: &str) -> PyResult<Option<f64>> { Ok(infermap_core::initialism_score(a, b)) }
```
(Adjust the `use infermap_core::detect_domain as core_detect;` import or use `infermap_core::` paths — match the crate's existing style. `Option<f64>` → Python `float | None`.)

- [ ] **Step 2: Register** — add to `#[pymodule] fn _native`, each with the **`self::`** qualifier (the `_WRAP` regex requirement):
```rust
    m.add_function(wrap_pyfunction!(self::exact_score, m)?)?;
    m.add_function(wrap_pyfunction!(self::fuzzy_name_score, m)?)?;
    m.add_function(wrap_pyfunction!(self::initialism_score, m)?)?;
```
(No Cargo change — `infermap-native` depends on `infermap-core`, which now pulls score-core transitively.)

- [ ] **Step 3: Verify (read-only). Commit** — `"feat(infermap-native): exact/fuzzy/initialism pyfunction shims (Wave 2)"`

---

### Task 3: Python dispatch in the scorer classes + gating

**Files:**
- Modify `packages/python/infermap/infermap/scorers/exact.py`
- Modify `packages/python/infermap/infermap/scorers/fuzzy_name.py`
- Modify `packages/python/infermap/infermap/scorers/initialism.py` (+ DELETE dead `_TOKEN_RE`)
- Modify `packages/python/infermap/infermap/_native_loader.py`
- Test: `packages/python/infermap/tests/test_scorers_dispatch.py` (new)

- [ ] **Step 1: Write the failing test** (`tests/test_scorers_dispatch.py`)
```python
"""Wave 2 scorer dispatch / pure-path tests (box-safe; INFERMAP_NATIVE=0)."""
from infermap.scorers.exact import _exact_score_pure
from infermap.scorers.fuzzy_name import _fuzzy_name_score_pure
from infermap.scorers.initialism import _score_pair
from infermap.types import FieldInfo


def test_exact_pure():
    assert _exact_score_pure("City", " city ") == 1.0
    assert _exact_score_pure("a", "b") == 0.0


def test_fuzzy_pure():
    assert _fuzzy_name_score_pure("city", "city") == 1.0
    assert _fuzzy_name_score_pure("abc", "xyz") == 0.0


def test_initialism_pure_abstain_and_score():
    assert _score_pair("city", "town") is None
    assert _score_pair("city", "city") is None
    s = _score_pair("assay_id", "ASSI")
    assert abs(s - (0.6 + 0.35 * (4 / 7))) < 1e-12


def test_scorer_classes_still_work():
    from infermap.scorers.exact import ExactScorer
    from infermap.scorers.fuzzy_name import FuzzyNameScorer
    a, b = FieldInfo(name="city"), FieldInfo(name="city")
    assert ExactScorer().score(a, b).score == 1.0
    assert FuzzyNameScorer().score(a, b).score == 1.0
```
Run (expect fail — `_exact_score_pure`/`_fuzzy_name_score_pure` don't exist):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q
```

- [ ] **Step 2: `exact.py`** — extract `_exact_score_pure` + `_exact_score` dispatcher; class uses RAW `source.name`/`target.name`:
```python
from infermap._native_loader import native_enabled, native_module

def _exact_score(a: str, b: str) -> float:
    if native_enabled("exact_score"):
        return native_module().exact_score(a, b)
    return _exact_score_pure(a, b)

def _exact_score_pure(a: str, b: str) -> float:
    return 1.0 if a.strip().lower() == b.strip().lower() else 0.0

class ExactScorer:
    name = "ExactScorer"; weight = 1.0
    def score(self, source, target):
        s = _exact_score(source.name, target.name)
        if s == 1.0:
            return ScorerResult(score=1.0, reasoning=f"Exact name match: '{source.name}'")
        return ScorerResult(score=0.0, reasoning=f"No exact match: '{source.name}' vs '{target.name}'")
```

- [ ] **Step 3: `fuzzy_name.py`** — extract `_fuzzy_name_score_pure` + `_fuzzy_name_score`; class uses `canonical_name or name`, formats reasoning with host `_normalize`:
```python
def _fuzzy_name_score(a: str, b: str) -> float:
    if native_enabled("fuzzy_name_score"):
        return native_module().fuzzy_name_score(a, b)
    return _fuzzy_name_score_pure(a, b)

def _fuzzy_name_score_pure(a: str, b: str) -> float:
    return JaroWinkler.similarity(_normalize(a), _normalize(b))

class FuzzyNameScorer:
    name = "FuzzyNameScorer"; weight = 0.4
    def score(self, source, target):
        src_name = source.canonical_name or source.name
        tgt_name = target.canonical_name or target.name
        similarity = _fuzzy_name_score(src_name, tgt_name)
        src_norm, tgt_norm = _normalize(src_name), _normalize(tgt_name)
        return ScorerResult(score=similarity,
            reasoning=f"Jaro-Winkler similarity between '{src_norm}' and '{tgt_norm}': {similarity:.3f}")
```

- [ ] **Step 4: `initialism.py`** — DELETE the dead `_TOKEN_RE` (line 26); add `_initialism_score` dispatcher over the existing `_score_pair`; class calls it:
```python
def _initialism_score(a: str, b: str):
    if native_enabled("initialism_score"):
        return native_module().initialism_score(a, b)  # float | None
    return _score_pair(a, b)

# in InitialismScorer.score: score = _initialism_score(src_name, tgt_name)  (was _score_pair)
```
Keep `_score_pair`/`_is_prefix_concat`/`_tokenize` as the pure reference. `import re` stays (still used by `_tokenize`).

- [ ] **Step 5: Gating** — `_native_loader.py`: add `exact_score`, `fuzzy_name_score`, `initialism_score` to BOTH `_GATED_ON` and `_COMPONENT_SYMBOLS`.

- [ ] **Step 6: Run dispatch tests + the FULL existing scorer tests** (refactor must not regress):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/infermap/tests/test_scorers_dispatch.py \
  packages/python/infermap/tests/ -k "scorer or exact or fuzzy or initialism" -q
ruff check packages/python/infermap/infermap/scorers/exact.py \
  packages/python/infermap/infermap/scorers/fuzzy_name.py \
  packages/python/infermap/infermap/scorers/initialism.py \
  packages/python/infermap/infermap/_native_loader.py \
  packages/python/infermap/tests/test_scorers_dispatch.py
```
Expected: PASS + ruff clean. (Byte-identity: `_*_pure` are the original logic verbatim; the classes call them via the dispatcher on the pure path.)

- [ ] **Step 7: Commit** — `"feat(infermap): dispatch exact/fuzzy/initialism scorers to native (Wave 2)"`

---

### Task 4: Native parity fixtures

**Files:** Modify `packages/python/infermap/tests/test_native_parity.py`.

- [ ] **Step 1: Add parity cases** (mirror Wave 1's `native_only` idiom):
```python
from infermap.scorers.exact import _exact_score_pure
from infermap.scorers.fuzzy_name import _fuzzy_name_score_pure
from infermap.scorers.initialism import _score_pair

_NAME_PAIRS = [
    ("City", "city"), ("provider_npi", "ProviderNPI"), ("first_name", "firstName"),
    ("assay_id", "ASSI"), ("confidence_score", "CONSC"), ("variant_id", "VARI"),
    ("order_id", "orderid"), ("abc", "xyz"), ("HTTPSConnection", "https_connection"),
    ("a", "a"), ("dob", "date_of_birth"),
    # tokenizer-boundary pairs (the providerIDs/URLs class the naive impl gets wrong)
    ("providerIDs", "provider_i_ds"), ("URLs", "ur_ls"), ("macOS", "mac_os"),
    ("iOS", "i_os"),
]

@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_exact_parity(a, b):
    assert native_module().exact_score(a, b) == _exact_score_pure(a, b)

@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_fuzzy_parity(a, b):
    # the rapidfuzz-rs vs Python-rapidfuzz byte-equality re-validation (spec §6.1)
    assert native_module().fuzzy_name_score(a, b) == _fuzzy_name_score_pure(a, b)

@native_only
@pytest.mark.parametrize("a,b", _NAME_PAIRS)
def test_initialism_parity(a, b):
    got = native_module().initialism_score(a, b)
    assert got == _score_pair(a, b)  # both None or both equal float
```
Include ASCII-only pairs (the Unicode-lower/`\s` edge is out of scope, spec §6.3) and abbreviation pairs that hit the graded score + abstain + camelCase tokenizer.

- [ ] **Step 2: Verify collects + skips cleanly** (no wheel):
```
PYTHONPATH=packages/python/infermap POLARS_SKIP_CPU_CHECK=1 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest packages/python/infermap/tests/test_native_parity.py -q
ruff check packages/python/infermap/tests/test_native_parity.py
```
Expected: new tests SKIPPED, 0 errors, ruff clean.

- [ ] **Step 3: Commit** — `"test(infermap): scorer native parity fixtures (Wave 2)"`

---

### Task 5: CLAUDE.md note

- [ ] **Step 1** — extend the InferMap CLAUDE note: Wave 2 cut the pure name-scorers (exact/fuzzy_name/initialism) to `infermap-core`; **kernel returns the SCORE, reasoning stays host** (dodges `.3f` float-format parity); `fuzzy_name` **reuses `goldenmatch-score-core::jaro_winkler_similarity`** (infermap-core → score-core path dep; pyo3-free invariant holds). GOTCHAs: per-scorer input differs (exact=raw name; fuzzy/initialism=`canonical_name or name`); initialism's camelCase tokenizer uses regex LOOKAHEAD → hand-written char-scanner (Rust `regex` has no lookahead); the dead `_TOKEN_RE` was deleted (mirror the inline line-40 regex); `len_short/len_long` is integer division in Rust → cast f64 first; use `.chars().count()` (char len, Python-equal) not byte `.len()`. pattern_type/alias/profile = later waves; llm stays host.
- [ ] **Step 2: Commit** — `"docs: InferMap Wave 2 CLAUDE note"`

---

### Finalize

- [ ] Push `feat/infermap-core-wave2-scorers`; open PR base `main` (shows Wave 1's commits until #1490 merges — stacked; REST if GraphQL limited). PR body: Wave 2 name-scorers, score-core reuse, kernel-returns-score/reasoning-host, the tokenizer-lookahead + rapidfuzz-parity risks, stacked-on-#1490.
- [ ] `gh pr merge --auto --squash` (NO `--delete-branch`) and STOP. Do not poll CI.
