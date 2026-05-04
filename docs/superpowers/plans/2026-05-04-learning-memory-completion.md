# Learning Memory Completion Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the already-shipped Learning Memory primitives (PR #9 — store, corrections, learner) into the goldenmatch pipeline and surfaces (CLI, MCP, REST, TUI), add row-ID-stable re-anchoring via `record_hash`, and surface applied/stale counts in postflight.

**Architecture:** Eight independently-mergeable phases following the spec's fixed implementation order. Re-anchoring lands first (foundational, localized, fully testable in isolation). Pipeline hook lands second (nothing observable works until it does). Collection points and surfaces stack on top. Each phase is one PR; each step inside a phase is 2–5 minutes of work.

**Tech Stack:** Python 3.11+, polars (vectorized hashing), Pydantic (config), Typer (CLI), MCP SDK (tools), Textual (TUI), pytest + pytest-asyncio.

**Spec:** `docs/superpowers/specs/2026-05-04-learning-memory-completion.md` — read it first; this plan does not duplicate the spec's rationale or design rationale.

**Foundation:** `_archive/goldenmatch-pre-fold/docs/superpowers/specs/2026-03-26-learning-memory-design.md` (approved 2026-03-26; Tasks 1–4 shipped via PR #9). Foundation plan at `_archive/goldenmatch-pre-fold/docs/superpowers/plans/2026-03-26-learning-memory-implementation.md` — Tasks 5–8 of that document inform but do not bind this plan; this plan supersedes them where they differ from the new spec.

---

## File Structure

All paths relative to `packages/python/goldenmatch/` unless otherwise noted.

**Create:**
- `goldenmatch/mcp/memory_tools.py` — five MCP tools (Phase 7)
- `goldenmatch/cli/memory.py` — Typer subgroup with five subcommands (Phase 6)
- `tests/test_memory_e2e.py` — integration suite (Phase 8)
- `tests/test_memory_reanchor.py` — re-anchor unit tests (Phase 1)
- `tests/test_memory_pipeline.py` — pipeline-hook tests (Phase 2)
- `tests/test_memory_postflight.py` — postflight rendering tests (Phase 3)
- `tests/test_memory_collection.py` — collection-point tests (Phase 4)
- `tests/test_memory_explainer.py` — explainer tests (Phase 5)
- `tests/test_memory_cli.py` — CLI tests (Phase 6)
- `tests/test_memory_tools.py` — MCP tool tests (Phase 7)

**Modify:**
- `goldenmatch/core/memory/corrections.py` — collision-safe vectorized re-anchor (Phase 1)
- `goldenmatch/config/schemas.py` — add `MemoryConfig.reanchor: bool = True` (Phase 1)
- `goldenmatch/core/pipeline.py:497` — insert memory hook between scoring and postflight; learner overlay near config materialization (Phase 2)
- `goldenmatch/_api.py:36` — add `memory_stats: CorrectionStats | None` to `DedupeResult` (Phase 2)
- `goldenmatch/_api.py:136` — add `memory_stats: CorrectionStats | None` to `MatchResult` (Phase 2)
- `goldenmatch/_api.py` — add `get_memory`, `add_correction`, `learn`, `memory_stats` API functions (Phase 6)
- `goldenmatch/__init__.py` — re-export the four new API functions (Phase 6)
- `goldenmatch/core/postflight.py` (or wherever `_apply_postflight` lives) — render memory line when `memory_stats` is set (Phase 3)
- `goldenmatch/core/review_queue.py:238,241` — `approve`/`reject` write to memory store (base class only) (Phase 4)
- `goldenmatch/tui/tabs/boost_tab.py:276` — `on_button_pressed` (`btn-match`/`btn-nonmatch`/`btn-skip` branches) writes to memory store (Phase 4)
- `goldenmatch/core/cluster.py:361,431` — `unmerge_record`/`unmerge_cluster` accept optional `memory_store`, write empty-hash corrections (Phase 4)
- `goldenmatch/core/llm_scorer.py:33` — `llm_score_pairs` accepts optional `memory_store`, writes per-decision corrections (Phase 4)
- `goldenmatch/mcp/agent_tools.py:104` — `agent_approve_reject` writes to memory store (Phase 4)
- `goldenmatch/api/server.py:309` — `POST /reviews/decide` writes to memory store (Phase 4)
- `goldenmatch/core/explain.py` — extend `explain_pair_nl` to accept correction context, optionally route to LLM (Phase 5)
- `goldenmatch/cli/main.py:28,109` — register `memory_app` (Phase 6)
- `goldenmatch/mcp/server.py:1266` — change description string from `"30 MCP tools"` to `"35 MCP tools"`; import + register `MEMORY_TOOLS` (Phase 7)

---

## Common patterns

**Test directory.** All new tests live in `packages/python/goldenmatch/tests/`. Run from `packages/python/goldenmatch/` so pytest picks up the package conftest.

**SQLite test isolation.** Use the existing pattern: `MemoryStore(backend="sqlite", path=str(tmp_path / "test.db"))`. The `tmp_path` fixture gives each test a fresh DB.

**Polyrun command.** Single-test: `pytest packages/python/goldenmatch/tests/test_<file>.py::<name> -v`. Phase smoke: `pytest packages/python/goldenmatch/tests/test_memory*.py -v --tb=short`.

**Commit message format.** `feat(memory): <description>` for new behavior, `test(memory): <description>` for test-only changes. Keep commits small — one TDD cycle per commit when practical.

**Branch.** Per the goldenmatch SOP, all phases land on `feature/learning-memory-completion` (or per-phase branches if you prefer). Squash-merge each PR.

---

## Phase 1: Re-anchor (Addition 1)

**Why first:** Fully testable in isolation against the existing 48 unit tests. Phases 2+ assume corrections survive row reordering; without this, pipeline-hook tests will pass but the feature won't be useful.

**Files:**
- Modify: `goldenmatch/config/schemas.py`
- Modify: `goldenmatch/core/memory/corrections.py`
- Test: `tests/test_memory_reanchor.py`

### Steps

- [ ] **Step 1.1: Add `reanchor` and `dataset` fields to `MemoryConfig`**

`goldenmatch/config/schemas.py:402` — find the existing `MemoryConfig` class. Add:

```python
class MemoryConfig(BaseModel):
    # ... existing fields ...
    reanchor: bool = True
    dataset: str | None = None
```

`dataset` is the scoping key referenced throughout the foundation spec but never schema-declared. Adding it now lets Phase 2 use `config.memory.dataset` as a real attribute instead of `hasattr` cargo-cult.

- [ ] **Step 1.2: Run existing memory tests — confirm green baseline**

```bash
pytest packages/python/goldenmatch/tests/test_memory_store.py packages/python/goldenmatch/tests/test_corrections.py packages/python/goldenmatch/tests/test_learner.py packages/python/goldenmatch/tests/test_memory_integration.py -v
```
Expected: 48 passed.

- [ ] **Step 1.3: Write the failing test — row reorder preserves correction**

`tests/test_memory_reanchor.py`:

```python
import polars as pl
import pytest
from goldenmatch.core.memory.store import MemoryStore, Correction
from goldenmatch.core.memory.corrections import (
    apply_corrections,
    compute_field_hash,
    build_row_lookup,
)
from datetime import datetime
import uuid


def _make_df(rows):
    """rows: list of (row_id, name, zip)"""
    return pl.DataFrame(
        {"__row_id__": [r[0] for r in rows],
         "name": [r[1] for r in rows],
         "zip": [r[2] for r in rows]}
    )


def _seed_correction(store, df, id_a, id_b, decision, *, fields=("name", "zip")):
    """Helper: store a correction with full hashes computed from df."""
    from goldenmatch.core.memory.corrections import compute_record_hash
    lookup = build_row_lookup(df, list(fields))
    fh = compute_field_hash(lookup[id_a], lookup[id_b])
    rh = f"{compute_record_hash(df, id_a)}:{compute_record_hash(df, id_b)}"
    store.add_correction(Correction(
        id=str(uuid.uuid4()), id_a=id_a, id_b=id_b,
        decision=decision, source="steward", trust=1.0,
        field_hash=fh, record_hash=rh,
        original_score=0.92, matchkey_name=None, reason=None,
        dataset="t", created_at=datetime.now(),
    ))


def test_reanchor_after_row_reorder(tmp_path):
    """Correction stays applied after rows are shuffled."""
    df1 = _make_df([(1, "Acme Corp", "10001"),
                    (2, "Acme LLC",  "10001"),
                    (3, "Beta Inc",  "20002")])
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")  # Acme Corp != Acme LLC

    # Same entities, different row IDs (sorted by name)
    df2 = _make_df([(10, "Acme Corp", "10001"),
                    (20, "Acme LLC",  "10001"),
                    (30, "Beta Inc",  "20002")])
    scored = [(10, 20, 0.92), (10, 30, 0.10), (20, 30, 0.10)]
    adjusted, stats = apply_corrections(scored, store, df2,
                                        ["name", "zip"], dataset="t")

    # Pair (10, 20) corresponds to the corrected pair (1, 2).
    pair_score = next(s for a, b, s in adjusted if (a, b) == (10, 20))
    assert pair_score == 0.0, "rejected correction should re-anchor and apply"
    assert stats.applied == 1
    assert stats.stale == 0
```

- [ ] **Step 1.4: Run test — confirm it fails**

Expected failure: `pair_score == 0.92` (correction not applied because row IDs no longer match).

- [ ] **Step 1.5: Implement vectorized record_hash map + collision-safe re-anchor**

In `goldenmatch/core/memory/corrections.py`, replace the body of `apply_corrections` with the algorithm from spec section "Addition 1". Key changes from current implementation:

- Fetch all corrections once via `store.get_corrections(dataset=dataset)`
- Build `hash_to_rids: dict[str, list[int]]` via vectorized `pl.concat_str` + `map_elements` (one O(N) pass over the df)
- For each correction, prefer direct row-ID match; fall back to record_hash re-anchor only when both sides resolve uniquely; count ambiguous (`len(cands) > 1`) as stale-ambiguous
- Existing dual-hash safety check runs unchanged on the resulting `active` map
- Add new field `stale_ambiguous: int = 0` to `CorrectionStats`
- Skip the re-anchor path entirely when `config.memory.reanchor is False` (the function takes `df` and `store` but not config; expose this via a new keyword arg `reanchor: bool = True` and have the pipeline hook pass `config.memory.reanchor`)

Reference: spec Addition 1 Algorithm block — the sketched code is the contract.

- [ ] **Step 1.6: Run the new test — confirm it passes**

Expected: `pair_score == 0.0`, `stats.applied == 1`.

- [ ] **Step 1.7: Run the full existing 48-test suite — confirm no regressions**

```bash
pytest packages/python/goldenmatch/tests/test_memory_store.py packages/python/goldenmatch/tests/test_corrections.py packages/python/goldenmatch/tests/test_learner.py packages/python/goldenmatch/tests/test_memory_integration.py -v
```
Expected: 48 passed.

- [ ] **Step 1.8: Write the failing test — duplicate row collision**

```python
def test_reanchor_skips_ambiguous_duplicates(tmp_path):
    """When two current rows share record_hash, refuse to re-anchor."""
    df1 = _make_df([(1, "Acme Corp", "10001"),
                    (2, "Acme LLC",  "10001")])
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    # Now the input has a literal duplicate of "Acme Corp"
    df2 = _make_df([(10, "Acme Corp", "10001"),
                    (11, "Acme Corp", "10001"),  # duplicate
                    (20, "Acme LLC",  "10001")])
    scored = [(10, 20, 0.92), (11, 20, 0.92), (10, 11, 1.0)]
    adjusted, stats = apply_corrections(scored, store, df2,
                                        ["name", "zip"], dataset="t")

    # Acme Corp side has 2 candidates → ambiguous → no application
    assert all(s == orig for (_, _, s), (_, _, orig) in zip(adjusted, scored))
    assert stats.applied == 0
    assert stats.stale_ambiguous == 1
```

- [ ] **Step 1.9: Run; confirm passes**

Expected: stats.applied == 0, stats.stale_ambiguous == 1.

- [ ] **Step 1.10: Add edit-on-matchkey-field test**

```python
def test_edit_on_matchkey_field_marks_stale(tmp_path):
    df1 = _make_df([(1, "Acme Corp", "10001"),
                    (2, "Acme LLC",  "10001")])
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    # Edit one of the matched fields
    df2 = _make_df([(1, "ACME CORPORATION", "10001"),  # name changed
                    (2, "Acme LLC",          "10001")])
    scored = [(1, 2, 0.85)]
    adjusted, stats = apply_corrections(scored, store, df2,
                                        ["name", "zip"], dataset="t")
    assert adjusted[0][2] == 0.85  # original score, not overridden
    assert stats.applied == 0
    assert stats.stale == 1
```

- [ ] **Step 1.11: Add edit-on-non-matchkey-field test (correction still applies)**

```python
def test_edit_on_non_matchkey_field_still_applies(tmp_path):
    df1 = _make_df([(1, "Acme Corp", "10001"),
                    (2, "Acme LLC",  "10001")])
    # Add an extra column that is NOT a matchkey field
    df1 = df1.with_columns(pl.lit("old_note").alias("note"))
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject", fields=("name", "zip"))

    df2 = df1.with_columns(pl.lit("new_note").alias("note"))
    # ⚠ record_hash includes ALL columns, so changing `note` will mark stale.
    # This is by design — the dual-hash captures full-row identity.
    # Test verifies that the staleness detection is consistent with design.
    scored = [(1, 2, 0.92)]
    adjusted, stats = apply_corrections(scored, store, df2,
                                        ["name", "zip"], dataset="t")
    assert stats.stale == 1
```

(Note: this test confirms the *current* dual-hash semantics; if a future spec relaxes record_hash to matchkey-only, this test changes.)

- [ ] **Step 1.12: Add `reanchor=False` opt-out test**

```python
def test_reanchor_disabled_falls_back_to_row_id_lookup(tmp_path):
    df1 = _make_df([(1, "Acme Corp", "10001"),
                    (2, "Acme LLC",  "10001")])
    store = MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))
    _seed_correction(store, df1, 1, 2, "reject")

    df2 = _make_df([(10, "Acme Corp", "10001"),  # different row IDs
                    (20, "Acme LLC",  "10001")])
    scored = [(10, 20, 0.92)]
    adjusted, stats = apply_corrections(scored, store, df2,
                                        ["name", "zip"], dataset="t",
                                        reanchor=False)
    assert adjusted[0][2] == 0.92  # not applied
    assert stats.applied == 0
```

- [ ] **Step 1.13: Run all phase-1 tests; confirm green**

```bash
pytest packages/python/goldenmatch/tests/test_memory_reanchor.py -v
```

- [ ] **Step 1.14: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py \
        packages/python/goldenmatch/goldenmatch/core/memory/corrections.py \
        packages/python/goldenmatch/tests/test_memory_reanchor.py
git commit -m "feat(memory): collision-safe vectorized re-anchor via record_hash"
```

---

## Phase 2: Pipeline hook (Task 5)

**Why second:** Nothing the user can observe works until this lands.

**Ordering note:** Phase 3 (postflight rendering) consumes `result.memory_stats` set here; Phase 3 must merge after Phase 2. Phase 2 itself is independently shippable (its tests don't read postflight strings).

**Files:**
- Modify: `goldenmatch/core/pipeline.py` (around line 497)
- Modify: `goldenmatch/_api.py:36` (DedupeResult), `:136` (MatchResult)
- Test: `tests/test_memory_pipeline.py`

### Steps

- [ ] **Step 2.1: Add `memory_stats` field to both result dataclasses**

`goldenmatch/_api.py` — at line 56 (after `postflight_report` in `DedupeResult`):

```python
    memory_stats: "CorrectionStats | None" = None
```

At line 148 (after `postflight_report` in `MatchResult`):

```python
    memory_stats: "CorrectionStats | None" = None
```

Add the import at top of file under TYPE_CHECKING:

```python
if TYPE_CHECKING:
    from goldenmatch.core.memory.corrections import CorrectionStats
```

- [ ] **Step 2.2: Write the failing pipeline-hook test**

`tests/test_memory_pipeline.py`:

```python
import polars as pl
import pytest
from datetime import datetime
import uuid

from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    GoldenMatchConfig, MemoryConfig, MatchkeyConfig, MatchkeyField,
    BlockingConfig, BlockingKeyConfig,
)
from goldenmatch.core.memory.store import MemoryStore, Correction


def test_pipeline_applies_seeded_correction(tmp_path):
    df = pl.DataFrame({
        "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
        "zip":  ["10001",     "10001",    "20002"],
    })

    db_path = tmp_path / "mem.db"
    # Seed: reject the (0, 1) pair
    store = MemoryStore(backend="sqlite", path=str(db_path))
    # Use the standard build_row_lookup / hash helpers
    from goldenmatch.core.memory.corrections import (
        build_row_lookup, compute_field_hash, compute_record_hash,
    )
    df_with_id = df.with_row_index(name="__row_id__")
    lookup = build_row_lookup(df_with_id, ["name", "zip"])
    fh = compute_field_hash(lookup[0], lookup[1])
    rh = f"{compute_record_hash(df_with_id, 0)}:{compute_record_hash(df_with_id, 1)}"
    store.add_correction(Correction(
        id=str(uuid.uuid4()), id_a=0, id_b=1, decision="reject",
        source="steward", trust=1.0, field_hash=fh, record_hash=rh,
        original_score=0.95, matchkey_name=None, reason=None,
        dataset=None, created_at=datetime.now(),
    ))
    store.close()

    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="identity", type="weighted", threshold=0.75,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                  transforms=["lowercase"], weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["lowercase"])],
            maxBlockSize=1000, skipOversized=True,
        ),
        memory=MemoryConfig(enabled=True, path=str(db_path)),
    )

    result = dedupe_df(df, config=config)

    # Acme pair should NOT be in clusters — its score got overridden to 0.0
    pairs_kept = {(a, b) for a, b, _ in result.scored_pairs}
    assert (0, 1) not in pairs_kept or all(
        s == 0.0 for a, b, s in result.scored_pairs if (a, b) == (0, 1)
    )
    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
```

- [ ] **Step 2.3: Run; confirm fails**

Expected failure: `result.memory_stats is None` (the hook isn't wired yet).

- [ ] **Step 2.4: Add learner-overlay block at start of `_run_dedupe_pipeline`**

`_run_dedupe_pipeline` already receives a `matchkeys` parameter from its caller (`dedupe()` at pipeline.py:206 and `match()` at :651/:691, threaded via `_run_dedupe_pipeline(... matchkeys, ...)`). The overlay must mutate **that parameter in place** — not rebind a fresh `list(config.get_matchkeys())`, which would create a shadow copy that the scoring loop (lines 393, 408, 422, 457) never reads.

In `goldenmatch/core/pipeline.py`, after the config is fully materialized but before scoring (find the section before `# Step 3: SCORE`), add:

```python
    # ── Learning Memory: pre-scoring learner overlay ──
    memory_store = None
    if config.memory and config.memory.enabled:
        try:
            from goldenmatch.core.memory.store import MemoryStore
            from goldenmatch.core.memory.learner import MemoryLearner
            memory_store = MemoryStore(
                backend=config.memory.backend,
                path=config.memory.path,
                connection=config.memory.connection,
            )
            learner = MemoryLearner(
                memory_store,
                threshold_min=config.memory.learning.threshold_min_corrections,
                weights_min=config.memory.learning.weights_min_corrections,
            )
            if learner.has_new_corrections():
                adjustments = learner.learn()
                # Overlay onto the matchkeys PARAMETER in place — do NOT rebind
                # to a new list, or the scoring loop downstream won't see it.
                for adj in adjustments:
                    if adj.threshold is None:
                        continue
                    for mk in matchkeys:  # mutate parameter, not a fresh copy
                        if mk.threshold is not None and (
                            not adj.matchkey_name or adj.matchkey_name == mk.name
                            or adj.matchkey_name == "_default"
                        ):
                            mk.threshold = adj.threshold
        except Exception as e:
            logger.warning("Memory store init failed, continuing without memory: %s", e)
            memory_store = None
```

Note: this mutates the `MatchkeyConfig` Pydantic objects on the in-memory matchkeys list. The original config's matchkeys are the *same objects* — be aware that calling the pipeline twice with the same config in the same Python process will see cumulative threshold overlay. If that becomes an issue, a deep-copy of the matchkeys parameter at function entry is the right fix; defer until/unless a test catches it.

- [ ] **Step 2.5: Insert post-scoring `apply_corrections` call before `_apply_postflight`**

At pipeline.py:497 (before the existing `all_pairs, postflight_report = _apply_postflight(...)` call), insert:

```python
    # ── Learning Memory: post-scoring corrections overlay ──
    memory_stats = None
    if memory_store is not None:
        from goldenmatch.core.memory.corrections import apply_corrections
        matchkey_field_names = [
            f.field
            for mk in config.get_matchkeys()
            for f in mk.fields
        ]
        dataset = config.memory.dataset
        all_pairs, memory_stats = apply_corrections(
            all_pairs, memory_store, collected_df, matchkey_field_names,
            dataset=dataset,
            reanchor=config.memory.reanchor,
        )
```

- [ ] **Step 2.6: Attach `memory_stats` and stale-pair enqueue at result-build time**

Find where `DedupeResult(...)` is constructed at end of `_run_dedupe_pipeline()`. Add `memory_stats=memory_stats` to the constructor.

After `DedupeResult` is built but before return, enqueue stale pairs:

```python
    if memory_stats is not None and memory_stats.stale_pairs:
        try:
            from goldenmatch.core.review_queue import ReviewQueue
            rq = ReviewQueue()  # in-memory backend; users wire SQLite/PG via config
            for (a, b) in memory_stats.stale_pairs:
                # Find score from all_pairs if present, else 0.0
                score = next(
                    (s for ai, bi, s in all_pairs if (ai, bi) == (a, b)), 0.0
                )
                rq.add(item_a_id=a, item_b_id=b, score=score, source="memory_stale")
        except Exception as e:
            logger.warning("Failed to enqueue stale pairs: %s", e)
```

- [ ] **Step 2.7: Mirror the same hook in `_run_match_pipeline()`**

Same pre-scoring overlay + post-scoring `apply_corrections` + result attach. `MatchResult` gains `memory_stats=memory_stats`.

- [ ] **Step 2.8: Close `memory_store` in a `finally` block**

Wrap the pipeline body so the SQLite connection always closes.

- [ ] **Step 2.9: Run the test — confirm passes**

```bash
pytest packages/python/goldenmatch/tests/test_memory_pipeline.py -v
```

- [ ] **Step 2.10: Add no-memory baseline test (regression guard)**

```python
def test_pipeline_no_memory_stats_when_disabled():
    df = pl.DataFrame({"name": ["A", "B"], "zip": ["1", "2"]})
    result = dedupe_df(df)  # default config, no memory
    assert result.memory_stats is None
```

- [ ] **Step 2.11: Add memory-disabled-but-config-present test**

```python
def test_pipeline_memory_disabled_does_not_open_store(tmp_path):
    df = pl.DataFrame({"name": ["A", "B"], "zip": ["1", "2"]})
    config = GoldenMatchConfig(
        # ... minimal valid matchkeys ...
        memory=MemoryConfig(enabled=False, path=str(tmp_path / "mem.db")),
    )
    result = dedupe_df(df, config=config)
    assert result.memory_stats is None
    # The DB file should not exist — store never opened
    assert not (tmp_path / "mem.db").exists()
```

- [ ] **Step 2.12: Run full memory test suite; confirm 48 + new tests green**

```bash
pytest packages/python/goldenmatch/tests/test_memory_*.py -v
```

- [ ] **Step 2.13: Commit**

```bash
git commit -m "feat(memory): pipeline hook for learner overlay + correction apply"
```

---

## Phase 3: Postflight wiring (Addition 3, postflight half)

**Files:**
- Modify: postflight rendering site (find via `grep -rn "_apply_postflight" packages/python/goldenmatch/goldenmatch/`)
- Test: `tests/test_memory_postflight.py`

### Steps

- [ ] **Step 3.1: Open the postflight render path**

The renderer lives at `core/autoconfig_verify.py:946` (function `postflight()`). It consumes a `PostflightReport` and emits a multi-line summary string. Open this file before drafting the test — the rendering site is the edit target for Step 3.4.

- [ ] **Step 3.2: Failing test for memory line in postflight**

```python
def test_postflight_renders_memory_section(tmp_path):
    # Use the same seeded fixture from Phase 2
    # ... set up store with one approve correction ...
    result = dedupe_df(df, config=config)
    text = str(result.postflight_report) if result.postflight_report else ""
    # Memory line is rendered into postflight summary
    assert "Memory:" in text
    assert "1 corrections applied" in text or "1 correction applied" in text
```

- [ ] **Step 3.3: Run; confirm fails**

- [ ] **Step 3.4: Implement memory line in postflight rendering**

Add a method to `PostflightReport` (or modify the renderer) that, when `memory_stats` is set on the parent `DedupeResult`/`MatchResult`, appends:

```
Memory: {applied} corrections applied, {stale} stale, {stale_ambiguous} stale-ambiguous
        (run `goldenmatch review` to re-decide stale pairs)
```

If counts are zero across the board, omit the line entirely (don't clutter the report when memory is enabled but had nothing to do).

The renderer needs access to `memory_stats`. Cleanest: pass `memory_stats` into the renderer alongside `postflight_report`, or attach `memory_stats` onto the `PostflightReport` object at result-build time.

- [ ] **Step 3.5: Run test; confirm passes**

- [ ] **Step 3.6: Stale-ambiguous test**

Verify that when `stats.stale_ambiguous > 0`, the rendered string includes `"stale-ambiguous"`.

- [ ] **Step 3.7: Zero-counts omission test**

Verify that with memory enabled but `applied == stale == stale_ambiguous == 0`, the postflight does NOT contain "Memory:".

- [ ] **Step 3.8: Commit**

```bash
git commit -m "feat(memory): postflight surfaces applied/stale counts"
```

---

## Phase 4: Collection points (Task 6)

**Why fourth:** With phases 1–3 done, every `add_correction` call is observable end-to-end via re-run + postflight.

**Files:**
- Modify: `goldenmatch/core/review_queue.py` (base class only)
- Modify: `goldenmatch/tui/tabs/boost_tab.py`
- Modify: `goldenmatch/core/cluster.py` (`unmerge_record`, `unmerge_cluster`)
- Modify: `goldenmatch/core/llm_scorer.py` (`llm_score_pairs`)
- Modify: `goldenmatch/mcp/agent_tools.py` (`agent_approve_reject` handler)
- Modify: `goldenmatch/api/server.py` (`POST /reviews/decide`)
- Test: `tests/test_memory_collection.py`

**Reference:** Foundation plan Task 6 has detailed code skeletons (review_queue, unmerge, llm_scorer). Reuse them with the spec-mandated changes:

- ReviewQueue hook lands on the **base class only** (not per-backend); pass `memory_store` into `ReviewQueue.__init__`.
- Unmerge functions write **empty hashes** (no df/matchkey_fields available; the empty-hash branch in `apply_corrections` already handles this).
- Boost tab: file is `tui/tabs/boost_tab.py`, NOT `tui/app.py`. Hook in `on_button_pressed` at **line 276**, in the `btn-match` / `btn-nonmatch` branches (NOT `btn-skip`). Note: BoostTab labels also feed an LR classifier downstream (`_record_label`) — keep that wiring untouched; the memory write is purely additive at the same handler. Source = `"boost"`, trust = 1.0.
- LLM scorer: at decision point, write `Correction` with `source="llm"`, `trust=0.5`, full hashes (df is in scope).
- agent_approve_reject (agent_tools.py:104): trust=0.5, source="agent".
- REST `POST /reviews/decide` (api/server.py:309): trust=1.0, source="steward". May not have df in scope — write empty hashes if so.

### Steps (per surface — repeat the TDD cycle)

For each surface, the cycle is:

- [ ] **A: Failing test** — invoke the surface with `memory_store` set; assert the store has the expected `Correction` record.
- [ ] **B: Run; confirm fails.**
- [ ] **C: Implement** — add `memory_store` parameter, wire `add_correction` call after existing logic, with the spec-mandated trust/source/hashes for that surface.
- [ ] **D: Run; confirm passes.**
- [ ] **E: Run full memory test suite; confirm no regressions.**
- [ ] **F: Commit per surface** — `feat(memory): wire <surface> as collection point`.

**Surface order (in the same PR, separate commits):**

- [ ] **4.1 ReviewQueue base class.**
- [ ] **4.2 unmerge_record + unmerge_cluster** (single commit; same file).
- [ ] **4.3 llm_score_pairs.**
- [ ] **4.4 agent_approve_reject.**
- [ ] **4.5 POST /reviews/decide.**
- [ ] **4.6 BoostTab y/n handlers.** TUI testing uses `pytest-asyncio` with `app.run_test()`. Per CLAUDE.md guidance, assert at the `MemoryStore` layer — don't introspect rendered UI.

- [ ] **4.7 Final commit covering all surfaces if not already split**

---

## Phase 5: Explainer integration (Addition 3, explainer half)

**Files:**
- Modify: `goldenmatch/core/explain.py`
- Test: `tests/test_memory_explainer.py`

### Steps

- [ ] **Step 5.1: Failing test — review queue items carry a `why` field**

`tests/test_memory_explainer.py`:

```python
from goldenmatch.core.review_queue import ReviewQueue
from goldenmatch.core.memory.store import MemoryStore

def test_review_queue_item_has_why_field(tmp_path):
    # ... seed a few weak-cluster pairs ...
    rq = ReviewQueue(memory_store=...)
    items = rq.list()
    assert all(hasattr(item, "why") and isinstance(item.why, str) and item.why for item in items)
```

- [ ] **Step 5.2: Implement default deterministic explainer path**

Extend `core/explain.py::explain_pair_nl` (already exists) so it accepts the matchkey + scores and returns a one-sentence string. Plumb a `why` field onto `ReviewItem`.

- [ ] **Step 5.3: Failing test — LLM upgrade path**

```python
def test_explainer_uses_llm_when_api_key_set(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    # ... mock the LLM call to return a known string ...
    # assert that string appears in the why field
```

- [ ] **Step 5.4: Implement LLM upgrade path**

When `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set AND `config.llm_scorer.enabled`, route through `core/llm_scorer.py` to generate richer prose. Reuse existing `BudgetTracker`. Fall back to deterministic on any error.

- [ ] **Step 5.5: Run all phase-5 tests; confirm green**

- [ ] **Step 5.6: Commit**

```bash
git commit -m "feat(memory): add why field to review queue + MCP results"
```

---

## Phase 6: CLI surfaces (Task 7) + Python API

**Files:**
- Create: `goldenmatch/cli/memory.py`
- Modify: `goldenmatch/cli/main.py:28,109`
- Modify: `goldenmatch/_api.py` — add `get_memory`, `add_correction`, `learn`, `memory_stats` functions
- Modify: `goldenmatch/__init__.py` — re-export the four new API functions
- Test: `tests/test_memory_cli.py`

### Steps

- [ ] **Step 6.1: Add Python API functions**

In `goldenmatch/_api.py`:

```python
def get_memory(path: str | None = None) -> "MemoryStore":
    from goldenmatch.core.memory.store import MemoryStore
    return MemoryStore(backend="sqlite", path=path or ".goldenmatch/memory.db")

def add_correction(id_a: int, id_b: int, decision: str, *,
                   source: str = "api", reason: str | None = None,
                   dataset: str | None = None,
                   matchkey_name: str | None = None,
                   path: str | None = None) -> None:
    import uuid
    from datetime import datetime
    from goldenmatch.core.memory.store import Correction
    store = get_memory(path)
    try:
        # Trust mapping per spec table: steward/boost/unmerge=1.0, llm/agent=0.5.
        # Default source "api" treated as agent-tier (0.5) — the Python API
        # caller is conceptually a programmatic actor; users wanting human
        # trust should pass source="steward" explicitly.
        store.add_correction(Correction(
            id=str(uuid.uuid4()), id_a=id_a, id_b=id_b,
            decision=decision, source=source,
            trust=1.0 if source in {"steward", "boost", "unmerge"} else 0.5,
            field_hash="", record_hash="", original_score=0.0,
            matchkey_name=matchkey_name, reason=reason,
            dataset=dataset, created_at=datetime.now(),
        ))
    finally:
        store.close()

def learn(matchkey_name: str | None = None,
          path: str | None = None) -> "list[LearnedAdjustment]":
    from goldenmatch.core.memory.learner import MemoryLearner
    store = get_memory(path)
    try:
        learner = MemoryLearner(store)
        return learner.learn(matchkey_name=matchkey_name)
    finally:
        store.close()

def memory_stats(path: str | None = None) -> dict:
    store = get_memory(path)
    try:
        return {
            "count": store.count_corrections(),
            "last_learn_time": store.last_learn_time(),
            "adjustments": [a.__dict__ for a in store.get_all_adjustments()],
        }
    finally:
        store.close()
```

Re-export from `goldenmatch/__init__.py`.

- [ ] **Step 6.2: Failing API tests**

`tests/test_memory_cli.py` (Python API portion):

```python
def test_api_add_and_count(tmp_path):
    import goldenmatch
    p = str(tmp_path / "mem.db")
    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    stats = goldenmatch.memory_stats(path=p)
    assert stats["count"] == 1
```

- [ ] **Step 6.3: Run; confirm passes** (functions exist after step 6.1)

- [ ] **Step 6.4: Create `cli/memory.py` with five subcommands**

Pattern from `cli/pprl.py` (look at it for reference). Each subcommand is a thin wrapper around the Python API. Use `rich.table.Table` for `stats` and `show` to match the rest of the CLI's output style.

```python
import typer
from rich.console import Console
from rich.table import Table
import goldenmatch

memory_app = typer.Typer(help="Inspect and manage Learning Memory.")

@memory_app.command("stats")
def stats_cmd(path: str = typer.Option(".goldenmatch/memory.db", "--path")):
    s = goldenmatch.memory_stats(path=path)
    # ... print table ...

@memory_app.command("learn")
def learn_cmd(...):
    ...

@memory_app.command("export")
def export_cmd(out: str, path: str = typer.Option(".goldenmatch/memory.db", "--path")):
    # write all corrections as CSV
    ...

@memory_app.command("import")
def import_cmd(src: str, path: str = typer.Option(".goldenmatch/memory.db", "--path")):
    # read CSV, validate columns, call store.add_correction per row
    ...

@memory_app.command("show")
def show_cmd(id_a: int, id_b: int, path: str = typer.Option(".goldenmatch/memory.db", "--path")):
    ...
```

- [ ] **Step 6.5: Register `memory_app` in `cli/main.py`**

Match the `pprl_app` pattern at `cli/main.py:28,109`:

```python
from goldenmatch.cli.memory import memory_app
# ...
app.add_typer(memory_app, name="memory")
```

- [ ] **Step 6.6: Failing CLI test**

```python
def test_cli_memory_stats_runs(tmp_path):
    from typer.testing import CliRunner
    from goldenmatch.cli.main import app
    runner = CliRunner()
    p = str(tmp_path / "mem.db")
    # Seed via API
    import goldenmatch
    goldenmatch.add_correction(1, 2, "approve", source="steward", path=p)
    result = runner.invoke(app, ["memory", "stats", "--path", p])
    assert result.exit_code == 0
    assert "1" in result.stdout
```

- [ ] **Step 6.7: Run; confirm passes**

- [ ] **Step 6.8: Round-trip export/import test**

```python
def test_cli_memory_export_import_roundtrip(tmp_path):
    # add 3 corrections, export to CSV, clear store, import, verify count == 3
```

- [ ] **Step 6.9: Commit**

```bash
git commit -m "feat(memory): CLI subgroup + Python API"
```

---

## Phase 7: MCP tools (Addition 2)

**Files:**
- Create: `goldenmatch/mcp/memory_tools.py`
- Modify: `goldenmatch/mcp/server.py:1266` (description string + import + register)
- Test: `tests/test_memory_tools.py`

### Steps

- [ ] **Step 7.1: Failing test — `list_corrections` tool registered**

```python
def test_memory_tools_registered():
    from goldenmatch.mcp.memory_tools import MEMORY_TOOLS, _MEMORY_TOOL_NAMES
    names = {t.name for t in MEMORY_TOOLS}
    assert names == {
        "list_corrections", "add_correction",
        "learn_thresholds", "memory_stats", "memory_export",
    }
    assert names == _MEMORY_TOOL_NAMES
```

- [ ] **Step 7.2: Run; confirm fails (module doesn't exist).**

- [ ] **Step 7.3: Create `mcp/memory_tools.py`**

Pattern matches `mcp/agent_tools.py` exactly. Five `Tool` definitions with `inputSchema`. One `handle_memory_tool(name, arguments)` function (mirrors `handle_agent_tool`) that routes to per-tool handlers and returns `list[TextContent]`. Each handler instantiates its own `MemoryStore` (no shared global state) and traps `sqlite3.OperationalError` to return structured errors rather than crash MCP.

Tools and required arguments (per spec Addition 2):

| Tool | Required args | Optional args |
|---|---|---|
| `list_corrections` | — | `dataset` |
| `add_correction` | `id_a`, `id_b`, `decision`, `dataset` | `reason`, `matchkey_name` |
| `learn_thresholds` | — | `matchkey_name` |
| `memory_stats` | — | `path` |
| `memory_export` | — | `dataset`, `path` |

`add_correction` writes with `source="agent"`, `trust=0.5`.

- [ ] **Step 7.4: Run test; confirm passes**

- [ ] **Step 7.5: Wire into `mcp/server.py`**

Find the existing tool registration section in `server.py`:
- Add `from goldenmatch.mcp.memory_tools import MEMORY_TOOLS, _MEMORY_TOOL_NAMES, handle_memory_tool` (name follows the existing `handle_agent_tool` symmetry in `agent_tools.py`)
- Extend the tool list returned by `list_tools()` with `MEMORY_TOOLS`
- In `call_tool()` dispatch, route to `handle_memory_tool` when `name in _MEMORY_TOOL_NAMES`
- At line 1266, change description from `"30 MCP tools"` to `"35 MCP tools"`

- [ ] **Step 7.6: Failing end-to-end MCP test**

```python
@pytest.mark.asyncio
async def test_mcp_add_and_list_correction(tmp_path):
    from goldenmatch.mcp.memory_tools import handle_memory_tool
    # add via tool
    res = await handle_memory_tool("add_correction", {
        "id_a": 1, "id_b": 2, "decision": "approve", "dataset": "test",
    })
    # list via tool
    res2 = await handle_memory_tool("list_corrections", {"dataset": "test"})
    text = res2[0].text
    assert "1" in text and "2" in text
```

- [ ] **Step 7.7: Run; confirm passes**

- [ ] **Step 7.8: Server-card description test**

```python
def test_server_card_description_count():
    import re
    from pathlib import Path
    src = Path("packages/python/goldenmatch/goldenmatch/mcp/server.py").read_text()
    match = re.search(r"(\d+) MCP tools", src)
    assert match and int(match.group(1)) == 35
```

- [ ] **Step 7.9: Commit**

```bash
git commit -m "feat(memory): five MCP tools (list/add/learn/stats/export)"
```

---

## Phase 8: Integration tests (Task 8)

**Files:**
- Create: `tests/test_memory_e2e.py`

### Test list (one test per scenario, end-to-end)

- [ ] **8.1 Happy path:** dedupe → reject one cluster pair via Python API → re-run → assert pair score is 0.0.
- [ ] **8.2 Re-anchor on reorder:** dedupe → reject pair → shuffle df → re-run → assert pair still rejected (`memory_stats.applied == 1`, `stats.stale == 0`).
- [ ] **8.3 Re-anchor + edit on matchkey field:** edit one matched field on a corrected entity → re-run → `memory_stats.stale >= 1`, pair appears in review queue.
- [ ] **8.4 Trust conflict:** LLM rejects pair (trust 0.5) → steward approves (trust 1.0) → assert steward's decision wins on next run.
- [ ] **8.5 Threshold learning:** seed 12 corrections covering a score range → re-run → assert `MemoryLearner.learn()` ran (`last_learn_time` advanced) and matchkey threshold was overlaid.
- [ ] **8.6 No API key, deterministic explainer:** unset `OPENAI_API_KEY` and `ANTHROPIC_API_KEY` → assert review queue items still have non-empty `why` (deterministic fallback).
- [ ] **8.7 Postflight surfaces stats:** run with seeded corrections → assert `memory_stats` populated and postflight string contains memory line.
- [ ] **8.8 Stale-ambiguous reported separately:** seed correction → re-run with literal duplicate row → assert `memory_stats.stale_ambiguous == 1` and postflight string contains "stale-ambiguous".

### Steps

- [ ] **Step 8.1: Write all eight tests in `tests/test_memory_e2e.py`**

Each test follows: build df → build config with memory enabled → seed correction(s) directly via `MemoryStore.add_correction` (in test setup, not via collection points — keeps tests fast) → run pipeline → assert result.

- [ ] **Step 8.2: Run; confirm all eight pass**

```bash
pytest packages/python/goldenmatch/tests/test_memory_e2e.py -v
```

- [ ] **Step 8.3: Run full goldenmatch test suite — no regressions**

```bash
pytest packages/python/goldenmatch/tests/ --tb=short -q
```

Expected: pre-existing 1319 + ~34 new (count varies by surface coverage in Phase 4) = ~1353+ tests, all green. Don't tie the assertion to a precise number — confirm the pre-existing 1319 stays green and the new tests pass.

- [ ] **Step 8.4: Commit**

```bash
git commit -m "test(memory): end-to-end integration suite"
```

- [ ] **Step 8.5: Bump version + changelog entry**

In `packages/python/goldenmatch/pyproject.toml` and `goldenmatch/__init__.py`, bump to `1.6.0` (minor — new feature, additive, fully backward compatible).

In `CHANGELOG.md`, add a "1.6.0" section describing Learning Memory wiring + re-anchor + MCP tools.

- [ ] **Step 8.6: Commit + open PR**

```bash
git commit -m "chore: bump goldenmatch 1.5.0 -> 1.6.0"
gh auth switch --user benzsevern
gh pr create --title "feat: Learning Memory completion (re-anchor, pipeline, surfaces)" \
  --body "$(cat <<'EOF'
## Summary
- Pipeline integration for Learning Memory (per spec 2026-05-04)
- Collision-safe vectorized re-anchor via record_hash
- Postflight surfaces applied/stale/stale-ambiguous counts
- Five MCP tools (list/add/learn/stats/export)
- Five CLI subcommands under `goldenmatch memory`
- Seven collection-point wirings across six files (review queue, boost, unmerge_record + unmerge_cluster in cluster.py, llm, agent, REST)
- Explainer integration (deterministic + LLM fallback)
- 58 new tests, 0 regressions on the existing 1319

## Spec
docs/superpowers/specs/2026-05-04-learning-memory-completion.md

## Test plan
- [x] All new tests pass
- [x] Pre-existing 1319 tests pass
- [ ] Manual smoke: `goldenmatch dedupe customers.csv` with seeded correction → applied
- [ ] Manual smoke: MCP `add_correction` from Claude Desktop → re-run → applied
EOF
)"
```

---

## Out of plan (deferred per spec)

- Rules layer (b) — separate brainstorm/spec.
- Web review surface — converges with browser-playground spec.
- MCP-sampling host-LLM explainer path.
- Identity Store extraction (Phase 2 of suite roadmap).
- Field-weight learning beyond the existing stub (requires schema addition).
- Postgres-backend parity testing for memory store (Postgres works today; full integration tests are SQLite-only).

---

## Risk register

- **Pipeline.py is 1024 LOC.** Phase 2's two insertions add ~30 LOC each. Avoid further growth — if Phase 2 + Phase 3 push it over 1100 LOC, consider extracting a `_pipeline_memory.py` helper module before merging.
- **Boost tab tests are slow.** Phase 4.6 must assert at the `MemoryStore` layer, not the rendered UI, or test runtime will balloon.
- **MCP server.py is 1281 LOC.** Phase 7's `memory_tools.py` keeps new tools out of `server.py`; only ~5 LOC of registration lands in server.py.
- **Test fixture seeding for Phase 2.** Until Phase 4 lands, pipeline-hook tests must seed `MemoryStore` directly. Document this in test docstrings.
- **Postgres backend.** The shipped code accepts a Postgres backend but only SQLite is fully exercised. Integration tests in Phase 8 are SQLite-only; if a user reports a Postgres issue, it's a follow-up — not a launch blocker.
