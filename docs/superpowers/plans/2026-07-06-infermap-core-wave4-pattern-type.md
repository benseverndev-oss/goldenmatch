# InferMap Wave 4 — `pattern_type` Scorer Cutover Implementation Plan (risk-gated)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `PatternTypeScorer`'s regex classification into a pyo3-free `infermap-core::pattern_match_types` kernel (the `regex` crate) + `infermap-native` PyO3 shim, dispatched via `native_module()` with a byte-identical pure-Python fallback, proven by a regex fixture-drift corpus.

**Architecture:** Regex-only kernel — the host filters + `.strip()`s samples and does all counting/scoring/reasoning; the kernel runs only the 8 compiled regexes and returns a per-sample `u32` bitmask (bit `i` = matches `SEMANTIC_TYPES[i]`). Bit order is the `SEMANTIC_TYPES` insertion order: `0=email, 1=uuid, 2=date_iso, 3=ip_v4, 4=url, 5=phone, 6=zip_us, 7=currency`. Parity contract = ASCII-domain byte-identity; the `\d`/`\s` Unicode-table edge is documented (informational fixtures, non-gating).

**Tech Stack:** Rust (`infermap-core` + `regex="1"`, `infermap-native` abi3/maturin), Python `re`, pytest fixture-drift corpus, `dorny/paths-filter` CI.

**Spec:** `docs/superpowers/specs/2026-07-06-infermap-core-wave4-pattern-type-design.md`

**Reference skill:** @superpowers:test-driven-development

---

## Environment & Conventions

**Repo:** `D:\show_case\gg-local-llm` — branch `feat/infermap-core-wave4-pattern-type` (checked out off fresh `origin/main`, spec committed).

**Box-runnable (Python pure path only):**
```bash
INTERP="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
export PYTHONPATH="packages/python/infermap" POLARS_SKIP_CPU_CHECK=1 INFERMAP_NATIVE=0
```
`cd "D:/show_case/gg-local-llm"` first. Run pytest via `"$INTERP" -m pytest ...`, ruff via `"$INTERP" -m ruff check <file>` on every touched Python file. (The `PYTHONPATH=packages/python/infermap` resolves within THIS `gg-local-llm` checkout; only the interpreter is borrowed from the goldenmatch venv.)

**CI-only (box CANNOT — do NOT run `cargo`):** Rust compile / clippy / `cargo test` of `infermap-core` + `infermap-native`; the wheel-built `test_pattern_type_parity` (runs in the advisory `infermap_native` lane under `INFERMAP_NATIVE=1`; `@native_only` skips it on the box).

**Ordering (TDD caveat):** Python tasks (1, 2) are real box red→green. Rust tasks (3, 4) are write-against-spec + verify-by-eye; CI compiles. Corpus + parity fixtures (5) collect-and-skip on the box. Do Python first, then Rust, then corpus.

**Git:** benzsevern (`unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)`). Merge-queue — `gh pr merge --auto --squash` WITHOUT `--delete-branch`. Commit trailer:
```
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
```

---

## File Structure

| File | Responsibility | Action |
| --- | --- | --- |
| `packages/python/infermap/infermap/scorers/pattern_type.py` | Add `_match_types_pure` (oracle) + `_match_types_batch` (dispatch); re-express `_classify_with_pct` over bitmasks (byte-identical) | Modify |
| `packages/python/infermap/infermap/_native_loader.py` | Add `pattern_match_types` to `_GATED_ON` + `_COMPONENT_SYMBOLS` | Modify |
| `packages/rust/extensions/infermap-core/Cargo.toml` | Add `regex = "1"` dep | Modify |
| `packages/rust/extensions/infermap-core/src/lib.rs` | Add `pattern_match_types` kernel (OnceLock `[Regex; 8]`) + unit test | Modify |
| `packages/rust/extensions/infermap-native/src/lib.rs` | Add `#[pyfunction] pattern_match_types` shim + register | Modify |
| `packages/python/infermap/tests/pattern_type_corpus.jsonl` | Fixture-drift corpus (tier must/informational) | Create |
| `packages/python/infermap/tests/test_native_parity.py` | Add corpus loader + `test_pattern_type_parity` (must) + informational recording test | Modify |
| `packages/python/infermap/tests/test_scorers_dispatch.py` | Box-runnable pure/dispatch/classify/abstain tests | Modify |
| `.github/workflows/ci.yml` | Add `pattern_type.py` + corpus to `infermap_native` path filter | Modify |

---

## Task 1: `pattern_type.py` — oracle + dispatch + bitmask refactor (box TDD)

**Files:**
- Modify: `packages/python/infermap/infermap/scorers/pattern_type.py`
- Test: `packages/python/infermap/tests/test_scorers_dispatch.py`

- [ ] **Step 1: Write the failing tests** (append to `test_scorers_dispatch.py`)

```python
# --- Wave 4: pattern_type scorer ---
from infermap.scorers.pattern_type import (  # noqa: E402
    _match_types_pure,
    _classify_with_pct,
    PatternTypeScorer,
)


def test_match_types_pure_bitmask():
    # bit0=email, bit7=currency; "hello" matches nothing.
    assert _match_types_pure("user@example.com") == 1 << 0
    # date_iso (bit2) AND phone (bit5) co-match by construction: an 8-digit
    # 2-hyphen string satisfies phone's ^[\+\d]?(\d[\s\-\.]?){7,14}\d$ (the
    # hyphens are absorbed as optional separators). This is expected, not a bug.
    assert _match_types_pure("2026-07-06") == (1 << 2) | (1 << 5)
    assert _match_types_pure("$5") == 1 << 7
    assert _match_types_pure("hello world") == 0


def test_classify_with_pct_unchanged_behavior():
    emails = FieldInfo(name="e", sample_values=["a@b.co", "x@y.com", "p@q.net"],
                       value_count=3)
    assert _classify_with_pct(emails) == ("email", 1.0)
    # below threshold (1 of 3 is an email) -> (None, 0.0)
    mixed = FieldInfo(name="m", sample_values=["a@b.co", "hello", "world"],
                      value_count=3)
    assert _classify_with_pct(mixed) == (None, 0.0)
    # no samples -> (None, 0.0)
    empty = FieldInfo(name="z", sample_values=["  ", None], value_count=0)
    assert _classify_with_pct(empty) == (None, 0.0)


def test_pattern_type_scorer_abstain_mismatch_match():
    emails_a = FieldInfo(name="a", sample_values=["a@b.co", "x@y.com"], value_count=2)
    emails_b = FieldInfo(name="b", sample_values=["p@q.net", "m@n.org"], value_count=2)
    dates = FieldInfo(name="d", sample_values=["2026-07-06", "2025-01-02"], value_count=2)
    none_field = FieldInfo(name="n", sample_values=["  ", None], value_count=0)
    # abstain when a side has no samples
    assert PatternTypeScorer().score(emails_a, none_field) is None
    # same type -> min of pcts, reasoning names the type
    r = PatternTypeScorer().score(emails_a, emails_b)
    assert r is not None and r.score == 1.0 and "email" in r.reasoning
    # different types -> 0.0 mismatch
    r2 = PatternTypeScorer().score(emails_a, dates)
    assert r2 is not None and r2.score == 0.0 and "mismatch" in r2.reasoning
```
(`FieldInfo` is imported at the top of that test file from the Wave 3 work — verify; if absent add `from infermap.types import FieldInfo`.)

- [ ] **Step 2: Run to verify FAIL**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k "match_types or classify_with_pct or pattern_type_scorer"
```
Expected: FAIL — `ImportError: cannot import name '_match_types_pure'`.

- [ ] **Step 3: Refactor `pattern_type.py`**

After the existing imports (`import re`, `from infermap.types import ...`), add:
```python
from infermap._native_loader import native_enabled, native_module
```

After the `_COMPILED` dict (before `_classify_with_pct`), add the oracle + dispatch:
```python
def _match_types_pure(s: str) -> int:
    """Bitmask oracle for ``infermap-core::pattern_match_types``.

    Bit ``i`` (LSB=0) is set iff ``s`` matches the i-th ``SEMANTIC_TYPES``
    pattern, in insertion order. ``s`` is expected pre-stripped by the caller.
    """
    mask = 0
    for i, pattern in enumerate(_COMPILED.values()):
        if pattern.match(s):
            mask |= 1 << i
    return mask


def _match_types_batch(stripped: list[str]) -> list[int]:
    """Per-sample bitmasks; native kernel when available, pure oracle otherwise."""
    if native_enabled("pattern_match_types"):
        return list(native_module().pattern_match_types(stripped))
    return [_match_types_pure(s) for s in stripped]
```

Replace the body of `_classify_with_pct` with the bitmask formulation (keep the signature and docstring):
```python
def _classify_with_pct(
    field: FieldInfo,
    threshold: float = 0.6,
) -> tuple[str | None, float]:
    """Return (best_type, match_pct) or (None, 0.0) if below threshold or no samples."""
    samples = [
        str(s).strip()
        for s in field.sample_values
        if s is not None and str(s).strip() != ""
    ]
    if not samples:
        return None, 0.0

    masks = _match_types_batch(samples)

    best_type: str | None = None
    best_pct: float = 0.0
    for i, type_name in enumerate(SEMANTIC_TYPES):  # insertion order == bit order
        matches = sum(1 for m in masks if m & (1 << i))
        pct = matches / len(samples)
        if pct > best_pct:
            best_pct = pct
            best_type = type_name

    if best_type is not None and best_pct >= threshold:
        return best_type, best_pct
    return None, 0.0
```

Leave `classify_field` and `PatternTypeScorer.score` **unchanged** (they call `_classify_with_pct`).

> Byte-identity: `samples` now holds the stripped strings, but the filter predicate
> and match input (`str(s).strip()`) are identical to the original, `len(samples)`
> is unchanged, per-type counting stays independent (a sample can set multiple bits
> exactly as it could match multiple patterns), the strict `>` selection runs in the
> same `SEMANTIC_TYPES` dict order, and the threshold gate is unchanged.

- [ ] **Step 4: Run to verify PASS**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k "match_types or classify_with_pct or pattern_type_scorer"
```
Expected: PASS.

- [ ] **Step 5: Full-file regression + ruff**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers/ -q
"$INTERP" -m ruff check packages/python/infermap/infermap/scorers/pattern_type.py packages/python/infermap/tests/test_scorers_dispatch.py
```
Expected: all pass; ruff clean. (`test_scorers/` catches any existing pattern_type unit tests that must still pass — if that dir/test doesn't exist, skip that line.)

- [ ] **Step 6: Commit**

```bash
git add packages/python/infermap/infermap/scorers/pattern_type.py packages/python/infermap/tests/test_scorers_dispatch.py
git commit -m "feat(infermap): pattern_type dispatch + bitmask oracle (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status: `DONE` / `BLOCKED` / `DONE_WITH_CONCERNS` + pytest summary + SHA. Do NOT push. Only touch the two files.

---

## Task 2: Loader wiring (`_native_loader.py`)

**Files:**
- Modify: `packages/python/infermap/infermap/_native_loader.py`
- Test: `packages/python/infermap/tests/test_scorers_dispatch.py`

- [ ] **Step 1: Write the failing test** (append to `test_scorers_dispatch.py`)

```python
def test_pattern_match_types_registered_in_loader():
    from infermap._native_loader import _COMPONENT_SYMBOLS, _GATED_ON
    assert _COMPONENT_SYMBOLS.get("pattern_match_types") == "pattern_match_types"
    assert "pattern_match_types" in _GATED_ON
```

- [ ] **Step 2: Run to verify FAIL**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k pattern_match_types_registered
```
Expected: FAIL (AssertionError).

- [ ] **Step 3: Edit `_native_loader.py`**

Add `"pattern_match_types"` to the `_GATED_ON` frozenset literal. This branch's base
has the 4-element set (Wave 3's `profile_score` is NOT here yet — the Task 7 rebase
merges it). Replace the whole literal with:
```python
_GATED_ON: frozenset[str] = frozenset(
    {"detect_domain", "exact_score", "fuzzy_name_score", "initialism_score",
     "pattern_match_types"}
)
```
> Do NOT add `profile_score` here — that's Wave 3's line; the rebase will merge it in.
> If the base literal you actually see differs (e.g. Wave 3 landed into this base after
> all), append `"pattern_match_types"` to whatever set is present rather than
> overwriting other entries.

Add to `_COMPONENT_SYMBOLS` (after the last entry):
```python
    "pattern_match_types": "pattern_match_types",
}
```

- [ ] **Step 4: Run to verify PASS**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py -q -k pattern_match_types_registered
```
Expected: PASS.

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/infermap/infermap/_native_loader.py
git add packages/python/infermap/infermap/_native_loader.py packages/python/infermap/tests/test_scorers_dispatch.py
git commit -m "feat(infermap): gate pattern_match_types in native loader (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status + SHA. Do NOT push. Only touch `_native_loader.py` + `test_scorers_dispatch.py`.

---

## Task 3: The kernel + regex dep (`infermap-core`) — CI-only

**Do NOT run cargo (box can't build).** Write against spec, verify by eye, commit. CI compiles + runs the unit test.

**Files:**
- Modify: `packages/rust/extensions/infermap-core/Cargo.toml`
- Modify: `packages/rust/extensions/infermap-core/src/lib.rs`

- [ ] **Step 1: Add the `regex` dependency**

In `packages/rust/extensions/infermap-core/Cargo.toml`, under `[dependencies]` (after the `goldenmatch-score-core` line):
```toml
regex = "1"
```

- [ ] **Step 2: Add the kernel** — insert at the END of `src/lib.rs`, BEFORE the `#[cfg(test)] mod tests { ... }` block. (If `mod tests` is the last item, insert the kernel just above it; the `use` goes at the top of the file with the other `use` lines.)

At the TOP of the file, add to the imports:
```rust
use regex::Regex;
use std::sync::OnceLock;
```

Then the kernel (module scope, before `#[cfg(test)]`):
```rust
const N_SEMANTIC_TYPES: usize = 8;

/// The 8 semantic-type regexes, in SEMANTIC_TYPES insertion order (bit index).
/// currency drops the non-ASCII backslash-escapes (`\£`/`\€` fail to compile in
/// the `regex` crate; `£`/`€` are literal codepoints either way).
fn semantic_patterns() -> &'static [Regex; N_SEMANTIC_TYPES] {
    static PATS: OnceLock<[Regex; N_SEMANTIC_TYPES]> = OnceLock::new();
    PATS.get_or_init(|| {
        [
            Regex::new(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$").unwrap(),
            Regex::new(
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
            )
            .unwrap(),
            Regex::new(r"^\d{4}-\d{2}-\d{2}$").unwrap(),
            Regex::new(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$").unwrap(),
            Regex::new(r"^https?://[^\s]+$").unwrap(),
            Regex::new(r"^[\+\d]?(\d[\s\-\.]?){7,14}\d$").unwrap(),
            Regex::new(r"^\d{5}(-\d{4})?$").unwrap(),
            Regex::new(r"^[$£€]\s?\d[\d,]*(\.\d{1,2})?$").unwrap(),
        ]
    })
}

/// Byte-parity reference: infermap.scorers.pattern_type._match_types_pure (per element).
/// bit i (LSB=0) set iff the (host-pre-stripped) sample matches SEMANTIC_TYPES[i].
/// Boolean membership only; `^...$` on a newline-free string == Python `.match` full-match.
pub fn pattern_match_types(samples: &[String]) -> Vec<u32> {
    let pats = semantic_patterns();
    samples
        .iter()
        .map(|s| {
            let mut mask = 0u32;
            for (i, re) in pats.iter().enumerate() {
                if re.is_match(s) {
                    mask |= 1 << i;
                }
            }
            mask
        })
        .collect()
}
```

- [ ] **Step 3: Add a unit test** — inside the existing `#[cfg(test)] mod tests { ... }` (has `use super::*;`):
```rust
    #[test]
    fn pattern_match_types_bits() {
        let mk = |x: &str| x.to_string();
        let out = pattern_match_types(&[
            mk("user@example.com"), // email          -> bit 0
            mk("2026-07-06"),       // date_iso + phone -> bits 2|5 (co-match by construction)
            mk("hello world"),      // none            -> 0
            mk("$5"),               // currency        -> bit 7
        ]);
        assert_eq!(
            out,
            vec![1u32 << 0, (1u32 << 2) | (1u32 << 5), 0u32, 1u32 << 7]
        );
    }
```

- [ ] **Step 4: Verify by eye (NO cargo)** — confirm ordering + syntax:
```bash
grep -n "use regex::Regex\|use std::sync::OnceLock\|fn semantic_patterns\|pub fn pattern_match_types\|mod tests\|fn pattern_match_types_bits" packages/rust/extensions/infermap-core/src/lib.rs
```
Confirm: the two `use` lines are near the top; `semantic_patterns` + `pattern_match_types` appear BEFORE `mod tests`; the test fn is AFTER `mod tests`. Check the 8 pattern strings char-for-char against Task 3 Step 2 (especially currency `[$£€]` unescaped), raw-string `r"..."` on each, balanced brackets.

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/infermap-core/Cargo.toml packages/rust/extensions/infermap-core/src/lib.rs
git commit -m "feat(infermap-core): pattern_match_types regex kernel (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status + grep output + SHA. Do NOT run cargo. Do NOT push. Only touch the two infermap-core files.

---

## Task 4: The native shim (`infermap-native`) — CI-only

**Do NOT run cargo.**

**Files:**
- Modify: `packages/rust/extensions/infermap-native/src/lib.rs`

- [ ] **Step 1: Add the shim** — after the `initialism_score` shim (or `profile_score` if Wave 3 merged into base), before `#[pymodule]`:
```rust
/// Wave 4 pattern-type scorer: host pre-strips; kernel returns per-sample type bitmask.
#[pyfunction]
fn pattern_match_types(samples: Vec<String>) -> PyResult<Vec<u32>> {
    Ok(infermap_core::pattern_match_types(&samples))
}
```

- [ ] **Step 2: Register** — after the last `wrap_pyfunction!(self::...)` line inside `#[pymodule]`:
```rust
    m.add_function(wrap_pyfunction!(self::pattern_match_types, m)?)?;
```

- [ ] **Step 3: Verify by eye (NO cargo)**
```bash
grep -n "fn pattern_match_types\|wrap_pyfunction!(self::pattern_match_types\|#\[pymodule\]" packages/rust/extensions/infermap-native/src/lib.rs
```
Confirm: shim fn BEFORE `#[pymodule]`; registration AFTER it; arg `Vec<String>`, return `PyResult<Vec<u32>>` with `Ok(...)`, `self::` form. Plain return type -> no `type_complexity`; single arg -> no `too_many_arguments`.

- [ ] **Step 4: Commit**
```bash
git add packages/rust/extensions/infermap-native/src/lib.rs
git commit -m "feat(infermap-native): pattern_match_types PyO3 shim (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status + grep output + SHA. Do NOT run cargo. Do NOT push. Only touch `infermap-native/src/lib.rs`.

---

## Task 5: Fixture-drift corpus + parity tests

**Files:**
- Create: `packages/python/infermap/tests/pattern_type_corpus.jsonl`
- Modify: `packages/python/infermap/tests/test_native_parity.py`

- [ ] **Step 1: Create the corpus** `packages/python/infermap/tests/pattern_type_corpus.jsonl` (one JSON object per line; `tier` defaults to `"must"`). Write EXACTLY this content:

```jsonl
{"s": "user@example.com", "tier": "must", "note": "email canonical"}
{"s": "a@b.co", "tier": "must", "note": "email 2-char TLD floor"}
{"s": "user.name+tag@ex-ample.com", "tier": "must", "note": "email dots/plus/hyphen"}
{"s": "no-at-sign.com", "tier": "must", "note": "email negative (no @)"}
{"s": "user@nodot", "tier": "must", "note": "email negative (no TLD dot)"}
{"s": "550e8400-e29b-41d4-a716-446655440000", "tier": "must", "note": "uuid canonical"}
{"s": "550E8400-E29B-41D4-A716-446655440000", "tier": "must", "note": "uuid uppercase"}
{"s": "550e8400-e29b-41d4-a716-44665544000", "tier": "must", "note": "uuid negative (11 in last group)"}
{"s": "550e8400e29b41d4a716446655440000", "tier": "must", "note": "uuid negative (no hyphens)"}
{"s": "2026-07-06", "tier": "must", "note": "date_iso canonical"}
{"s": "2026-13-99", "tier": "must", "note": "date_iso structural match (invalid date)"}
{"s": "2026-7-6", "tier": "must", "note": "date_iso negative (single digits)"}
{"s": "26-07-06", "tier": "must", "note": "date_iso negative (2-digit year)"}
{"s": "192.168.0.1", "tier": "must", "note": "ip_v4 canonical"}
{"s": "999.999.999.999", "tier": "must", "note": "ip_v4 structural match"}
{"s": "1.2.3", "tier": "must", "note": "ip_v4 negative (3 octets)"}
{"s": "1.2.3.4.5", "tier": "must", "note": "ip_v4 negative (5 octets)"}
{"s": "http://example.com", "tier": "must", "note": "url http"}
{"s": "https://example.com/path?q=1", "tier": "must", "note": "url https with query"}
{"s": "ftp://example.com", "tier": "must", "note": "url negative (scheme)"}
{"s": "http://has space.com", "tier": "must", "note": "url negative (interior space, [^\\s])"}
{"s": "+12345678", "tier": "must", "note": "phone 7-rep floor with lead +"}
{"s": "123-456-7890", "tier": "must", "note": "phone with separators"}
{"s": "12345", "tier": "must", "note": "phone negative (too few) / zip_us positive"}
{"s": "1234567890123456", "tier": "must", "note": "phone ceiling region"}
{"s": "12345-6789", "tier": "must", "note": "zip_us +4"}
{"s": "1234", "tier": "must", "note": "zip_us negative (4 digits)"}
{"s": "123456", "tier": "must", "note": "zip_us negative (6 digits)"}
{"s": "$5", "tier": "must", "note": "currency dollar minimal"}
{"s": "$1,000.00", "tier": "must", "note": "currency dollar grouped+cents"}
{"s": "£12.50", "tier": "must", "note": "currency pound literal (U+00A3)"}
{"s": "€1,000.00", "tier": "must", "note": "currency euro literal (U+20AC)"}
{"s": "5.00", "tier": "must", "note": "currency negative (no symbol)"}
{"s": "hello world", "tier": "must", "note": "matches nothing (mask 0)"}
{"s": "٥", "tier": "informational", "note": "Arabic-Indic digit: \\d Unicode edge"}
{"s": "５", "tier": "informational", "note": "fullwidth digit: \\d Unicode edge"}
{"s": "२०२६-०७-०६", "tier": "informational", "note": "Devanagari date: \\d Unicode edge"}
{"s": "http://a\u00a0b", "tier": "informational", "note": "NBSP in url: \\s edge"}
{"s": "1234567\u001c890", "tier": "informational", "note": "interior \\x1c in phone: sharpest \\s edge"}
```

- [ ] **Step 2: Append the loader + tests to `test_native_parity.py`** (at end of file):

```python
# ---------------------------------------------------------------------------
# Wave 4: pattern_type scorer parity (regex fixture-drift gate)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import pathlib  # noqa: E402

from infermap.scorers.pattern_type import _match_types_pure  # noqa: E402

_CORPUS_PATH = pathlib.Path(__file__).parent / "pattern_type_corpus.jsonl"


def _load_corpus() -> list[dict]:
    rows: list[dict] = []
    for line in _CORPUS_PATH.read_text(encoding="utf-8").split("\n"):
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def _corpus_must() -> list[str]:
    return [r["s"] for r in _load_corpus() if r.get("tier", "must") == "must"]


def _corpus_informational() -> list[str]:
    return [r["s"] for r in _load_corpus() if r.get("tier") == "informational"]


@native_only
@pytest.mark.parametrize("s", _corpus_must())
def test_pattern_type_parity(s):
    # exact bitmask byte-equality across the ASCII must-pass contract.
    assert native_module().pattern_match_types([s]) == [_match_types_pure(s)]


@native_only
def test_pattern_type_unicode_edge_recorded():
    """Documented parity edge (\\d/\\s Unicode tables): RECORD divergence, do not gate.

    Prints an AGREE/DIVERGE line per informational fixture so CI logs pin where the
    boundary actually falls. Intentionally asserts nothing about agreement.
    """
    for s in _corpus_informational():
        native = native_module().pattern_match_types([s])[0]
        pure = _match_types_pure(s)
        verdict = "AGREE" if native == pure else "DIVERGE"
        print(f"[pattern_type edge] {verdict} native={native:#010b} "
              f"pure={pure:#010b} {s!r}")
```

- [ ] **Step 3: Verify collection + clean skip on the box**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_native_parity.py -q -k "pattern_type"
```
Expected: the parametrized `test_pattern_type_parity` cases (one per must-tier line, ~35) + `test_pattern_type_unicode_edge_recorded` — **all SKIPPED** (`native_only`, no wheel). NO collection/import/JSON errors. If a JSON line fails to parse, fix the corpus (watch the `\\s`/`\\x` escaping — inside a JSON string a literal backslash is `\\`, and ` `/`` are the unicode escapes).

- [ ] **Step 4: Sanity-check the corpus parses + oracle runs (box-runnable, pure)**

```bash
"$INTERP" -c "import json,pathlib; p=pathlib.Path('packages/python/infermap/tests/pattern_type_corpus.jsonl'); rows=[json.loads(l) for l in p.read_text(encoding='utf-8').splitlines() if l.strip()]; print(len(rows),'rows'); import sys; sys.path.insert(0,'packages/python/infermap'); from infermap.scorers.pattern_type import _match_types_pure; print('email bit', _match_types_pure('user@example.com')==1, 'none', _match_types_pure('hello world')==0)"
```
Expected: prints the row count and `email bit True none True`. This proves the corpus is valid JSON and the oracle behaves.

- [ ] **Step 5: ruff + commit**

```bash
"$INTERP" -m ruff check packages/python/infermap/tests/test_native_parity.py
git add packages/python/infermap/tests/pattern_type_corpus.jsonl packages/python/infermap/tests/test_native_parity.py
git commit -m "test(infermap): pattern_type fixture-drift corpus + parity gate (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status + pytest summary (N skipped) + corpus row count + SHA. Do NOT push. Only touch the corpus + `test_native_parity.py`.

---

## Task 6: Symbol-gate reconcile + CI path filter

**Files:**
- Modify: `.github/workflows/ci.yml`
- Verify (no edit): `scripts/check_native_symbols.py`

- [ ] **Step 1: Run the symbol gate (box-runnable)**

```bash
"$INTERP" scripts/check_native_symbols.py infermap
```
Expected: exit 0, `native-symbol reconciliation OK`, `pattern_match_types` NOT in a MISSING list (registered via `wrap_pyfunction!(self::pattern_match_types` + referenced via `native_module().pattern_match_types`). If MISSING: STOP, report BLOCKED with output. Do NOT edit the script.

- [ ] **Step 2: Add two lines to the `infermap_native` path filter in `ci.yml`**

Find the `infermap_native:` filter block (the `- 'packages/python/infermap/tests/test_native_parity.py'` line). Add immediately after it, same 14-space indentation:
```yaml
              - 'packages/python/infermap/infermap/scorers/pattern_type.py'
              - 'packages/python/infermap/tests/pattern_type_corpus.jsonl'
```
Add ONLY these two lines. Do NOT touch anything else in ci.yml.

- [ ] **Step 3: Validate YAML**

```bash
"$INTERP" -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
grep -n "scorers/pattern_type.py\|pattern_type_corpus.jsonl\|test_native_parity.py" .github/workflows/ci.yml
```
Expected: `ci.yml YAML OK`; the two new hits adjacent to the infermap `test_native_parity.py` filter line (a few lines below the `infermap_native:` filter key, NOT the top-level output declaration).

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci(infermap): trigger native lane on pattern_type + corpus (Wave 4)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44"
```

## Report status + full check_native_symbols output + YAML-OK + grep + SHA. Do NOT push. Only touch `ci.yml`.

---

## Task 7: Full regression + rebase + push + PR + arm auto-merge

**Files:** none (integration). Controller runs this task directly.

- [ ] **Step 1: Full box-runnable regression + ruff**

```bash
"$INTERP" -m pytest packages/python/infermap/tests/test_scorers_dispatch.py packages/python/infermap/tests/test_native_parity.py -q
"$INTERP" -m ruff check packages/python/infermap/infermap/scorers/pattern_type.py packages/python/infermap/infermap/_native_loader.py packages/python/infermap/tests/test_scorers_dispatch.py packages/python/infermap/tests/test_native_parity.py
```
Expected: dispatch tests PASS; native-parity tests SKIP; ruff `All checks passed!`.

- [ ] **Step 2: Rebase onto fresh origin/main** (main moves fast — Wave 3 likely merged; keep the branch current for the merge queue)

```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git fetch origin main -q
git rebase origin/main
```
If conflicts (most likely in `_native_loader.py` `_GATED_ON`/`_COMPONENT_SYMBOLS`, `test_native_parity.py`, `infermap-core/native src/lib.rs`, or `ci.yml` against Wave 3's additions): resolve by KEEPING BOTH sides' additions (Wave 3 `profile_score` + Wave 4 `pattern_match_types`). After resolving, re-validate:
```bash
"$INTERP" -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml YAML OK')"
"$INTERP" scripts/check_native_symbols.py infermap
```

- [ ] **Step 3: Confirm the PR diff is clean (three-dot)**

```bash
git diff --stat origin/main...HEAD
```
Expected: only Wave 4 files (the 2 spec/plan docs, `pattern_type.py`, `_native_loader.py`, `infermap-core` Cargo.toml + lib.rs, `infermap-native` lib.rs, corpus, `test_native_parity.py`, `test_scorers_dispatch.py`, `ci.yml`). If unrelated files appear, STOP and investigate (stale base).

- [ ] **Step 4: Push**

```bash
git push -u origin feat/infermap-core-wave4-pattern-type
```

- [ ] **Step 5: Open the PR** (REST fallback if GraphQL rate-limited)

```bash
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat(infermap): Wave 4 — pattern_type scorer Rust cutover (regex fixture-drift gated)" \
  --body "$(cat <<'EOF'
## What

Wave 4 of the InferMap Rust cutover: moves `PatternTypeScorer`'s regex classification into a pyo3-free `infermap-core::pattern_match_types` kernel (the `regex` crate) + `infermap-native` shim, dispatched via `native_module()` with a byte-identical pure-Python fallback.

Regex-only kernel: the host filters + strips samples and does all counting/scoring/reasoning; the kernel runs only the 8 compiled regexes and returns a per-sample `u32` bitmask (bit `i` = matches `SEMANTIC_TYPES[i]`).

## The risk gate

This is the risk-gated wave (regex engines differ). `test_pattern_type_parity` asserts exact per-string bitmask `==` between the native kernel and the Python `_match_types_pure` oracle across an adversarial ASCII corpus (`pattern_type_corpus.jsonl`). The `\d`/`\s` Unicode-table divergence is a **documented parity edge** — informational fixtures are recorded (not gated) by `test_pattern_type_unicode_edge_recorded`, mirroring the Wave 1/2 Unicode boundary.

Parity is safe within the ASCII contract: boolean-only usage (no capture-content/backtracking dependence), host-side strip (neutralizes `$`-newline + strip/trim divergence), and `currency`'s Rust pattern drops the non-ASCII backslash-escapes (`[$£€]`, semantically identical).

## Scope

Pure cutover — same output (score + reasoning), new backend. `alias`/`llm` scorers + the WASM/TS surface remain deferred.

Spec: `docs/superpowers/specs/2026-07-06-infermap-core-wave4-pattern-type-design.md`
Plan: `docs/superpowers/plans/2026-07-06-infermap-core-wave4-pattern-type.md`

🤖 Generated with [Claude Code](https://claude.com/claude-code)

https://claude.ai/code/session_01F2g8Snk1Akef5z3yZdtt44
EOF
)"
```

- [ ] **Step 6: Arm auto-merge and STOP**

```bash
gh pr merge <PR#> --repo benseverndev-oss/goldenmatch --squash --auto
```
Do NOT poll CI. Do NOT `--delete-branch`. Report the PR number and STOP.

---

## Verification Summary

| What | How | Where |
| --- | --- | --- |
| Oracle bitmask correct | `_match_types_pure` unit tests | Box (Task 1) |
| `_classify_with_pct` byte-identical | classify + scorer tests (email 1.0, mismatch, abstain) | Box (Task 1) |
| Loader gates symbol | `_COMPONENT_SYMBOLS`/`_GATED_ON` assertion | Box (Task 2) |
| Kernel compiles + bits correct | Rust `#[cfg(test)]` unit test | CI (Task 3) |
| **Native == pure across ASCII corpus** | `test_pattern_type_parity` exact `==` (~35 fixtures) | CI `infermap_native` lane (Task 5) |
| Unicode edge recorded, not gated | `test_pattern_type_unicode_edge_recorded` (prints, no assert) | CI (Task 5) |
| No silent fallback | `check_native_symbols.py infermap` reconciles the symbol | Box (Task 6) |
| CI triggers on pattern_type + corpus | path-filter entries + YAML valid | Box (Task 6) |
| No regressions | full infermap test surface + ruff | Box (Task 7) |
