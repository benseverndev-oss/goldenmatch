# Recall-Tuned Extraction Prompt — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A gated `GOLDENGRAPH_EXTRACT_RECALL=1` recall-tuned extract prompt, measured on the wiki corpus via `coverage` — the first real-prose extraction-recall lever (and a diagnostic: framing vs density).

**Spec:** `docs/superpowers/specs/2026-07-01-extract-recall-prompt-design.md`
**Branch:** `feat/extract-recall-prompt` (off main; rebase onto main-with-#1345 before the Modal run for the aliased aligner).

**Box-safe test invocation (goldengraph pkg, worktree shadow):**
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldengraph
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
```

---

## Task 1: `_RECALL_INSTRUCTION` + gate

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/extract.py`
- Test: `packages/python/goldengraph/tests/test_extract_recall.py`

- [ ] **Step 1: Write the failing test** (mirror `test_entity_type_constraint.py`):

```python
"""GOLDENGRAPH_EXTRACT_RECALL prepends an exhaustive-entity instruction to the extract prompt."""
from goldengraph.extract import extract


class _CaptureLLM:
    def __init__(self):
        self.prompt = None

    def complete(self, prompt):
        self.prompt = prompt
        return '{"entities": [], "relationships": []}'
    # no complete_json -> extract() falls back to .complete


def test_recall_instruction_absent_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_EXTRACT_RECALL", raising=False)
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "Extract EVERY named entity" not in llm.prompt


def test_recall_instruction_present_when_gated(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_RECALL", "1")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force .complete for the stub
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "Extract EVERY named entity" in llm.prompt
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement in `extract.py`** — add the constant near `_ENTITY_TYPE_VOCAB_INSTRUCTION`:

```python
#: Prepended when GOLDENGRAPH_EXTRACT_RECALL is on. The default prompt is relation-centric (entities exist
#: to be indexed by relationships), so a 7B drops entities mentioned without a clear relation -- the ~0.44
#: real-prose extraction-recall ceiling (L2). This instruction pushes exhaustive named-entity extraction.
_RECALL_INSTRUCTION = (
    "Extract EVERY named entity mentioned -- people, organizations, places, products, works -- and list "
    "it in `entities` even if it does not participate in any relationship. Do not omit an entity just "
    "because it lacks a clear relation.\n\n"
)


def extract_recall_enabled() -> bool:
    """`GOLDENGRAPH_EXTRACT_RECALL` gate: push exhaustive named-entity extraction (recall over precision)."""
    return os.environ.get("GOLDENGRAPH_EXTRACT_RECALL", "0") not in ("0", "false", "")
```
In `extract()`, after the entity-type-vocab prepend (before `return parse_extraction(...)`):
```python
    if extract_recall_enabled():
        prompt = _RECALL_INSTRUCTION + prompt
```

- [ ] **Step 4: Run tests, verify pass** + `ruff check extract.py`.
- [ ] **Step 5: Commit** — `feat(goldengraph): gated recall-tuned extraction prompt (GOLDENGRAPH_EXTRACT_RECALL)`.

---

## Task 2: Modal wiki measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-01-extract-recall-prompt-verdict.md`.

- [ ] **Step 0: Rebase onto main once #1345 merges** (so the wiki eval uses the aliased aligner):
```bash
git fetch origin && git rebase origin/main   # or --onto if stacked
```

- [ ] **Step 1: Fire two legs** (wiki, `name_ci`, baseline vs recall; distinct `--n`; `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`):
```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 70 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn                     # control (no recall)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 71 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_EXTRACT_RECALL=1' --spawn   # recall
```
Monitor `results/substrate_7{0,1}_*.md` for `[substrate-wiki]`.

- [ ] **Step 2: Read** `coverage` (0.44 →?), `R(B)`, `P(B)`, `components` for both. The control leg re-confirms the ~0.44 baseline on the current build.

- [ ] **Step 3: Write the verdict** — coverage delta + the P(B)/components guardrails, and the diagnostic read: framing (ship) / density (→ chunking) / over-extraction tradeoff.

- [ ] **Step 4: Commit** the report.

---

## Completion

Use superpowers:finishing-a-development-branch: box-safe tests, PR (base `main`), arm auto-merge. If refuted (flat coverage), the chunking lever is the next sub-project.
