# Splink -> GoldenMatch Config Converter (+ N-level Probabilistic Fields) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert Splink settings dicts / trained model JSON into a valid `GoldenMatchConfig` (+ optional imported `EMResult`), with a structured `ConversionReport`, exposed as library API, CLI command, and MCP tool; extend GoldenMatch probabilistic fields to N custom-threshold levels so the conversion is faithful.

**Architecture:** Stage 1 adds `level_thresholds` to `MatchkeyField` and generalizes the few remaining 2/3-level assumptions in `core/probabilistic.py` (the EM core is already N-level generic). Stage 2 adds `goldenmatch/config/from_splink.py`: anchored-regex recognizers over Splink's serialized `sql_condition` strings, assembling one probabilistic `MatchkeyConfig` + `BlockingConfig`, importing trained m/u into an `EMResult`.

**Tech Stack:** Pure Python (POC phase), Pydantic v2 schemas, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-13-splink-config-converter-design.md` (read it first)

**Working branch:** `feat/splink-config-converter` (worktree `..\goldenmatch-wt-splink-converter`)

**Package root for all paths below:** `packages/python/goldenmatch/`

**Test invocation (Windows box):** `..\..\..\.venv\Scripts\python.exe -m pytest <file> -v` from the package root, or the repo's `uv run pytest`. Set `GOLDENMATCH_NATIVE=0` and `POLARS_SKIP_CPU_CHECK=1` when running core tests in a worktree (see memory: worktree Py tests use main .venv + PYTHONPATH). Run targeted tests only — never the full suite locally.

Use @superpowers:test-driven-development for every task. Frequent commits (one per task minimum).

---

## Stage 1: N-level probabilistic fields

### Task 1: `level_thresholds` on MatchkeyField

**Files:**
- Modify: `goldenmatch/config/schemas.py` (MatchkeyField, ~line 86-149)
- Test: `tests/test_nlevel_schema.py` (create)

- [ ] **Step 1: Write failing tests**

```python
"""N-level MatchkeyField schema tests (Splink-converter Stage 1)."""
import pytest
from goldenmatch.config.schemas import MatchkeyField


def test_level_thresholds_accepted():
    f = MatchkeyField(field="first_name", scorer="jaro_winkler",
                      levels=4, level_thresholds=[1.0, 0.92, 0.88])
    assert f.level_thresholds == [1.0, 0.92, 0.88]


def test_level_thresholds_wrong_length_rejected():
    with pytest.raises(ValueError, match="level_thresholds"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=4, level_thresholds=[1.0, 0.9])  # needs levels-1 = 3


def test_level_thresholds_must_descend():
    with pytest.raises(ValueError, match="descending"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=3, level_thresholds=[0.8, 0.9])


def test_level_thresholds_range():
    with pytest.raises(ValueError, match="0, 1"):
        MatchkeyField(field="x", scorer="jaro_winkler",
                      levels=3, level_thresholds=[1.2, 0.9])


def test_default_none_backcompat():
    f = MatchkeyField(field="x", scorer="jaro_winkler", levels=3)
    assert f.level_thresholds is None
    assert f.partial_threshold == 0.8
```

- [ ] **Step 2: Run to verify failure** — `pytest tests/test_nlevel_schema.py -v` → FAIL (`level_thresholds` unexpected keyword / validation missing).

- [ ] **Step 3: Implement.** In `MatchkeyField`, after `partial_threshold` (line ~96) add:

```python
    # N-level custom banding (Splink-converter Stage 1). Descending similarity
    # cutoffs; level index = count of satisfied thresholds (0 = disagree,
    # levels-1 = top agree). None => legacy banding (partial_threshold for
    # 2/3 levels, even k/N spacing for N>3). Length must be levels-1.
    level_thresholds: list[float] | None = None
```

And extend `_resolve_field_column` (before `return self`):

```python
        if self.level_thresholds is not None:
            if len(self.level_thresholds) != self.levels - 1:
                raise ValueError(
                    f"level_thresholds must have levels-1={self.levels - 1} entries, "
                    f"got {len(self.level_thresholds)}."
                )
            if any(not (0.0 < t <= 1.0) for t in self.level_thresholds):
                raise ValueError("level_thresholds values must be in (0, 1].")
            if any(a <= b for a, b in zip(self.level_thresholds, self.level_thresholds[1:])):
                raise ValueError("level_thresholds must be strictly descending.")
```

- [ ] **Step 4: Run tests** → PASS. Also run the existing schema tests: `pytest tests/test_config* -v` (or the closest existing schema test file — find with `ls tests/ | grep -i config`) → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(schemas): level_thresholds for N-level probabilistic fields"`

### Task 2: Banding honors `level_thresholds` (scalar + vectorized)

**Files:**
- Modify: `goldenmatch/core/probabilistic.py` — `comparison_vector` (~line 300-344), `_levels_from_similarity` (~line 1361-1380) and its two call sites (~lines 1544, 1654)
- Test: `tests/test_nlevel_banding.py` (create)

- [ ] **Step 1: Write failing tests**

```python
"""N-level banding tests: comparison_vector + _levels_from_similarity."""
import numpy as np
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
from goldenmatch.core.probabilistic import comparison_vector, _levels_from_similarity


def _mk(**field_kw):
    return MatchkeyConfig(name="t", type="probabilistic",
                          fields=[MatchkeyField(**field_kw)])


def test_custom_thresholds_scalar():
    mk = _mk(field="name", scorer="jaro_winkler", levels=4,
             level_thresholds=[1.0, 0.92, 0.88])
    # identical strings -> similarity 1.0 -> top level 3
    assert comparison_vector({"name": "smith"}, {"name": "smith"}, mk) == [3]
    # totally different -> level 0
    assert comparison_vector({"name": "smith"}, {"name": "qqqqq"}, mk) == [0]


def test_levels_from_similarity_custom():
    sim = np.array([1.0, 0.95, 0.90, 0.5, 0.88])
    lvl = _levels_from_similarity(sim, 4, 0.8, level_thresholds=[1.0, 0.92, 0.88])
    # counts of satisfied descending thresholds
    assert lvl.tolist() == [3, 2, 1, 0, 1]


def test_levels_from_similarity_legacy_unchanged():
    sim = np.array([1.0, 0.9, 0.5])
    assert _levels_from_similarity(sim, 3, 0.8).tolist() == [2, 1, 0]
    assert _levels_from_similarity(sim, 2, 0.8).tolist() == [1, 1, 0]
```

- [ ] **Step 2: Run** → FAIL (unexpected `level_thresholds` argument; scalar path ignores custom thresholds).

- [ ] **Step 3: Implement.** `_levels_from_similarity` gains a keyword param and a custom branch (order-independent sum of satisfied thresholds):

```python
def _levels_from_similarity(
    sim: np.ndarray, levels: int, partial_threshold: float,
    level_thresholds: list[float] | None = None,
) -> np.ndarray:
    if level_thresholds is not None:
        lvl = np.zeros(sim.shape, dtype=np.intp)
        for t in level_thresholds:
            lvl += (sim >= t).astype(np.intp)
        return lvl
    ...  # existing 2 / 3 / N-even branches unchanged
```

Both call sites (~1544, ~1654) pass `level_thresholds=f.level_thresholds`. In `comparison_vector`, insert a custom branch BEFORE the `f.levels == 2` branch:

```python
        elif f.level_thresholds is not None:
            level = 0
            for t in f.level_thresholds:
                if s >= t:
                    level += 1
            levels.append(level)
```

(keep the `s is None -> 0` null branch first, unchanged).

- [ ] **Step 4: Run new + existing probabilistic tests** — `pytest tests/test_nlevel_banding.py tests/test_probabilistic*.py -v` (locate exact existing file names first) → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(probabilistic): custom level_thresholds banding, scalar + vectorized"`

### Task 3: Generalize residual 2/3-level assumptions

**Files:**
- Modify: `goldenmatch/core/probabilistic.py` — neutral-u overrides (~lines 563-566 and ~839), `_fallback_result` (~line 1180-1201)
- Test: extend `tests/test_nlevel_banding.py`

- [ ] **Step 1: Write failing test**

```python
def test_fallback_and_neutral_u_nlevel():
    from goldenmatch.core.probabilistic import _fallback_result
    mk = _mk(field="name", scorer="jaro_winkler", levels=5,
             level_thresholds=[1.0, 0.95, 0.9, 0.85])
    r = _fallback_result(mk)
    assert len(r.m_probs["name"]) == 5
    assert len(r.u_probs["name"]) == 5
    assert abs(sum(r.m_probs["name"]) - 1.0) < 1e-9


def test_fallback_2_and_3_level_literals_unchanged():
    # Back-compat guarantee from the spec: existing 2/3-level behavior untouched.
    from goldenmatch.core.probabilistic import _fallback_result
    r2 = _fallback_result(_mk(field="x", scorer="exact", levels=2))
    assert r2.m_probs["x"] == [0.1, 0.9]
    r3 = _fallback_result(_mk(field="x", scorer="jaro_winkler", levels=3))
    assert r3.m_probs["x"] == [0.05, 0.15, 0.80]
```

- [ ] **Step 2: Run** → FAIL (fallback emits 3-level lists for `levels != 2`).

- [ ] **Step 3: Implement.** BACK-COMPAT RULE: levels 2 and 3 keep their existing literals EXACTLY (the spec guarantees existing configs are untouched); the general formulas apply only for `f.levels > 3`.
  - `_fallback_result`: keep the current `levels == 2` branch (`m=[0.1, 0.9]`, `u=[0.9, 0.1]`) and add an explicit `levels == 3` branch with the current literals (`m=[0.05, 0.15, 0.80]`, `u=[0.80, 0.15, 0.05]`); for `levels > 3` use the exponential shape — `raw = [2.0 ** k for k in range(f.levels)]`; m = raw/sum, u = reversed(m); `match_weights = [math.log2(m_i/u_i)]` elementwise.
  - Neutral-u blocking override (both spots): keep `[0.50, 0.50]` for 2 levels and `[0.34, 0.33, 0.33]` for 3 levels exactly as-is; add `[1.0 / f.levels] * f.levels` only for `f.levels > 3`.
  - Also grep the whole file for remaining hardcoded level literals: `grep -n "0\.34\|f.levels == 2\|f.levels == 3" goldenmatch/core/probabilistic.py` — each hit must be either generalized or provably fine for N (document per-hit in the commit message).

- [ ] **Step 4: Run** `pytest tests/test_nlevel_banding.py tests/test_probabilistic*.py -v` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "feat(probabilistic): N-level fallback + neutral-u"`

### Task 4: Native-path guard (spec's explicit early verification)

**Files:**
- Inspect: `goldenmatch/core/probabilistic.py` ~line 1838 (native rapidfuzz-rs FS path), `goldenmatch/core/_native_loader.py`, the native scoring entry it feeds
- Test: extend `tests/test_nlevel_banding.py`

- [ ] **Step 1: Investigate.** Read the native FS scoring path around line 1838. Determine: does the NATIVE kernel assign levels (Rust decides banding), or does Python (`_levels_from_similarity`) band the native similarity outputs? Record the answer in the commit message.

- [ ] **Step 2: If Python bands** (expected, since `_levels_from_similarity` is called at ~1544/~1654 on similarity matrices): no guard needed — Task 2 already covers it. Write a test that runs a `level_thresholds` matchkey through the same code path with `GOLDENMATCH_NATIVE=0` vs unset and asserts identical comparison levels (skip the native leg with `pytest.mark.skipif` when the native module is unavailable).

- [ ] **Step 3: If Rust bands:** add a routing guard where the native path is selected — fields with `level_thresholds is not None` (or `levels > 3` if that's the native limit) fall back to the pure-Python scorer for that matchkey, mirroring the existing native-fallback pattern in `_native_loader.py` consumers. Test: `level_thresholds` matchkey produces correct levels with native enabled.

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit** — `git commit -m "test(probabilistic): native-path level banding guard/verification"`

### Task 5: N-level EM end-to-end

**Files:**
- Test: `tests/test_nlevel_em.py` (create)

- [ ] **Step 1: Write test** — synthetic ~200-row DataFrame with duplicates (REMEMBER: spread surnames across soundex codes — see memory `feedback_synthetic_surname_fixtures`), one probabilistic matchkey with a 4-level `level_thresholds` field + a 2-level exact field; call `train_em`; assert: converges (or at least returns), `len(m_probs[field]) == 4`, all prob lists sum to ~1.0, `match_weights` monotone non-decreasing across levels (the file's `_monotone` fix already enforces this — check its name at ~line 155).

- [ ] **Step 2: Run** → should PASS if Tasks 1-3 are correct (this is a verification task; if it fails, debug with @superpowers:systematic-debugging).

- [ ] **Step 3: Also run the FULL existing probabilistic + autoconfig-FS test files** (`pytest tests/test_probabilistic*.py tests/test_fs_autoconfig_v2.py -v`) → all PASS (back-compat proof).

- [ ] **Step 4: Commit** — `git commit -m "test(probabilistic): N-level EM end-to-end"`

---

## Stage 2: the converter

### Task 6: Module scaffold — report, errors, dataclasses

**Files:**
- Create: `goldenmatch/config/from_splink.py`
- Test: `tests/test_from_splink_report.py` (create)

- [ ] **Step 1: Write failing tests**

```python
from goldenmatch.config.from_splink import (
    ConversionFinding, ConversionReport, SplinkConversionError,
)


def test_report_severity_filtering():
    r = ConversionReport()
    r.info("settings.sql_dialect", "ignored (engine infra)", mapped_to=None)
    r.warn("comparisons[0].levels[2]", "unrecognized SQL, level dropped", mapped_to=None)
    assert len(r.findings) == 2
    assert r.has_warnings and not r.has_errors
    assert "warning" in r.summary().lower()


def test_error_findings():
    r = ConversionReport()
    r.error("blocking_rules", "no blocking rule could be converted", mapped_to=None)
    assert r.has_errors
```

- [ ] **Step 2: Run** → FAIL (module missing).

- [ ] **Step 3: Implement** in `goldenmatch/config/from_splink.py`:

```python
"""Splink -> GoldenMatch config converter.

Spec: docs/superpowers/specs/2026-07-13-splink-config-converter-design.md
Accepts a Splink settings dict / JSON path (bare or trained model) and
produces a validated GoldenMatchConfig + ConversionReport (+ EMResult when
the input carried trained m/u probabilities).
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Literal

Severity = Literal["info", "warning", "error"]


@dataclass
class ConversionFinding:
    severity: Severity
    splink_path: str      # where in the Splink input, e.g. "comparisons[1].comparison_levels[3]"
    message: str
    mapped_to: str | None  # GoldenMatch destination, e.g. "matchkeys[0].fields[1]"


@dataclass
class ConversionReport:
    findings: list[ConversionFinding] = dc_field(default_factory=list)

    def info(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("info", splink_path, message, mapped_to))

    def warn(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("warning", splink_path, message, mapped_to))

    def error(self, splink_path: str, message: str, mapped_to: str | None) -> None:
        self.findings.append(ConversionFinding("error", splink_path, message, mapped_to))

    @property
    def has_warnings(self) -> bool:
        return any(f.severity == "warning" for f in self.findings)

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def summary(self) -> str:
        counts = {"info": 0, "warning": 0, "error": 0}
        for f in self.findings:
            counts[f.severity] += 1
        return (f"{counts['error']} error(s), {counts['warning']} warning(s), "
                f"{counts['info']} info note(s)")


class SplinkConversionError(ValueError):
    """Raised in strict mode on any lossy mapping, or always on error-severity."""
```

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): report/finding scaffold"`

### Task 7: Level recognizers

**Files:**
- Modify: `goldenmatch/config/from_splink.py`
- Test: `tests/test_from_splink_recognizers.py` (create)

- [ ] **Step 1: Write failing table-driven tests** covering, for BOTH quoted (`"first_name_l"`) and bare (`first_name_l`) column spellings and case-insensitive function names:
  - null level: `"first_name_l" IS NULL OR "first_name_r" IS NULL` → `("null", "first_name", None)`
  - exact: `"first_name_l" = "first_name_r"` → `("exact", "first_name", 1.0)`
  - JW: `jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92` → `("jaro_winkler", "first_name", 0.92)`
  - jaro (approximation): `jaro_similarity(...) >= 0.9` → `("jaro_winkler", col, 0.9)` AND recognizer flags `approx=True`
  - levenshtein: `levenshtein("dob_l", "dob_r") <= 1` → `("levenshtein", "dob", <converted>)`, `damerau_levenshtein` same (both `approx=True`)
  - jaccard: `jaccard("email_l", "email_r") >= 0.9` → `("jaccard", "email", 0.9)`
  - ELSE → `("else", None, None)`
  - cross-column (`"first_name_l" = "surname_r" AND ...`) → `None` (unrecognized)
  - arbitrary SQL (`abs("amount_l" - "amount_r") < 5`) → `None`

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement.** One `RecognizedLevel` dataclass `{kind, column, sim_threshold, approx: bool}` + a `recognize_level(sql: str, *, is_null_level: bool) -> RecognizedLevel | None` walking an ordered list of compiled anchored regexes. Key regex building blocks:

```python
_COL = r'"?(?P<col>\w+?)"?_(?P<side>[lr])"?'   # matches  "first_name_l"  or  first_name_l
# NOTE: Splink serializes columns as "col_l"/"col_r" (suffix INSIDE the quotes).
```

Recognizers (all `re.IGNORECASE`, fullmatch on `sql.strip()` with whitespace normalized via `" ".join(sql.split())`):
  - exact: `^"?(\w+)_l"? = "?(\w+)_r"?$` and require group1 == group2
  - similarity fns: `^(jaro_winkler_similarity|jaro_similarity|jaro_winkler|jaccard)\( "?(\w+)_l"? , ... \) >= (\d*\.?\d+)$` (allow flexible spacing; require same column both args)
  - distance fns: `^(levenshtein|damerau_levenshtein)\(...\) <= (\d+)$`; convert distance n → similarity via `max(0.0, 1.0 - n / _LEV_ASSUMED_LEN)` with module constant `_LEV_ASSUMED_LEN = 10` (documented approximation; recognizer sets `approx=True`)
  - `ELSE` literal; null level trusted from `is_null_level: true` flag (fall back to the `IS NULL` regex when the flag is absent)
  - anything else → `None`

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): sql_condition level recognizers"`

### Task 8: Comparison assembly → MatchkeyField

**Files:**
- Modify: `goldenmatch/config/from_splink.py`
- Test: `tests/test_from_splink_comparisons.py` (create)

- [ ] **Step 1: Write failing tests** using a realistic serialized comparison (copy the shape from the spec / Splink docs):

```python
def _jw_comparison():
    return {
        "output_column_name": "first_name",
        "comparison_levels": [
            {"sql_condition": '"first_name_l" IS NULL OR "first_name_r" IS NULL',
             "is_null_level": True},
            {"sql_condition": '"first_name_l" = "first_name_r"'},
            {"sql_condition": 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.92'},
            {"sql_condition": 'jaro_winkler_similarity("first_name_l", "first_name_r") >= 0.88'},
            {"sql_condition": "ELSE"},
        ],
    }
```

Assert: converts to `MatchkeyField(field="first_name", scorer="jaro_winkler", levels=4, level_thresholds=[1.0, 0.92, 0.88])` (3 agree-bands + ELSE disagree = 4 levels). Also test: mixed-family comparison (exact + levenshtein + jaro levels) → exact rides along at 1.0 with the majority family; genuinely mixed families (JW level + levenshtein level) → whole comparison dropped with a warning; single unrecognized level inside an otherwise-good comparison → that level dropped with a warning, thresholds re-derived from the survivors; TF flags (`tf_adjustment_column` on a level → `tf_adjustment=True`, warning when `tf_adjustment_weight` != 1.0).

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `convert_comparison(comp: dict, idx: int, report: ConversionReport) -> MatchkeyField | None`:
  1. Recognize every level (Task 7). Null levels: skip + `report.info` ("Splink null level = no evidence; GoldenMatch scores nulls as disagree — behavior differs on sparse fields"). ELSE: contributes the implicit disagree level.
  2. Collect agree-bands (exact + one similarity family). Determine family = the unique non-exact scorer among recognized bands; exact bands become threshold 1.0 in that family (pure-exact comparison → `scorer="exact"`, `levels=2`). Two different non-exact families → drop comparison + warn.
  3. Column consistency: all recognized bands must reference the same column, else drop + warn.
  4. Sort thresholds strictly descending, dedupe; `levels = len(bands) + 1`; when `levels == 2`, emit legacy shape (`levels=2, partial_threshold=<t>`) instead of `level_thresholds` (keeps simple configs simple).
  5. `approx=True` on any band → `report.warn` with the conversion formula in the message.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): comparison -> MatchkeyField assembly"`

### Task 9: Blocking rules → BlockingConfig

**Files:**
- Modify: `goldenmatch/config/from_splink.py`
- Test: `tests/test_from_splink_blocking.py` (create)

- [ ] **Step 1: Write failing tests:**
  - `'l."postcode" = r."postcode"'` → one key, `fields=["postcode"]`, no transforms
  - `'l."surname" = r."surname" AND SUBSTR(l."dob", 1, 4) = SUBSTR(r."dob", 1, 4)'` → one key, `fields=["surname", "dob"]`, dob transform `substring:0:4` (VERIFY the repo's substring transform indexing convention first — `grep -n "substring" goldenmatch/utils/transforms.py` — Splink `SUBSTR(x,1,4)` is 1-based chars 1-4; adjust the emitted `substring:<start>:<end>` accordingly and pin it in the test)
  - two rules → `strategy="multi_pass"` with two passes; one rule → `strategy="static"`
  - `'l.amount / r.amount > 0.7'` → dropped + warning
  - all rules dropped → `report.error` and converter returns no BlockingConfig

- [ ] **Step 2: Run** → FAIL.

- [ ] **Step 3: Implement** `convert_blocking(rules: list[str | dict], report) -> BlockingConfig | None`. Note Splink 4 serializes each rule either as a plain SQL string or as a dict `{"blocking_rule": "<sql>", ...}` — handle both (`rule.get("blocking_rule", rule)` when dict). Split on top-level `AND` (parenthesis-free conjunctions only — a rule containing `OR` or parens is dropped + warned). Per conjunct, two regexes: plain equality `l."?(\w+)"? = r."?(\w+)"?` (same column both sides; note blocking rules use the `l.col` / `r.col` PREFIX style, unlike comparison levels' `col_l` suffix style) and `SUBSTR\( l\."?(\w+)"? , (\d+) , (\d+) \) = SUBSTR\( r\....` (same col, same offsets). Any unrecognized conjunct poisons its whole rule (drop + warn). Assemble per the multi-pass/static decision above (check `BlockingConfig`'s `passes` shape at `schemas.py` ~line 521-578 (`passes` ~536, multi_pass validation ~556) before writing — mirror an existing multi_pass fixture from tests if one exists: `grep -rn "multi_pass" tests/ | head`).

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): blocking rule conversion"`

### Task 10: Trained-model import + settings scalars

**Files:**
- Modify: `goldenmatch/config/from_splink.py`
- Test: `tests/test_from_splink_model_import.py` (create)

- [ ] **Step 1: Write failing tests.** Build a trained comparison (same shape as Task 8's but each non-null level carries `m_probability` / `u_probability`) plus top-level `probability_two_random_records_match: 0.0002`. Assert:
  - `EMResult.m_probs["first_name"]` has 4 entries, index N-1 = the strongest level's m, index 0 = ELSE's m (Splink lists strongest→weakest AFTER the null level; GoldenMatch level index is reversed — the ELSE level's m/u land at index 0)
  - exact copy: values equal to 1e-9 (it's a copy, not a fit)
  - `match_weights == log2(m/u)` elementwise
  - `proportion_matched == 0.0002`
  - dropped levels (unrecognized) also drop their m/u with a warning that says re-normalization occurred; surviving m/u re-normalized to sum to 1.0
  - bare settings (no `m_probability` anywhere) → `em_model is None`
  - scalars: `em_convergence` → `MatchkeyConfig.convergence_threshold`; `max_iterations` → `em_iterations`; `unique_id_column_name` → report info (mapped to `input.files[*].id_column` guidance — converter emits no InputConfig since Splink settings carry no file paths); `link_type: "link_and_dedupe"` → warning + recipe note; `sql_dialect`/`retain_*`/prefixes → info "ignored (engine infra)"

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `import_em(comparisons_converted, report) -> EMResult | None` + `convert_scalars(settings, mk_kwargs, report)`. `EMResult` import: `converged=True, iterations=0` (marker for "imported, not trained"), `tf_freqs=None`. Detection: any level in any comparison has `m_probability` → trained.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): trained m/u import + scalar mapping"`

### Task 11: `from_splink()` top-level + strict mode + exports

**Files:**
- Modify: `goldenmatch/config/from_splink.py`, `goldenmatch/_api.py`, `goldenmatch/__init__.py`
- Test: `tests/test_from_splink_api.py` (create)

- [ ] **Step 1: Write failing tests:**
  - full settings dict (2 comparisons + 2 block_on rules) → `SplinkConversion` with a Pydantic-valid `GoldenMatchConfig`: one `matchkeys[0]` probabilistic entry containing both fields, `blocking` present; `config.get_matchkeys()` works; round-trips through `GoldenMatchConfig(**conversion.config.model_dump(exclude_none=True))`
  - JSON file path input (write fixture with `tmp_path`) → same result
  - `strict=True` on input containing one unmappable level → raises `SplinkConversionError`
  - zero convertible comparisons → raises `SplinkConversionError` (error severity) even in default mode
  - `from goldenmatch import from_splink` works

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement:**

```python
@dataclass
class SplinkConversion:
    config: GoldenMatchConfig
    report: ConversionReport
    em_model: EMResult | None  # persist via em_model.save_json(path); then set
                               # config matchkey model_path to that path


def from_splink(source: dict | str | Path, *, strict: bool = False) -> SplinkConversion:
    ...
```

Flow: load (dict passthrough / `json.load` on path) → convert comparisons → blocking → scalars → EM import → assemble `GoldenMatchConfig(matchkeys=[mk], blocking=...)` → Pydantic-validate → strict check (`if strict and (report.has_warnings or report.has_errors): raise`) → default check (`if report.has_errors: raise`). Matchkey name: `"splink_import"`. Export: add lazy import in `_api.py` next to `load_config` (~line 365) and re-export in `__init__.py` (follow how `load_config` is listed — `grep -n "load_config" goldenmatch/__init__.py`).

- [ ] **Step 4: Run + also `pytest tests/test_from_splink*.py -v`** → all PASS. **Step 5: Commit** — `git commit -m "feat(from_splink): public from_splink() + strict mode + exports"`

### Task 12: CLI `import-splink`

**Files:**
- Create: `goldenmatch/cli/import_splink.py`
- Modify: `goldenmatch/cli/main.py` (import + `app.command` registration, follow the `sync_cmd` pattern at ~line 38/114)
- Test: `tests/test_cli_import_splink.py` (create; use `typer.testing.CliRunner` — check how existing CLI tests invoke, `grep -rn "CliRunner" tests/ | head -3`; do NOT assert on Rich --help text, see memory `feedback_no_scrape_rich_help_in_tests`)

- [ ] **Step 1: Write failing tests:** happy path (settings JSON in `tmp_path` → YAML written to `-o` path, exit 0, report table printed); trained model + `--model-out model.json` → model file exists, YAML's matchkey `model_path` points at it; `--strict` on lossy input → exit 1; error-severity conversion → exit 1.

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** `import_splink_cmd(input_path, output=typer.Option("goldenmatch.yaml", "--output", "-o"), model_out=typer.Option(None, "--model-out"), strict=typer.Option(False, "--strict"))`: call `from_splink`, on `SplinkConversionError` print findings + `raise typer.Exit(1)`; write YAML via `yaml.safe_dump(conversion.config.model_dump(exclude_none=True, exclude_defaults=True))`; when `model_out` and `em_model`: `em_model.save_json(model_out)` and set `model_path` before dumping; print findings as a Rich `Table` (severity / splink path / message / mapped to) + `report.summary()`. Register: `app.command("import-splink", help="Convert a Splink settings or trained-model JSON to a GoldenMatch config.")(import_splink_cmd)`.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(cli): import-splink command"`

### Task 13: MCP tool `convert_splink_config`

**Files:**
- Modify: `goldenmatch/mcp/server.py` (follow the pattern of an existing config-producing tool — read how `suggest_config` / `auto_configure` are registered before writing)
- Test: mirror the existing MCP tool test pattern (`grep -rln "suggest_config" tests/ | head -3` and copy its harness)

- [ ] **Step 1: Write failing test:** tool takes `settings_json: str` (inline JSON, consistent with the remote-MCP inline-content pattern — memory `project_mcp_inline_file_ingestion`) + optional `strict: bool`; returns YAML config text + findings list + summary. Trained models: return the `EMResult.to_dict()` inline in the response (remote server has no useful filesystem for the caller).

- [ ] **Step 2: Run** → FAIL. **Step 3: Implement** thin wrapper: parse JSON → `from_splink` → serialize. Errors return the findings with `isError`/structured error per the server's existing convention.

- [ ] **Step 4: Run** → PASS. **Step 5: Commit** — `git commit -m "feat(mcp): convert_splink_config tool"`

### Task 14: Bakeoff parity gate (success bar)

**Files:**
- Create: `scripts/bench_er_headtohead/run_converted_splink.py` (repo root `scripts/`, NOT the package)
- Reference: `scripts/bench_er_headtohead/{run_splink.py,run_goldenmatch.py,run_panel.py,README.md}`

- [ ] **Step 1: Read the harness README + `run_splink.py`** to identify: the default dataset name, how truth is loaded, and how F1 is computed (`compare_panels.py`).

- [ ] **Step 2: Implement the script:** serialize the harness's Splink settings for the chosen dataset (`SettingsCreator` → `.get_settings("duckdb").as_dict()` or the Splink-4 equivalent used in `run_splink.py`), feed to `from_splink()`, print the ConversionReport, run the converted `GoldenMatchConfig` through the same dedupe path `run_goldenmatch.py` uses, score F1 against truth, and print `splink_f1`, `converted_gm_f1`, `delta`. Exit 1 when `delta > 0.05`.

- [ ] **Step 3: Run it** (`uv run python scripts/bench_er_headtohead/run_converted_splink.py`). Expected: exit 0, delta <= 0.05. If Splink isn't installed in the env, install per the harness README (it's already a bakeoff dependency). If delta > 0.05: debug with @superpowers:systematic-debugging — likely suspects are threshold semantics (`link_threshold`) and the levenshtein distance→similarity approximation.

- [ ] **Step 4: Record the numbers** in the script's module docstring and in the PR body. **Step 5: Commit** — `git commit -m "bench: Splink-conversion parity gate (F1 delta <= 0.05)"`

### Task 15: Docs + finish

- [ ] **Step 1:** Run @superpowers:verification-before-completion — re-run every test file added by this plan plus `tests/test_probabilistic*.py`, `tests/test_fs_autoconfig_v2.py`; paste outputs.
- [ ] **Step 2:** Docs sweep per the `rollout-docs-sweep` skill (README converter section, docs-site tuning/config page for `level_thresholds`, CHANGELOG).
- [ ] **Step 3:** Push + PR per @superpowers:finishing-a-development-branch. Remember: `gh auth switch --user benzsevern` before push (memory `feedback_github_auth_switch`); arm `gh pr merge --auto --squash` and stop (memory `feedback_dont_poll_ci_arm_automerge`).
