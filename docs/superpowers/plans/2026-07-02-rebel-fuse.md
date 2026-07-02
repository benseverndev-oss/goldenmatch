# REBEL Fusion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A gated `GOLDENGRAPH_REBEL_FUSE=1` pass that runs REBEL per sentence-window, maps each `(head, rel, tail)` triple's endpoints onto the already-extracted entities, and appends an edge only when both map — measured as the marginal delta on top of the shipped relation re-prompt.

**Architecture:** New pure module `goldengraph/rebel_fuse.py` (gate + windowing + surface→entity mapping, injectable REBEL) called from one gated seam in `ingest._prepare_doc` right after the re-prompt block and BEFORE `_maybe_canonicalize`. The real REBEL model loads once via a lock-guarded cached singleton; tests always inject a fake, so no model/network in the box.

**Tech Stack:** Python 3.11, stdlib `os`/`threading`; reuses `chunk_extract` windowing + `extract`/`extract_local` helpers; transformers/torch only inside `_load_rebel` (Modal image already has them via the gliner dep). pytest with an injected fake triple-extractor.

**Spec:** `docs/superpowers/specs/2026-07-02-rebel-fuse-design.md`
**Branch:** `feat/rebel-fuse` (off `main`, which has chunking + re-prompt).

**Box-safe test invocation** (run yourself; subagents ruff+py_compile only):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldengraph
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_rebel_fuse.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/python/goldengraph/goldengraph/rebel_fuse.py` | **Create.** `rebel_fuse_enabled`, `_rebel_params`, `_load_rebel` (cached singleton), `_match_mention`, `rebel_fuse`. |
| `packages/python/goldengraph/tests/test_rebel_fuse.py` | **Create.** 7 tests (mapping, drop-unmapped/self-loop, windowing, gate/empty, wiring, raise-preserves). All inject a fake REBEL. |
| `packages/python/goldengraph/goldengraph/ingest.py` | **Modify** — import + gated seam after line 687 (after re-prompt, before `_maybe_canonicalize`). |
| `docs/superpowers/reports/2026-07-02-rebel-fuse-verdict.md` | **Create** in Task 3 after the Modal run. |

---

## Task 1: `rebel_fuse.py` module

**Files:**
- Create: `packages/python/goldengraph/goldengraph/rebel_fuse.py`
- Test: `packages/python/goldengraph/tests/test_rebel_fuse.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_rebel_fuse.py`:

```python
"""GOLDENGRAPH_REBEL_FUSE: map REBEL (head,rel,tail) triples onto existing entities as edges."""
from goldengraph.extract import Mention
from goldengraph.rebel_fuse import rebel_fuse, rebel_fuse_enabled


def _mentions():
    return [Mention(name="Amazon", typ="org"), Mention(name="Jeff Bezos", typ="person")]


def _fake_rebel(triples_per_call):
    """Returns a callable that yields a fixed triple list on every call, recording call count."""
    state = {"calls": 0}

    def rebel(text):
        state["calls"] += 1
        return list(triples_per_call)
    rebel.state = state
    return rebel


def test_maps_triple_to_existing_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")  # one window over the short text
    rebel = _fake_rebel([("Amazon", "founded by", "Jeff Bezos")])
    rels = rebel_fuse("Amazon was founded by Jeff Bezos.", _mentions(), rebel=rebel)
    assert len(rels) == 1
    assert (rels[0].subj, rels[0].predicate, rels[0].obj) == (0, "founded by", 1)


def test_substring_and_casefold_match(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")
    rebel = _fake_rebel([("amazon", "employs", "Bezos")])  # cased + substring
    rels = rebel_fuse("t.", _mentions(), rebel=rebel)
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "employs", 1)]


def test_drops_unmapped_and_self_loops(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "50")
    rebel = _fake_rebel([
        ("Amazon", "rivals", "Microsoft"),   # tail maps to no mention -> dropped
        ("Amazon", "is", "Amazon"),           # both map to 0 -> self-loop dropped
        ("Amazon", "led by", "Jeff Bezos"),   # valid
    ])
    rels = rebel_fuse("t.", _mentions(), rebel=rebel)
    assert [(r.subj, r.predicate, r.obj) for r in rels] == [(0, "led by", 1)]


def test_runs_once_per_window(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_REBEL_SENTENCES", "2")
    monkeypatch.setenv("GOLDENGRAPH_REBEL_OVERLAP", "0")
    # 4 sentences, size=2 overlap=0 -> 2 windows
    text = "S one here. S two here. S three here. S four here."
    rebel = _fake_rebel([("Amazon", "r", "Jeff Bezos")])
    rels = rebel_fuse(text, _mentions(), rebel=rebel)
    assert rebel.state["calls"] == 2          # one call per window
    assert len(rels) == 2                      # each window contributed the mapped edge


def test_gate_enabled(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_REBEL_FUSE", raising=False)
    assert rebel_fuse_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", "1")
    assert rebel_fuse_enabled() is True
    for off in ("", "0", "False", "off", " no "):
        monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", off)
        assert rebel_fuse_enabled() is False, off


def test_empty_mentions_no_rebel_call():
    def boom(text):
        raise AssertionError("must not be called on empty mentions")
    assert rebel_fuse("t.", [], rebel=boom) == []
```

- [ ] **Step 2: Run, verify fail.** Box-safe invocation. Expected: `ModuleNotFoundError: No module named 'goldengraph.rebel_fuse'`.

- [ ] **Step 3: Implement `rebel_fuse.py`:**

```python
"""REBEL fusion (GOLDENGRAPH_REBEL_FUSE): a distinct relation-recall lever. Runs REBEL
(Babelscape/rebel-large, discriminative end-to-end relation extraction) per sentence-window,
and maps each (head, rel, tail) triple's endpoints onto the ALREADY-extracted entities --
adding an edge only when BOTH endpoints map, never a new node. Composes with (and is measured
on top of) the relation re-prompt. Pure w.r.t. the store; REBEL injectable for tests; gate off
by default.

SCHEMA_CANON note: REBEL emits Wikidata-style predicates that a closed relation vocab won't
contain, so under GOLDENGRAPH_SCHEMA_CANON=1 canonicalization drops them. This lever targets
canon-off configs (see the spec)."""

from __future__ import annotations

import os
import threading

from .chunk_extract import _env_int, sentence_windows, split_sentences
from .extract import Mention, Relationship

_REBEL_LOCK = threading.Lock()
_REBEL = None  # cached `text -> list[(head, rel, tail)]` callable


def rebel_fuse_enabled() -> bool:
    """`GOLDENGRAPH_REBEL_FUSE` gate. Off by default; case-insensitive, stripped:
    ""/"0"/"false"/"no"/"off" -> off."""
    return os.environ.get("GOLDENGRAPH_REBEL_FUSE", "0").strip().lower() not in (
        "",
        "0",
        "false",
        "no",
        "off",
    )


def _rebel_params() -> tuple[int, int]:
    """(window size, overlap) for REBEL's 256-token input; defaults (4, 1). Defensive parse."""
    return _env_int("GOLDENGRAPH_REBEL_SENTENCES", 4), _env_int("GOLDENGRAPH_REBEL_OVERLAP", 1)


def _load_rebel():
    """Lazily load Babelscape/rebel-large ONCE (lock-guarded, double-checked, for the concurrent
    prepare phase). Returns a `text -> list[(head, rel, tail)]` callable reusing the existing
    unit-tested `extract_local.parse_rebel_triplets` decoder."""
    global _REBEL
    if _REBEL is not None:
        return _REBEL
    with _REBEL_LOCK:
        if _REBEL is None:
            from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

            from .extract_local import parse_rebel_triplets

            tok = AutoTokenizer.from_pretrained("Babelscape/rebel-large")
            mdl = AutoModelForSeq2SeqLM.from_pretrained("Babelscape/rebel-large")

            def _triples(text: str):
                inp = tok(text, return_tensors="pt", truncation=True, max_length=256)
                out = mdl.generate(**inp, max_length=256)
                return parse_rebel_triplets(tok.decode(out[0], skip_special_tokens=False))

            _REBEL = _triples
    return _REBEL


def _match_mention(surface_lc: str, mentions: list[Mention]) -> int | None:
    """Index of the mention whose (case-folded) name matches `surface_lc` -- exact preferred over
    substring-either-way, lowest index breaking ties; None if none match."""
    if not surface_lc:
        return None
    for i, m in enumerate(mentions):  # exact first
        if m.name.strip().lower() == surface_lc:
            return i
    for i, m in enumerate(mentions):  # substring either way
        n = m.name.strip().lower()
        if n and (surface_lc in n or n in surface_lc):
            return i
    return None


def rebel_fuse(text: str, mentions: list[Mention], *, rebel=None) -> list[Relationship]:
    """Run REBEL per sentence-window over `text`, map triple endpoints onto `mentions`, and return
    Relationships for triples where BOTH endpoints map and are distinct. Empty mentions -> [] (no
    model call). Any error -> [] (fail-soft). `rebel` (injectable) is a `text -> list[(head,rel,tail)]`
    callable; default None -> the cached real REBEL."""
    if not mentions:
        return []
    try:
        triples_fn = rebel or _load_rebel()
        size, overlap = _rebel_params()
        out: list[Relationship] = []
        for window in sentence_windows(split_sentences(text), size, overlap):
            try:
                triples = triples_fn(window)
            except Exception:
                continue  # a bad window skips, never sinks the doc
            for head, rel, tail in triples:
                s = _match_mention(str(head).strip().lower(), mentions)
                o = _match_mention(str(tail).strip().lower(), mentions)
                if s is not None and o is not None and s != o:
                    out.append(Relationship(subj=s, predicate=str(rel), obj=o))
        return out
    except Exception:
        return []
```

- [ ] **Step 4: Run tests, verify pass** (box-safe). Expected: 6 passed. Then `ruff check goldengraph/rebel_fuse.py`.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/rebel_fuse.py tests/test_rebel_fuse.py
git commit -m "feat(goldengraph): REBEL fusion (GOLDENGRAPH_REBEL_FUSE) -- per-window triples mapped to existing entities"
```

---

## Task 2: wire the gated seam into `_prepare_doc`

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/ingest.py` (import near line 23; seam after line 687, before line 688)
- Test: `packages/python/goldengraph/tests/test_rebel_fuse.py`

- [ ] **Step 1: Add the failing wiring test.** Append to `tests/test_rebel_fuse.py`:

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


def test_prepare_doc_appends_rebel_edges_only_when_gated(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention, Relationship
    ingest = importlib.import_module("goldengraph.ingest")  # __init__ shadows the submodule name

    calls = {"n": 0}

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="Amazon", typ="org"),
                                    Mention(name="Jeff Bezos", typ="person")],
                          relationships=[])

    def fake_fuse(text, mentions):
        calls["n"] += 1
        return [Relationship(subj=0, predicate="founded by", obj=1)]

    monkeypatch.setattr(ingest, "rebel_fuse", fake_fuse)
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    resolver = _identity_resolver()

    # gate OFF -> no fuse, no edges
    monkeypatch.delenv("GOLDENGRAPH_REBEL_FUSE", raising=False)
    ex, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                   extractor=base_extractor)
    assert calls["n"] == 0 and len(ex.relationships) == 0

    # gate ON -> fuse called once, edge appended
    monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", "1")
    ex2, _, _ = ingest._prepare_doc("t", llm=None, resolver=resolver, profile_fps=False,
                                    extractor=base_extractor)
    assert calls["n"] == 1 and len(ex2.relationships) == 1


def test_prepare_doc_rebel_raise_preserves_first_pass(monkeypatch):
    import importlib

    from goldengraph.extract import Extraction, Mention, Relationship
    ingest = importlib.import_module("goldengraph.ingest")

    def base_extractor(text, llm=None):
        return Extraction(mentions=[Mention(name="A", typ="org"), Mention(name="B", typ="org")],
                          relationships=[Relationship(subj=0, predicate="rel", obj=1)])

    def boom(text, mentions):
        raise RuntimeError("rebel exploded")

    monkeypatch.setattr(ingest, "rebel_fuse", boom)
    monkeypatch.setenv("GOLDENGRAPH_REBEL_FUSE", "1")
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_RELATION_REPROMPT", raising=False)
    ex, ents, _ = ingest._prepare_doc("t", llm=None, resolver=_identity_resolver(),
                                      profile_fps=False, extractor=base_extractor)
    assert len(ex.mentions) == 2 and len(ex.relationships) == 1 and len(ents) == 2
```

- [ ] **Step 2: Run, verify fail.** Expected: `AttributeError` (no `ingest.rebel_fuse` to patch) or the gate-ON assertion failing.

- [ ] **Step 3: Wire the seam.** In `ingest.py`, add the import beside the relation_reprompt import (after line 23 `from .relation_reprompt import relation_reprompt, relation_reprompt_enabled`):

```python
from .rebel_fuse import rebel_fuse, rebel_fuse_enabled
```
(ruff may reorder the local-import block; run `ruff check --fix` if it flags I001.)

Then insert the gated append immediately after the re-prompt block's closing `pass` (after line 687, before the `# In discovery mode...` comment at line 688):

```python
        # REBEL fusion (2nd relation source): map REBEL triples onto existing entities as edges.
        # Same before-canonicalization placement + own try/except as the re-prompt.
        if rebel_fuse_enabled():
            try:
                extraction.relationships += rebel_fuse(text, extraction.mentions)
            except Exception:
                pass
```

- [ ] **Step 4: Run tests, verify pass.** Full `tests/test_rebel_fuse.py` (box-safe). Expected: 8 passed. Then a regression sanity on the neighbouring gates + ruff:

```bash
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_rebel_fuse.py tests/test_relation_reprompt.py tests/test_chunk_extract.py -q -p no:cacheprovider
"$PY" -m ruff check goldengraph/ingest.py goldengraph/rebel_fuse.py
```

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/ingest.py tests/test_rebel_fuse.py
git commit -m "feat(goldengraph): gate REBEL fusion into _prepare_doc (after re-prompt, before canon, fail-soft)"
```

---

## Task 3: Modal 3-way measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-02-rebel-fuse-verdict.md`.

Run yourself (Modal, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, `--detach --spawn`, distinct `--n`). Rig is `SCHEMA_CANON` off / no relation vocab (do NOT set `GOLDENGRAPH_RELATION_VOCAB` or `GOLDENGRAPH_SCHEMA_CANON`).

- [ ] **Step 1: Fire the 3-way (+ optional REBEL-alone):**

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BEST=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2'
# control
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 110 --opts "$BEST" --spawn
# re-prompt only (reproduce the shipped win baseline)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 111 \
  --opts "$BEST"$'\nGOLDENGRAPH_RELATION_REPROMPT=1' --spawn
# re-prompt + REBEL (the marginal-delta question)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 112 \
  --opts "$BEST"$'\nGOLDENGRAPH_RELATION_REPROMPT=1\nGOLDENGRAPH_REBEL_FUSE=1' --spawn
# REBEL alone (optional bonus read)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 113 \
  --opts "$BEST"$'\nGOLDENGRAPH_REBEL_FUSE=1' --spawn
```
Poll `gg-bench-cache` for `results/substrate_11{0,1,2,3}_*.md`; read the `[substrate-wiki]` line.

> Note: first REBEL leg downloads Babelscape/rebel-large from HF (~1.6GB) into the container — allow extra startup. transformers/torch are already in the image (gliner dep). If a leg errors on the model download, re-run it (HF hiccup); the fail-soft path would otherwise yield the re-prompt baseline silently.

- [ ] **Step 2: Read all legs.** Tabulate R(B) / P(B) / F1 / coverage / components. The decision compares **112 (re-prompt+REBEL) vs 111 (re-prompt)**.

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-02-rebel-fuse-verdict.md`:
  - **WIN:** 112 R(B)/F1 above 111, P(B) ~1.0, components not worse.
  - **REFUTED (redundant):** 112 ≈ 111 → REBEL overlaps the re-prompt; relation-recall saturates there.
  - **REFUTED (harmful):** P(B) drops / components collapse → REBEL mis-mapped edges (surface collision).
  - Report the REBEL-alone (113) read as context (standalone relation recall vs control). Note the canon-off scope + the truncation caveat if REFUTED.

- [ ] **Step 4: Commit** the report.

```bash
git add docs/superpowers/reports/2026-07-02-rebel-fuse-verdict.md
git commit -m "docs(goldengraph): REBEL fusion verdict (wiki 3-way marginal delta)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run the box-safe `tests/test_rebel_fuse.py` suite, open a PR (base `main`), arm auto-merge. The PR ships the gate default-off regardless of the verdict (an opt-in second relation source). If REFUTED, the relation-recall thread closes at the re-prompt and the arc turns to cross-corpus robustness of the shipped stack (name_ci + chunking + re-prompt).
