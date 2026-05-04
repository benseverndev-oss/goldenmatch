# Design: Learning Memory completion (slice-1)

**Date:** 2026-05-04
**Author:** Ben Severn (with Claude)
**Status:** Draft
**Foundation:** [`_archive/goldenmatch-pre-fold/docs/superpowers/specs/2026-03-26-learning-memory-design.md`](../../../_archive/goldenmatch-pre-fold/docs/superpowers/specs/2026-03-26-learning-memory-design.md) (Approved 2026-03-26; Tasks 1–4 shipped)

## Context

The 2026-03-26 Learning Memory spec was approved and Tasks 1–4 (config, store, corrections, learner) were shipped under PR #9. Tasks 5–8 (pipeline integration, collection-point wiring, surfaces, integration tests) remain unchecked. Since the pre-fold work, three gaps have surfaced that the original spec does not address:

1. **Row-ID stability under input refresh.** Today, `apply_corrections()` looks up corrections by `(id_a, id_b)` (the positional `__row_id__`). If the input data is refreshed and rows reorder, stored corrections never match — they go silently unapplied. The dual-hash check protects safety (no false positives) but loses durability (false negatives on every refresh).
2. **MCP tool surface.** The original spec lists Python API + CLI; MCP is not covered, despite goldenmatch having 30+ MCP tools elsewhere.
3. **Explainer + postflight reporting.** The original spec does not describe how corrections are narrated in the review queue, nor how applied/stale counts surface in `DedupeResult`.

This spec closes the unchecked tasks **and** addresses these three gaps. The rules layer (LLM-generalized IF-THEN principles from corrections) is explicitly **out of scope** and will be a separate brainstorm/spec.

## Goals

- All collection points in the original spec write to `MemoryStore`.
- Pipeline applies corrections after scoring, applies learned threshold/weight adjustments before scoring, and surfaces `CorrectionStats` on `DedupeResult`.
- Corrections survive input refresh (row reorder, row insertion/deletion) without becoming stale, *as long as* the entity rows themselves are unchanged.
- Surfaces: Python API, CLI, MCP tools.
- Integration tests cover end-to-end correct → re-run → apply, refresh → re-anchor, trust conflict, no-API-key fallback.
- Zero-config posture preserved: nothing about auto-config or default behavior changes for users who don't enable memory.

## Non-goals

- Rules layer (LLM generalization of corrections into IF-THEN principles).
- Web review surface.
- MCP-sampling for explainer (host-LLM-driven explanation).
- Identity Store extraction (the schema is forward-compatible; migration is deferred).
- Team sync, multi-user backends beyond the existing Postgres option.
- Field-weight learning beyond what `MemoryLearner` already produces (per-field scores not yet stored on corrections; learner stub returns `None` and that stays for slice-1).

## Foundation summary (from 2026-03-26 spec, shipped)

For reference. None of this is changing.

- `MemoryStore` — SQLite (default `.goldenmatch/memory.db`) and Postgres backends; trust-based upsert; canonical `(min, max)` pair ordering; `UNIQUE(id_a, id_b, dataset)`.
- `Correction` — id_a, id_b, decision (approve/reject), source, trust (1.0 human / 0.5 agent), field_hash, record_hash, original_score, matchkey_name, reason, dataset, created_at.
- `apply_corrections()` — bulk fetch by row IDs, dual-hash staleness check (field_hash for matched fields, record_hash for full row), hard override (1.0 / 0.0) when both hashes match.
- `MemoryLearner` — threshold tuning at 10+ corrections via grid search weighted by trust; field-weight learning stubbed (returns `None`).
- `MemoryConfig`, `LearningConfig` — Pydantic models in `config/schemas.py`. `GoldenMatchConfig.memory: MemoryConfig | None`.
- 48 tests in `tests/test_memory_store.py`, `test_corrections.py`, `test_learner.py`, `test_memory_integration.py`.

## Addition 1: Re-anchoring via record_hash

**Problem.** `apply_corrections()` looks up by `(id_a, id_b)`. When input rows reorder (e.g., resorted CSV, refreshed warehouse table), the same entities now have different row IDs, so lookups miss and corrections silently fail to apply. Users perceive this as "the system forgot."

**Insight.** `record_hash` is already stored on every correction and is durable — it identifies the entity by content rather than position.

**Fix.** Augment `apply_corrections()` with a record-hash-keyed re-anchoring path. No schema migration; uses existing fields.

**Algorithm:**

```python
def apply_corrections(
    scored_pairs: list[tuple[int, int, float]],
    store: MemoryStore,
    df: pl.DataFrame,
    matchkey_fields: list[str],
    dataset: str | None = None,
) -> tuple[list[tuple[int, int, float]], CorrectionStats]:
    # 1. Single fetch (replaces both the existing get_pair_corrections_bulk
    #    and the re-anchor pass; bulk already loads all dataset corrections).
    all_corrections = store.get_corrections(dataset=dataset)
    if not all_corrections:
        return scored_pairs, stats

    # 2. Build vectorized record_hash → [row_ids] map ONCE.
    #    Use polars-native concat_str + hash to avoid O(N) per-row filters.
    sorted_cols = sorted(df.columns)
    hashed = (
        df.select(
            pl.col("__row_id__"),
            pl.concat_str([pl.col(c).cast(pl.Utf8) for c in sorted_cols], separator="|")
              .map_elements(lambda s: hashlib.sha256(s.encode()).hexdigest()[:16],
                            return_dtype=pl.Utf8)
              .alias("__rec_hash__"),
        )
    )
    hash_to_rids: dict[str, list[int]] = {}
    for rid, h in zip(hashed["__row_id__"].to_list(), hashed["__rec_hash__"].to_list()):
        hash_to_rids.setdefault(h, []).append(int(rid))

    # 3. Build the active correction map. Direct match (id_a/id_b still valid)
    #    takes precedence; only fall back to re-anchor when direct row IDs are
    #    not present in the current df.
    current_rids = {rid for rids in hash_to_rids.values() for rid in rids}
    active: dict[tuple[int, int], Correction] = {}
    stats_skipped_ambiguous = 0
    for c in all_corrections:
        if c.id_a in current_rids and c.id_b in current_rids:
            active[_canon_pair(c.id_a, c.id_b)] = c
            continue
        # Re-anchor via record_hash. Stored as "<hash_a>:<hash_b>".
        ha, hb = (c.record_hash.split(":", 1) + [""])[:2]
        cands_a = hash_to_rids.get(ha, [])
        cands_b = hash_to_rids.get(hb, [])
        # Collision-safe: only re-anchor when each side resolves uniquely.
        # Ambiguous resolutions (duplicate rows) are counted as stale and surfaced.
        if len(cands_a) == 1 and len(cands_b) == 1:
            active[_canon_pair(cands_a[0], cands_b[0])] = c
        elif cands_a and cands_b:
            stats_skipped_ambiguous += 1
            stats.stale_pairs.append((c.id_a, c.id_b))
        # else: entity no longer present → silently dropped (existing stale path)

    # 4. Apply with existing dual-hash safety check (unchanged behavior).
    # For each scored pair: look up active[canon_pair(a,b)]; if found, run the
    # existing field_hash + record_hash check; on match, override score (1.0/0.0).
    # ... existing apply logic ...
```

**Invariants preserved:**
- Dual-hash staleness check still runs before applying any correction; re-anchored corrections referencing now-stale entity content still fail safely.
- No schema change; existing tests stay green.
- **Collision safety:** when two current rows share an identical `record_hash` (real case: actual duplicate rows in input), the re-anchor refuses to guess and counts the correction as stale-ambiguous. No false-positive applications.

**Cost:**
- One vectorized O(N) hash pass over the df via `pl.concat_str` + `map_elements`. Single Python function call across all rows; no O(N) filter per row.
- O(C) work for C stored corrections (lookups in `hash_to_rids`).
- Skipped entirely on cold runs (no corrections in store).
- Opt-out: `config.memory.reanchor: bool = True` (added to `MemoryConfig`, see Surfaces).

**New tests:**
- Row reorder: shuffle df between runs, verify corrections still apply.
- Row insert/delete: insert new rows; verify corrections on unchanged entities still apply, corrections on deleted entities are reported as stale via the existing path.
- Edit on non-matchkey field: verify correction still applies (field_hash unchanged) — captures intent of the original dual-hash design.
- Edit on matchkey field: verify correction goes stale (field_hash changes) — captures intent.

## Addition 2: MCP tool surface

Pattern follows `mcp/agent_tools.py` (additive Tool list + dispatch handler + server-card count update).

**New tools** in `mcp/memory_tools.py`:

| Tool | Purpose |
|---|---|
| `list_corrections` | Returns all corrections (optionally filtered by `dataset`). Includes `why` field from explainer. |
| `add_correction` | Adds a pair correction. Required: `id_a`, `id_b`, `decision`, `dataset`. Optional: `reason`, `matchkey_name`. Trust = 0.5 (agent source). |
| `learn_thresholds` | Forces `MemoryLearner.learn()`. Returns list of `LearnedAdjustment`. |
| `memory_stats` | Returns counts, last learn time, current adjustments. Cheap; safe for status checks. |
| `memory_export` | Returns corrections as a list of dicts (CSV-shaped). Caller writes the file. |

Each handler instantiates its own `MemoryStore` (matches the AgentSession pattern — no shared global state). All handlers validate `dataset` is non-empty and trap `sqlite3.OperationalError` to return a structured error rather than crash the MCP session.

**Module exports** in `mcp/memory_tools.py`:
- `MEMORY_TOOLS: list[Tool]` — the five tool definitions
- `_MEMORY_TOOL_NAMES: frozenset[str]` — for fast membership checks
- `handle_memory_tool(name: str, arguments: dict) -> list[TextContent]` — routes to per-tool handlers (mirrors `handle_agent_tool` in agent_tools.py)

`mcp/server.py` imports these and extends its tool list + dispatch chain — pattern matches `agent_tools.py` (`AGENT_TOOLS`, `_AGENT_TOOL_NAMES`, `handle_agent_tool`).

**Server-card update:** edit the literal description string in `mcp/server.py:1266` from `"30 MCP tools"` to `"35 MCP tools"`. (The count is hardcoded in the server-card description, not dynamically computed.) Add `test_memory_tools_registered` to `tests/test_mcp_and_watch.py`.

## Addition 3: Explainer + postflight

**Explainer.** Every correction surfaced through MCP `list_corrections`, the review queue, and the TUI boost tab carries a `why` field — a one-sentence explanation of the original match decision.

- Default: `core/explain.py::explain_pair_nl()` — deterministic, template-based, zero cost. Already shipped.
- Upgrade: when `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` is set AND `config.llm_scorer.enabled`, route through `core/llm_scorer.py` for richer prose. Reuses existing `BudgetTracker`; respects existing budget caps. No new API surface.
- MCP-sampling path (host LLM does the prose) is explicitly **deferred** to a later spec.

**Postflight.** Both `DedupeResult` and `MatchResult` (in `_api.py`) gain:

```python
@dataclass
class DedupeResult:  # also MatchResult
    # ... existing fields ...
    memory_stats: CorrectionStats | None = None  # populated when memory enabled
```

The existing v1.5.0 postflight report (`postflight_report` field, present on both result types) gains a one-line memory section when `memory_stats` is set, rendered by the same code that renders the rest of postflight:

```
Memory: 23 corrections applied, 4 stale, 1 stale-ambiguous (run `goldenmatch review` to re-decide stale pairs)
```

`memory_stats.stale_pairs` is automatically enqueued into `ReviewQueue` so the next `goldenmatch review` invocation surfaces them. Stale-ambiguous pairs (collision-safe re-anchor refused — see Addition 1) are reported separately because they need user attention to resolve which of the duplicate rows the original correction was about.

## Pipeline integration (Task 5 acceptance criteria)

In `core/pipeline.py`, both `_run_dedupe_pipeline()` and `_run_match_pipeline()`:

1. **Pre-scoring (learning overlay):** if `config.memory and config.memory.enabled`, open `MemoryStore`, instantiate `MemoryLearner`, call `learner.has_new_corrections()` → `learner.learn()`. Overlay each `LearnedAdjustment.threshold` onto the matching matchkey's `threshold` field on the **in-memory config copy only** (original YAML/Pydantic config never mutated).
2. **Post-scoring (correction overlay):** call `apply_corrections(scored_pairs, store, df, matchkey_fields, dataset=dataset)` between scoring and clustering. Replace `scored_pairs` with the returned adjusted list. Attach returned `CorrectionStats` to `DedupeResult.memory_stats`.
3. **Stale enqueue:** push `stats.stale_pairs` to `ReviewQueue` for next-run review.
4. **Dataset scoping:** default to input file path (or `"<DataFrame>"` for df entry points); honor `config.memory.dataset` override.

Failure mode: if `MemoryStore` open fails (corrupt DB, permission error), log a warning and continue without memory. Never block the pipeline.

## Collection points (Task 6 acceptance criteria)

All additive — existing behavior unchanged when memory disabled. Each surface accepts an optional `MemoryStore` parameter; if provided, calls `store.add_correction()` alongside its existing logic.

| Surface | File | Source | Trust | Trigger |
|---|---|---|---|---|
| Review Queue | `core/review_queue.py` (base class `ReviewQueue.approve()`/`.reject()`) | `"steward"` | 1.0 | hook lands once on the base class so memory/SQLite/Postgres backends inherit it; do not duplicate per-backend |
| Boost Tab | `tui/tabs/boost_tab.py` (`BoostTab` class, around the y/n button handlers) | `"boost"` | 1.0 | y/n keypress |
| Unmerge record | `core/cluster.py::unmerge_record()` | `"unmerge"` | 1.0 | reject correction for every pair `(record_id, other)` in cluster's `pair_scores`; **empty hashes** (function takes no df) — see hash-collection note below |
| Unmerge cluster | `core/cluster.py::unmerge_cluster()` | `"unmerge"` | 1.0 | reject correction for every pair in `pair_scores`; **empty hashes** — see hash-collection note below |
| LLM Scorer | `core/llm_scorer.py::llm_score_pairs()` | `"llm"` | 0.5 | LLM returns approve/reject decision (only when `store` provided) |
| Agent Tools | `mcp/agent_tools.py::agent_approve_reject` (line 104) | `"agent"` | 0.5 | tool invocation |
| REST API | `api/server.py::POST /reviews/decide` (line 309) | `"steward"` | 1.0 | endpoint call |

**Hash collection.** Where the df is in scope, `field_hash` and `record_hash` are computed at correction time. Where it isn't (`unmerge_record`, `unmerge_cluster`, possibly REST API receiving raw decisions), corrections are written with **empty hashes**. The shipped `apply_corrections()` already handles this branch (`hashes_empty = (not correction.field_hash and not correction.record_hash)` at corrections.py:107) — empty-hash corrections always apply when the row IDs match, with no staleness gate. This is a deliberate trade-off: unmerge corrections cannot detect data drift, but they will always reflect the user's "these don't belong together" intent. Re-anchoring still works for empty-hash corrections via the row-ID-presence path; it cannot work via record_hash because none was stored.

**Trust same-tier semantics.** `MemoryStore.add_correction()` (store.py:108-111) only ignores incoming corrections of *strictly lower* trust than the existing one. Same-tier writes overwrite (latest wins). Document this in the user-facing CLI help and MCP tool descriptions so users aren't surprised when a second `add_correction` from the same source replaces the first.

## Surfaces (Task 7 acceptance criteria)

**Python API** (additions to `_api.py`, re-exported via `__init__.py`):
- `get_memory(path: str | None = None) -> MemoryStore`
- `add_correction(id_a: int, id_b: int, decision: str, *, source: str = "api", reason: str | None = None, dataset: str | None = None, matchkey_name: str | None = None) -> None`
- `learn(matchkey_name: str | None = None) -> list[LearnedAdjustment]`
- `memory_stats() -> dict`

**CLI** (new file `cli/memory.py` exposing a `memory_app: typer.Typer` object, registered via `app.add_typer(memory_app, name="memory")` in `cli/main.py` — pattern matches `pprl_app` at cli/main.py:28/109):
- `goldenmatch memory stats` — counts, last learn time, current adjustments
- `goldenmatch memory learn` — force learning pass; print results
- `goldenmatch memory export <path>` — dump corrections as CSV
- `goldenmatch memory import <path>` — load corrections from CSV (validates schema, respects trust upsert)
- `goldenmatch memory show <id_a> <id_b>` — pretty-print a single correction

**Config schema** addition to `MemoryConfig` (already in `config/schemas.py`):
```python
class MemoryConfig(BaseModel):
    # ... existing fields ...
    reanchor: bool = True              # NEW: enable record_hash re-anchoring (Addition 1)
    dataset: str | None = None          # NEW: scope corrections to a named dataset (referenced
                                        # but not previously schema-declared in the 2026-03-26 spec)
```
`reanchor` defaults to `True` so re-anchoring is on by default; `False` disables and falls back to row-ID-only lookup. `dataset` is the scoping key for `MemoryStore`; defaults to `None`, in which case the pipeline derives a default (input file path, or `"<DataFrame>"` for df entry points).

**MCP** — five tools per Addition 2.

## Integration tests (Task 8 acceptance criteria)

New file `tests/test_memory_e2e.py`:

1. **Happy path:** dedupe → reject one cluster pair via TUI → re-run → verify pair score is now 0.0.
2. **Re-anchor on reorder:** dedupe customers.csv → reject a pair → shuffle df → re-run → verify pair still rejected.
3. **Re-anchor + edit on matchkey field:** edit one matched field on a corrected entity → re-run → verify correction goes stale, pair shows up in review queue.
4. **Trust conflict:** LLM rejects a pair (trust 0.5), then steward approves (trust 1.0) → verify steward's decision wins.
5. **Threshold learning:** seed 12 corrections covering a range of scores → re-run → verify `MemoryLearner.learn()` ran, threshold overlay applied to matchkey, `last_learn_time` updated.
6. **No API key, no LLM:** unset env vars → verify deterministic explainer fallback for review queue and MCP `list_corrections`.
7. **Postflight surfaces stats:** run with corrections present → verify `DedupeResult.memory_stats` populated and postflight string contains the one-line memory section.

Existing 48 unit tests must remain green. Test count target: 48 + ~10 = ~58.

## Out of scope (deferred to later specs)

- **Rules layer** — LLM-generalized IF-THEN principles from corrections.
- **Web review surface** — converges with the standalone "browser playground" idea; separate effort.
- **MCP-sampling explainer path** — host LLM does prose generation via MCP `sampling`. Requires host support that's still uneven.
- **Identity Store extraction** — the schema is forward-compatible (entities, aliases, assertions, provenance, dataset scoping) but extracting it as a separate package and wiring reverse-ETL is Phase 2 work.
- **Field-weight learning beyond stub** — requires storing per-field scores on corrections; schema addition.
- **Team sync / external backend beyond existing Postgres** — multi-user/multi-machine semantics.

## Implementation order

Each phase mergeable on its own behind feature flag (`config.memory.enabled` already gates everything).

1. **Re-anchoring (Addition 1).** Localized change to `core/memory/corrections.py`; new tests for reorder/edit/duplicate-row cases. Foundational because everything else depends on corrections actually persisting across runs.
2. **Pipeline hook (Task 5).** Nothing observable works until this lands. Includes `DedupeResult.memory_stats` and `MatchResult.memory_stats` fields, stale-pair enqueue, `MemoryConfig.reanchor` flag plumbing. **Test note:** until phase 4 (collection points) lands, pipeline-hook tests must seed the store via fixtures (write `Correction` objects directly through `MemoryStore.add_correction()` in test setup).
3. **Postflight wiring (Addition 3, postflight half).** One-line memory section in postflight report on both result types.
4. **Collection points (Task 6).** Corrections start flowing into the store from real surfaces. ReviewQueue hook on the base class only (not per-backend).
5. **Explainer integration (Addition 3, explainer half).** `why` field on review queue items and MCP results.
6. **CLI surfaces (Task 7).** `goldenmatch memory stats|learn|export|import|show` via `memory_app` Typer subgroup.
7. **MCP tools (Addition 2).** Five new tools in `mcp/memory_tools.py`, server-card string update at server.py:1266, registration test.
8. **Integration tests (Task 8).** End-to-end coverage gates the release.

Each phase is its own PR. Estimated effort: 1–2 days per phase for phases 1–6, 0.5 days for phase 7, 1 day for phase 8.

## Risks

- **Re-anchoring cost on large inputs.** Building `{record_hash → row_id}` is O(N). On a 1M-row input with 100 corrections, this is one O(N) pass plus 100 O(1) lookups — well under existing pipeline costs. Validated by an opt-out flag (`config.memory.reanchor: bool = True`) in case a pathological case appears.
- **Postgres backend drift.** Original spec mentions Postgres support but the shipped code only fully exercises SQLite. Slice-1 keeps Postgres as a documented option but does not gate on it; integration tests use SQLite. Postgres parity is a follow-up if/when a user needs it.
- **Boost tab and TUI tests are slow.** Existing `tests/test_tui.py` uses `pytest-asyncio` with `app.run_test()` pilot — adding correction-write assertions will slow this further. Mitigation: assert at the `MemoryStore` layer in TUI tests, not the rendered UI.
- **MCP server.py is 1,281 LOC.** Adding tools inline would worsen this. Per pattern in `mcp/agent_tools.py`, new tools live in `mcp/memory_tools.py` and are imported into `server.py` with a single registration call.

## Open questions resolved

- **Q4 row-ID stability:** re-anchor via existing `record_hash`; no schema migration; collision-safe (ambiguous re-anchors counted stale, never silently misapplied); single `reanchor` config flag for opt-out.
- **Gap-fill spec vs new full design:** gap-fill, referencing 2026-03-26 spec.
- **Rules layer (b):** deferred to a separate spec.

## Review notes (2026-05-04)

This spec was reviewed against the shipped code (PR #9 modules + foundation pipeline/api/cluster/tui code) before plan generation. Findings folded into the spec rather than left as a separate review log:

- Boost tab path corrected (`tui/tabs/boost_tab.py`, not `tui/app.py`).
- Unmerge collection points cannot compute hashes; documented as deliberate empty-hash writes.
- `MatchResult` parity with `DedupeResult` for `memory_stats`.
- Server-card edit is a literal-string change at server.py:1266, not a dynamic count.
- Re-anchor algorithm rewritten to be collision-safe and vectorized (was O(N²) per call as originally drafted).
- ReviewQueue hook lands on the base class to cover all three backends.
- `MemoryConfig.reanchor` flag added explicitly.
- Trust same-tier (latest-wins) semantics documented.

48 unit tests confirmed across the four memory test files; pipeline hook insertion point at pipeline.py:497-514 (between scoring and `_apply_postflight`) is sensible and uncontested.
