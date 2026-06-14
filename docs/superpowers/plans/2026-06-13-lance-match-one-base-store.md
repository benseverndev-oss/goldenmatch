# Plan — LanceBaseStore behind match_one / incremental / streaming

**Spec:** `docs/superpowers/specs/2026-06-13-lance-match-one-base-store-design.md`
**Branch:** `claude/lance-match-one-spike` (or a fresh `feat/lance-base-store`)
**Status:** ready to execute · TDD, phase-by-phase, commit each task

## Goal & invariants

Let `match_one` (and its `streaming`/`incremental` callers) retrieve candidate
rows from a pluggable **candidate store** instead of always holding the base as a
polars frame and calling `df.to_dicts()` **every probe** (today's
`core/match_one.py:81` — O(N) per call, RAM-bound). Add an opt-in
`LanceCandidateStore` that serves per-probe gathers from disk (measured 3.6 ms/probe
at 182 MB vs 1233 MB in-RAM, see spec).

**Hard invariants:**
- Default path is **byte-identical** to today. `store=None` ⇒ wrap `df` in the
  in-memory store; existing callers and results unchanged (parity-gated).
- Lance is **opt-in** (config/env + base-size threshold) and an **optional dep**
  (`pip install goldenmatch[lance]`); plain installs never import lance.
- Parquet stays the interchange format; Lance is an internal base-acceleration
  store only.

## Affected files (exact)

- `core/match_one.py` — `match_one()` + `_match_one_ann()` (the `to_dicts()` site).
- `core/streaming.py:130`, `core/incremental.py:97`, `mcp/server.py:1031`,
  `tui/engine.py:470` — the four `match_one(...)` call sites.
- `core/ann_blocker.py` — `query_one` returns `(faiss_idx, score)`; faiss_idx is
  the base row position the store must resolve.
- `config/schemas.py` — new opt-in flag.
- `pyproject.toml` — new `[project.optional-dependencies] lance` extra (mirror the
  `ray`/`native` blocks ~line 125/156).
- New: `core/candidate_store.py`, `tests/test_candidate_store.py`.

---

## Phase 0 — optional dependency plumbing

**Task 0.1** Add the extra. `pyproject.toml`: `lance = ["pylance>=0.20"]` under
`[project.optional-dependencies]`; add `{ path = ... }` workspace note only if
needed (it's PyPI, so no). Update root `pyproject.toml`/`uv` only if CI resolves
the extra graph (see packages/python/CLAUDE.md friction note).
- **Test:** `tests/test_candidate_store.py::test_lance_extra_optional` — importing
  `goldenmatch.core.candidate_store` succeeds without lance installed (lazy import).
- **Commit:** `Add optional lance extra + lazy import guard`.

---

## Phase 1 — CandidateStore protocol + in-memory impl (pure refactor)

**Task 1.1 (failing test first)** `tests/test_candidate_store.py`:
- `test_frame_store_take_positions` — `FrameCandidateStore(df).take([2,5,9])`
  returns those rows (as dicts) + their `__row_id__`, in order.
- `test_frame_store_gather_block` — `.gather_block(key)` returns rows where
  `block_key == key`.

**Task 1.2 (impl)** `core/candidate_store.py`:
```python
class CandidateStore(Protocol):
    def take(self, positions: Sequence[int]) -> tuple[list[dict], list[int]]: ...
    def gather_block(self, key: str) -> tuple[list[dict], list[int]]: ...
    def __len__(self) -> int: ...

class FrameCandidateStore:        # wraps today's df; gathers ONLY needed rows
    def take(self, positions): 
        sub = self._df[list(positions)]          # not df.to_dicts() over all N
        return sub.to_dicts(), sub["__row_id__"].to_list()
```
- **Commit:** `CandidateStore protocol + FrameCandidateStore`.

**Task 1.3 (refactor _match_one_ann, parity-gated)** Rewrite `_match_one_ann` to
build a `FrameCandidateStore(df)` (when no store passed) and replace the
`rows = df.to_dicts(); rows[faiss_idx]` loop with one `store.take(positions)`.
- **Test:** `tests/test_match_one.py::test_ann_results_unchanged_after_refactor`
  — same `(row_id, score)` list as before on a fixed fixture (golden parity).
- **Test:** `test_match_one_ann_no_full_to_dicts` — monkeypatch `pl.DataFrame.to_dicts`
  to assert it is NOT called on the full frame per probe (regression lock for the
  O(N)-per-call bug).
- **Run:** `pytest tests/test_match_one.py tests/test_candidate_store.py -q`
- **Commit:** `Route _match_one_ann through CandidateStore (byte-identical)`.

---

## Phase 2 — LanceCandidateStore

**Task 2.1 (failing parity test)** `tests/test_candidate_store.py::test_lance_matches_frame`
(skip if no lance): build both stores over the same base; assert
`lance.take(P) == frame.take(P)` (rows + row_ids) and `gather_block(k)` parity for
several P and k. Marked `@pytest.mark.skipif(not _HAS_LANCE)`.

**Task 2.2 (impl)** `LanceCandidateStore`:
- `from_frame(df, path)` — write Lance dataset, cast `block_key` large_string→string
  (lance BTREE requirement, learned in the bench), `create_scalar_index("block_key","BTREE")`.
- `take(positions)` → `ds.take(list(positions), columns=...)` → dicts + row_ids.
- `gather_block(key)` → indexed `ds.scanner(filter=f"block_key = '{key}'")`.
- `open(path)` classmethod for reusing an existing base store.
- **Run:** `pytest tests/test_candidate_store.py -q` (with lance installed).
- **Commit:** `LanceCandidateStore (take + BTREE block gather)`.

---

## Phase 3 — opt-in wiring

**Task 3.1** `match_one(..., store: CandidateStore | None = None)`: when provided,
use it; else `FrameCandidateStore(df)`. `df` stays for back-compat (callers that
pass only `df` are unchanged). **Test:** existing `tests/test_match_one.py` all green;
add `test_match_one_with_lance_store` (skipif) asserting identical results to the df path.

**Task 3.2** `config/schemas.py`: add `IncrementalConfig.base_store: Literal["memory","lance"] = "memory"`
+ `base_store_threshold_rows: int` (default e.g. 2_000_000). Resolver
`resolve_base_store(n_rows, config, env=GOLDENMATCH_BASE_STORE)` → picks lance only
when explicitly set OR base exceeds threshold AND lance importable; else memory.
**Test:** `tests/test_candidate_store.py::test_resolve_base_store_*` (env/threshold/missing-dep fallback).

**Task 3.3** Wire `StreamProcessor` (`core/streaming.py:86` `__init__`) to build the
store once from its base and pass it to `match_one` at line 130 (not per-record).
**Test:** `tests/test_streaming.py::test_stream_with_lance_store` (skipif) — same
matches as the memory path on a small base.

**Task 3.4** Wire `core/incremental.py:97` `run_incremental` to build the store from
`base_file` once. `mcp/server.py:1031` and `tui/engine.py:470` keep passing `df`
(memory) — no behavior change; document that the large-base path is incremental/streaming.
**Commit each task.**

---

## Phase 4 — docs + close-out

- `packages/python/goldenmatch/CLAUDE.md` Code Patterns: add a `CandidateStore` /
  `LanceCandidateStore` entry (opt-in, out-of-core base, memory default).
- `docs-site/goldenmatch/` incremental/streaming page: note the `[lance]` extra +
  `base_store` config for out-of-core bases.
- Mark spec **Status: implemented**; link the PR.

## Validation gate (before flipping any default)

Run `scripts/bench_match_one_lance.py` *plus* a real-data check on an NCVR-scale
base (`tests/benchmarks/datasets/NCVR`, gitignored — skip if absent): confirm the
spec's ~7x memory win + low-ms latency hold on real skew, and that the memory
path stays byte-identical. **Defaults stay `memory`**; lance is opt-in until a
real-data run justifies the threshold value.

## Out of scope (future)

- Distributed / Ray base store; incremental base UPDATES keeping FAISS + Lance
  row-id-consistent (note in spec risks); replacing the FAISS index itself with
  Lance's native vector index (separate spike).
