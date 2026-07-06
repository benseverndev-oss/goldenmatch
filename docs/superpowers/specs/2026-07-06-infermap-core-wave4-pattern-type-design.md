# InferMap Wave 4 — `pattern_type` scorer cutover (design, risk-gated)

**Date:** 2026-07-06
**Status:** Approved (design)
**Depends on:** Wave 1 (`detect_domain`), Wave 2 (name-scorers), Wave 3 (`profile_score`) — all merged / in-flight to `main`.
**Branch:** `feat/infermap-core-wave4-pattern-type` off fresh `origin/main`.

## 1. Goal

Cut over `PatternTypeScorer`'s regex classification into a pyo3-free
`infermap-core` kernel (`pattern_match_types`) + `infermap-native` PyO3 shim,
dispatched via `native_module()` with a byte-identical pure-Python fallback.

This is the **risk-gated** wave: unlike Waves 1–3 (string/float math), the muscle
here is *regex*, and Python's `re` engine and Rust's `regex` crate are different
implementations. The wave's defining artifact is a **regex fixture-drift gate** —
an adversarial per-string corpus proving byte-identical classification across the
agreed parity contract before the kernel may run under `INFERMAP_NATIVE=auto`.

Pattern: kernel returns the classification bits; all aggregation + reasoning stay
host, exactly as Wave 3 kept avg-length + reasoning host.

## 2. Background — what `PatternTypeScorer` does today

`packages/python/infermap/infermap/scorers/pattern_type.py`. `SEMANTIC_TYPES` is
an **ordered** dict of 8 name→regex entries; `_COMPILED` compiles them.

`_classify_with_pct(field, threshold=0.6)`:
1. `samples = [s for s in field.sample_values if s is not None and str(s).strip() != ""]`
2. if none → `(None, 0.0)`
3. for each `(type_name, pattern)` **in dict order**:
   `matches = sum(1 for s in samples if pattern.match(str(s).strip()))`;
   `pct = matches / len(samples)`; keep as best if `pct > best_pct` (strict `>`).
4. if `best_type is not None and best_pct >= threshold` → `(best_type, best_pct)`
   else `(None, 0.0)`.

`PatternTypeScorer.score(source, target)`: filter samples per side; abstain
(`None`) if either side has zero samples; classify both; then:
- both `None` → `ScorerResult(0.0, "No semantic type detected in either field's samples")`
- `src_type != tgt_type` → `ScorerResult(0.0, "Semantic type mismatch: source=… vs target=…")`
- same type → `ScorerResult(min(src_pct, tgt_pct), "Both fields classified as '…' (src=…%, tgt=…%)")`

The 8 patterns (`re`, `str` mode, no flags):

| bit | type | pattern |
| --- | --- | --- |
| 0 | email | `^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$` |
| 1 | uuid | `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$` |
| 2 | date_iso | `^\d{4}-\d{2}-\d{2}$` |
| 3 | ip_v4 | `^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$` |
| 4 | url | `^https?://[^\s]+$` |
| 5 | phone | `^[\+\d]?(\d[\s\-\.]?){7,14}\d$` |
| 6 | zip_us | `^\d{5}(-\d{4})?$` |
| 7 | currency | `^[\$\£\€]\s?\d[\d,]*(\.\d{1,2})?$` |

## 3. Kernel/host split (decided)

**Regex-only kernel; host aggregates.** The host filters `None`/blank samples and
`.strip()`s them (byte-identical to Python, trivially), passes the pre-stripped
list, and the kernel runs only the 8 regexes, returning a **per-sample bitmask**
(`u32`, bit `i` set iff the sample matches pattern `i`, in the table order above).
The host does the per-type count (for each bit `i`, `sum(1 for m in masks if m &
(1 << i))` — one bit counted across all samples) / pct / best-by-order /
threshold / score / reasoning.

Rationale:
- **Minimal risk surface.** Regex matching is the *only* thing the gate must
  prove. Counting (`popcount` per bit), the strict-`>` best-selection, the
  threshold gate, and the score/reasoning are pure Python — byte-identical by
  construction, nothing to gate.
- **Strip-parity sidestepped.** `str.strip()` (Python `isspace()` set) and Rust
  `str::trim()` (`char::is_whitespace` / Unicode White_Space) differ on control
  chars like `\x1c`–`\x1f`. Keeping strip host-side removes that divergence from
  the kernel entirely (mirrors Wave 3 keeping `_avg_value_length` host).
- **One FFI call** for the whole sample list (not per-string).

### Kernel signature (`infermap-core`)

```rust
/// Byte-parity reference: infermap.scorers.pattern_type._match_types_pure (per element).
/// bit i (LSB=0) set iff sample matches SEMANTIC_TYPES[i] (table order). Input is the
/// host-pre-stripped, non-blank sample list; returns one bitmask per input element.
pub fn pattern_match_types(samples: &[String]) -> Vec<u32>
```

Compiled once via `std::sync::OnceLock<[Regex; 8]>` (the `regex` crate). Matching
uses `Regex::is_match` on the pre-stripped (newline-free) string, so `^…$` is a
full-string match in both engines.

### Dependency

Add `regex = "1"` to `infermap-core`'s `[dependencies]`. `infermap-core` is a
standalone crate (own `[profile.release]`), so this is a direct dep, not a
workspace-inherited one. The `regex` crate is pyo3-free, preserving the invariant.
(NB: a sibling uses `fancy-regex`; we do **not** — none of the 8 patterns use
lookaround/backreferences, so the lighter plain `regex` is the correct fit.)

## 4. Rust pattern strings — semantic equivalents (NOT textual copies)

Seven patterns port character-for-character. **`currency` must drop the
non-ASCII backslash-escapes** — `\£` / `\€` are unrecognized escapes that fail to
compile in the `regex` crate, whereas in Python `\£` is simply a literal `£`:

| type | Rust pattern literal |
| --- | --- |
| email | `^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$` |
| uuid | `^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$` |
| date_iso | `^\d{4}-\d{2}-\d{2}$` |
| ip_v4 | `^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$` |
| url | `^https?://[^\s]+$` |
| phone | `^[\+\d]?(\d[\s\-\.]?){7,14}\d$` |
| zip_us | `^\d{5}(-\d{4})?$` |
| **currency** | `^[$£€]\s?\d[\d,]*(\.\d{1,2})?$`  ← `£`/`€` unescaped |

`£` (U+00A3) and `€` (U+20AC) are literal codepoints in both engines — a literal
codepoint match with no character-class table involved.

## 5. Parity analysis — why this is safe within the ASCII contract

- **Boolean-only usage.** The scorer only asks "did it match?"
  (`sum(1 for … if pattern.match(s))`). Capture-group contents are never read, so
  NFA-vs-backtracking, lookaround, and backreferences are all irrelevant (and none
  appear in the 8 patterns). Both engines agree on *language membership* for these
  constructs, which is all a boolean needs. The repeated capturing group in
  `phone` (`(\d[\s\-\.]?){7,14}`) is bounded — both engines accept it.
- **Anchoring / `$` newline.** Python `$` matches at end-of-string *or before a
  trailing `\n`*; Rust `$` (non-multiline) matches only at end of haystack. The
  host strips before calling the kernel, so no input carries a trailing newline —
  the divergence is unreachable. `^` is start-of-string in both.
- **Literal non-ASCII (`£`/`€`).** Deterministic codepoint match, no table →
  **must-pass** (currency fixtures with `£`/`€` + ASCII digits are in the gate).
- **`\d` / `\s` on non-ASCII input.** The one genuine divergence: Python `\d`
  (Unicode) and Rust `\d` = `\p{Nd}`, likewise `\s` / `\p{White_Space}`, draw on
  Unicode tables that can skew by version between CPython's `unicodedata` and the
  `regex` crate's bundled tables. This affects `date_iso`, `ip_v4`, `url`,
  `phone`, `zip_us`, `currency`. → **documented parity edge, informational
  fixtures only** (§6.3), mirroring the `str.lower()`/`\s` edge already documented
  for Waves 1–2.

## 6. The fixture-drift gate

The heart of this wave. A per-string corpus at
`packages/python/infermap/tests/pattern_type_corpus.jsonl` (one JSON object per
line: `{"s": "<sample>", "tier": "must" | "informational", "note": "<why>"}` —
`tier` defaults to `"must"` when absent), driving an exact bitmask comparison.

### 6.1 Assertion

```python
@native_only
@pytest.mark.parametrize("s", _corpus_must())  # tier=="must" strings only
def test_pattern_type_parity(s):
    assert native_module().pattern_match_types([s]) == [_match_types_pure(s)]
```

Exact `==` on the returned `u32` bitmask (list of length 1). `_match_types_pure`
(§7) is the oracle.

### 6.2 Must-pass coverage (ASCII contract)

Per type: canonical positive; structural near-misses that still match
(`date_iso` `2026-13-99`, `ip_v4` `999.999.999.999` — the patterns are structural,
not semantic); genuine negatives (`email` missing `@`, `uuid` wrong hyphen
offsets, `url` `ftp://x`, `phone` below the `{7,14}` floor and above the ceiling,
`zip_us` `1234`/`123456`, `currency` `$`-only). Plus:
- **Cross-type ambiguity**: strings matching >1 pattern, to exercise the
  per-type-independent popcount (e.g. an all-digit string vs `phone`/`ip_v4`
  shapes; ensure the bitmask has multiple bits set and the host still counts each
  type independently).
- **Boundary**: `phone` with exactly 7 and exactly 14 interior reps; `currency`
  `£12.50` / `€1,000.00` / `$5`; `email` 2-char TLD floor.
- **Post-strip empties are handled host-side** (never reach the kernel), but
  include a whitespace-only string in the host-level dispatch test (§7) to prove
  the filter.

### 6.3 Informational fixtures (documented edge — NOT must-pass)

Recorded in the corpus with `"tier": "informational"` and asserted **only** under
a separate, non-gating test that XFAILs/records rather than blocks:
Arabic-Indic `٥`, fullwidth `５`, Devanagari digits, NBSP inside a `url` sample,
and an interior `\x1c` inside a `phone` sample (the sharpest `\s`-skew case:
Python-whitespace but NOT Unicode `White_Space`, and it survives `strip()` when
interior — the canonical edge already named in Wave 1's `detect.rs`).
This pins *where* the `\d`/`\s` Unicode boundary actually falls without making the
gate hostage to Unicode-table version skew. If CI shows these actually agree, we
note it; if they diverge, that is the expected, documented edge.

> The corpus loader partitions by a `tier` field (`"must"` default vs
> `"informational"`); `test_pattern_type_parity` consumes only `must`.

## 7. Host dispatch (`pattern_type.py`)

Mirror Wave 3's structure so both paths share the aggregation:

- `_match_types_pure(s: str) -> int` — the oracle: for `i, pattern in
  enumerate(_COMPILED.values())`, set bit `i` if `pattern.match(s)`. Returns the
  bitmask. (Replaces the inline per-pattern `.match` in the counting loop.)
- `_match_types_batch(stripped: list[str]) -> list[int]` — dispatch:
  `native_module().pattern_match_types(stripped)` when
  `native_enabled("pattern_match_types")`, else `[_match_types_pure(s) for s in
  stripped]`.
- `_classify_with_pct(field, threshold=0.6)` — unchanged control flow, but the
  counting is re-expressed over bitmasks:
  ```python
  samples = [str(s).strip() for s in field.sample_values
             if s is not None and str(s).strip() != ""]
  if not samples:
      return None, 0.0
  masks = _match_types_batch(samples)
  best_type, best_pct = None, 0.0
  for i, type_name in enumerate(SEMANTIC_TYPES):        # dict order
      matches = sum(1 for m in masks if m & (1 << i))
      pct = matches / len(samples)
      if pct > best_pct:                                # strict >, unchanged
          best_pct, best_type = pct, type_name
  if best_type is not None and best_pct >= threshold:
      return best_type, best_pct
  return None, 0.0
  ```
  This is byte-identical to the current logic: same sample filter, same
  per-type-independent count (a sample can set multiple bits, exactly as it could
  match multiple patterns today), same strict-`>` selection in the same dict
  order, same threshold. `classify_field` and `PatternTypeScorer.score` are
  **unchanged** (they call `_classify_with_pct`).

`SEMANTIC_TYPES` bit order == `_COMPILED` order == kernel array order — a single
canonical ordering the spec pins; the kernel's `[Regex; 8]` is built in the same
order.

## 8. Native shim + loader + gate wiring

- **`infermap-native`**: `#[pyfunction] fn pattern_match_types(samples: Vec<String>)
  -> PyResult<Vec<u32>> { Ok(infermap_core::pattern_match_types(&samples)) }`;
  register `wrap_pyfunction!(self::pattern_match_types, m)`. Plain return type — no
  `clippy::type_complexity`. (`Vec<String>` arg is 1 param — no `too_many_arguments`.)
- **`_native_loader`**: add `pattern_match_types` to `_GATED_ON` and
  `_COMPONENT_SYMBOLS` (component name == symbol).
- **`check_native_symbols.py`**: `infermap` REGISTRY entry exists; the new symbol
  reconciles automatically (kernel `wrap_pyfunction!(self::pattern_match_types` +
  host `native_module().pattern_match_types`). `infermap.allow` stays empty.
- **CI path filter** (`ci.yml` `infermap_native` block): add
  `packages/python/infermap/infermap/scorers/pattern_type.py` and
  `packages/python/infermap/tests/pattern_type_corpus.jsonl`.

## 9. Out of scope

- `alias` (dictionary/host) and `llm` (external call) scorers.
- The WASM/TS `infermap` surface (still deferred, Wave 1b-style).
- Any change to `SEMANTIC_TYPES`, thresholds, precedence, weights, or reasoning
  strings. Pure cutover — same output, new backend.

## 10. Risk assessment

Contained, and honestly bounded:

- The regex engines agree on **language membership** for all 8 constructs within
  the ASCII domain (no lookaround/backref/capture-content dependence).
- The one real divergence (`\d`/`\s` Unicode tables) is **explicitly outside the
  must-pass contract** and *measured* by informational fixtures rather than
  assumed away — the gate records the boundary.
- Textual pattern differences (currency escapes) are semantic no-ops, proven by
  the corpus.
- Strip / anchoring divergences are structurally unreachable (strip stays host).

The failure mode to watch: if a **must-pass ASCII** fixture ever drifts in CI,
that is a real engine-semantics surprise (not the documented edge) and blocks the
wave until understood — which is exactly what a fixture-drift gate is for.

## 11. Build environment constraints

- **Box-runnable (pure path):** `_match_types_pure`, `_classify_with_pct` via the
  pure branch, the corpus loader, dispatch tests, and `check_native_symbols.py
  infermap` run locally with `PYTHONPATH=packages/python/infermap
  POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0` on
  `D:/show_case/goldenmatch/.venv/Scripts/python.exe`. `ruff check` touched Python.
- **CI-only:** Rust compile / clippy / `cargo test` of the kernel; the wheel-built
  `test_pattern_type_parity` (skips on the box via `@native_only`, runs in the
  advisory `infermap_native` lane under `INFERMAP_NATIVE=1`).
- **Merge-queue repo:** `gh pr merge --auto --squash` without `--delete-branch`;
  benzsevern gh account.
