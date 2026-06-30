# Stage-2-A: MuSiQue Lever-Ranking Instrument — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add narrow, deterministic answer-string canonicalization (dates, times, standalone number-words) to the bench metrics so `answer_match` is format-fair, then run an N≈50 MuSiQue eval and commit a verdict report ranking the three real failure levers (extraction-recall / retrieval / synthesis).

**Architecture:** One new pure function `_canonicalize_spans(s)` in `metrics.py`, called at the top of the existing `_normalize` so every metric AND the localize buckets (which derive from `answer_match`) get fairer matching from one change point. Then a measurement run + a committed report. No graph/retrieval/synthesis feature work.

**Tech Stack:** Python (pure stdlib `re`), pytest, the existing `scripts/distill/modal_bench.py --corpus musique` Modal harness.

**Spec:** `docs/superpowers/specs/2026-06-29-stage2a-musique-lever-ranking-design.md`

**Branch:** `feat/stage2a-musique-lever-ranking` (already created off `origin/main`).

---

## Environment notes (read before starting)

- **Run tests box-safely** (the box OOMs on the full suite). Use the main venv with the bench dir on the path:
  ```bash
  cd packages/python/goldenmatch/benchmarks/er-kg-bench
  PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_qa_answer_normalization.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole pytest suite locally.** Targeted file runs only.
- `ruff check` via the same venv before each commit.
- GitHub auth for any push: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- All code goes in `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/metrics.py` and a new test file `tests/test_qa_answer_normalization.py` under the same bench package.

## Spec-deviation to note (decided here, in the plan)

The spec lists `100 ≡ one hundred` as a positive case, but its own scope says "no compound parsing". `one hundred` is a compound (`one`+`hundred`). **Resolution: support STANDALONE cardinals only.** `hundred`≡`100`, `twenty`≡`20`, `one`≡`1` all canonicalize. Compound handling is split by separator, and the regex is **hyphen/word-guarded** (lookarounds, not `\b`) so the two cases are consistent and intentional:
- **Hyphenated** compounds (`twenty-one`) **fall through untouched** — the guard refuses to match a cardinal adjacent to `-` or another word char.
- **Whitespace-separated** compounds (`one hundred`) split into two tokens (`1 100`). This does NOT equal `100`, so it simply does not match — acceptable, because we never *claim* a compound match and distinct values stay distinct. We do not assert `one hundred` as either a match or a fall-through.

The positive number-word tests use standalone words only.

## File structure

- **Modify:** `erkgbench/qa_e2e/metrics.py` — add `_MONTH_NUM`, `_NUMWORD`, span regexes, `_canonicalize_spans(s)`; call it as the first line of `_normalize`.
- **Create:** `tests/test_qa_answer_normalization.py` — all unit tests (canonicalization + coupling + no-regression).
- **Create:** `docs/superpowers/reports/2026-06-29-stage2a-musique-lever-ranking.md` — the verdict report (Task 5).

---

### Task 1: Date canonicalization

**Files:**
- Modify: `erkgbench/qa_e2e/metrics.py`
- Test: `tests/test_qa_answer_normalization.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_qa_answer_normalization.py
"""Fair-metric answer canonicalization: dates/times/standalone-number-words compare equal across
formats, WITHOUT making distinct answers collide. Pure, no LLM. Spec: 2026-06-29-stage2a-...-design.md"""
from __future__ import annotations

from erkgbench.qa_e2e import metrics
from erkgbench.qa_e2e.metrics import answer_match


def test_date_formats_canonicalize_equal():
    # the three date phrasings all normalize to the same ISO token sequence
    for a, b in [
        ("11 February 1929", "February 11, 1929"),
        ("11 February 1929", "1929-02-11"),
        ("February 11, 1929", "1929-02-11"),
    ]:
        assert metrics._normalize(a) == metrics._normalize(b), (a, b)


def test_date_distinct_years_still_differ():
    assert metrics._normalize("1928") != metrics._normalize("11 February 1929")
    # same month/day, different year must NOT collide
    assert metrics._normalize("11 February 1928") != metrics._normalize("11 February 1929")


def test_bare_year_not_forced_to_match_full_date():
    # gold = full date, pred mentions only the year -> incomplete, must NOT match (containment)
    assert answer_match("the year was 1929", "11 February 1929") == 0.0
```

- [ ] **Step 2: Run, verify FAIL**

Run: `... -m pytest tests/test_qa_answer_normalization.py -q`
Expected: FAIL (`_normalize` not yet canonicalizing dates → format strings differ).

- [ ] **Step 3: Implement date canonicalization**

In `metrics.py`, after the `_MONTHS` definition (reuse it), add:

```python
_MONTH_NUM = {m: i for i, m in enumerate(
    "january february march april may june july august september october november december".split(),
    start=1,
)}

# Non-anchored date spans (input is already lowercased by `_normalize`). Each ->
# ISO `YYYY-MM-DD`; the later punctuation-strip collapses the dashes so all formats
# converge. A BARE year is deliberately NOT matched here (left as the 4-digit token),
# so it never collides with a full date.
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})\s+({_MONTHS})\s+(\d{{3,4}})\b")          # 11 february 1929
_DATE_MDY = re.compile(rf"\b({_MONTHS})\s+(\d{{1,2}}),?\s+(\d{{3,4}})\b")        # february 11, 1929
_DATE_ISO = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")                       # 1929-02-11
_DATE_SLASH = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b")                   # 02/11/1929 (M/D/Y)


def _iso(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _canon_dates(s: str) -> str:
    s = _DATE_DMY.sub(lambda m: _iso(int(m.group(3)), _MONTH_NUM[m.group(2)], int(m.group(1))), s)
    s = _DATE_MDY.sub(lambda m: _iso(int(m.group(3)), _MONTH_NUM[m.group(1)], int(m.group(2))), s)
    s = _DATE_ISO.sub(lambda m: _iso(int(m.group(1)), int(m.group(2)), int(m.group(3))), s)
    s = _DATE_SLASH.sub(
        lambda m: _iso(int(m.group(3)) + (1900 if int(m.group(3)) < 100 else 0),
                       int(m.group(1)), int(m.group(2))), s)
    return s


def _canonicalize_spans(s: str) -> str:
    """Canonicalize date/time/standalone-number-word spans in a LOWERCASED string so equivalent
    answers compare equal after `_normalize`. Narrow + fail-soft: only the recognized span types are
    touched; everything else (and anything out of scope) passes through unchanged."""
    return _canon_dates(s)
```

Then wire it into `_normalize` (change only the first lines):

```python
def _normalize(s: str) -> str:
    s = _canonicalize_spans(s.lower())   # NEW: canonicalize while punctuation/structure intact
    s = s.translate(_PUNCT)
    s = _ARTICLES.sub(" ", s)
    return " ".join(s.split())
```

(Note: `_normalize` previously called `s.lower()` itself; the lowercase now happens in the `_canonicalize_spans(s.lower())` call, so the body no longer needs a separate `.lower()`.)

- [ ] **Step 4: Run, verify PASS**

Run: `... -m pytest tests/test_qa_answer_normalization.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add erkgbench/qa_e2e/metrics.py tests/test_qa_answer_normalization.py
git commit -m "feat(er-kg-bench): date canonicalization in answer normalization (ISO)"
```

---

### Task 2: Time canonicalization

**Files:**
- Modify: `erkgbench/qa_e2e/metrics.py`
- Test: `tests/test_qa_answer_normalization.py`

- [ ] **Step 1: Write failing tests** (append)

```python
def test_time_formats_canonicalize_equal():
    for a, b in [("5am", "5 a.m."), ("5am", "5 AM"), ("5pm", "5 p.m.")]:
        assert metrics._normalize(a) == metrics._normalize(b), (a, b)


def test_time_am_pm_distinct():
    assert metrics._normalize("5am") != metrics._normalize("5pm")
```

- [ ] **Step 2: Run, verify FAIL** (`5 a.m.` -> punct-strip -> `5 am` two tokens; `5am` one token).

- [ ] **Step 3: Implement** — add the time regex + extend `_canonicalize_spans`:

```python
# 5am / 5 am / 5 a.m. / 5 AM -> "5am"; 5pm / 5 p.m. -> "5pm". hour (+ optional :minute) only.
_TIME_RE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\.?\b")


def _canon_times(s: str) -> str:
    def repl(m):
        hh, mm, ap = m.group(1), m.group(2), m.group(3)
        return f"{int(hh)}{(':' + mm) if mm else ''}{ap}m"
    return _TIME_RE.sub(repl, s)
```

Extend `_canonicalize_spans` to `return _canon_times(_canon_dates(s))`.

- [ ] **Step 4: Run, verify PASS** (Task 1 tests still green too).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(er-kg-bench): time canonicalization (5 a.m. == 5am)"
```

---

### Task 3: Standalone number-word canonicalization

**Files:**
- Modify: `erkgbench/qa_e2e/metrics.py`
- Test: `tests/test_qa_answer_normalization.py`

- [ ] **Step 1: Write failing tests** (append)

```python
def test_standalone_number_words_canonicalize():
    assert metrics._normalize("hundred") == metrics._normalize("100")
    assert metrics._normalize("twenty") == metrics._normalize("20")
    assert metrics._normalize("one") == metrics._normalize("1")


def test_number_word_distinct_values_differ():
    assert metrics._normalize("hundred") != metrics._normalize("1000")
    assert metrics._normalize("twenty") != metrics._normalize("twelve")


def test_out_of_scope_number_words_fall_through():
    # hyphenated compound / decimal+magnitude / ordinal are NOT parsed (left as the old normalization).
    # NB: `one hundred` is deliberately NOT here -- whitespace compounds split to `1 100` by design
    # (see the spec-deviation note); we assert neither a match nor a fall-through for it.
    for w in ["twenty-one", "1.5 million", "third"]:
        assert metrics._normalize(w) == _legacy(w)
```

> `_legacy` is a tiny test-only helper (top of the test file, NOT in `metrics.py`) that applies ONLY
> the old normalization (lower/punct/articles/whitespace, no span-canon), so the fall-through assertion
> is precise:
> ```python
> import re as _re, string as _string
> _OLD_PUNCT = str.maketrans("", "", _string.punctuation)
> _OLD_ART = _re.compile(r"\b(a|an|the)\b")
> def _legacy(s):
>     s = s.lower().translate(_OLD_PUNCT); s = _OLD_ART.sub(" ", s); return " ".join(s.split())
> ```
> Trace for `twenty-one` under the guarded regex (Step 3): the lookarounds refuse `twenty` (followed
> by `-`) and `one` (preceded by `-`), so canon is a no-op → `_normalize` = `twentyone` = `_legacy`. ✓

- [ ] **Step 2: Run, verify FAIL**.

- [ ] **Step 3: Implement** — fixed standalone lookup + word-boundary replace:

```python
_NUMWORD = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4", "five": "5",
    "six": "6", "seven": "7", "eight": "8", "nine": "9", "ten": "10", "eleven": "11",
    "twelve": "12", "thirteen": "13", "fourteen": "14", "fifteen": "15", "sixteen": "16",
    "seventeen": "17", "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
    "ninety": "90", "hundred": "100",
}
# Hyphen/word-GUARDED (lookarounds, not \b): a cardinal adjacent to `-` or another word char is NOT
# matched, so hyphenated compounds ("twenty-one") fall through untouched. Whitespace-separated
# compounds ("one hundred") still split to "1 100" -- non-matching by design (see spec-deviation note).
_NUMWORD_RE = re.compile(r"(?<![\w-])(" + "|".join(_NUMWORD) + r")(?![\w-])")


def _canon_numwords(s: str) -> str:
    # Replace ONLY standalone cardinal words (guarded). "one hundred" -> "1 100" (won't equal "100",
    # so it simply doesn't match); "twenty-one" -> untouched. Distinct values stay distinct.
    return _NUMWORD_RE.sub(lambda m: _NUMWORD[m.group(1)], s)
```

Extend `_canonicalize_spans` to `return _canon_numwords(_canon_times(_canon_dates(s)))`.

> **Order matters:** dates first (consume month-name spans), then times, then number-words (so a
> bare "one" inside an already-canonicalized date can't be re-touched — dates are now pure digits).

- [ ] **Step 4: Run, verify PASS** (all prior tests green).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(er-kg-bench): standalone number-word canonicalization (fixed lookup)"
```

---

### Task 4: Bucket-coupling proof + no-regression

**Files:**
- Test: `tests/test_qa_answer_normalization.py`
- Verify: existing `tests/test_qa_metrics.py` still green.

- [ ] **Step 1: Write the coupling + regression tests** (append)

```python
def test_answer_match_date_crossformat_containment():
    # harness.py:153/155 compute in_graph/in_ball as answer_match(" ".join(names), gold).
    # A graph node holding the date in a DIFFERENT format must now read as a hit.
    graph_names = "foo bar February 11, 1929 baz"
    assert answer_match(graph_names, "11 February 1929") == 1.0


def test_entity_answers_unchanged_no_regression():
    # engineered-style entity answers contain no date/time/number-word spans -> canon is a no-op
    for name in ["Exeter College", "the Politburo", "Sega Genesis", "Lana Wood"]:
        assert metrics._normalize(name) == _legacy(name)


def test_answer_match_entity_still_works():
    assert answer_match("the final entity is Acme Corp", "Acme Corp") == 1.0
    assert answer_match("Acme Corp", "Globex") == 0.0
```

- [ ] **Step 2: Run the new file, verify PASS.**

- [ ] **Step 3: Run the EXISTING metrics suite for no-regression**

Run: `... -m pytest tests/test_qa_metrics.py tests/test_qa_aggregation_metric.py -q -p no:cacheprovider`
Expected: PASS. If any test asserts a `_normalize`/`answer_match` value on a date/number that the
canonicalization now changes, inspect it: a *correct* fairness change may require updating that
assertion (do so, noting why in the commit); a *wrong* collision means the regex is too greedy — fix
the regex, not the test.

- [ ] **Step 4: ruff**

Run: `... -m ruff check erkgbench/qa_e2e/metrics.py tests/test_qa_answer_normalization.py`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git commit -am "test(er-kg-bench): bucket-coupling + no-regression for answer canonicalization"
```

---

### Task 5: N≈50 MuSiQue ranking run + verdict report

**Files:**
- Create: `docs/superpowers/reports/2026-06-29-stage2a-musique-lever-ranking.md`

This task is a MEASUREMENT, not code. Use the established detached Modal pattern (the box OOM-reaps the local CLI ~1 min in).

- [ ] **Step 1: Push the branch so the run uses committed code** (Modal uploads the local tree, but push first so the metric change is on the PR)

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/stage2a-musique-lever-ranking
```

- [ ] **Step 2: Fire the N=50 MuSiQue run (detached + spawn)**

```bash
P="a99885f0-c5af-4ae1-9dc8-255cc60aa129"
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId "$P" --env dev --plain --silent)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId "$P" --env dev --plain --silent)
M="D:/show_case/goldenmatch/.venv/Scripts/modal.exe"
PYTHONIOENCODING=utf-8 "$M" run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --corpus musique --n 50 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto'
```

Result lands at `results/end_to_end_50_goldengraph-qwen2.5-7b-instruct-musique.md` on the `gg-bench-cache` volume. Poll with a Monitor (until-loop, `volume get --force`, grep for `support_recall=`). **N=50 of real prose may approach the ~60-min cap; if it times out, re-run at N=40.**

- [ ] **Step 3: Aggregate the buckets from the trace**

```bash
# pull the result, count buckets
grep -oE "(EXTRACTION|RETRIEVAL-BROKEN-CHAIN|SYNTHESIS)" /tmp/musique_n50.md | sort | uniq -c | sort -rn
grep -iE "support_recall=|musique \| 0" /tmp/musique_n50.md | head -2
```

- [ ] **Step 4: Write the verdict report**

Create `docs/superpowers/reports/2026-06-29-stage2a-musique-lever-ranking.md` with:
- The run config (N, model, corpus, opts, date) and the headline `answer_match` (fair-metric) + `support_recall`.
- **A table of ACTUAL per-bucket counts** (raw N per bucket — do not assume an even split).
- The **ranked recommendation**: which lever dominates → the target of the next sub-project.
- An explicit **confidence statement** scaled to the per-bucket N (e.g. "EXTRACTION leads at X/50; the retrieval/synthesis split is within sampling noise at this N").
- A one-line pointer to the spec.

- [ ] **Step 5: Commit the report**

```bash
git add docs/superpowers/reports/2026-06-29-stage2a-musique-lever-ranking.md
git commit -m "docs(stage-2a): MuSiQue N=50 lever-ranking verdict report"
```

---

## Done criterion

- The 4 code tasks merged behind green tests (new file + no-regression on existing metrics suite).
- A committed verdict report with actual per-bucket counts + a ranked, confidence-qualified recommendation for the next stage-2 sub-project.
- Open a PR for the branch; arm auto-merge once CI is green.
