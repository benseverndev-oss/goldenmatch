# Substrate Config Suggester (SP-C) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `erkgbench/substrate_suggest.py` — an LLM corpus-reader that proposes the `for_profile` inputs it can't self-derive (homographs / known schema / vocabs), plus a gold-verified self-verify that only accepts a proposal if it beats the deterministic baseline.

**Architecture:** One pure module. `propose_corpus_flags` makes a schema-constrained LLM call (injected `chat`) over a doc sample → `CorpusFlags`; `_parse_flags` salvages the JSON and defaults to all-off on garbage (never raises). `suggest_substrate_config` scores `for_profile(flags-off)` vs `for_profile(**flags)` via an injected `build_and_score` and accepts the proposal only if `_score` improves — so the LLM can never do worse than deterministic. Both the `chat` and the build are injected, so the whole flow is box-TDD'd with fakes; a thin `_unverified` MCP wrapper exposes the perception only.

**Tech Stack:** Python stdlib (`dataclasses`, `json`), pytest. Imports `goldengraph.config` (SP-B1) + `erkgbench.substrate_tuner` (SP-B2).

**Spec:** `docs/superpowers/specs/2026-07-02-substrate-suggest-design.md`

**Branch:** `feat/substrate-suggest` (off `origin/main`). SP-B2 (#1375) is on main. The reset fix (#1380) is armed-not-merged — **the box tests don't need it** (fake build); **rebase onto `origin/main` after #1380 merges before the Modal smoke** (Task 6) so the real verify is reproducible.

---

## Files

- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py` — the whole SP-C surface.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py` — the homograph-corpus runner (Modal, not box-TDD).
- **Modify** `scripts/distill/modal_bench.py` — add a `suggest` eval mode.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py` — pure box-safe tests (fake `chat` + fake `build_and_score`).

## Test runner (box-safe)

From the er-kg-bench dir (`goldengraph` needs to be on the path too — it's a sibling package):

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_substrate_suggest.py -q
```

## Ground-truth facts (verified — do not re-derive)

- **`for_profile(profile, *, has_known_schema=False, expect_homographs=False, relation_vocab=())`** — does NOT take `entity_type_vocab`. Sets `entity_type_canon=True` iff `expect_homographs`. `SubstrateConfig` HAS an `entity_type_vocab` field.
- **`_score(scorecard)`** (substrate_tuner) = `relational.f1 (+ presence.coverage when presence not None)`. On the engineered corpus presence is None → `_score` = `relational.f1`.
- **`build_and_score_real(config, dataset)`**, `dataset=(docs, gold, qid_aliases)`; passes `qid_aliases=None` straight through → `substrate_scorecard` presence=None; only preserves engineered doc-ids when `docs[0]` has `.text`.
- **`substrate_scorecard`** shape: `{"presence": {"coverage"}|None, "relational": {"f1","recall","precision"}, "connectivity": {...}, "coherence": {...}}`.
- **Engineered corpus:** `generate_engineered(*, seed, n_questions, ambiguity)` honors `GOLDENGRAPH_BENCH_HOMOGRAPH=k`; `emit_gold_mentions(corpus.documents)` → gold with `src::rel::dst` doc-ids; `corpus.documents` carry `.text`/`.id`.

---

### Task 1: `CorpusFlags` + `_parse_flags` (JSON salvage)

**Files:**
- Create: `erkgbench/substrate_suggest.py`
- Test: `tests/test_substrate_suggest.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_substrate_suggest.py`:

```python
"""SP-C substrate config suggester: parse / propose / self-verify (pure, box-safe with fakes)."""
from __future__ import annotations

from collections import namedtuple

from goldengraph.config import CorpusProfile, SubstrateConfig, for_profile

from erkgbench import substrate_suggest as ss


def test_parse_flags_clean_json():
    f = ss._parse_flags('{"expect_homographs": true, "has_known_schema": false, '
                        '"relation_vocab": ["acquired", "works_at"], "entity_type_vocab": ["person"]}')
    assert f.expect_homographs is True and f.has_known_schema is False
    assert f.relation_vocab == ("acquired", "works_at") and f.entity_type_vocab == ("person",)


def test_parse_flags_fenced():
    raw = "```json\n{\"expect_homographs\": true}\n```"
    assert ss._parse_flags(raw).expect_homographs is True


def test_parse_flags_garbage_defaults():
    for raw in ("not json", "", "{oops", "[1,2,3]"):
        f = ss._parse_flags(raw)
        assert f == ss.CorpusFlags()  # all-off default, never raises


def test_parse_flags_drops_unknown_and_coerces():
    f = ss._parse_flags('{"expect_homographs": 1, "bogus": "x", "relation_vocab": "acquired"}')
    assert f.expect_homographs is True          # truthy coerced to bool
    assert f.relation_vocab == ()               # a non-list vocab is ignored, not split
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k parse_flags`. Expected: `ModuleNotFoundError: erkgbench.substrate_suggest`.

- [ ] **Step 3: Implement**

Create `erkgbench/substrate_suggest.py`:

```python
"""SP-C substrate config suggester.

An LLM reads a sample of the corpus and proposes the corpus-characteristic inputs `for_profile` cannot
self-derive (homographs / known schema / vocabs). `suggest_substrate_config` then measurement-verifies
the resulting config against the flags-off baseline and accepts only a net improvement -- so the LLM can
never do worse than the deterministic baseline. `chat` and `build_and_score` are injected (box-testable
with fakes). See docs/superpowers/specs/2026-07-02-substrate-suggest-design.md.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, replace

from goldengraph.config import SubstrateConfig, for_profile, profile_corpus

from erkgbench.substrate_tuner import _score, build_and_score_real  # noqa: F401 (real adapter for the runner)


@dataclass(frozen=True)
class CorpusFlags:
    """The corpus-characteristic inputs `for_profile` needs but can't self-derive. All-off default =
    the deterministic baseline (a bad/empty LLM read degrades to it)."""
    expect_homographs: bool = False
    has_known_schema: bool = False
    relation_vocab: tuple[str, ...] = ()
    entity_type_vocab: tuple[str, ...] = ()


def _strip_fence(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s.strip()


def _vocab(v) -> tuple[str, ...]:
    """A list of non-empty strings -> lowercased tuple; anything else -> ()."""
    if not isinstance(v, list):
        return ()
    return tuple(s.strip().lower() for s in v if isinstance(s, str) and s.strip())


def _parse_flags(raw: str) -> CorpusFlags:
    """Salvage the LLM's JSON into CorpusFlags. Fence-tolerant; drops unknown keys; coerces types;
    returns the all-off default on ANYTHING unparseable (never raises) so a bad read == baseline."""
    try:
        data = json.loads(_strip_fence(raw))
    except Exception:  # noqa: BLE001 -- any parse failure degrades to the deterministic baseline
        return CorpusFlags()
    if not isinstance(data, dict):
        return CorpusFlags()
    return CorpusFlags(
        expect_homographs=bool(data.get("expect_homographs", False)),
        has_known_schema=bool(data.get("has_known_schema", False)),
        relation_vocab=_vocab(data.get("relation_vocab")),
        entity_type_vocab=_vocab(data.get("entity_type_vocab")),
    )
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k parse_flags`. Expected: PASS (4).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py
git commit -m "feat(erkgbench): CorpusFlags + _parse_flags JSON salvage (SP-C task 1)"
```

---

### Task 2: `propose_corpus_flags` (injected `chat`)

**Files:**
- Modify: `erkgbench/substrate_suggest.py`
- Test: `tests/test_substrate_suggest.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_propose_flags_calls_chat_once_and_parses():
    calls = []

    def fake_chat(prompt):
        calls.append(prompt)
        return '{"expect_homographs": true, "entity_type_vocab": ["person", "organization"]}'

    f = ss.propose_corpus_flags(["Apple grows.", "Apple ships iPhones."], chat=fake_chat)
    assert len(calls) == 1
    assert "Apple grows." in calls[0]  # the sample is in the prompt
    assert f.expect_homographs is True and f.entity_type_vocab == ("person", "organization")


def test_propose_flags_bad_read_is_default():
    f = ss.propose_corpus_flags(["doc"], chat=lambda p: "sorry I cannot help")
    assert f == ss.CorpusFlags()
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k propose_flags`. Expected: FAIL — no `propose_corpus_flags`.

- [ ] **Step 3: Implement** — add to `substrate_suggest.py`:

```python
_PROMPT = """You are analyzing a document corpus to configure an entity-resolution pipeline.
Read the sample documents and answer STRICTLY as a JSON object with these keys:
- "expect_homographs": true if the corpus contains DISTINCT entities that share the SAME name
  (e.g. Apple the company vs Apple the fruit), else false.
- "has_known_schema": true if the relationships form a small, closed set worth constraining, else false.
- "relation_vocab": a list of the canonical relation names if has_known_schema, else [].
- "entity_type_vocab": a list of coarse entity types (e.g. ["person","organization","concept"]) if
  expect_homographs, else [].
Output ONLY the JSON object, no prose.

SAMPLE DOCUMENTS:
{sample}
"""


def propose_corpus_flags(sample_docs, *, chat) -> CorpusFlags:
    """One schema-constrained LLM call (`chat(prompt) -> str`) over the sample -> CorpusFlags. Any
    unparseable output degrades to the all-off default (see _parse_flags)."""
    sample = "\n\n".join(f"[doc {i}] {t}" for i, t in enumerate(sample_docs))
    return _parse_flags(chat(_PROMPT.format(sample=sample)))
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k propose_flags`. Expected: PASS (2).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py
git commit -m "feat(erkgbench): propose_corpus_flags (schema-constrained LLM read, injected chat) (SP-C task 2)"
```

---

### Task 3: `suggest_substrate_config` self-verify

**Files:**
- Modify: `erkgbench/substrate_suggest.py`
- Test: `tests/test_substrate_suggest.py`

- [ ] **Step 1: Write the failing tests**

```python
_Doc = namedtuple("_Doc", "text")


def _sc(rel_f1):
    """A presence=None scorecard (engineered path) with a given relational F1 -> _score == rel_f1."""
    return {"presence": None, "relational": {"f1": rel_f1, "recall": rel_f1, "precision": 1.0},
            "connectivity": {"coverage": None, "f1": None, "edge_recall": 0.9},
            "coherence": {"components": 1, "largest_fraction": 1.0}}


def _short_profile():
    return CorpusProfile(n_docs=4, mean_sentences_per_doc=2.0, mean_chars_per_doc=40.0)


def _fake_chat_homograph(_prompt):
    return '{"expect_homographs": true, "entity_type_vocab": ["person", "organization"]}'


def _bykey(name_ci_score, name_ci_type_score):
    """Fake build_and_score keyed on config.xdoc_key so accept/fallback is deterministic."""
    def fake(config, dataset):
        return _sc(name_ci_type_score if config.xdoc_key == "name_ci_type" else name_ci_score)
    return fake


def test_suggest_accepts_when_proposed_beats_baseline():
    docs = [_Doc("Apple grows."), _Doc("Apple Inc ships.")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.40, 0.70), chat=_fake_chat_homograph)
    assert res.accepted is True and res.config.xdoc_key == "name_ci_type"


def test_suggest_falls_back_when_proposed_worse():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.70, 0.40), chat=_fake_chat_homograph)
    assert res.accepted is False
    assert res.config == for_profile(_short_profile())   # exactly the baseline, no vocab stamped


def test_suggest_homograph_stamps_type_and_vocab():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.40, 0.70), chat=_fake_chat_homograph)
    assert res.config.xdoc_key == "name_ci_type" and res.config.entity_type_canon is True
    assert res.config.entity_type_vocab == ("person", "organization")


def test_suggest_schema_only_vocab_not_stamped():
    # expect_homographs False -> canon OFF -> entity_type_vocab must NOT be stamped even though accepted
    def chat(_p):
        return '{"has_known_schema": true, "relation_vocab": ["acquired"], "entity_type_vocab": ["person"]}'
    docs = [_Doc("a"), _Doc("b")]
    # proposed differs from baseline via schema_canon (xdoc_key stays name_ci in BOTH) -> key the fake on
    # schema_canon instead of xdoc_key so proposed scores higher.
    def fake(config, dataset):
        return _sc(0.70 if config.schema_canon else 0.40)
    res = ss.suggest_substrate_config(docs, gold=[], qid_aliases=None, profile=_short_profile(),
                                      build_and_score=fake, chat=chat)
    assert res.accepted is True and res.config.schema_canon is True
    assert res.config.entity_type_canon is False and res.config.entity_type_vocab == ()


def test_suggest_bad_llm_read_is_safe():
    docs = [_Doc("a"), _Doc("b")]
    res = ss.suggest_substrate_config(
        docs, gold=[], qid_aliases=None, profile=_short_profile(),
        build_and_score=_bykey(0.50, 0.50), chat=lambda p: "garbage")
    # flags default -> proposed == baseline -> equal scores -> not accepted -> baseline
    assert res.accepted is False and res.config == for_profile(_short_profile())
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k suggest_`. Expected: FAIL — no `suggest_substrate_config`.

- [ ] **Step 3: Implement** — add to `substrate_suggest.py`:

```python
@dataclass(frozen=True)
class SuggestResult:
    config: SubstrateConfig
    flags: CorpusFlags
    accepted: bool
    baseline_scorecard: dict
    proposed_scorecard: dict


def suggest_substrate_config(docs, *, gold, qid_aliases, build_and_score, chat,
                             profile=None, sample_docs=6) -> SuggestResult:
    """LLM proposes for_profile flags from a corpus sample; accept the proposed config ONLY if it beats
    the flags-off baseline on `_score` (else fall back to baseline). `docs` must be Document objects
    (build_and_score needs .text/.id). `build_and_score(config, (docs, gold, qid_aliases))` injected."""
    profile = profile or profile_corpus([d.text for d in docs])
    sample = [d.text for d in docs[:sample_docs]]
    flags = propose_corpus_flags(sample, chat=chat)

    baseline = for_profile(profile)
    proposed = for_profile(profile, expect_homographs=flags.expect_homographs,
                           has_known_schema=flags.has_known_schema, relation_vocab=flags.relation_vocab)
    dataset = (docs, gold, qid_aliases)
    base_sc = build_and_score(baseline, dataset)
    prop_sc = build_and_score(proposed, dataset)
    accepted = _score(prop_sc) > _score(base_sc)
    winner = proposed if accepted else baseline
    # Stamp entity_type_vocab ONLY on an accepted homograph winner (canon is on only via
    # expect_homographs; `accepted` alone does NOT imply it -- a schema-only proposal can be accepted
    # with canon off). Both terms required -> never dirties a canon-off config.
    if accepted and flags.expect_homographs and flags.entity_type_vocab:
        winner = replace(winner, entity_type_vocab=flags.entity_type_vocab)
    return SuggestResult(winner, flags, accepted, base_sc, prop_sc)
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k suggest_`. Expected: PASS (5).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py
git commit -m "feat(erkgbench): suggest_substrate_config self-verify (accept iff beats baseline; gated vocab) (SP-C task 3)"
```

---

### Task 4: `suggest_substrate_config_unverified` (no-gold MCP surface)

**Files:**
- Modify: `erkgbench/substrate_suggest.py`
- Test: `tests/test_substrate_suggest.py`

- [ ] **Step 1: Write the failing test**

```python
def test_mcp_unverified_returns_config_and_flag():
    out = ss.suggest_substrate_config_unverified(
        ["Apple grows.", "Apple Inc ships."], chat=_fake_chat_homograph)
    assert out["verified"] is False
    assert out["config"].xdoc_key == "name_ci_type"           # homograph -> name_ci_type
    assert out["config"].entity_type_vocab == ("person", "organization")
    assert out["flags"].expect_homographs is True and "verify" in out["note"].lower()


def test_mcp_unverified_bad_read_is_baseline():
    out = ss.suggest_substrate_config_unverified(["doc"], chat=lambda p: "nope")
    assert out["config"] == for_profile(profile_corpus(["doc"]))  # baseline, no vocab
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k mcp_unverified`. Expected: FAIL — no `suggest_substrate_config_unverified`.

- [ ] **Step 3: Implement** — add to `substrate_suggest.py` (`docs` here are text STRINGS — the MCP caller has no Document objects):

```python
def suggest_substrate_config_unverified(sample_texts, *, chat, sample_docs=6) -> dict:
    """No-gold MCP surface: return the LLM's PERCEIVED config from a corpus sample, labeled UNVERIFIED
    (an MCP caller has no gold to self-verify). `sample_texts` = list of raw doc strings."""
    sample = list(sample_texts[:sample_docs])
    flags = propose_corpus_flags(sample, chat=chat)
    cfg = for_profile(profile_corpus(sample), expect_homographs=flags.expect_homographs,
                     has_known_schema=flags.has_known_schema, relation_vocab=flags.relation_vocab)
    if flags.expect_homographs and flags.entity_type_vocab:
        cfg = replace(cfg, entity_type_vocab=flags.entity_type_vocab)
    return {"config": cfg, "flags": flags, "verified": False,
            "note": "LLM perception only; measurement-verify with gold on the bench"}
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k mcp_unverified`. Expected: PASS (2).

- [ ] **Step 5: Full file green + commit**

```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" PYTHONIOENCODING=utf-8 PYTHONUTF8=1 \
  POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe \
  -m pytest tests/test_substrate_suggest.py -q   # all 13 green
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/substrate_suggest.py \
        packages/python/goldenmatch/benchmarks/er-kg-bench/tests/test_substrate_suggest.py
git commit -m "feat(erkgbench): suggest_substrate_config_unverified no-gold MCP surface (SP-C task 4)"
```

---

### Task 5: Runner + `modal_bench.py` `suggest` mode (homograph corpus, Modal-smoke only)

**Files:**
- Create: `erkgbench/run_substrate_suggest.py`
- Modify: `scripts/distill/modal_bench.py`

- [ ] **Step 1: Create the runner** (`run_substrate_suggest.py`)

```python
"""SP-C suggester smoke: run suggest_substrate_config on the HOMOGRAPH engineered corpus with the real
LLM + build. The LLM should detect the injected homographs -> expect_homographs -> name_ci_type, and the
self-verify should ACCEPT it (beats the naive name_ci baseline on relational F1 via precision recovery).
Needs the native store + an LLM -> Modal only.
"""
from __future__ import annotations

import argparse
import os

from erkgbench.qa_e2e.engineered import emit_gold_mentions, generate_engineered
from erkgbench.substrate_suggest import build_and_score_real, suggest_substrate_config


def _chat(prompt: str) -> str:
    from goldengraph.llm import OpenAIClient

    # json_mode=True (response_format=json_object): the 7B homograph perception is exactly the weak-OSS-
    # model "emits prose/fenced/invalid JSON" failure mode -- forcing JSON avoids a null result being
    # MISATTRIBUTED to "the LLM can't perceive homographs" when it's really a fixable formatting artifact.
    return OpenAIClient(model=os.environ.get("OPENAI_MODEL") or "gpt-4o-mini")._chat(prompt, json_mode=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="SP-C suggester smoke (homograph engineered corpus).")
    ap.add_argument("--homograph", type=int, default=4)
    ap.add_argument("--ambiguity", type=float, default=0.0)
    ap.add_argument("--out-md", default="SUBSTRATE_SUGGEST.md")
    args = ap.parse_args()

    os.environ["GOLDENGRAPH_BENCH_HOMOGRAPH"] = str(args.homograph)
    os.environ.pop("GOLDENGRAPH_BENCH_COOCCUR", None)
    corpus = generate_engineered(seed=20260620, n_questions=1, ambiguity=args.ambiguity)
    gold = emit_gold_mentions(corpus.documents)
    # PASS Document objects (with .text/.id), NOT [d.text] -- build_and_score_real needs the real doc-ids.
    res = suggest_substrate_config(corpus.documents, gold=gold, qid_aliases=None,
                                   build_and_score=build_and_score_real, chat=_chat)

    b, p = res.baseline_scorecard, res.proposed_scorecard
    print(f"[suggest] accepted={res.accepted} flags={res.flags} "
          f"baseline_F1={b['relational']['f1']:.4f} proposed_F1={p['relational']['f1']:.4f} "
          f"winner_xdoc={res.config.xdoc_key} canon={res.config.entity_type_canon}", flush=True)
    md = (
        "# SP-C Suggester Smoke (homograph engineered)\n\n"
        f"- accepted: `{res.accepted}`  flags: `{res.flags}`\n"
        f"- baseline relational F1: {b['relational']['f1']:.4f} (P={b['relational']['precision']:.4f})\n"
        f"- proposed relational F1: {p['relational']['f1']:.4f} (P={p['relational']['precision']:.4f})\n"
        f"- winner: xdoc_key=`{res.config.xdoc_key}` entity_type_canon={res.config.entity_type_canon}\n"
    )
    with open(args.out_md, "w", encoding="utf-8") as fh:
        fh.write(md)
    print("\n" + md, flush=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Add the `suggest` eval mode to `modal_bench.py`**

In `_EVAL` (after the `tuner` entry):
```python
    "suggest": ("erkgbench.run_substrate_suggest", []),  # SP-C suggester smoke (handled specially)
```

In `_bench_impl`, after the `tuner` branch:
```python
    if eval == "suggest":
        proc = subprocess.run(
            ["python", "-m", "erkgbench.run_substrate_suggest", "--out-md", out_md],
            cwd=_BENCH, env=env, capture_output=True, text=True, check=True,
        )
        results = pathlib.Path(out_md).read_text() if os.path.exists(out_md) else "(no results md)"
        return _persist(eval, n, f"{engine}-{chat}", proc.stdout + "\n\n===== RESULTS_MD =====\n" + results)
```

In `main()`, add `"suggest"` to the tag tuple: `if eval in ("end_to_end", "substrate", "tuner", "suggest")`.

- [ ] **Step 3: Static-check** the runner parses + imports box-safe (heavy imports are function-local / engineered is pure):
```bash
cd /d/show_case/gg-local-llm/packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "import ast; ast.parse(open('erkgbench/run_substrate_suggest.py').read()); import erkgbench.run_substrate_suggest; print('ok')"
```
Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/run_substrate_suggest.py \
        scripts/distill/modal_bench.py
git commit -m "feat(erkgbench): SP-C suggester runner + modal_bench suggest mode (homograph corpus) (SP-C task 5)"
```

---

### Task 6: Finish the branch + Modal smoke

- [ ] **Step 1: Rebase onto main** once #1380 (the LLM reset in `build_and_score_real`) has merged, so the verify is reproducible:
  ```bash
  unset GH_TOKEN; export GH_TOKEN=$(gh auth token --user benzsevern)
  git fetch origin main && git rebase origin/main
  ```
  Re-run the box tests (13 green). If #1380 hasn't merged, note it and land the smoke later.
- [ ] **Step 2: Lint** — `ruff check` the three files; fix findings.
- [ ] **Step 3: Modal smoke** (needs Infisical Modal creds; see `feedback_infisical_usage`). Fire `--eval suggest --n 1 --spawn --opts "GOLDENGRAPH_LLM_SEED=42"`, detach, poll `results/suggest_1_goldengraph-qwen2.5-7b-instruct.md`. **Expected:** `accepted=True`, `expect_homographs=True`, winner `xdoc_key=name_ci_type`, proposed F1 > baseline F1. If the 7B doesn't detect homographs (flags stay off → proposed==baseline → accepted=False), that's an HONEST finding (the perception is the bottleneck) — record it, try DeepSeek-V3 (`--chat deepseek-chat`) as the ceiling ref before concluding.
- [ ] **Step 4: Verdict** — write `docs/superpowers/reports/2026-07-02-suggester-smoke-verdict.md` (did the LLM perceive the homographs; did the self-verify accept; baseline-vs-proposed numbers).
- [ ] **Step 5: PR + arm** — push, open PR base `main`, arm `--auto`, STOP.
- [ ] **Step 6: Memory** — append SP-C shipped to `project_goldengraph_local_oss_llm_lane.md`.

---

## Notes for the implementer

- **Box-safe only.** Run just `tests/test_substrate_suggest.py`. The pure surface has NO Modal/LLM/native dependency (fakes injected). The runner + `build_and_score_real`'s heavy imports stay function-local.
- **`docs` to `suggest_substrate_config` are Document objects** (need `.text`/`.id`); the *sample text* passed to the LLM is `[d.text for d in docs[:n]]`. The MCP `_unverified` variant takes raw text STRINGS (an MCP caller has no Documents).
- **The LLM only proposes; measurement decides.** If a proposal doesn't beat baseline the winner is exactly `for_profile(profile)` — verified by `test_suggest_falls_back_when_proposed_worse`. Never stamp `entity_type_vocab` unless BOTH `accepted` and `flags.expect_homographs`.
- **`_score` on the engineered (presence=None) corpus is `relational.f1`.** The homograph win is a precision recovery that raises F1.
