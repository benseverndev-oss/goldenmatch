# Relation Re-Prompt Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A gated `GOLDENGRAPH_RELATION_REPROMPT=1` second pass that hands the 7B the already-extracted entity list plus the full doc and asks only for the relations among them, appending the found edges — the first relation-recall lever against the real-prose edge-miss residual.

**Architecture:** New pure module `goldengraph/relation_reprompt.py` (prompt + gate + parse, LLM injected), called from one gated seam in `ingest._prepare_doc` immediately after the extraction is built and BEFORE `_maybe_canonicalize`, so re-prompt edges get the same direction/schema canonicalization as first-pass edges. Everything downstream is untouched; the extra edges give the edge-miss entities the edges the aligner needs.

**Tech Stack:** Python 3.11, stdlib `json`/`os`, reuses `extract` helpers. pytest with a stub LLM.

**Spec:** `docs/superpowers/specs/2026-07-01-relation-reprompt-design.md`
**Branch:** `feat/relation-reprompt` (off `main`).

**Box-safe test invocation** (run yourself; subagents ruff+py_compile only on this box):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldengraph
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_relation_reprompt.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/python/goldengraph/goldengraph/relation_reprompt.py` | **Create.** `relation_reprompt_enabled`, `_parse_relationships`, `relation_reprompt`. Pure w.r.t. store; reuses `extract` helpers. |
| `packages/python/goldengraph/tests/test_relation_reprompt.py` | **Create.** 7 tests (prompt format, parse+index, defensive drops, gate/empty, wiring, raise-preserves-first-pass, canon ordering). |
| `packages/python/goldengraph/goldengraph/ingest.py` | **Modify** — import + gated seam at line ~677 (before `_maybe_canonicalize`). |
| `docs/superpowers/reports/2026-07-01-relation-reprompt-verdict.md` | **Create** in Task 3 after the Modal run. |

---

## Task 1: `relation_reprompt.py` module

**Files:**
- Create: `packages/python/goldengraph/goldengraph/relation_reprompt.py`
- Test: `packages/python/goldengraph/tests/test_relation_reprompt.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_relation_reprompt.py`:

```python
"""GOLDENGRAPH_RELATION_REPROMPT: a 2nd pass that adds relations among the given entities."""
from goldengraph.extract import Mention
from goldengraph.relation_reprompt import relation_reprompt, relation_reprompt_enabled


class _CaptureLLM:
    """Records the prompt; returns a fixed relationships JSON (rel 0->1)."""
    def __init__(self, payload='{"relationships": [{"subj": 0, "predicate": "founded_by", "obj": 1}]}'):
        self.prompt = None
        self.payload = payload

    def complete(self, prompt):
        self.prompt = prompt
        return self.payload
    # no complete_json -> _complete_extraction falls back to .complete


def _mentions():
    return [Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")]


def test_gate_enabled(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    assert relation_reprompt_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    assert relation_reprompt_enabled() is True
    for off in ("", "0", "False", "off", " no "):
        monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", off)
        assert relation_reprompt_enabled() is False, off


def test_prompt_lists_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force .complete on the stub
    llm = _CaptureLLM()
    relation_reprompt("Amazon was founded by Jeff Bezos.", _mentions(), llm)
    assert "0: Amazon (org)" in llm.prompt
    assert "1: Jeff Bezos (person)" in llm.prompt


def test_parses_and_maps_indices(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    rels = relation_reprompt("t", _mentions(), _CaptureLLM())
    assert len(rels) == 1
    assert (rels[0].subj, rels[0].predicate, rels[0].obj) == (0, "founded_by", 1)


def test_drops_out_of_range_and_self_loops(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    payload = ('{"relationships": ['
               '{"subj": 0, "predicate": "x", "obj": 5},'    # obj out of range (n=2)
               '{"subj": 1, "predicate": "y", "obj": 1},'    # self-loop
               '{"subj": 0, "predicate": "ok", "obj": 1}]}')  # valid
    rels = relation_reprompt("t", _mentions(), _CaptureLLM(payload))
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "ok", 1)]


def test_malformed_json_returns_empty(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    assert relation_reprompt("t", _mentions(), _CaptureLLM("not json")) == []


def test_empty_mentions_no_llm_call():
    class _Boom:
        def complete(self, prompt):
            raise AssertionError("must not be called on empty mentions")
    assert relation_reprompt("t", [], _Boom()) == []


def test_vocab_instruction_prepended(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")
    llm = _CaptureLLM()
    relation_reprompt("t", _mentions(), llm, relation_vocab=["founded_by", "works_at"])
    assert "founded_by, works_at" in llm.prompt        # .format(vocab=...) applied, not raw {vocab}
    assert "{vocab}" not in llm.prompt
```

- [ ] **Step 2: Run, verify fail.** Box-safe invocation. Expected: `ModuleNotFoundError: No module named 'goldengraph.relation_reprompt'`.

- [ ] **Step 3: Implement `relation_reprompt.py`:**

```python
"""Relation re-prompt (GOLDENGRAPH_RELATION_REPROMPT): a 2nd extraction pass that, given the
already-extracted entities + full doc text, asks the LLM only for the relations AMONG them.
The edge-miss diagnostic showed the real-prose residual is relation-never-extracted -- entities
correct, edges missing. Narrowing the task (entities provided; only connect them) recovers those
edges. Runs whole-doc over the unioned entity set, so it also targets chunking's cross-window
relation loss. Pure w.r.t. the store; LLM injected; gate off by default."""

from __future__ import annotations

import json
import os

from .extract import (
    _RELATION_VOCAB_INSTRUCTION,
    Mention,
    Relationship,
    _complete_extraction,
    _relation_vocab,
    _strip_fence,
)

_REPROMPT = """Given this text and a numbered list of entities found in it, list every \
relation that holds BETWEEN TWO of these entities, grounded in the text. Return STRICT JSON \
only, no prose, in exactly this shape:
{{"relationships": [{{"subj": <entity number>, "predicate": "<verb phrase>", "obj": <entity number>}}]}}
`subj` and `obj` are numbers from the entity list. Use only relations stated or clearly implied \
by the text. Omit an entity if it has no relation.
Entities:
{entities}
Text:
{text}"""


def relation_reprompt_enabled() -> bool:
    """`GOLDENGRAPH_RELATION_REPROMPT` gate. Off by default; case-insensitive, stripped:
    ""/"0"/"false"/"no"/"off" -> off."""
    return os.environ.get("GOLDENGRAPH_RELATION_REPROMPT", "0").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def _parse_relationships(raw: str, n: int) -> list[Relationship]:
    """Parse a `{"relationships": [...]}` blob into Relationships indexed into a mentions list of
    length `n`. Drops non-int / out-of-range endpoints and self-loops (defensive, like
    parse_extraction). Malformed top-level JSON -> []."""
    data = json.loads(_strip_fence(raw))
    out: list[Relationship] = []
    for r in data.get("relationships", []):
        s, o = r.get("subj"), r.get("obj")
        if isinstance(s, int) and isinstance(o, int) and 0 <= s < n and 0 <= o < n and s != o:
            out.append(Relationship(subj=s, predicate=str(r.get("predicate", "")), obj=o))
    return out


def relation_reprompt(text: str, mentions: list[Mention], llm, *, relation_vocab=None) -> list[Relationship]:
    """Second pass: ask `llm` for the relations among the already-extracted `mentions`, grounded in
    `text`. Returns new Relationships indexed into `mentions` (unchanged). Empty mentions -> [] (no
    LLM call). Any LLM/parse error -> [] (fail-soft; the caller keeps its first-pass extraction)."""
    if not mentions:
        return []
    try:
        entity_lines = "\n".join(f"{i}: {m.name} ({m.typ})" for i, m in enumerate(mentions))
        prompt = _REPROMPT.format(entities=entity_lines, text=text)
        vocab = _relation_vocab(relation_vocab)
        if vocab:
            prompt = _RELATION_VOCAB_INSTRUCTION.format(vocab=", ".join(vocab)) + prompt
        return _parse_relationships(_complete_extraction(llm, prompt), len(mentions))
    except Exception:
        return []
```

- [ ] **Step 4: Run tests, verify pass** (box-safe). Expected: 7 passed. Then `ruff check goldengraph/relation_reprompt.py`.

> Note: if ruff flags `Mention` as import-unused, keep it — it is used in the `relation_reprompt` type hint (`list[Mention]`), which ruff sees under `from __future__ import annotations`. If it genuinely complains, the hint is load-bearing for readability; a `# noqa: F401` is acceptable, but first confirm it is actually flagged.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/relation_reprompt.py tests/test_relation_reprompt.py
git commit -m "feat(goldengraph): relation re-prompt 2nd pass (GOLDENGRAPH_RELATION_REPROMPT)"
```

---

## Task 2: wire the gated seam into `_prepare_doc`

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/ingest.py` (import near line 19-24; seam after line 677, before line 680)
- Test: `packages/python/goldengraph/tests/test_relation_reprompt.py`

- [ ] **Step 1: Add the failing wiring tests.** Append to `tests/test_relation_reprompt.py`:

```python
def _identity_resolver():
    from goldengraph.resolve import ResolvedEntity

    def resolver(mentions):
        return [
            ResolvedEntity(local_id=i, canonical_name=m.name, typ=m.typ,
                           surface_names=[m.name], record_keys=[], member_idx=[i])
            for i, m in enumerate(mentions)
        ]
    return resolver


def test_prepare_doc_appends_reprompt_edges_only_when_gated(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention
    ingest = importlib.import_module("goldengraph.ingest")  # __init__ shadows the submodule name

    calls = {"n": 0}

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="Amazon", typ="org"),
                                    Mention(name="Jeff Bezos", typ="person")],
                          relationships=[])

    def fake_reprompt(text, mentions, llm, *, relation_vocab=None):
        calls["n"] += 1
        from goldengraph.extract import Relationship
        return [Relationship(subj=0, predicate="founded_by", obj=1)]

    monkeypatch.setattr(ingest, "relation_reprompt", fake_reprompt)
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    resolver = _identity_resolver()

    # gate OFF -> no reprompt, no edges
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    ex, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                   extractor=base_extractor)
    assert calls["n"] == 0 and len(ex.relationships) == 0

    # gate ON -> reprompt called once, edge appended
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    ex2, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                    extractor=base_extractor)
    assert calls["n"] == 1 and len(ex2.relationships) == 1


def test_prepare_doc_reprompt_raise_preserves_first_pass(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention, Relationship
    ingest = importlib.import_module("goldengraph.ingest")

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="A", typ="org"), Mention(name="B", typ="org")],
                          relationships=[Relationship(subj=0, predicate="rel", obj=1)])

    def boom(text, mentions, llm, *, relation_vocab=None):
        raise RuntimeError("reprompt exploded")

    monkeypatch.setattr(ingest, "relation_reprompt", boom)
    monkeypatch.setenv("GOLDENGRAPH_RELATION_REPROMPT", "1")
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    ex, ents, _ = ingest._prepare_doc("t", llm=None, resolver=_identity_resolver(),
                                      profile_fps=False, extractor=base_extractor)
    # first-pass entities + edge survive; NOT the empty-extraction fallback
    assert len(ex.mentions) == 2 and len(ex.relationships) == 1 and len(ents) == 2
```

- [ ] **Step 2: Run, verify fail.** Expected: `AttributeError` (no `ingest.relation_reprompt` to monkeypatch) or the gate-ON assertion failing.

- [ ] **Step 3: Wire the seam.** In `ingest.py`, add the import beside the chunk_extract import (after line ~21 `from .chunk_extract import chunk_extract, chunk_extract_enabled`):

```python
from .relation_reprompt import relation_reprompt, relation_reprompt_enabled
```

Then insert the gated append immediately after the extraction assignment (after line 677, before the `# In discovery mode...` comment at line 678):

```python
        if relation_reprompt_enabled():
            try:
                extraction.relationships += relation_reprompt(text, extraction.mentions, llm)
            except Exception:
                pass  # a 2nd-pass failure must never discard the first-pass extraction
```

- [ ] **Step 4: Run tests, verify pass.** Full `tests/test_relation_reprompt.py` (box-safe). Expected: 9 passed. Then a regression sanity on the neighbouring gates:

```bash
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_relation_reprompt.py tests/test_chunk_extract.py -q -p no:cacheprovider
```
Then `ruff check goldengraph/ingest.py goldengraph/relation_reprompt.py`.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/ingest.py tests/test_relation_reprompt.py
git commit -m "feat(goldengraph): gate relation re-prompt into _prepare_doc (before canonicalization, fail-soft)"
```

---

## Task 3: Modal wiki measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-01-relation-reprompt-verdict.md`.

Run yourself (Modal, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, `--detach --spawn`, distinct `--n`).

- [ ] **Step 1: Fire control + re-prompt legs** (wiki, best config = name_ci + chunking (6,2)):

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BEST=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2'
# control (re-prompt off) -- re-confirm the ~0.49 baseline on this build
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 100 --opts "$BEST" --spawn
# re-prompt on
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 101 \
  --opts "$BEST"$'\nGOLDENGRAPH_RELATION_REPROMPT=1' --spawn
```
Poll `gg-bench-cache` for `results/substrate_10{0,1}_*.md`; read the `[substrate-wiki]` line (coverage / R(B) / P(B) / components).

> **Optional edge_miss readout:** if PR #1353 (the `--gliner-probe` tooling) has merged to `main` by now, rebase this branch onto main and add `\nGOLDENGRAPH_GLINER_PROBE=1` to the re-prompt leg to read `edge_miss` directly (should drop from 33). If not merged, skip it — `run_wiki` coverage is the verdict signal.

- [ ] **Step 2: Read both legs.** Tabulate coverage / R(B) / P(B) / components, control vs re-prompt.

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-01-relation-reprompt-verdict.md`:
  - **WIN:** coverage/R(B) up, P(B) holds ~1.0, components not materially worse (and edge_miss down if measured).
  - **REFUTED:** coverage flat (density still dominates — the hypothesis under test fails) → next lever is per-window re-prompt or REBEL fusion.
  - **OVER-CONNECTION (watch):** coverage up but P(B) drops / components collapse → the re-prompt invented spurious edges; report it as a partial/negative, not a win.

- [ ] **Step 4: Commit** the report.

```bash
git add docs/superpowers/reports/2026-07-01-relation-reprompt-verdict.md
git commit -m "docs(goldengraph): relation re-prompt verdict (wiki)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run the box-safe `tests/test_relation_reprompt.py` suite, open a PR (base `main`), arm auto-merge. The PR ships the gate default-off regardless of the verdict (an opt-in relation-recall knob). If REFUTED, REBEL fusion (`extract_local.rebel_extractor`) is the next relation-recall lever.
