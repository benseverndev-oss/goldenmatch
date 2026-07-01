# Homograph-Safe Entity-Type Canonicalization — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a gated `(name_ci, coarse_type)` cross-doc merge key (`GOLDENGRAPH_XDOC_KEY=name_ci_type`) plus an extraction-time closed type vocab, so the type-jitter recall win survives while same-name/different-coarse-type homographs stay separate — validated on a homograph-injected engineered corpus.

**Architecture:** Source-side + deterministic. Constrain extraction to a closed type vocab; canonicalize the emitted type deterministically; fold the coarse type back into the cross-doc key at the `_key_payload` chokepoint. Validate with a new gated homograph injection in the engineered generator.

**Tech Stack:** Python (goldengraph pkg + er-kg-bench), pytest, Modal for the live substrate run.

**Spec:** `docs/superpowers/specs/2026-07-01-entity-type-canon-design.md`

**Prerequisite (satisfied):** `GOLDENGRAPH_XDOC_KEY` / `_key_payload` merged in #1331 (on `main` as of 2026-07-01 10:04).

---

## Setup

- [ ] **Rebase the branch onto current main** (which now has #1331's `_key_payload`).

```bash
git fetch origin
git checkout feat/entity-type-canon
git rebase origin/main
# confirm _key_payload is the single chokepoint (spec prerequisite):
grep -n "_key_payload\|_record_key" packages/python/goldengraph/goldengraph/resolve.py
grep -rn "_record_key(" packages/python/goldengraph/goldengraph/
```
Expected: `_record_key` calls `_key_payload`; every cross-doc key routes through `_record_key`.

**Box-safe test invocation** (used throughout — the machine OOMs on full imports):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
# goldengraph pkg tests need the worktree shadow:
cd packages/python/goldengraph
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
# er-kg-bench tests:
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="." POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 "$PY" -m pytest <test> -q -p no:cacheprovider
```

---

## Task 1: `canonicalize_entity_type` + vocab helpers

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/schema.py`
- Test: `packages/python/goldengraph/tests/test_entity_type_canon.py`

- [ ] **Step 1: Write the failing test**

```python
# test_entity_type_canon.py
"""Deterministic coarse-type canonicalization (pure; no goldenmatch)."""
from goldengraph.schema import canonicalize_entity_type, entity_type_vocab


def test_exact_vocab_match_case_folded():
    assert canonicalize_entity_type("Organization") == "organization"
    assert canonicalize_entity_type("PERSON") == "person"


def test_substring_hint_maps_open_prose_to_coarse():
    # the real 7B jitter: all of these are one coarse class
    for t in ("Data Processing Technique", "Statistical Method", "Algorithm", "process", "metric"):
        assert canonicalize_entity_type(t) == "concept"
    assert canonicalize_entity_type("Tech Company") == "organization"


def test_off_vocab_falls_back_to_other():
    assert canonicalize_entity_type("wibble") == "other"
    assert canonicalize_entity_type("") == "other"


def test_custom_vocab_via_env(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_ENTITY_TYPE_VOCAB", "person, org")
    assert entity_type_vocab() == ("person", "org")
    # 'organization' not in the custom vocab, 'company' hint -> 'organization' not present -> other-fallback:
    # with no 'other' in vocab, fall back to the last entry
    assert canonicalize_entity_type("random", ("person", "org")) == "org"
```

- [ ] **Step 2: Run it, verify it fails** (`ImportError: cannot import name 'canonicalize_entity_type'`).

- [ ] **Step 3: Implement in `schema.py`** (append near the predicate canon helpers):

```python
#: Coarse entity-type vocab -- the closed set the extractor is constrained to and the cross-doc key
#: coarsens to. Deliberately small so a weak model is CONSISTENT within it (kills type jitter) while
#: still separating homograph classes (person vs organization). Override via GOLDENGRAPH_ENTITY_TYPE_VOCAB.
DEFAULT_ENTITY_TYPE_VOCAB = (
    "person", "organization", "location", "concept", "work", "event", "product", "other",
)

#: Substring keyword -> coarse type, for the open prose a 7B emits when it ignores the constraint.
_ENTITY_TYPE_HINTS = {
    "technique": "concept", "method": "concept", "algorithm": "concept", "process": "concept",
    "index": "concept", "measure": "concept", "metric": "concept", "model": "concept",
    "concept": "concept", "theory": "concept", "approach": "concept", "function": "concept",
    "company": "organization", "corp": "organization", "inc": "organization", "ltd": "organization",
    "organization": "organization", "organisation": "organization", "university": "organization",
    "lab": "organization", "institute": "organization", "agency": "organization", "team": "organization",
    "person": "person", "author": "person", "researcher": "person", "scientist": "person",
    "city": "location", "country": "location", "region": "location", "place": "location",
    "location": "location", "site": "location",
    "book": "work", "paper": "work", "article": "work", "publication": "work", "work": "work",
    "event": "event", "conference": "event", "war": "event",
    "product": "product", "tool": "product", "software": "product", "system": "product",
    "device": "product", "technology": "product",
}


def entity_type_vocab() -> tuple:
    raw = os.environ.get("GOLDENGRAPH_ENTITY_TYPE_VOCAB", "")
    vocab = tuple(dict.fromkeys(v.strip().lower() for v in raw.split(",") if v.strip()))
    return vocab or DEFAULT_ENTITY_TYPE_VOCAB


def canonicalize_entity_type(raw: str, vocab: tuple | None = None) -> str:
    """Snap an open-vocab type string to the closed coarse vocab: exact match, else a substring hint,
    else `other` (or the vocab's last entry if it has no `other`). Pure + goldenmatch-free."""
    vocab = vocab or entity_type_vocab()
    t = (raw or "").strip().lower()
    if t in vocab:
        return t
    for kw, coarse in _ENTITY_TYPE_HINTS.items():
        if kw in t and coarse in vocab:
            return coarse
    return "other" if "other" in vocab else (vocab[-1] if vocab else "other")


def entity_type_canon_enabled() -> bool:
    """`GOLDENGRAPH_ENTITY_TYPE_CANON` gate: constrain extraction to `entity_type_vocab()`."""
    return os.environ.get("GOLDENGRAPH_ENTITY_TYPE_CANON", "0") not in ("0", "false", "")
```

- [ ] **Step 4: Run the test, verify pass** + `ruff check schema.py`.
- [ ] **Step 5: Commit** — `feat(goldengraph): coarse entity-type vocab + canonicalize_entity_type`.

---

## Task 2: `name_ci_type` cross-doc key mode

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/resolve.py` (`_key_payload`)
- Test: `packages/python/goldengraph/tests/test_xdoc_key.py` (extend)

- [ ] **Step 1: Add the failing test** to `test_xdoc_key.py`:

```python
def test_name_ci_type_mode_folds_case_and_coarsens_type(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_XDOC_KEY", "name_ci_type")
    # same entity, jittered type across docs -> SAME key (both coarsen to 'concept')
    assert (
        _key_payload("Schema Matching", "Process")
        == _key_payload("schema matching", "Algorithm")
        == {"name": "schema matching", "typ": "concept"}
    )
    # homograph: same name, DIFFERENT coarse class -> DIFFERENT key (stays separate)
    assert _key_payload("Vertex", "company") != _key_payload("Vertex", "product")
```

- [ ] **Step 2: Run, verify fail** (returns `{"name","typ"}` un-coarsened → keys not equal for the jitter case).

- [ ] **Step 3: Implement** — add the branch to `_key_payload`:

```python
    if mode == "name_ci_type":
        from .schema import canonicalize_entity_type
        return {"name": name.strip().lower(), "typ": canonicalize_entity_type(typ)}
```
(insert before the final `return {"name": name, "typ": typ}`.)

- [ ] **Step 4: Run test, verify pass.** Also re-run existing `test_xdoc_key.py` cases.
- [ ] **Step 5: Commit** — `feat(goldengraph): name_ci_type cross-doc key mode`.

---

## Task 3: extraction-time type-vocab constraint

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/extract.py`
- Test: `packages/python/goldengraph/tests/test_entity_type_constraint.py`

- [ ] **Step 1: Write the failing test** (a capturing stub LLM — no network):

```python
"""GOLDENGRAPH_ENTITY_TYPE_CANON prepends the type-vocab instruction to the extract prompt."""
from goldengraph.extract import extract


class _CaptureLLM:
    def __init__(self):
        self.prompt = None
    def complete(self, prompt):
        self.prompt = prompt
        return '{"entities": [], "relationships": []}'
    # no complete_json -> extract() falls back to complete()


def test_type_vocab_instruction_absent_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_ENTITY_TYPE_CANON", raising=False)
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "MUST be exactly one of" not in llm.prompt


def test_type_vocab_instruction_present_when_gated(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_ENTITY_TYPE_CANON", "1")
    monkeypatch.setenv("GOLDENGRAPH_EXTRACT_JSON_MODE", "0")  # force .complete path for the stub
    llm = _CaptureLLM()
    extract("some text", llm)
    assert "MUST be exactly one of" in llm.prompt
    assert "organization" in llm.prompt and "concept" in llm.prompt
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement in `extract.py`** — add the constant and prepend it in `extract()`:

```python
_ENTITY_TYPE_VOCAB_INSTRUCTION = (
    "Every entity's `type` MUST be exactly one of: {vocab}. "
    "Pick the single closest; do not invent other type labels.\n\n"
)
```
In `extract()`, alongside the existing relation-vocab prepend:
```python
    from .schema import entity_type_canon_enabled, entity_type_vocab
    if entity_type_canon_enabled():
        prompt = _ENTITY_TYPE_VOCAB_INSTRUCTION.format(vocab=", ".join(entity_type_vocab())) + prompt
```
(place after the `prompt = (_PROMPT_LITERALS if literals else _PROMPT).format(text=text)` line and any relation-vocab prepend, so both instructions can stack.)

- [ ] **Step 4: Run tests, verify pass** + ruff.
- [ ] **Step 5: Commit** — `feat(goldengraph): gated extraction-time entity-type vocab constraint`.

---

## Task 4: homograph injection in the engineered generator

**Files:**
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py`
- Test: `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_homograph_corpus.py`

- [ ] **Step 1: Write the failing test:**

```python
"""GOLDENGRAPH_BENCH_HOMOGRAPH injects same-surface / different-coarse-type collisions."""
from erkgbench.qa_e2e.engineered import emit_gold_mentions, generate_engineered


def test_homograph_injection_shares_surface_across_distinct_entities(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_BENCH_HOMOGRAPH", "1")
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold = emit_gold_mentions(corpus.documents)
    # find a surface shared by >= 2 DISTINCT gold entity_ids -> the injected collision
    by_surface = {}
    for eid, surface, _doc in gold:
        by_surface.setdefault(surface, set()).add(eid)
    shared = {s: ids for s, ids in by_surface.items() if len(ids) > 1}
    assert shared, "no homograph collision injected"
    # the collision docs carry the appositive coarse-type cue
    homo_surface = next(iter(shared))
    cued = [d for d in corpus.documents if homo_surface in d.text and ", a " in d.text]
    assert cued, "homograph docs must render the coarse-type appositive cue"


def test_homograph_off_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_BENCH_HOMOGRAPH", raising=False)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=0.0)
    gold = emit_gold_mentions(corpus.documents)
    by_surface = {}
    for eid, surface, _doc in gold:
        by_surface.setdefault(surface, set()).add(eid)
    # baseline concept corpus: no surface maps to >1 distinct entity id
    assert not any(len(ids) > 1 for ids in by_surface.values())
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** In `engineered.py`:

1. Add `coarse_type` to `_Entity` and populate it in `_load_entities`:
```python
@dataclass(frozen=True)
class _Entity:
    id: str
    canonical: str
    variants: tuple[str, ...]
    coarse_type: str = "concept"
```
```python
# in _load_entities, inside the loop:
from goldengraph.schema import canonicalize_entity_type
entities.append(_Entity(id=c.canonical_id, canonical=c.concept, variants=variants,
                        coarse_type=canonicalize_entity_type(getattr(c, "entity_type", ""))))
```

2. After `edges` is built in `generate_engineered`, inject collisions:
```python
import os as _os2
homo_k = int(_os2.environ.get("GOLDENGRAPH_BENCH_HOMOGRAPH", "0") or "0")
homo_surface: dict[str, str] = {}   # entity_id -> shared surface
homo_type: dict[str, str] = {}      # entity_id -> coarse type (for the cue)
if homo_k > 0:
    # entities that actually appear as an edge endpoint (else they emit no docs)
    endpoints = set(e for e in ids if edges[e]) | {d for e in ids for d in edges[e].values()}
    adj = {e: set(edges[e].values()) for e in ids}
    pool = sorted(endpoints)
    picks = random.Random(f"{seed}:homograph").sample(pool, min(len(pool), homo_k * 4))
    used: set[str] = set()
    made = 0
    for a in picks:
        if made >= homo_k or a in used:
            continue
        for b in picks:
            if b in used or b == a or b in adj[a] or a in adj[b]:
                continue
            if by_id[a].coarse_type != by_id[b].coarse_type:  # DIFFERENT coarse type
                shared = f"HG{made}"
                for e in (a, b):
                    homo_surface[e] = shared
                    homo_type[e] = by_id[e].coarse_type
                used.update({a, b}); made += 1
                break
```

3. In the doc-render loop, use the shared surface + appositive cue for homograph entities. Replace the `s`/`o` render + `Document` text:
```python
s = homo_surface.get(src_id) or _render_mention(by_id[src_id], rng, ambiguity)
o = homo_surface.get(dst_id) or _render_mention(by_id[dst_id], rng, ambiguity)
s_txt = f"{s}, a {homo_type[src_id]}," if src_id in homo_surface else s
o_txt = f"{o}, a {homo_type[dst_id]}," if dst_id in homo_surface else o
documents.append(Document(id=_edge_doc_id(src_id, rel, dst_id),
                          text=f"{s_txt} {_render_relation(rel, rng)} {o_txt}.",
                          src_surface=s, dst_surface=o))
```
(`src_surface`/`dst_surface` stay the bare shared surface so `emit_gold_mentions` gold matches what the extractor's name resolves to; the appositive is context only.)

- [ ] **Step 4: Run tests, verify pass** + ruff.
- [ ] **Step 5: Commit** — `feat(er-kg-bench): homograph injection for the substrate eval`.

---

## Task 5: validation sweep + verdict report

**Files:**
- Create: `docs/superpowers/reports/2026-07-01-entity-type-canon-verdict.md`

- [ ] **Step 1: Fire the four Modal legs** (detached+spawn, distinct `--n` per leg so files don't clash; `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`). Standard corpus recall parity + homograph precision:

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
# standard corpus: name_ci (baseline) vs name_ci_type + ENTITY_TYPE_CANON
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 30 \
  --opts $'GOLDENGRAPH_SUBSTRATE_AMBIGUITY=0.0\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 31 \
  --opts $'GOLDENGRAPH_SUBSTRATE_AMBIGUITY=0.0\nGOLDENGRAPH_XDOC_KEY=name_ci_type\nGOLDENGRAPH_ENTITY_TYPE_CANON=1' --spawn
# homograph corpus: name_ci (control, MUST drop P) vs name_ci_type (holds P)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 32 \
  --opts $'GOLDENGRAPH_SUBSTRATE_AMBIGUITY=0.0\nGOLDENGRAPH_BENCH_HOMOGRAPH=6\nGOLDENGRAPH_XDOC_KEY=name_ci' --spawn
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 33 \
  --opts $'GOLDENGRAPH_SUBSTRATE_AMBIGUITY=0.0\nGOLDENGRAPH_BENCH_HOMOGRAPH=6\nGOLDENGRAPH_XDOC_KEY=name_ci_type\nGOLDENGRAPH_ENTITY_TYPE_CANON=1' --spawn
```
Poll each `results/substrate_<n>_goldengraph-qwen2.5-7b-instruct.md` with a Monitor (content-appears).

- [ ] **Step 2: Assert the gate.**
  - Standard: `name_ci_type` R(B) within 0.05 of `name_ci` R(B) (≈ 0.75).
  - Homograph: `name_ci` P(B) **drops** below its standard-corpus level (the negative control fires); `name_ci_type` P(B) **holds** near it.
  - If the control does NOT drop, STOP — the injection is broken, not the fix (see spec Risks).

- [ ] **Step 3: Write the verdict report** — the 4-cell table (corpus × key), the gate outcome, and the honest boundary (same-coarse-class homographs unaddressed).

- [ ] **Step 4: Commit** the report.

---

## Completion

Use superpowers:finishing-a-development-branch: verify box-safe tests pass, then open a PR (base `main`), arm auto-merge. Follow-ons stay out of scope (embedding/LLM type derivation; default flip; same-coarse-class disambiguation via profile-link-on-name_ci).
