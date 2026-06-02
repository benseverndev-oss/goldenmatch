# Decouple DedupeResult.scored_pairs from cluster pair_scores (Phase 2 SP3) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Source `DedupeResult.scored_pairs` from the pipeline's pre-cluster scored-pair stream (normalized via `dedup_pairs_max_score`), stored once on the result, so the `scored_pairs`-consuming surfaces stop reconstructing it from cluster `pair_scores`.

**Architecture:** In `_run_dedupe_pipeline`, after the cluster stage, store `results["scored_pairs"] = dedup_pairs_max_score(<scored-pair stream>)` (list path: `all_pairs`; columnar path: `pairs_df_to_list(_columnar_pairs_df)`). `_api.py` reads that field for `DedupeResult.scored_pairs` (removing `_extract_pairs`); `cli/label.py` + web `run`/`preview` read it instead of looping `cinfo["pair_scores"]`. This is a documented behavior change (scored_pairs becomes the full canonical, max-deduped, sorted scored set — a superset when oversized clusters split), NOT byte-identical.

**Tech Stack:** Python 3.11+, Polars. Pure-Python SP3. No gate (unconditional source change).

**Spec:** `docs/superpowers/specs/2026-06-02-scored-pairs-decouple-design.md` — READ it. **Branch:** `feat/scored-pairs-decouple`.

**Run tests:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v`. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`. Do NOT run the full suite (xdist OOMs); targeted files only.

## Background the implementer needs

- `core/pipeline.py::_run_dedupe_pipeline`: the cluster stage is at ~:1446-1461 with branch `if _use_columnar and _columnar_pairs_df is not None:` (columnar `build_clusters_columnar`) `else:` (`build_clusters(all_pairs, ...)`). `all_pairs` (`list[(int,int,float)]`) and `_columnar_pairs_df` (a `PAIR_STREAM_SCHEMA` Polars df) are BOTH still in scope at the `results` assembly (~:1732) — verified, not `del`'d (`all_pairs` is used later at lineage ~:1701 / identity ~:1728).
- `core/pairs.py::dedup_pairs_max_score(pairs: list[(a,b,score)]) -> list[(a,b,score)]` (`:35`): canonicalizes to `(min,max)`, keeps the MAX score per canonical pair, returns sorted ascending by `(a,b)`. Import: `from goldenmatch.core.pairs import dedup_pairs_max_score`.
- `core/scorer.py::pairs_df_to_list(df) -> list[(int,int,float)]` (`:1283`): converts a `PAIR_STREAM_SCHEMA` df (`id_a,id_b,score`) to the list shape. Import: `from goldenmatch.core.scorer import pairs_df_to_list`. Its docstring says "Removed in Phase 1c" — SP3 makes it a live dependency; UPDATE that stale docstring line.
- `_api.py`: `_extract_pairs(result)` (`:1135`) flattens cluster `pair_scores`; called at `:300` (`dedupe`) and `:473` (`dedupe_df`) to set `DedupeResult.scored_pairs`. `DedupeResult.scored_pairs` field + docstring at `:76`/`:84`.
- Consumers reconstructing scored_pairs from `cinfo["pair_scores"]`: `cli/label.py:43-45`, `web/routers/run.py:127-129`, `web/preview.py:185-188`.
- `cli/label.py` calls `run_dedupe` (returns the RAW pipeline dict) — read `result.get("scored_pairs", [])` there. `_api.dedupe/dedupe_df` callers get `DedupeResult.scored_pairs`.
- `tests/test_api.py:653-663` has `test_extract_pairs` + `test_extract_pairs_empty` which directly test `_extract_pairs` — these must be DELETED when the function is removed (not adjusted).
- **Behavior change is accepted** (spec): scored_pairs = canonical + max-deduped + sorted full scored set. Equal to today's on no-split; superset (gains cross-cut edges) on split.

---

## File Structure

- **Modify** `goldenmatch/core/pipeline.py`: store `results["scored_pairs"]` from the normalized stream (both paths).
- **Modify** `goldenmatch/_api.py`: source `scored_pairs` from the result field; remove `_extract_pairs`; update `DedupeResult.scored_pairs` docstring.
- **Modify** `goldenmatch/core/scorer.py`: update the stale `pairs_df_to_list` docstring (live dependency now).
- **Modify** `goldenmatch/cli/label.py`, `goldenmatch/web/routers/run.py`, `goldenmatch/web/preview.py`: read the stored `scored_pairs`.
- **Create** `tests/test_scored_pairs_decouple.py`: parity (no-split canonical-set + scores), split-superset, columnar==list.
- **Modify** `tests/test_api.py`: delete `test_extract_pairs` / `test_extract_pairs_empty`.

---

## Task 1: Pipeline capture + `_api` source + parity tests

**Files:**
- Modify: `goldenmatch/core/pipeline.py`, `goldenmatch/_api.py`, `goldenmatch/core/scorer.py`
- Test: `tests/test_scored_pairs_decouple.py` (create); `tests/test_api.py` (delete 2 tests)

- [ ] **Step 1: Write the failing parity tests**

Create `tests/test_scored_pairs_decouple.py`. Use `dedupe_df` (or `run_dedupe_df`) on small synthetic frames. Helper to flatten cluster pair_scores to a canonical-pair set:

```python
import polars as pl
import pytest
from goldenmatch import dedupe_df


def _cluster_pair_keys(result_clusters) -> set:
    keys = set()
    for cinfo in result_clusters.values():
        for (a, b) in cinfo.get("pair_scores", {}):
            keys.add((min(a, b), max(a, b)))
    return keys


def _dup_df():
    # Small person-ish frame with obvious duplicates, no oversized clusters.
    return pl.DataFrame({
        "name": ["Jon Smith", "Jon Smith", "Jane Doe", "Jane Doe", "Bob Lee"],
        "city": ["NYC", "NYC", "LA", "LA", "SF"],
    })


def test_scored_pairs_canonical_set_matches_clusters_no_split():
    res = dedupe_df(_dup_df(), exact=["name", "city"])
    sp_keys = {(min(a, b), max(a, b)) for (a, b, _s) in res.scored_pairs}
    assert sp_keys == _cluster_pair_keys(res.clusters)
    # scores: every scored pair's score is present (exact -> 1.0)
    assert all(0.0 <= s <= 1.0 for (_a, _b, s) in res.scored_pairs)


def test_scored_pairs_sorted_and_deduped():
    res = dedupe_df(_dup_df(), exact=["name", "city"])
    pairs = [(a, b) for (a, b, _s) in res.scored_pairs]
    assert pairs == sorted(pairs)                 # sorted by (a,b)
    assert len(pairs) == len(set(pairs))          # canonical-deduped
```

(Adapt the `exact=`/`dedupe_df` call to the real signature — check `_api.dedupe_df`. Use whatever minimal call produces multi-member clusters. If `dedupe_df` zero-config triggers model downloads, pass an explicit config or `exact=` kwargs per the package CLAUDE.md offline pattern.)

- [ ] **Step 2: Run — verify FAIL**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_scored_pairs_decouple.py -v`
Expected: FAIL — today `scored_pairs` is cluster-grouped (not sorted by (a,b)) so `test_scored_pairs_sorted_and_deduped` fails; the canonical-set test may pass or fail depending on orientation. (Genuine RED on at least the sorted/deduped assertion.)

- [ ] **Step 3: Capture normalized scored_pairs in the pipeline**

In `core/pipeline.py::_run_dedupe_pipeline`, after the cluster stage and before/at the `results` assembly (~:1732), add:

```python
from goldenmatch.core.pairs import dedup_pairs_max_score
from goldenmatch.core.scorer import pairs_df_to_list
if _use_columnar and _columnar_pairs_df is not None:
    _scored_pairs = dedup_pairs_max_score(pairs_df_to_list(_columnar_pairs_df))
else:
    _scored_pairs = dedup_pairs_max_score(all_pairs)
```

and include `"scored_pairs": _scored_pairs` in the `results` dict. (Put the imports at module top per the repo's import convention; the inline form here is illustrative.) Mirror the existing cluster-stage branch key EXACTLY (`_use_columnar and _columnar_pairs_df is not None`) so capture matches the path the clusters were built from.

- [ ] **Step 4: Source `scored_pairs` from the field in `_api.py`; remove `_extract_pairs`**

- At `_api.py:300` and `:473`, replace `scored_pairs=_extract_pairs(result)` with `scored_pairs=result.get("scored_pairs", [])`.
- DELETE the `_extract_pairs` function (`:1135-1141`) — grep confirms no other callers.
- Update the `DedupeResult.scored_pairs` docstring (`:76`/`:84`) to: "All scored pairs as canonical `(min_id, max_id, score)`, sorted by id pair, max-score deduped. The full scored set (includes pairs auto-split later removed from clusters)."
- In `tests/test_api.py`, DELETE `test_extract_pairs` and `test_extract_pairs_empty` (`:653-663`).

- [ ] **Step 5: Update the stale `pairs_df_to_list` docstring**

In `core/scorer.py:1283`, remove/replace the "Removed in Phase 1c" line (the function is now a live SP3 dependency).

- [ ] **Step 6: Run the parity tests — verify PASS**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_scored_pairs_decouple.py -v`
Expected: PASS.

- [ ] **Step 7: Split-superset + columnar==list tests**

Add to `tests/test_scored_pairs_decouple.py`:
- **Split superset:** build a frame whose dedupe produces an oversized cluster that splits (pass a config with a small `golden_rules` `max_cluster_size`, e.g. 2, via `dedupe_df(df, config=...)`, OR construct clusters directly through `run_dedupe_df` with a config). Assert `{(min(a,b),max(a,b)) for (a,b,_) in res.scored_pairs}` is a SUPERSET of `_cluster_pair_keys(res.clusters)` (cross-cut edges present in scored_pairs but dropped from post-split clusters). If wiring a real split through the public API is impractical, assert the weaker invariant that `_cluster_pair_keys ⊆ scored_pairs canonical set` always holds (subset, never missing) — document why.
- **Columnar == list:** run the same frame with the columnar pipeline gate OFF and ON (`monkeypatch.setenv("GOLDENMATCH_COLUMNAR_PIPELINE", "1")` for ON; confirm the env var name via `core/pipeline.py::_columnar_pipeline_enabled`), assert `set(res_off.scored_pairs) == set(res_on.scored_pairs)` (multiset/set equal).

Run the file; verify PASS. If the split can't be triggered via the public API cleanly, keep the subset assertion and note it.

- [ ] **Step 8: ruff + commit**

ruff check the changed files. Commit: `feat(api): source DedupeResult.scored_pairs from pre-cluster stream (decouple from cluster pair_scores)`.

---

## Task 2: migrate the consumer reads

**Files:**
- Modify: `goldenmatch/cli/label.py`, `goldenmatch/web/routers/run.py`, `goldenmatch/web/preview.py`

- [ ] **Step 1: Write/adjust a consumer smoke test**

In `tests/test_scored_pairs_decouple.py`, add a test that `goldenmatch label` (or its underlying candidate-pair extraction) produces a non-empty candidate-pair list from a dup fixture via the new source. Prefer a focused unit/integration test over a full CLI invocation if the CLI is heavy — e.g. assert that the code path reading `result.get("scored_pairs", [])` yields the expected pairs. (If a web lineage test exists under `tests/web/`, extend it to assert pairs still populate; otherwise a label-path test suffices.)

- [ ] **Step 2: Migrate `cli/label.py:43-45`**

Replace the `for ... cinfo["pair_scores"]` reconstruction loop with `pairs = result.get("scored_pairs", [])` (the raw pipeline dict from `run_dedupe`). Keep the downstream "No pairs found" guard.

- [ ] **Step 3: Migrate `web/routers/run.py:127-129` and `web/preview.py:185-188`**

Replace the `cinfo["pair_scores"]` reconstruction with reading the stored `scored_pairs` (`result.get("scored_pairs", [])`) before feeding `build_lineage`.

- [ ] **Step 4: Run — verify PASS + no regressions**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_scored_pairs_decouple.py tests/test_api.py -q` (+ any `tests/web/` lineage test you touched). Confirm green. Flag any pre-existing unrelated failures.

- [ ] **Step 5: ruff + commit**

Commit: `feat(cli,web): read scored_pairs from result field, not cluster pair_scores reconstruction`.

---

## Final validation (orchestrator)

1. `pytest tests/test_scored_pairs_decouple.py tests/test_api.py` — green.
2. Open the PR; CI runs the goldenmatch lane. Watch for any test asserting the OLD `scored_pairs` order/content (update as part of the accepted behavior change).
3. No gate, no bench (unconditional source change; the behavior change is accepted per spec).

## Notes for the implementer

- **`scored_pairs` is now a documented behavior change** — full canonical max-deduped sorted scored set, superset on splits. If a test elsewhere asserts the old cluster-grouped order/content, update it to the new contract (don't revert the source change).
- **Both pipeline paths must produce the identical normalized list** — apply `dedup_pairs_max_score` to both; mirror the cluster-stage branch key.
- **`DedupeResult.clusters[cid]["pair_scores"]` is UNCHANGED** — do not touch it; unmerge + graceful degraders still use it.
- **Two gates in pipeline.py** — `_use_columnar` (`GOLDENMATCH_COLUMNAR_PIPELINE`) drives the cluster-stage path; this SP3 capture follows that SAME flag for the stream source. (Unrelated to `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` from SP1/SP2.)
- **Delete, don't adjust** `test_extract_pairs` / `test_extract_pairs_empty` when removing `_extract_pairs`.
- **Skill:** @superpowers:test-driven-development per task.
