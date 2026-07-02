# Substrate Config Surface (SP-B1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship `goldengraph/config.py` — a `SubstrateConfig` value object + `apply()` env-materializer + a `for_profile` rule-table that picks a sane-default config from cheap corpus signals — the deterministic foundation SP-B2/SP-C build on.

**Architecture:** One new pure module. `SubstrateConfig` (frozen, validated dataclass) mirrors the substrate levers; `to_env()` renders a *total* map over `MANAGED_ENV_VARS` (12 field vars + a `SCHEMA_DISCOVER` leak-guard); `apply()` snapshots/sets/restores those keys around a build; `profile_corpus()` derives cheap signals from raw text; `for_profile()` encodes the arc's measured findings as deterministic rules. No LLM, no build, no native imports in the module.

**Tech Stack:** Python stdlib only (`dataclasses`, `contextlib`, `os`, `re`), pytest.

**Spec:** `docs/superpowers/specs/2026-07-02-substrate-config-surface-design.md`

**Branch:** `feat/substrate-config` (already created off `origin/main`).

---

## Files

- **Create** `packages/python/goldengraph/goldengraph/config.py` — the whole SP-B1 surface.
- **Create** `packages/python/goldengraph/tests/test_config.py` — pure box-safe tests.

## Test runner (box-safe)

From the goldengraph package dir. `goldengraph` resolves to THIS worktree (verified — no `PYTHONPATH` needed); the env flags avoid the polars WMI hang + stale native wheel:

```bash
cd /d/show_case/gg-local-llm/packages/python/goldengraph
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_config.py -q
```

> NOTE: importing `goldengraph.config` runs `goldengraph/__init__.py` (which imports the LLM/embed path) — this is why the env flags are needed, and it is the same wrapping every goldengraph test wears. `config.py` itself must NOT `import` any of `.llm`, `.embed`, `.chunk_extract`, `.ingest` (keep it stdlib-only).
>
> **Pre-flight (run once before Task 1):** confirm `goldengraph` resolves to THIS worktree, not a stale cross-repo editable install (the `reference_py_worktree_test_native_skew` footgun):
> ```bash
> cd /d/show_case/gg-local-llm/packages/python/goldengraph
> POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe -c "import goldengraph; print(goldengraph.__file__)"
> ```
> Expected: a path under `D:\show_case\gg-local-llm\...`. If it prints a different repo, prepend `PYTHONPATH="$PWD"` to every test command below.

## Ground-truth lever facts (verified against source — do not re-derive)

- `GOLDENGRAPH_XDOC_KEY` ∈ `{"", "name", "name_ci", "name_ci_type"}`; `""` == unset == `(name, typ)` (resolve.py `_key_payload`).
- `GOLDENGRAPH_CHUNK_EXTRACT` bool; `GOLDENGRAPH_CHUNK_SENTENCES` default 6; `GOLDENGRAPH_CHUNK_OVERLAP` default 2.
- `GOLDENGRAPH_ENTITY_TYPE_CANON` bool; `GOLDENGRAPH_ENTITY_TYPE_VOCAB` csv, `""` → default `("person","organization","concept","other")`.
- `GOLDENGRAPH_SCHEMA_CANON` bool; `GOLDENGRAPH_RELATION_VOCAB` csv.
- `GOLDENGRAPH_EXTRACTOR` ∈ `{api, rebel, gliner}` (engine also accepts `""`/`"llm"` as api; the config rejects those and canonicalizes to `"api"`).
- `GOLDENGRAPH_RELATION_REPROMPT`, `_REBEL_FUSE`, `_EXTRACT_RECALL` — bool gates (all REFUTED levers).
- `GOLDENGRAPH_SCHEMA_DISCOVER` bool — the leak-guard: ambient `=1` makes `ingest_corpus` discover schema and IGNORE `RELATION_VOCAB`. `to_env()` always emits `"0"`.
- Every gate reads off as `not in ("0", "false", "")`, so emitting `"0"` (or `""` for `XDOC_KEY`/vocabs) reproduces the engine default.

---

### Task 1: `SubstrateConfig` dataclass + validation

**Files:**
- Create: `packages/python/goldengraph/goldengraph/config.py`
- Test: `packages/python/goldengraph/tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_config.py`:

```python
"""SP-B1 substrate config surface: SubstrateConfig / apply() / for_profile (pure, box-safe)."""
from __future__ import annotations

import os

import pytest

from goldengraph.config import (
    MANAGED_ENV_VARS,
    CorpusProfile,
    SubstrateConfig,
    for_profile,
    profile_corpus,
)


def test_config_defaults_construct():
    c = SubstrateConfig()
    assert c.xdoc_key == "" and c.chunk_extract is False and c.extractor == "api"
    assert c.chunk_sentences == 6 and c.chunk_overlap == 2


def test_config_rejects_bad_xdoc_key():
    with pytest.raises(ValueError):
        SubstrateConfig(xdoc_key="nope")


def test_config_rejects_bad_extractor():
    with pytest.raises(ValueError):
        SubstrateConfig(extractor="llm")  # engine alias, but config demands canonical "api"


def test_config_rejects_overlap_ge_sentences():
    with pytest.raises(ValueError):
        SubstrateConfig(chunk_sentences=4, chunk_overlap=4)


def test_config_is_frozen():
    c = SubstrateConfig()
    with pytest.raises(Exception):
        c.xdoc_key = "name_ci"  # frozen dataclass
```

- [ ] **Step 2: Run to verify it fails**

Run box-safe command with `-k config`. Expected: FAIL — `ModuleNotFoundError: No module named 'goldengraph.config'`.

- [ ] **Step 3: Implement the dataclass**

Create `goldengraph/config.py`:

```python
"""SP-B1 substrate config surface.

A `SubstrateConfig` is a frozen, validated value object over the goldengraph substrate levers.
`apply()` materializes it into the `GOLDENGRAPH_*` process env around a build (working WITH the
existing call-time env reads); `for_profile()` picks a sane default from cheap corpus signals.

Pure: stdlib only. MUST NOT import .llm / .embed / .chunk_extract / .ingest -- keep the LLM/build
path out of this module. See docs/superpowers/specs/2026-07-02-substrate-config-surface-design.md.
"""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, fields

_XDOC_KEYS = ("", "name", "name_ci", "name_ci_type")
_EXTRACTORS = ("api", "rebel", "gliner")


@dataclass(frozen=True)
class SubstrateConfig:
    """Immutable substrate-builder configuration. Defaults reproduce today's engine behavior (a
    default config materializes to a no-op env). Refuted levers are present but default off and are
    never selected by `for_profile`."""
    xdoc_key: str = ""                       # "" | name | name_ci | name_ci_type  ("" = (name,typ))
    chunk_extract: bool = False
    chunk_sentences: int = 6
    chunk_overlap: int = 2
    entity_type_canon: bool = False
    entity_type_vocab: tuple[str, ...] = ()  # () = engine default 4-type
    schema_canon: bool = False
    relation_vocab: tuple[str, ...] = ()
    extractor: str = "api"                   # api | rebel | gliner
    relation_reprompt: bool = False          # REFUTED (#1360)
    rebel_fuse: bool = False                 # REFUTED (#1357)
    extract_recall: bool = False             # REFUTED (#1348)

    def __post_init__(self) -> None:
        if self.xdoc_key not in _XDOC_KEYS:
            raise ValueError(f"xdoc_key must be one of {_XDOC_KEYS}, got {self.xdoc_key!r}")
        if self.extractor not in _EXTRACTORS:
            raise ValueError(f"extractor must be one of {_EXTRACTORS}, got {self.extractor!r}")
        if self.chunk_sentences < 1:
            raise ValueError(f"chunk_sentences must be >= 1, got {self.chunk_sentences}")
        if not (0 <= self.chunk_overlap < self.chunk_sentences):
            raise ValueError(
                f"chunk_overlap must be in [0, chunk_sentences), got {self.chunk_overlap} "
                f"(chunk_sentences={self.chunk_sentences})"
            )
```

- [ ] **Step 4: Run to verify passes**

Run box-safe with `-k config`. Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
git commit -m "feat(goldengraph): SubstrateConfig frozen dataclass + validation (SP-B1 task 1)"
```

---

### Task 2: `MANAGED_ENV_VARS` + `to_env()` (incl. SCHEMA_DISCOVER guard)

**Files:**
- Modify: `goldengraph/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_default_config_is_noop_env():
    env = SubstrateConfig().to_env()
    assert env["GOLDENGRAPH_XDOC_KEY"] == ""
    for k in ("GOLDENGRAPH_CHUNK_EXTRACT", "GOLDENGRAPH_ENTITY_TYPE_CANON",
              "GOLDENGRAPH_SCHEMA_CANON", "GOLDENGRAPH_RELATION_REPROMPT",
              "GOLDENGRAPH_REBEL_FUSE", "GOLDENGRAPH_EXTRACT_RECALL"):
        assert env[k] == "0"
    assert env["GOLDENGRAPH_EXTRACTOR"] == "api"
    assert env["GOLDENGRAPH_ENTITY_TYPE_VOCAB"] == "" and env["GOLDENGRAPH_RELATION_VOCAB"] == ""
    assert env["GOLDENGRAPH_CHUNK_SENTENCES"] == "6" and env["GOLDENGRAPH_CHUNK_OVERLAP"] == "2"


def test_to_env_maps_types():
    c = SubstrateConfig(xdoc_key="name_ci_type", chunk_extract=True, entity_type_canon=True,
                        entity_type_vocab=("person", "organization"), schema_canon=True,
                        relation_vocab=("acquired", "works_at"))
    env = c.to_env()
    assert env["GOLDENGRAPH_XDOC_KEY"] == "name_ci_type"
    assert env["GOLDENGRAPH_CHUNK_EXTRACT"] == "1"
    assert env["GOLDENGRAPH_ENTITY_TYPE_VOCAB"] == "person,organization"
    assert env["GOLDENGRAPH_RELATION_VOCAB"] == "acquired,works_at"


def test_schema_discover_guard_in_managed_and_off():
    assert "GOLDENGRAPH_SCHEMA_DISCOVER" in MANAGED_ENV_VARS
    assert SubstrateConfig().to_env()["GOLDENGRAPH_SCHEMA_DISCOVER"] == "0"


def test_to_env_keys_equal_managed():
    # to_env must be TOTAL over the managed set (leak-proof invariant).
    assert set(SubstrateConfig().to_env()) == set(MANAGED_ENV_VARS)
```

- [ ] **Step 2: Run to verify it fails**

Run box-safe with `-k "to_env or schema_discover or noop or managed"`. Expected: FAIL — `AttributeError`/`ImportError` for `MANAGED_ENV_VARS`/`to_env`.

- [ ] **Step 3: Implement**

Add to `config.py` (module constant after the `_EXTRACTORS` line, and a method on the dataclass):

```python
#: Every GOLDENGRAPH_* var this config OWNS: 12 field vars + the SCHEMA_DISCOVER leak-guard. Ambient
#: SCHEMA_DISCOVER=1 makes ingest_corpus discover schema and ignore RELATION_VOCAB, silently defeating
#: the has_known_schema rule -- so the config forces it off. (The ~18 other substrate GOLDENGRAPH_*
#: vars are NOT managed; none of them defeats a for_profile rule. See the spec's Residual note.)
MANAGED_ENV_VARS: tuple[str, ...] = (
    "GOLDENGRAPH_XDOC_KEY",
    "GOLDENGRAPH_CHUNK_EXTRACT",
    "GOLDENGRAPH_CHUNK_SENTENCES",
    "GOLDENGRAPH_CHUNK_OVERLAP",
    "GOLDENGRAPH_ENTITY_TYPE_CANON",
    "GOLDENGRAPH_ENTITY_TYPE_VOCAB",
    "GOLDENGRAPH_SCHEMA_CANON",
    "GOLDENGRAPH_RELATION_VOCAB",
    "GOLDENGRAPH_EXTRACTOR",
    "GOLDENGRAPH_RELATION_REPROMPT",
    "GOLDENGRAPH_REBEL_FUSE",
    "GOLDENGRAPH_EXTRACT_RECALL",
    "GOLDENGRAPH_SCHEMA_DISCOVER",  # leak-guard, not a field
)
```

Add as a method on `SubstrateConfig`:

```python
    def to_env(self) -> dict[str, str]:
        """Total map over MANAGED_ENV_VARS: applying it fully determines those keys (leak-proof over
        the managed set). Bool -> '1'/'0'; empty xdoc_key/vocabs -> ''; tuples -> csv; SCHEMA_DISCOVER
        forced '0'."""
        def b(x: bool) -> str:
            return "1" if x else "0"

        return {
            "GOLDENGRAPH_XDOC_KEY": self.xdoc_key,
            "GOLDENGRAPH_CHUNK_EXTRACT": b(self.chunk_extract),
            "GOLDENGRAPH_CHUNK_SENTENCES": str(self.chunk_sentences),
            "GOLDENGRAPH_CHUNK_OVERLAP": str(self.chunk_overlap),
            "GOLDENGRAPH_ENTITY_TYPE_CANON": b(self.entity_type_canon),
            "GOLDENGRAPH_ENTITY_TYPE_VOCAB": ",".join(self.entity_type_vocab),
            "GOLDENGRAPH_SCHEMA_CANON": b(self.schema_canon),
            "GOLDENGRAPH_RELATION_VOCAB": ",".join(self.relation_vocab),
            "GOLDENGRAPH_EXTRACTOR": self.extractor,
            "GOLDENGRAPH_RELATION_REPROMPT": b(self.relation_reprompt),
            "GOLDENGRAPH_REBEL_FUSE": b(self.rebel_fuse),
            "GOLDENGRAPH_EXTRACT_RECALL": b(self.extract_recall),
            "GOLDENGRAPH_SCHEMA_DISCOVER": "0",
        }
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k "to_env or schema_discover or noop or managed"`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
git commit -m "feat(goldengraph): MANAGED_ENV_VARS + to_env total map incl SCHEMA_DISCOVER guard (SP-B1 task 2)"
```

---

### Task 3: `apply()` context manager + guard behavior

**Files:**
- Modify: `goldengraph/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def _clear_managed():
    # Box-safe test isolation: pops all managed keys to test from a known-clean state. Destructive to
    # ambient GOLDENGRAPH_* with no restore -- fine for CI/box (those vars are unset there), not for a
    # shell that has them set. The two tests that need a prior value use try/finally to restore.
    for k in MANAGED_ENV_VARS:
        os.environ.pop(k, None)


def test_apply_sets_and_restores_absent_key():
    _clear_managed()
    c = SubstrateConfig(xdoc_key="name_ci")
    with c.apply():
        assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name_ci"
        assert os.environ["GOLDENGRAPH_CHUNK_EXTRACT"] == "0"
    # keys absent before must be GONE after (not left as "")
    assert "GOLDENGRAPH_XDOC_KEY" not in os.environ
    assert "GOLDENGRAPH_CHUNK_EXTRACT" not in os.environ


def test_apply_restores_prior_value():
    _clear_managed()
    os.environ["GOLDENGRAPH_XDOC_KEY"] = "name"          # a pre-existing value
    try:
        with SubstrateConfig(xdoc_key="name_ci").apply():
            assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name_ci"
        assert os.environ["GOLDENGRAPH_XDOC_KEY"] == "name"   # restored to prior
    finally:
        _clear_managed()


def test_apply_forces_schema_discover_off_and_restores():
    _clear_managed()
    os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] = "1"       # ambient discovery ON
    try:
        with SubstrateConfig(schema_canon=True, relation_vocab=("acquired",)).apply():
            assert os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] == "0"   # forced off during build
        assert os.environ["GOLDENGRAPH_SCHEMA_DISCOVER"] == "1"       # restored after
    finally:
        _clear_managed()
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k apply`. Expected: FAIL — no `apply`.

- [ ] **Step 3: Implement**

Add as a method on `SubstrateConfig`:

```python
    @contextmanager
    def apply(self):
        """Set the MANAGED_ENV_VARS from `to_env()` for the duration, then restore the prior process
        env exactly (delete keys that were absent, restore prior values). Env is process-global: set
        the config ONCE before ingest_corpus fans per-doc work out to threads (which inherit the env).
        NOT safe for two different configs building concurrently in one process."""
        env = self.to_env()
        sentinel = object()
        prior = {k: os.environ.get(k, sentinel) for k in MANAGED_ENV_VARS}
        try:
            os.environ.update(env)
            yield
        finally:
            for k, v in prior.items():
                if v is sentinel:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k apply`. Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
git commit -m "feat(goldengraph): SubstrateConfig.apply() env snapshot/restore + SCHEMA_DISCOVER force-off (SP-B1 task 3)"
```

---

### Task 4: `CorpusProfile` + `profile_corpus()` (local splitter)

**Files:**
- Modify: `goldengraph/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_profile_corpus_signals():
    docs = ["One. Two. Three.", "Solo sentence"]  # 3 sentences + 1 = mean 2.0
    p = profile_corpus(docs)
    assert p.n_docs == 2
    assert p.mean_sentences_per_doc == 2.0
    assert p.mean_chars_per_doc == (len(docs[0]) + len(docs[1])) / 2


def test_profile_corpus_empty():
    p = profile_corpus([])
    assert p == CorpusProfile(n_docs=0, mean_sentences_per_doc=0.0, mean_chars_per_doc=0.0)
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k profile_corpus`. Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `config.py`:

```python
@dataclass(frozen=True)
class CorpusProfile:
    """Cheap raw-text signals (no LLM, no build) that drive `for_profile`."""
    n_docs: int
    mean_sentences_per_doc: float
    mean_chars_per_doc: float


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def _count_sentences(text: str) -> int:
    """Sentence count via a local [.!?]-boundary split (config.py stays free of the LLM path, so we do
    NOT reuse chunk_extract's splitter -- importing it drags in .llm)."""
    t = text.strip()
    if not t:
        return 0
    return len([s for s in _SENT_SPLIT.split(t) if s.strip()])


def profile_corpus(docs) -> CorpusProfile:
    """Derive a CorpusProfile from raw document texts. Empty corpus -> zeros."""
    docs = list(docs)
    n = len(docs)
    if n == 0:
        return CorpusProfile(n_docs=0, mean_sentences_per_doc=0.0, mean_chars_per_doc=0.0)
    total_sents = sum(_count_sentences(d) for d in docs)
    total_chars = sum(len(d) for d in docs)
    return CorpusProfile(
        n_docs=n,
        mean_sentences_per_doc=total_sents / n,
        mean_chars_per_doc=total_chars / n,
    )
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k profile_corpus`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
git commit -m "feat(goldengraph): CorpusProfile + profile_corpus (local sentence splitter) (SP-B1 task 4)"
```

---

### Task 5: `for_profile()` rule table

**Files:**
- Modify: `goldengraph/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

```python
def _short():
    return CorpusProfile(n_docs=5, mean_sentences_per_doc=2.0, mean_chars_per_doc=40.0)


def _dense():
    return CorpusProfile(n_docs=19, mean_sentences_per_doc=20.0, mean_chars_per_doc=900.0)


def test_for_profile_short_docs_no_chunking():
    c = for_profile(_short())
    assert c.xdoc_key == "name_ci"     # base near-universal relational win
    assert c.chunk_extract is False    # short docs -> chunking off


def test_for_profile_dense_docs_enables_chunking():
    c = for_profile(_dense())
    assert c.chunk_extract is True and c.chunk_sentences == 6 and c.chunk_overlap == 2
    assert c.xdoc_key == "name_ci"


def test_for_profile_homographs_override():
    c = for_profile(_dense(), expect_homographs=True)
    assert c.xdoc_key == "name_ci_type"      # homograph OVERRIDES base name_ci
    assert c.entity_type_canon is True


def test_for_profile_known_schema():
    c = for_profile(_dense(), has_known_schema=True, relation_vocab=("acquired", "works_at"))
    assert c.schema_canon is True and c.relation_vocab == ("acquired", "works_at")


def test_for_profile_never_selects_refuted():
    for kw in ({}, {"expect_homographs": True}, {"has_known_schema": True}):
        c = for_profile(_dense(), **kw)
        assert c.relation_reprompt is False and c.rebel_fuse is False and c.extract_recall is False
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k for_profile`. Expected: FAIL.

- [ ] **Step 3: Implement**

Add to `config.py`:

```python
#: Dense multi-sentence docs benefit from chunked extraction; short docs get a no-op + 4-10x cost.
#: Threshold from the wiki finding (leads ~20 sentences, chunking won at (6,2), #1350). Env-overridable.
CHUNK_MIN_SENTENCES: int = int(os.environ.get("GOLDENGRAPH_AUTOCFG_CHUNK_MIN_SENTENCES", "8") or "8")


def for_profile(profile: CorpusProfile, *, has_known_schema: bool = False,
                expect_homographs: bool = False, relation_vocab: tuple[str, ...] = ()) -> SubstrateConfig:
    """Deterministic rule table over a CorpusProfile -> a sane-default SubstrateConfig. Encodes the
    arc's MEASURED findings. Precedence: base name_ci -> homograph override -> chunk + schema (orthogonal).
    Refuted levers are never selected."""
    # base: name_ci is the near-universal relational win (L0/L1/L2, #1331/#1340/#1341)
    xdoc_key = "name_ci"
    entity_type_canon = False
    # homograph override: name_ci_type + type-canon (homograph-safe, ~0.06 recall cost, #1335/#1336)
    if expect_homographs:
        xdoc_key = "name_ci_type"
        entity_type_canon = True
    # chunking: only on dense multi-sentence docs (#1350)
    chunk = profile.mean_sentences_per_doc >= CHUNK_MIN_SENTENCES
    # known schema: closed-vocab predicate canonicalization (SCHEMA_CANON arc)
    schema_canon = bool(has_known_schema)
    vocab = tuple(relation_vocab) if has_known_schema else ()
    return SubstrateConfig(
        xdoc_key=xdoc_key,
        chunk_extract=chunk,
        entity_type_canon=entity_type_canon,
        schema_canon=schema_canon,
        relation_vocab=vocab,
    )
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k for_profile`. Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
git commit -m "feat(goldengraph): for_profile rule table (name_ci base / homograph override / dense-chunk / known-schema) (SP-B1 task 5)"
```

---

### Task 6: cross-contract consistency test (skip-if-unimportable)

**Files:**
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the test**

```python
def test_config_fields_cover_known_levers():
    # SP-A ships erkgbench.substrate_eval.KNOWN_LEVERS (lever-name -> env). Every such lever must be a
    # SubstrateConfig field, keeping the SP-A contract and the SP-B object in sync. Skips if erkgbench
    # isn't importable on this branch (SP-A #1371 not yet merged/rebased in).
    try:
        from erkgbench.substrate_eval import KNOWN_LEVERS
    except Exception:
        pytest.skip("erkgbench.substrate_eval.KNOWN_LEVERS not importable on this branch (pre-#1371)")
    field_names = {f.name for f in __import__("dataclasses").fields(SubstrateConfig)}
    missing = set(KNOWN_LEVERS) - field_names
    assert not missing, f"KNOWN_LEVERS not covered by SubstrateConfig fields: {missing}"
```

- [ ] **Step 2: Run** — box-safe `-k known_levers`. Expected: PASS or SKIP (skip is acceptable on this pre-rebase branch; it must PASS after the Task 7 rebase).

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldengraph/tests/test_config.py
git commit -m "test(goldengraph): SubstrateConfig fields cover SP-A KNOWN_LEVERS (skip-if-unimportable) (SP-B1 task 6)"
```

---

### Task 7: Finish the branch

- [ ] **Step 1: Full file green** — run the box-safe command on the whole file:
  ```bash
  cd /d/show_case/gg-local-llm/packages/python/goldengraph
  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
    /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_config.py -q
  ```
  Expected: all green (Task 6 may SKIP pre-rebase).

- [ ] **Step 2: Lint** — `ruff check` the two files; fix any finding.
  ```bash
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldengraph/goldengraph/config.py packages/python/goldengraph/tests/test_config.py
  ```

- [ ] **Step 3: Rebase onto main (once #1371 has merged)** so `config_fields_cover_known_levers` actually runs:
  ```bash
  unset GH_TOKEN; export GH_TOKEN=$(gh auth token --user benzsevern)
  git fetch origin main
  # only if #1371 (SP-A) is in origin/main:
  git rebase origin/main
  # re-run tests; Task 6 test should now PASS (not skip)
  ```
  If #1371 has NOT merged yet, skip the rebase, note it in the PR, and land with the skip (rebase later).

  **Expected to PASS, not fail (verified against SP-A):** SP-A's `KNOWN_LEVERS` is keyed by lever-NAME — `xdoc_key, chunk_extract, chunk_extract, extract_recall, extractor, entity_type_canon, schema_canon, relation_vocab, relation_reprompt, rebel_fuse` — every one of which is a `SubstrateConfig` field, so `set(KNOWN_LEVERS) - field_names == set()`. `schema_discover` is deliberately NOT a `KNOWN_LEVERS` key (it's a leak-guard, not a lever), so it can't trip the subset check. If the rebased test unexpectedly FAILS, the cause is a key-SHAPE mismatch (SP-A keying by env-var name instead of lever-name) — reconcile the two, don't just skip.

- [ ] **Step 4: Update the spec status** — flip the spec header `Status:` to `implemented`; commit `docs(substrate): mark SP-B1 spec implemented`.

- [ ] **Step 5: Push + PR** — `git push -u origin feat/substrate-config`; open PR base `main`; arm `gh pr merge <N> --repo benseverndev-oss/goldenmatch --auto` and STOP (no CI poll loop).

- [ ] **Step 6: Update memory** — append SP-B1 shipped to `project_goldengraph_local_oss_llm_lane.md` (config surface + rule-table picker; SP-B2 staged harness is next).

---

## Notes for the implementer

- **Box-safe only.** Run just `tests/test_config.py` via the box-safe command. No full suite, no Modal.
- **`config.py` is stdlib-only.** Never `import` `.llm`, `.embed`, `.chunk_extract`, `.ingest`, or anything native — that is the whole point of the module. (Importing the package `__init__` still happens at test collection; that's fine and unavoidable, but `config.py`'s own imports must stay stdlib.)
- **`to_env()` is total by design.** Every managed key is emitted (including `"0"`/`""`) so `apply()` can't leak an ambient managed var into a build. Do not "optimize" it to emit only non-defaults.
- **`for_profile` never returns a config with a refuted lever on.** Those fields exist only so SP-C can measurement-gate a re-test later.
