# InferMap Rust cutover — Wave 2 (pure name-scorers) — design

**Status:** approved (brainstorm 2026-07-06), pending spec review
**Motivation:** Wave 2 cuts the pure field-name similarity **scorers** — the M×N muscle —
into the `infermap-core` scaffold Wave 1 stood up (#1490). Three name-based scorers
(`exact`, `fuzzy_name`, `initialism`) become Rust kernels; `fuzzy_name` **reuses**
goldenmatch's `score-core` Jaro-Winkler (cross-package, no new kernel). Native + Python
this wave; WASM/TS deferred (a later wave, as in Wave 1b). Stacked on Wave 1's branch.
Related: `project_infermap_rust_cutover`.

## 1. Scope + decomposition (the muscle+clean-boundary test)

InferMap scores every (source field × target field) pair with a weighted list of
scorers, then assigns via Hungarian. Wave 2 = the **pure name-based** scorers:

| scorer | cut? | why |
|--------|------|-----|
| `exact` | YES (uniformity) | trivial `trim().lower()==` → 1.0/0.0; no muscle, but included for the "all name scorers Rust-sourced" story |
| `fuzzy_name` | YES (the win) | M×N Jaro-Winkler = the real muscle; clean 2-strings→f64 boundary; **reuses `score-core`** |
| `initialism` | YES (the algorithm) | tokenize + prefix-concat DP + compression-ratio; pure, drift-prone, abstains |
| `pattern_type` | later | regex over *sample values* + Python-`re`/Rust-`regex` parity risk |
| `alias` | later/host | dictionary lookup (data-dependent) |
| `profile` | later/host | profiling-stats dependent |
| `llm` | **host** | LLM call, uncuttable |

Orchestration (`MapEngine`, weighting, Hungarian) stays host ("smart pipe").

## 2. Architecture — per-pair, kernel returns the SCORE, reasoning stays host

Each scorer scores one `(a, b)` name pair → `ScorerResult{score, reasoning}` (or `None`
to abstain). The **muscle is the score number**; the `reasoning` is free-form diagnostic
text with formatted floats. So the kernel returns **only the score** (an `Option<f64>`
for the abstaining `initialism`); each Python scorer class keeps its own reasoning-string
formatting. This keeps the muscle in Rust and **dodges float-format parity** entirely
(`f"{x:.3f}"` never crosses the boundary).

`infermap-core` gains a pure-Rust dependency on `goldenmatch-score-core` (both pyo3-free;
`rapidfuzz` arrives transitively). The pyo3-free invariant holds — pyo3-free ≠
dependency-free.

## 3. The three kernels (`infermap-core`)

```rust
use goldenmatch_score_core::jaro_winkler_similarity;

/// ExactScorer: 1.0 iff trimmed-lowercased names are equal, else 0.0.
pub fn exact_score(a: &str, b: &str) -> f64 {
    if a.trim().to_lowercase() == b.trim().to_lowercase() { 1.0 } else { 0.0 }
}

/// FuzzyNameScorer: Jaro-Winkler on normalized names (reuses score-core).
/// normalize = strip + lower + remove `_`, `-`, ` ` (mirrors `fuzzy_name._normalize`).
pub fn fuzzy_name_score(a: &str, b: &str) -> f64 {
    jaro_winkler_similarity(&normalize(a), &normalize(b))
}
fn normalize(s: &str) -> String {
    s.trim().to_lowercase().chars().filter(|&c| c != '_' && c != '-' && c != ' ').collect()
}

/// InitialismScorer: `0.6 + 0.35*(len_short/len_long)` when one side is a
/// prefix-concat abbreviation of the other's tokens; `None` (abstain) otherwise.
/// Mirrors `initialism._score_pair` + `_is_prefix_concat` + `_tokenize`.
pub fn initialism_score(a: &str, b: &str) -> Option<f64> { ... }
```

### 3.1 `initialism` — the tokenizer is the load-bearing risk (§6)

`initialism._tokenize` splits camelCase/PascalCase/snake/kebab via a regex with a
**lookahead** — the **inline** regex at `initialism.py:40`
(`re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", chunk)`).
**WARNING:** `initialism.py:26` defines a module-level `_TOKEN_RE` that is a DIFFERENT,
DEAD regex (never used — different alternation + a `\b` in the lookahead). Mirror the
INLINE line-40 regex, NOT `_TOKEN_RE`. **Delete `_TOKEN_RE` as part of this wave** to
remove the trap. VERIFIED token outputs (live Python) to fixture against:
`HTTPSConnection`→`[https, connection]`, `providerID`→`[provider, id]`,
`order_id`→`[order, id]`, `ABC`→`[abc]`, `v2Name`→`[v, 2, name]`.
**Rust's `regex` crate does NOT support lookahead**, so the Rust tokenizer is a
**hand-written char-scanner** replicating the exact boundaries:
- pre-split on `_ - . <space>` (collapse runs) → chunks;
- within a chunk, emit tokens by the alternation's semantics: an uppercase run keeps all
  but its last char as an *acronym* token when a `[A-Z][a-z]` word follows (`HTTPSConnection`
  → `https`, `connection`; `providerID` → `provider`, `id`); a `[A-Z]?[a-z]+` word; a bare
  `[A-Z]+` acronym; a `\d+` run — each lowercased.
Then `_is_prefix_concat` (a DP: can `target` be formed by concatenating ≥1-char prefixes
of the source tokens in order, using each exactly once) and the ratio score. The DP is
the muscle; the tokenizer is the parity-risky port.
**Integer-division trap:** the score is `0.6 + 0.35 * (len_short / len_long)`. In Rust,
`usize / usize` is INTEGER division (always `0` here, since `short < long`), collapsing
every graded score to `0.6`. Cast to f64 FIRST and preserve Python's op order:
`0.6 + 0.35 * (short as f64 / long as f64)` — byte-parity needs the exact order (e.g.
`assay_id`/`ASSI` → `0.7999999999999999`, not `0.8`). **Contingency:** if the char-scanner
can't be made byte-identical, fall back to a DP-only cut — host tokenizes (Python regex),
kernel takes `Vec<Vec<String>>` token lists — single-sourcing the DP muscle but not the
tokenizer. (Decide during implementation from the parity fixture.)

Unit tests in `infermap-core`: exact match/mismatch; fuzzy on `city`/`prospectcity`;
tokenizer on `HTTPSConnection`/`providerID`/`order_id`/`ABC`/`v2Name`; initialism
abstain (non-abbreviation) + graded score + the `joined_a == joined_b` → None edge.

## 4. `infermap-native` — 3 pyfunction shims

```rust
#[pyfunction] fn exact_score(a: &str, b: &str) -> PyResult<f64> { Ok(infermap_core::exact_score(a, b)) }
#[pyfunction] fn fuzzy_name_score(a: &str, b: &str) -> PyResult<f64> { Ok(infermap_core::fuzzy_name_score(a, b)) }
#[pyfunction] fn initialism_score(a: &str, b: &str) -> PyResult<Option<f64>> { Ok(infermap_core::initialism_score(a, b)) }
```
Register each with the `self::`-qualified form (`wrap_pyfunction!(self::exact_score, m)`
…) — the `check_native_symbols._WRAP` regex requirement from Wave 1. `Option<f64>` maps
to Python `float | None` (abstain). `infermap-native/Cargo.toml` gains
`goldenmatch-score-core = { path = "../score-core" }` transitively via `infermap-core`
(no direct dep needed — it depends on `infermap-core`).

## 5. Python dispatch + gate

**Per-scorer kernel input — NOT uniform (load-bearing).** The three scorers feed the
kernel DIFFERENT strings; passing the wrong one silently changes behavior, and because
the `_*_pure` reference must ALSO preserve the original input, a wrong choice can pass
the `native == _pure` gate while diverging from the real scorer:

| scorer | strings passed to the kernel | reasoning uses |
|--------|------------------------------|----------------|
| `exact` | **raw** `source.name`, `target.name` | raw `source.name` |
| `fuzzy_name` | `source.canonical_name or source.name`, `target.canonical_name or target.name` | the normalized names |
| `initialism` | `source.canonical_name or source.name`, `target.canonical_name or target.name` | (host-formatted) |

The `_*_pure` references are the CURRENT scorer logic extracted verbatim (same input
selection), so the gate compares native against a faithful reference — not a co-drifted one.

Each scorer class calls the kernel for the score, keeps its reasoning. E.g.:
```python
# fuzzy_name.py
def score(self, source, target):
    src_name = source.canonical_name or source.name
    tgt_name = target.canonical_name or target.name
    similarity = _fuzzy_name_score(src_name, tgt_name)   # dispatcher
    src_norm, tgt_norm = _normalize(src_name), _normalize(tgt_name)  # host, for reasoning
    return ScorerResult(score=similarity, reasoning=f"Jaro-Winkler similarity between '{src_norm}' and '{tgt_norm}': {similarity:.3f}")
```
Dispatchers (one per scorer, e.g. in `scorers/_native.py` or inline): `if
native_enabled("<scorer>"): return native_module().<fn>(a, b) else: return
_<scorer>_pure(a, b)`. The `_*_pure` reference = the current scorer's score logic
extracted verbatim (`exact` equality; `fuzzy` `_normalize`+rapidfuzz; `initialism`
`_score_pair`). NO try/except (string inputs, no dtype fallback).
- `initialism`: the dispatcher returns `float | None`; the class maps `None`→abstain
  (return `None`), else builds the ScorerResult with reasoning.
- `_native_loader.py`: `_GATED_ON` + `_COMPONENT_SYMBOLS` gain `exact_score`,
  `fuzzy_name_score`, `initialism_score`. Update the loader-test exact-set if one exists.

## 6. Load-bearing risks

1. **`fuzzy_name` — rapidfuzz parity.** `score-core`'s `rapidfuzz-rs`
   `jaro_winkler::normalized_similarity` vs Python `rapidfuzz.distance.JaroWinkler.similarity`
   at the last ULP. Same algorithm/author; de-risked by goldenmatch's existing native-jaro
   parity (the native path ships in production scoring). The Wave 2 `native == pure` gate
   RE-VALIDATES on InferMap's normalized-name inputs — a random-name-pair fixture asserts
   byte-equality. If a divergence surfaces, `score-core` becomes the reference and the
   fixture documents the ULP delta (as goldenmatch already treats it).
2. **`initialism` — the lookahead tokenizer** (§3.1). Rust `regex` has no lookahead → a
   hand-written char-scanner, pinned by a camelCase/PascalCase/acronym/digit fixture. The
   riskiest port; DP-only fallback documented.
3. **`trim`/`to_lowercase` Unicode edge — ALL THREE, not just `exact`** — Python
   `.strip()`/`.lower()` vs Rust `.trim()`/`.to_lowercase()` diverge on non-ASCII (the
   Wave 1 edge). It lives in `exact` (equality), `fuzzy_name`'s `normalize()`, AND the
   `initialism` tokenizer's per-token `.lower()`. ASCII names in practice; documented,
   ASCII-fixtured across all three.

## 7. Parity gate + rollout

- Extend `tests/test_native_parity.py` (Wave 1's `native_only` skip harness): `native ==
  _<scorer>_pure` for all three across fixtures — exact match/mismatch/whitespace; fuzzy
  random-name pairs (the rapidfuzz-agreement set) + identical/disjoint; initialism
  abstain + graded + camelCase tokenizer edges. Box-safe pure-path unit tests too.
- native_symbols: the 3 new `self::`-registered exports + the 3 `native_module().X` host
  refs self-reconcile (added together); the gate already includes `infermap` (Wave 1).
- Stacked on `feat/infermap-core-wave1-detect` (#1490 queued). Rebase onto main if #1490
  merges first. Rust is CI-built; Python pure path box-verified; `ruff` on touched Python.
- CLAUDE.md: Wave 2 note (name-scorers cut; score-core reuse; initialism lookahead-
  tokenizer hand-scanner; reasoning stays host; pattern_type/alias/profile later, llm host).
