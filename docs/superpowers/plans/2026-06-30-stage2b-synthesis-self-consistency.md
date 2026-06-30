# Stage-2-B: Synthesis Self-Consistency Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add opt-in (default-off) self-consistency to `synthesize_local` — sample the LLM N times at temperature and majority-vote the parsed answer — to recover the synthesis answers that are already in the retrieved ball (the stage-2-A SYNTHESIS lever: 18 retrieved-but-wrong on MuSiQue N=50).

**Architecture:** `OpenAIClient` gains `complete_many` (N-loop at temperature, token-tracked). `synthesize_local` samples via `complete_many` when `GOLDENGRAPH_SYNTH_SAMPLES > 1`, parses each with the existing `_extract_answer`, and votes via a small goldengraph-local normalizer (returning the most-common RAW form). Default off = byte-identical single-call behavior. Then an N=50 MuSiQue validation → ship-or-honest-null report.

**Tech Stack:** Python (stdlib), pytest, the existing `scripts/distill/modal_bench.py --corpus musique` Modal harness.

**Spec:** `docs/superpowers/specs/2026-06-30-stage2b-synthesis-self-consistency-design.md`
**Branch:** `feat/stage2b-synthesis-self-consistency` (already created off `origin/main`).

---

## Environment notes (read before starting)

- **Run tests box-safely** (the box OOMs on the full suite + on native imports). These tests are pure (no native, no LLM, no polars):
  ```bash
  cd packages/python/goldengraph
  PYTHONPATH="." GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
    -m pytest tests/test_synthesis_self_consistency.py tests/test_synthesize_format.py tests/test_budget.py -q -p no:cacheprovider
  ```
- **Do NOT run the whole pytest suite locally.** Targeted file runs only. `ruff check` + `py_compile` before each commit.
- GitHub auth for push: `GH_TOKEN=$(gh auth token --user benzsevern)`.
- Existing stub LLMs in `tests/conftest.py`: `StubLLM(response)` and `RecordingLLM(response="ANSWER")` — both implement ONLY `complete` (no `complete_many`), which is exactly what the fallback-parity test needs. The self-consistency test defines its own `_ManyStub` in the new test file.

## File structure

- **Modify:** `goldengraph/llm.py` — thread `temperature` through `_chat` (default 0; `complete()` unchanged); add `complete_many(prompt, *, n, temperature)`; document `complete_many` in the `LLMClient` Protocol as an optional capability.
- **Modify:** `goldengraph/synthesize.py` — `_synth_samples()` / `_synth_temperature()` env parsers, `_vote_answer()` helper, and the self-consistency branch in `synthesize_local`.
- **Create:** `tests/test_synthesis_self_consistency.py` — vote-logic, stub-LLM, fallback-parity, and `complete_many` tests.
- **Create (Task 4):** `docs/superpowers/reports/2026-06-30-stage2b-synthesis-self-consistency.md` — validation verdict.

---

### Task 1: `complete_many` on the LLM client

**Files:**
- Modify: `goldengraph/llm.py`
- Test: `tests/test_synthesis_self_consistency.py`

- [ ] **Step 1: Write the failing test** (create the file)

```python
# tests/test_synthesis_self_consistency.py
"""Synthesis self-consistency: sample N times + majority-vote (stage-2-B). Pure, no live LLM."""
from __future__ import annotations

from goldengraph.llm import OpenAIClient


class _FakeChat:
    """Minimal stand-in for openai's client: records every create() call's kwargs and
    returns a canned completion with a usage object (so budget accounting doesn't crash)."""
    def __init__(self):
        self.calls = []

        class _Msg:  # resp.choices[0].message.content
            content = "hello"

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        self._resp = _Resp()

    # mimic client.chat.completions.create(**kwargs)
    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return self._outer._resp

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = _FakeChat._Completions(outer)

        return _Chat()


def test_complete_many_issues_n_calls_at_temperature():
    fake = _FakeChat()
    client = OpenAIClient(model="m", client=fake)
    out = client.complete_many("p", n=3, temperature=0.7)
    assert out == ["hello", "hello", "hello"]
    assert len(fake.calls) == 3
    assert all(c["temperature"] == 0.7 for c in fake.calls)


def test_complete_unchanged_temperature_zero():
    fake = _FakeChat()
    client = OpenAIClient(model="m", client=fake)
    client.complete("p")
    assert fake.calls[-1]["temperature"] == 0
```

- [ ] **Step 2: Run, verify FAIL**

Run the box-safe command above (just this file). Expected: FAIL (`complete_many` undefined; `_FakeChat` ok).

- [ ] **Step 3: Implement** — in `goldengraph/llm.py`, thread temperature + add `complete_many`:

Change `_chat` signature + the kwargs line:
```python
    def _chat(self, prompt: str, *, json_mode: bool = False, temperature: float = 0) -> str:
        client = self._ensure_client()
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        # ... (rest unchanged: json_mode, create, usage/budget) ...
```
(`complete` / `complete_json` call `_chat` without `temperature`, so they stay `temperature=0` — unchanged.)

Add the method (after `complete_json`):
```python
    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        """N independent completions at `temperature` (for synthesis self-consistency).
        A LOOP of single calls -- Ollama's OpenAI-compatible endpoint does not reliably
        honor the `n=` param -- each token-tracked through `_chat`'s budget path."""
        return [self._chat(prompt, temperature=temperature) for _ in range(max(1, n))]
```

Document it in the Protocol (after the `complete_json` comment block):
```python
    # Optional: N independent samples at a temperature, for synthesis self-consistency.
    # Callers feature-detect with `hasattr(llm, "complete_many")` and fall back to `complete`.
    # def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]: ...
```

- [ ] **Step 4: Run, verify PASS** (2 tests).

- [ ] **Step 5: Commit**

```bash
git add goldengraph/llm.py tests/test_synthesis_self_consistency.py
git commit -m "feat(goldengraph): complete_many on OpenAIClient (N samples at temperature)"
```

---

### Task 2: vote helper + env parsers

**Files:**
- Modify: `goldengraph/synthesize.py`
- Test: `tests/test_synthesis_self_consistency.py`

- [ ] **Step 1: Write failing tests** (append)

```python
from goldengraph.synthesize import _vote_answer, _synth_samples, _synth_temperature


def test_vote_majority_returns_raw_form():
    # 'Firefox' and 'firefox.' share a normalized key -> 2 votes; raw winner keeps casing
    assert _vote_answer(["Firefox", "firefox.", "Chrome"]) == "Firefox"


def test_vote_skips_empty_and_handles_single():
    assert _vote_answer(["", "Acme", ""]) == "Acme"
    assert _vote_answer(["Solo"]) == "Solo"
    assert _vote_answer([]) == ""
    assert _vote_answer(["", "  "]) == ""


def test_vote_tie_breaks_first_seen():
    # one each -> the key seen first wins
    assert _vote_answer(["Beta", "Alpha"]) == "Beta"


def test_synth_env_parsers_defensive(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_SAMPLES", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_TEMPERATURE", raising=False)
    assert _synth_samples() == 1 and _synth_temperature() == 0.7
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "5")
    assert _synth_samples() == 5
    for bad in ("abc", "0", "-3", "1"):  # non-int / <=1 -> single call
        monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", bad)
        assert _synth_samples() == 1
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_TEMPERATURE", "xyz")
    assert _synth_temperature() == 0.7
```

- [ ] **Step 2: Run, verify FAIL** (`_vote_answer`/`_synth_*` undefined).

- [ ] **Step 3: Implement** — in `goldengraph/synthesize.py`, add near the top (after imports):

```python
import os
import string
from collections import Counter


def _synth_samples() -> int:
    """`GOLDENGRAPH_SYNTH_SAMPLES` (default 1 = single call). Non-int / <=1 -> 1 (fail-safe)."""
    try:
        n = int(os.environ.get("GOLDENGRAPH_SYNTH_SAMPLES", "1"))
    except ValueError:
        return 1
    return n if n > 1 else 1


def _synth_temperature() -> float:
    """`GOLDENGRAPH_SYNTH_TEMPERATURE` (default 0.7). Non-float -> 0.7."""
    try:
        return float(os.environ.get("GOLDENGRAPH_SYNTH_TEMPERATURE", "0.7"))
    except ValueError:
        return 0.7


def _vote_key(s: str) -> str:
    """Group-key for voting: lowercase, collapse whitespace, strip surrounding punctuation.
    goldengraph-LOCAL + minimal (cannot import the bench's metrics._normalize); its only job is
    to make 'Firefox' and 'firefox.' vote together."""
    return " ".join(s.lower().split()).strip(string.punctuation + " ")


def _vote_answer(answers: list[str]) -> str:
    """Majority vote over parsed answers. Group by `_vote_key`, pick the key with the most votes
    (tie -> first-seen key), return the FIRST raw answer carrying that key (preserves real casing).
    Empty/blank answers are skipped; no candidates -> ''."""
    cand = [a for a in answers if a and a.strip()]
    if not cand:
        return ""
    keys = [_vote_key(a) for a in cand]
    counts = Counter(keys)
    # max() is stable -> first-seen order breaks ties; iterate keys in first-seen order
    best_key = max(dict.fromkeys(keys), key=lambda k: counts[k])
    return next(a for a, k in zip(cand, keys) if k == best_key)
```

(If `os` is already imported at module level, do not duplicate; `_literals_enabled` currently imports `os` locally, so a module-level `import os` is a safe addition — remove the local import inside `_literals_enabled` only if it becomes redundant and tests still pass.)

- [ ] **Step 4: Run, verify PASS** (Task 1 tests still green).

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(goldengraph): synthesis vote helper + defensive env parsers"
```

---

### Task 3: wire self-consistency into `synthesize_local`

**Files:**
- Modify: `goldengraph/synthesize.py`
- Test: `tests/test_synthesis_self_consistency.py`

- [ ] **Step 1: Write failing tests** (append)

```python
from goldengraph.synthesize import synthesize_local

_SUB = {
    "entities": [{"entity_id": 0, "canonical_name": "Acme", "typ": "org"}],
    "edges": [],
}


class _ManyStub:
    """LLM stub with complete_many returning a CANNED list of completions (already in the
    'show hops then Answer: X' shape). Records whether complete_many vs complete was used."""
    def __init__(self, samples: list[str], single: str = "Answer: SingleFallback"):
        self._samples = samples
        self._single = single
        self.many_calls = 0
        self.single_calls = 0

    def complete(self, prompt: str) -> str:
        self.single_calls += 1
        return self._single

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        self.many_calls += 1
        return list(self._samples)


def test_self_consistency_votes_when_enabled(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")
    llm = _ManyStub(["Answer: Firefox", "Answer: Firefox", "Answer: Chrome"])
    assert synthesize_local("q?", _SUB, llm) == "Firefox"
    assert llm.many_calls == 1 and llm.single_calls == 0


def test_default_off_uses_single_complete(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_SAMPLES", raising=False)
    llm = _ManyStub(["Answer: X"])
    out = synthesize_local("q?", _SUB, llm)
    assert out == "SingleFallback"           # the single-call path
    assert llm.single_calls == 1 and llm.many_calls == 0


def test_stub_without_complete_many_falls_back(monkeypatch):
    from conftest import RecordingLLM
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")   # enabled, but stub lacks complete_many
    llm = RecordingLLM("Answer: Y")
    assert synthesize_local("q?", _SUB, llm) == "Y"
    assert len(llm.prompts) == 1                            # single call, no crash


def test_all_samples_empty_falls_back(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")
    llm = _ManyStub(["", "   ", ""], single="Answer: Recovered")
    assert synthesize_local("q?", _SUB, llm) == "Recovered"
    assert llm.many_calls == 1 and llm.single_calls == 1   # sampled, all empty -> one fallback
```

- [ ] **Step 2: Run, verify FAIL** (synthesize_local has no sampling branch yet → `test_self_consistency_votes_when_enabled` fails).

- [ ] **Step 3: Implement** — replace the body of `synthesize_local` (keep signature + docstring; the seeds/prompt build is unchanged):

```python
def synthesize_local(query, subgraph, llm, *, seed_names=None):
    # ... (unchanged) build `seeds` and select `prompt` (LITERALS variant or not) ...
    filled = prompt.format(q=query, seeds=seeds, sub=_format_subgraph(subgraph))
    n = _synth_samples()
    if n > 1 and hasattr(llm, "complete_many"):
        try:
            samples = llm.complete_many(filled, n=n, temperature=_synth_temperature())
        except Exception:
            samples = []
        voted = _vote_answer([_extract_answer(s) for s in samples])
        if voted:
            return voted
        # all samples empty/failed -> single-call fallback below
    return _extract_answer(llm.complete(filled))
```

Note: `filled` is built ONCE; the single-call fallback reuses it. The `samples=1` / no-`complete_many` / all-empty paths all reach the final `return _extract_answer(llm.complete(filled))` — byte-identical to today when off.

- [ ] **Step 4: Run, verify PASS** — the new file + the existing synthesis tests for no-regression:

```bash
PYTHONPATH="." GOLDENGRAPH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 "D:/show_case/goldenmatch/.venv/Scripts/python.exe" \
  -m pytest tests/test_synthesis_self_consistency.py tests/test_synthesize_format.py tests/test_hybrid_synthesis.py -q -p no:cacheprovider
```
Expected: all PASS (self-consistency tests + the existing synthesis tests unchanged — `synthesize_local`'s default path is byte-identical).

- [ ] **Step 5: ruff + commit**

```bash
"D:/show_case/goldenmatch/.venv/Scripts/python.exe" -m ruff check goldengraph/llm.py goldengraph/synthesize.py tests/test_synthesis_self_consistency.py
git commit -am "feat(goldengraph): opt-in synthesis self-consistency in synthesize_local"
```

---

### Task 4: N=50 MuSiQue validation → ship-or-honest-null report

**Files:**
- Create: `docs/superpowers/reports/2026-06-30-stage2b-synthesis-self-consistency.md`

A MEASUREMENT, not code. Detached Modal pattern (box OOM-reaps the local CLI ~1 min in).

- [ ] **Step 1: Push the branch**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/stage2b-synthesis-self-consistency
```

- [ ] **Step 2: Fire the validation run (SAMPLES=5)**

```bash
P="a99885f0-c5af-4ae1-9dc8-255cc60aa129"
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId "$P" --env dev --plain --silent)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId "$P" --env dev --plain --silent)
M="D:/show_case/goldenmatch/.venv/Scripts/modal.exe"
PYTHONIOENCODING=utf-8 "$M" run --detach scripts/distill/modal_bench.py \
  --engine goldengraph --eval end_to_end --corpus musique --n 50 --spawn \
  --opts $'GOLDENGRAPH_QA_MODE=auto\nGOLDENGRAPH_SYNTH_SAMPLES=5'
```
Result: `results/end_to_end_50_goldengraph-qwen2.5-7b-instruct-musique.md` on `gg-bench-cache`. Poll with a Monitor (until-loop, `volume get --force`, grep `support_recall=`). **5× synthesis calls + real prose → this is heavier than the stage-2-A baseline; the run_bench timeout is already 5400s (90 min). If it still caps, drop to N=40.**

- [ ] **Step 3: Aggregate vs the stage-2-A baseline**

```bash
grep -iE "support_recall=|musique \| 0" /tmp/s2b.md | head -2
grep -oE "(EXTRACTION|RETRIEVAL-BROKEN-CHAIN|SYNTHESIS)" /tmp/s2b.md | sort | uniq -c | sort -rn
```
Baseline to beat: `answer_match` 0.12, SYNTHESIS bucket 18.

- [ ] **Step 4: Write the verdict report** — `docs/superpowers/reports/2026-06-30-stage2b-synthesis-self-consistency.md`:
  - Run config (N, model, SAMPLES=5, temperature, date).
  - Before/after: `answer_match` (0.12 → ?), SYNTHESIS bucket (18 → ?), `support_recall`.
  - **Verdict, per the pre-committed gate:**
    - *Rises + SYNTHESIS drops* → SUCCESS: keep default-off (opt-in lever); note the win; optional 3/5/8 sample mini-sweep as a follow-up.
    - *Flat* → HONEST-NULL: 7B synthesis errors are systematic not variance; keep the code default-off; next lever = constrained-selection or retrieval. State it plainly, no tuning.
  - Confidence statement scaled to N.

- [ ] **Step 5: Commit the report**

```bash
git add docs/superpowers/reports/2026-06-30-stage2b-synthesis-self-consistency.md
git commit -m "docs(stage-2b): synthesis self-consistency validation verdict"
```

---

## Done criterion

- Tasks 1-3 merged behind green tests (new file + existing synthesis suites, no regression; default path byte-identical).
- A committed validation report with the before/after and a ship-or-honest-null verdict per the pre-committed gate.
- Open a PR; arm auto-merge once CI is green. (The code lands regardless of the verdict — it is default-off; the report records whether the lever is worth enabling.)
