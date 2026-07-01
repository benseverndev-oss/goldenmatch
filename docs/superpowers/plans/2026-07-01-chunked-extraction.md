# Chunked Extraction — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A gated `GOLDENGRAPH_CHUNK_EXTRACT=1` path that splits each document into overlapping sentence windows, extracts each window with the same extractor, and unions the results before resolution — the second real-prose extraction-recall lever, measured on the wiki corpus.

**Architecture:** A new pure-Python module `goldengraph/chunk_extract.py` holds three units: `split_sentences` (regex sentence split), `sentence_windows` (overlapping windows), and `chunk_extract` (per-window extraction + index-offset union). `ingest._prepare_doc` calls `chunk_extract` instead of the single `_extract` when the gate is on; off = byte-identical to today. Everything downstream (`resolve → build_batch → cross-doc link → append`) is untouched.

**Tech Stack:** Python 3.11, stdlib `re`/`os` only (no nltk/spacy/network), pytest. Reuses `goldengraph.extract` dataclasses (`Extraction`, `Mention`, `Relationship`, `Attribute`).

**Spec:** `docs/superpowers/specs/2026-07-01-chunked-extraction-design.md`
**Branch:** `feat/chunked-extraction` (off `main`, already created).

**Box-safe test invocation** (run these yourself — do NOT let a subagent import/pytest/uv on this box):
```bash
PY="/d/show_case/goldenmatch/.venv/Scripts/python.exe"
cd packages/python/goldengraph
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_chunk_extract.py -q -p no:cacheprovider
```

## File structure

| File | Responsibility |
|---|---|
| `packages/python/goldengraph/goldengraph/chunk_extract.py` | **Create.** `split_sentences`, `sentence_windows`, config helpers, `chunk_extract`. Pure w.r.t. the store; only depends on `extract` dataclasses + `os`/`re`. |
| `packages/python/goldengraph/tests/test_chunk_extract.py` | **Create.** Unit tests for all three units + gate wiring (via a stub, no LLM). |
| `packages/python/goldengraph/goldengraph/ingest.py` | **Modify** line 671 (the one extraction call site) + add import. |
| `docs/superpowers/reports/2026-07-01-chunked-extraction-verdict.md` | **Create** in Task 4 after the Modal sweep. |

---

## Task 1: `split_sentences` + `sentence_windows` (pure, no LLM)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/chunk_extract.py`
- Test: `packages/python/goldengraph/tests/test_chunk_extract.py`

- [ ] **Step 1: Write the failing tests.** Create `tests/test_chunk_extract.py`:

```python
"""Chunked extraction: sentence splitting, overlapping windows, and index-offset union."""
from goldengraph.chunk_extract import sentence_windows, split_sentences


def test_split_sentences_basic():
    s = "Amazon was founded in 1994. Jeff Bezos was the CEO. It sells books."
    assert len(split_sentences(s)) == 3


def test_split_sentences_empty():
    assert split_sentences("") == []
    assert split_sentences("   ") == []


def test_split_sentences_lower_bound_with_abbreviations():
    # "Inc." may over-split; we only require the real breaks are found (lower bound).
    s = "Apple Inc. is a company. Steve Jobs founded it."
    assert len(split_sentences(s)) >= 2


def test_windows_size_and_overlap_spans():
    sents = [f"s{i}." for i in range(9)]  # 9 sentences
    # size=4, overlap=1 -> stride 3 -> windows [0:4], [3:7], [6:9]
    wins = sentence_windows(sents, size=4, overlap=1)
    assert wins == ["s0. s1. s2. s3.", "s3. s4. s5. s6.", "s6. s7. s8."]


def test_windows_shorter_than_size_is_one_window():
    sents = ["a.", "b."]
    assert sentence_windows(sents, size=4, overlap=1) == ["a. b."]


def test_windows_empty_input_no_windows():
    assert sentence_windows([], size=4, overlap=1) == []


def test_windows_overlap_ge_size_clamped_terminates():
    sents = [f"s{i}." for i in range(6)]
    # overlap >= size must clamp to size-1 (stride 1), cover all, and terminate.
    wins = sentence_windows(sents, size=3, overlap=5)
    assert wins[0] == "s0. s1. s2."
    assert wins[-1].endswith("s5.")
    assert len(wins) == 4  # stride 1: [0:3],[1:4],[2:5],[3:6]


def test_windows_size_zero_floored_to_one():
    sents = ["a.", "b.", "c."]
    # size<=0 must floor to 1 (no zero/negative stride, no infinite loop).
    wins = sentence_windows(sents, size=0, overlap=0)
    assert wins == ["a.", "b.", "c."]
```

- [ ] **Step 2: Run, verify fail.**

Run the box-safe invocation above. Expected: FAIL with `ModuleNotFoundError: No module named 'goldengraph.chunk_extract'`.

- [ ] **Step 3: Implement `split_sentences` + `sentence_windows`.** Create `goldengraph/chunk_extract.py`:

```python
"""Chunked extraction (GOLDENGRAPH_CHUNK_EXTRACT): split a dense document into
overlapping sentence windows, extract each window with the SAME extractor, and
union the results before resolution. The default single-pass extraction attends
over a whole ~20-sentence Wikipedia lead in one call and drops entities (the ~0.44
real-prose extraction-recall ceiling); a short window lets a weak model extract
both entities AND relations well, and the union aggregates. resolve() collapses
duplicate mentions across windows downstream, so no new dedup is needed here.

Pure w.r.t. the store: stdlib only, depends on the extract dataclasses. Gate is
off by default -- the single-pass path is unchanged when GOLDENGRAPH_CHUNK_EXTRACT
is unset.
"""

from __future__ import annotations

import os
import re

from .extract import Attribute, Extraction, Mention, Relationship  # noqa: F401  (Mention re-exported for callers/tests)
from .llm import LLMClient

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text: str) -> list[str]:
    """Split `text` into sentences on `.!?` boundaries (stdlib regex, network-free).
    Empty / whitespace-only input -> [] (no windows, no wasted extractor call).
    Abbreviations may over-split; harmless for extraction (a fragment yields fewer
    entities, never wrong ones)."""
    if not text or not text.strip():
        return []
    return [s.strip() for s in _SENTENCE_SPLIT.split(text.strip()) if s.strip()]


def sentence_windows(sents: list[str], size: int, overlap: int) -> list[str]:
    """Overlapping sentence windows joined back into text. Advances by `size -
    overlap`. Guards (in order): size<=0 -> 1; overlap<0 -> 0; overlap>=size ->
    size-1 (stride>=1, terminates); [] -> []; len<=size -> one whole-doc window."""
    size = max(1, size)
    overlap = max(0, overlap)
    if overlap >= size:
        overlap = size - 1
    stride = size - overlap  # >= 1
    if not sents:
        return []
    if len(sents) <= size:
        return [" ".join(sents)]
    out: list[str] = []
    i = 0
    n = len(sents)
    while i < n:
        out.append(" ".join(sents[i : i + size]))
        if i + size >= n:  # last window reached the end
            break
        i += stride
    return out
```

- [ ] **Step 4: Run tests, verify pass** (box-safe invocation). Expected: 7 passed. Then `ruff check goldengraph/chunk_extract.py`.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/chunk_extract.py tests/test_chunk_extract.py
git commit -m "feat(goldengraph): sentence splitting + overlapping windows for chunked extraction"
```

---

## Task 2: config helpers + `chunk_extract` union

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/chunk_extract.py`
- Test: `packages/python/goldengraph/tests/test_chunk_extract.py`

- [ ] **Step 1: Add the failing tests.** Append to `tests/test_chunk_extract.py`:

```python
from goldengraph.chunk_extract import (
    chunk_extract,
    chunk_extract_enabled,
    _chunk_params,
)
from goldengraph.extract import Extraction, Mention, Relationship


class _WindowStub:
    """Extractor stub: records each window text it saw and returns a fixed
    2-entity/1-relationship extraction per call (rel points 0->1 within the call)."""

    def __init__(self):
        self.seen = []

    def __call__(self, text, llm=None):
        self.seen.append(text)
        k = len(self.seen)
        return Extraction(
            mentions=[Mention(name=f"E{k}a", typ="org"), Mention(name=f"E{k}b", typ="person")],
            relationships=[Relationship(subj=0, predicate="founded_by", obj=1)],
        )


def test_chunk_extract_enabled_gate(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    assert chunk_extract_enabled() is False
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "1")
    assert chunk_extract_enabled() is True
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "")  # set-but-empty -> off
    assert chunk_extract_enabled() is False


def test_chunk_params_defaults_and_empty_string(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_SENTENCES", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_OVERLAP", raising=False)
    assert _chunk_params() == (4, 1)
    # empty-string env must fall back to default, not raise ValueError
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "")
    assert _chunk_params() == (4, 1)
    # garbage falls back too
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "abc")
    assert _chunk_params() == (4, 1)
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "3")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "2")
    assert _chunk_params() == (3, 2)


def test_chunk_extract_unions_and_offsets_indices(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "0")
    text = "Sentence one is here. Sentence two is here. Sentence three is here."
    stub = _WindowStub()
    ex = chunk_extract(text, llm=None, extractor=stub)
    # 3 windows -> 6 mentions, 3 relationships
    assert len(stub.seen) == 3
    assert len(ex.mentions) == 6
    assert len(ex.relationships) == 3
    # window k's relationship must point into window k's mention block (offset applied)
    # window 0: mentions 0,1 -> rel (0,1); window 1: mentions 2,3 -> rel (2,3); window 2: (4,5)
    assert (ex.relationships[0].subj, ex.relationships[0].obj) == (0, 1)
    assert (ex.relationships[1].subj, ex.relationships[1].obj) == (2, 3)
    assert (ex.relationships[2].subj, ex.relationships[2].obj) == (4, 5)


def test_chunk_extract_skips_failing_window(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "0")

    calls = {"n": 0}

    def flaky(text, llm=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])

    text = "One here. Two here. Three here."
    ex = chunk_extract(text, llm=None, extractor=flaky)
    # 3 windows, middle one raises -> 2 mentions survive, no crash
    assert len(ex.mentions) == 2
```

- [ ] **Step 2: Run, verify fail.** Expected: `ImportError: cannot import name 'chunk_extract'` (and `_chunk_params`, `chunk_extract_enabled`).

- [ ] **Step 3: Implement.** Append to `goldengraph/chunk_extract.py`:

```python
def _env_int(name: str, default: int) -> int:
    """Parse an int env var defensively: unset OR set-but-empty OR non-numeric ->
    `default` (the empty-string-env footgun -- `NAME=` must not raise ValueError)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def chunk_extract_enabled() -> bool:
    """`GOLDENGRAPH_CHUNK_EXTRACT` gate. Off by default; "0"/"false"/"" -> off."""
    return os.environ.get("GOLDENGRAPH_CHUNK_EXTRACT", "0") not in ("0", "false", "")


def _chunk_params() -> tuple[int, int]:
    """(window size, overlap) from env; defaults (4, 1). Defensive parse."""
    return _env_int("GOLDENGRAPH_CHUNK_SENTENCES", 4), _env_int("GOLDENGRAPH_CHUNK_OVERLAP", 1)


def chunk_extract(text: str, llm: LLMClient | None, extractor) -> Extraction:
    """Split `text` into overlapping sentence windows, run `extractor(window, llm)`
    on each, and union: concatenate mentions and OFFSET each window's relationship /
    attribute indices by the running mention count. A window whose extractor raises
    is skipped (its mentions just don't contribute), never fatal to the doc.

    `extractor` is the same callable the single-pass path uses (`extract.extract`,
    or a rebel/gliner closure) -- it still honors LITERAL_ATTRS / vocab / recall
    gates internally per window."""
    size, overlap = _chunk_params()
    windows = sentence_windows(split_sentences(text), size, overlap)
    merged_mentions: list[Mention] = []
    merged_rels: list[Relationship] = []
    merged_attrs: list[Attribute] = []
    for window in windows:
        try:
            ex = extractor(window, llm)
        except Exception:
            continue  # a bad window degrades recall, never sinks the doc
        base = len(merged_mentions)  # captured BEFORE the append -> correct per-window offset
        merged_mentions += ex.mentions
        merged_rels += [
            Relationship(r.subj + base, r.predicate, r.obj + base) for r in ex.relationships
        ]
        merged_attrs += [
            Attribute(a.subj + base, a.predicate, a.value, a.typ)
            for a in getattr(ex, "attributes", ())
        ]
    return Extraction(mentions=merged_mentions, relationships=merged_rels, attributes=merged_attrs)
```

- [ ] **Step 4: Run tests, verify pass** (box-safe). Expected: 11 passed total. Then `ruff check goldengraph/chunk_extract.py`.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/chunk_extract.py tests/test_chunk_extract.py
git commit -m "feat(goldengraph): chunk_extract union + config gates (GOLDENGRAPH_CHUNK_EXTRACT/_SENTENCES/_OVERLAP)"
```

---

## Task 3: wire the gate into `_prepare_doc`

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/ingest.py` (import near line 19-23; call site line 671)
- Test: `packages/python/goldengraph/tests/test_chunk_extract.py`

- [ ] **Step 1: Add the failing wiring test.** Append to `tests/test_chunk_extract.py`:

```python
def test_prepare_doc_uses_chunking_only_when_gated(monkeypatch):
    """_prepare_doc calls the extractor once when off, once-per-window when on."""
    from goldengraph import ingest
    from goldengraph.extract import Extraction, Mention

    calls = {"n": 0}

    def counting_extractor(text, llm=None):
        calls["n"] += 1
        return Extraction(mentions=[Mention(name="X", typ="org")], relationships=[])

    # identity resolver: one entity per mention (no goldenmatch needed)
    from goldengraph.resolve import ResolvedEntity

    def resolver(mentions):
        return [
            ResolvedEntity(
                local_id=i, canonical_name=m.name, typ=m.typ,
                surface_names=[m.name], record_keys=[], member_idx=[i],
            )
            for i, m in enumerate(mentions)
        ]

    text = "One here. Two here. Three here. Four here. Five here. Six here. Seven here."

    # gate OFF -> single call
    monkeypatch.delenv("GOLDENGRAPH_CHUNK_EXTRACT", raising=False)
    calls["n"] = 0
    ingest._prepare_doc(text, llm=None, resolver=resolver, profile_fps=False,
                        extractor=counting_extractor)
    assert calls["n"] == 1

    # gate ON, size=3 overlap=1 (stride 2) over 7 sentences -> windows [0:3],[2:5],[4:7] = 3 calls
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_EXTRACT", "1")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_SENTENCES", "3")
    monkeypatch.setenv("GOLDENGRAPH_CHUNK_OVERLAP", "1")
    calls["n"] = 0
    ingest._prepare_doc(text, llm=None, resolver=resolver, profile_fps=False,
                        extractor=counting_extractor)
    assert calls["n"] == 3
```

> Note: `ResolvedEntity`'s fields are verified against `goldengraph/resolve.py:20-27` — `local_id, canonical_name, typ, surface_names, record_keys, member_idx` (exactly as used above). The resolver just returns one entity per mention so no goldenmatch import is needed; the assertion that matters is the extractor call count.

- [ ] **Step 2: Run, verify fail.** Expected: gate-ON assertion fails (`assert 1 == 3`) because `_prepare_doc` still calls the extractor once.

- [ ] **Step 3: Wire the gate.** In `ingest.py`, add the import beside the existing extract imports (after line 20 `from .extract import extract as _extract`):

```python
from .chunk_extract import chunk_extract, chunk_extract_enabled
```

Then replace line 671:

```python
        extraction = (extractor or _extract)(text, llm)
```

with:

```python
        _extractor = extractor or _extract
        extraction = (
            chunk_extract(text, llm, _extractor)
            if chunk_extract_enabled()
            else _extractor(text, llm)
        )
```

- [ ] **Step 4: Run tests, verify pass.** Run the full `tests/test_chunk_extract.py` (box-safe). Expected: 12 passed. Then a quick regression on the neighbouring extract tests to prove the off-path is unchanged:

```bash
PYTHONPATH="D:/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENGRAPH_NATIVE=0 \
  "$PY" -m pytest tests/test_extract_recall.py tests/test_chunk_extract.py -q -p no:cacheprovider
```
Then `ruff check goldengraph/ingest.py goldengraph/chunk_extract.py`.

- [ ] **Step 5: Commit.**

```bash
git add goldengraph/ingest.py tests/test_chunk_extract.py
git commit -m "feat(goldengraph): gate chunked extraction into _prepare_doc (off = single-pass unchanged)"
```

---

## Task 4: Modal wiki measurement + verdict

**Files:** Create `docs/superpowers/reports/2026-07-01-chunked-extraction-verdict.md`.

Run these yourself (Modal, `PYTHONIOENCODING=utf-8 PYTHONUTF8=1`, `--detach --spawn`, distinct `--n` so the `gg-bench-cache` volume doesn't clobber). The substrate branch of `scripts/distill/modal_bench.py` honors the env passed via `--opts`.

- [ ] **Step 1: Fire the control + sweep legs** (wiki, `name_ci`, aliased aligner on `main`):

```bash
M="/d/show_case/goldenmatch/.venv/Scripts/modal.exe"
BASE=$'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci'
# control (chunking off) -- establishes the real aliased baseline
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 80 \
  --opts "$BASE" --spawn
# chunked (4,1)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 81 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=4\nGOLDENGRAPH_CHUNK_OVERLAP=1' --spawn
# chunked (3,1) -- more granular
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 82 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=3\nGOLDENGRAPH_CHUNK_OVERLAP=1' --spawn
# chunked (6,2) -- larger window (recover cross-window edges)
$M run --detach scripts/distill/modal_bench.py --engine goldengraph --eval substrate --n 83 \
  --opts $'GOLDENGRAPH_SUBSTRATE_CORPUS=wiki\nGOLDENGRAPH_XDOC_KEY=name_ci\nGOLDENGRAPH_CHUNK_EXTRACT=1\nGOLDENGRAPH_CHUNK_SENTENCES=6\nGOLDENGRAPH_CHUNK_OVERLAP=2' --spawn
```

Poll the `gg-bench-cache` volume for `results/substrate_8{0,1,2,3}_*.md` (Monitor the volume; do not sit in a foreground loop). Look for the `[substrate-wiki]` lines: `coverage`, `R(B)`, `P(B)`, `components`.

- [ ] **Step 2: Read the four legs.** Tabulate coverage / R(B) / P(B) / components for control vs each `(size, overlap)`.

- [ ] **Step 3: Write the verdict** `docs/superpowers/reports/2026-07-01-chunked-extraction-verdict.md`. State the control baseline (aliased), the sweep table, and the read against the falsifiable bar from the spec:
  - **WIN:** coverage ↑ AND R(B) ↑ with components not materially worse and P(B) ~1.0. Name the winning `(size, overlap)`.
  - **REFUTED:** coverage flat/down, or coverage up only by fragmenting (components blow up). Ship gate default-off, escalate to GLiNER.
  - Either way, report the sweep *shape* (does smaller window buy entities at the cost of edges?).

- [ ] **Step 4: Commit** the report.

```bash
git add docs/superpowers/reports/2026-07-01-chunked-extraction-verdict.md
git commit -m "docs(goldengraph): chunked-extraction verdict (wiki sweep)"
```

---

## Completion

Use superpowers:finishing-a-development-branch: run the box-safe `tests/test_chunk_extract.py` suite, then open a PR (base `main`) and arm auto-merge (`gh pr merge --auto --squash`). The PR ships the gate default-off regardless of the verdict (an opt-in recall knob); the verdict records whether the substrate improved. If REFUTED, GLiNER (`extract_local.gliner_extractor`) is the next extraction-recall lever.
