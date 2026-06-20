# GoldenSynonym GS1 — framework + table-backed scorer (implementation plan)

> **For agentic workers:** use superpowers:executing-plans / subagent-driven-development.
> Steps use `- [ ]`. TDD: failing test → minimal impl → run → commit.

**Goal:** Ship the reusable `synonym` scorer surface (GS1 of the GoldenSynonym spec,
`docs/superpowers/specs/2026-06-20-goldensynonym-trained-synonym-scorer-design.md`) —
a `ScorerPlugin` resolving a per-domain `SynonymModel`, with a table-lookup fast-path
+ Jaro-Winkler fallback. **No training, no RxNorm** (those are GS2). GS1 is useful
today: drop in a per-domain synonym JSON table and `scorer: synonym` resolves it.

**Architecture:** mirrors `goldenmatch/refdata/` (the `given_name_aliased_jw`
precedent) + the embedder provider registry. New package `goldenmatch/synonym/`.
Pure Python → covered by the standard `ci.yml` python suite (no new lane).

**Paths** (under `packages/python/goldenmatch/goldenmatch/`):
`synonym/__init__.py`, `synonym/providers.py`, `synonym/table.py`, `synonym/scorer.py`,
`synonym/data/`, tests under `goldenmatch/synonym/tests/` or the package `tests/`.
Verified contracts (read-only): `plugins/base.py::ScorerPlugin` (`name`,
`score_pair(a,b)->float|None`, optional `score_matrix(values)->np.ndarray`);
`plugins/registry.py::PluginRegistry.instance().register_scorer(name, plugin)`;
`refdata/given_names.py` (alias-table load + `are_equivalent`) + `refdata/__init__.py`
(registration at import); `core/scorer.py` dispatch falls through to the registry for
non-`VALID_SCORERS` names (so `synonym` needs NO `VALID_SCORERS` entry).

---

### Task 1: `SynonymModel` protocol + provider registry + stub
**Files:** Create `synonym/providers.py`, `synonym/model.py`; Test `synonym/tests/test_providers.py`.

- [ ] **Step 1 — failing test.** `test_resolve_default_is_stub_and_scores_zeroish`: `resolve_synonym_model("drug")` returns an object with `score(a, b) -> float | None`; the default `StubSynonymModel` returns `None` (no learned signal) so the scorer falls back to JW. `test_register_and_resolve`: `register_synonym_model("drug", m)` then `resolve_synonym_model("drug") is m`; unknown domain → the default stub (never raises).
- [ ] **Step 2 — run, expect fail** (module missing): `pytest packages/python/goldenmatch/goldenmatch/synonym/tests/test_providers.py -q`.
- [ ] **Step 3 — implement.** `model.py`: `SynonymModel` Protocol (`score(self, a: str, b: str) -> float | None`) + `StubSynonymModel` (returns `None`). `providers.py`: module-level `_REGISTRY: dict[str, SynonymModel]`, `register_synonym_model(domain, model)`, `resolve_synonym_model(domain) -> SynonymModel` (registry hit, else `StubSynonymModel()`). Thread-safe lazy default.
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(synonym): SynonymModel protocol + provider registry + stub (GS1 T1)`.

### Task 2: per-domain synonym alias table (the table fast-path data)
**Files:** Create `synonym/table.py`, `synonym/data/.gitkeep`; Test `synonym/tests/test_table.py`.

- [ ] **Step 1 — failing test.** Using a tmp/fixture JSON `{"aliases": {"ibuprofen": ["advil","motrin"]}}`: `SynonymTable.from_json(path)`; `are_equivalent("Advil","ibuprofen") is True` (case/space-normalized, symmetric); `are_equivalent("Advil","Tylenol") is False`; a MISSING table file → `SynonymTable.empty()` whose `are_equivalent` is always False + `is_available() is False` (graceful, like `refdata/given_names.py`).
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `table.py`: `SynonymTable` with `_normalize` (lower/strip non-alnum, mirror refdata), a canonical→set(aliases) map expanded to a symmetric equivalence lookup, `are_equivalent(a,b)`, `is_available()`, `from_json(path)`, `empty()`. Per-domain tables load lazily from `synonym/data/<domain>_synonyms.json` (none committed in GS1 — GS2 adds the drug table off RxNorm; GS1 ships the loader + an `empty()` default).
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(synonym): per-domain synonym alias table loader (GS1 T2)`.

### Task 3: `SynonymScorer(ScorerPlugin)` — score_pair + score_matrix
**Files:** Create `synonym/scorer.py`; Test `synonym/tests/test_scorer.py`.

- [ ] **Step 1 — failing test.** `SynonymScorer(domain="drug", table=<fixture>, model=<stub>)`:
  - table hit → `score_pair("Advil","ibuprofen") == 1.0`;
  - no table hit, stub model returns None → falls back to Jaro-Winkler (assert == `rapidfuzz` JW of the two, e.g. "Advil" vs "Advel");
  - injected model returning 0.9 (no table hit) → `score_pair == 0.9` (model-primary when present);
  - `score_pair(None, x) is None`;
  - `score_matrix(["Advil","ibuprofen","Tylenol"])` is a symmetric float32 NxN, diagonal 1.0, [Advil,ibuprofen]==1.0 via table.
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `SynonymScorer`: `name="synonym"`. `score_pair`: None-guard → table `are_equivalent` →1.0 → model.score (if not None) → else JW. `score_matrix`: vectorized JW base (`rapidfuzz.process.cdist`, like `refdata/scorer.py`), then overwrite table-equivalent + model-scored cells; diagonal 1.0; float32 symmetric. Domain/table/model are constructor args (defaults: domain="generic", `SynonymTable.empty()`, `resolve_synonym_model(domain)`).
- [ ] **Step 4 — run, expect pass.**
- [ ] **Step 5 — commit:** `feat(synonym): SynonymScorer plugin (table -> model -> JW) (GS1 T3)`.

### Task 4: register at import + registry-resolution test
**Files:** Create `synonym/__init__.py`; Modify `goldenmatch/__init__.py`; Test `synonym/tests/test_registration.py`.

**Registration mechanism (verified, NOT "mirror refdata"):** `goldenmatch/__init__.py`
does NOT import refdata; `PluginRegistry.get_scorer` is a plain dict lookup (no
auto-`discover()`); refdata scorers only register incidentally because auto-config
imports refdata when it *selects* them. `synonym` is OPT-IN (never auto-selected in
GS1), so that lazy trigger never fires — a user's `scorer: synonym` would miss the
registry. So GS1 registers `synonym` at **goldenmatch package import** by importing
`goldenmatch.synonym` from `goldenmatch/__init__.py`. This is cheap + safe because GS1
`synonym` has **no heavy import-time cost** (lazy/empty table, stub model) and does JW
via `rapidfuzz` DIRECTLY — it must NOT import `core.scorer` at module level (that
would risk a circular import, since `core.scorer` reaches the registry).

- [ ] **Step 1 — failing test.** `test_synonym_registered_on_package_import`: in a
  subprocess (clean import), `import goldenmatch` then
  `PluginRegistry.instance().get_scorer("synonym")` is a `SynonymScorer` (NOT None) —
  proves package-init registration, not just `import goldenmatch.synonym`.
  `test_score_field_dispatches_synonym`: `core.scorer.score_field("Advil","ibuprofen","synonym")`
  returns a float (no "unknown scorer"). `test_valid_scorers_unchanged`:
  `"synonym" not in VALID_SCORERS`.
- [ ] **Step 2 — run, expect fail.**
- [ ] **Step 3 — implement.** `synonym/__init__.py`: `register_synonym_scorer()` →
  `PluginRegistry.instance().register_scorer("synonym", SynonymScorer())`, called at
  module init; export it. Add `from goldenmatch import synonym as _synonym  # noqa: F401`
  (or `import goldenmatch.synonym`) near the end of `goldenmatch/__init__.py` (after
  core imports, to avoid circulars). Confirm no circular import by running the package
  import smoke. Do NOT touch `VALID_SCORERS`. Verify `synonym/scorer.py` imports only
  `rapidfuzz` + `plugins.base` + the local table/providers (no `core.scorer`).
- [ ] **Step 4 — run, expect pass:** `pytest packages/python/goldenmatch/goldenmatch/synonym/tests/ -q`.
- [ ] **Step 5 — commit:** `feat(synonym): register synonym scorer at package import (GS1 T4)`.

### Finish
- [ ] Full GS1 suite: `pytest packages/python/goldenmatch/goldenmatch/synonym/tests/ -q` (all green).
- [ ] Quick import smoke: `python -c "import goldenmatch; from goldenmatch.plugins.registry import PluginRegistry; assert PluginRegistry.instance().get_scorer('synonym')"`.
- [ ] PR; GS1 runs in the standard `ci.yml` python lane (no new workflow). superpowers:finishing-a-development-branch.

## Notes / risks
- **Local test env:** goldenmatch import pulls polars → set `POLARS_SKIP_CPU_CHECK=1` (+ `GOLDENMATCH_ANALYTICS=0`); avoid the full xdist suite locally (OOM) — run only `synonym/tests/`.
- **score_matrix parity:** float32 JW base matches `_fuzzy_score_matrix`; table/model overwrites are exact — no float drift concern (table=1.0, model=its own value).
- **GS2 dependency:** the drug `synonym_synonyms.json` table + the trained `SynonymModel` are GS2 (need the off-CI RxNorm derivation + a UMLS license). GS1 ships with `empty()` table + `StubSynonymModel` → `synonym` degrades to JW until GS2 lands. That's intended (GS1 = surface).
- **Don't reuse the refdata `alias_match` builtin:** `synonym` is the new pluggable, model-capable path; the existing `given_name_aliased_jw`/`alias_match` stay as-is (additive).
