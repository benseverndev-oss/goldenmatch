# DataFusion Spine — Stage D: scale-mode contract (Implementation Plan)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `mode="scale"` an explicit, opt-in, deterministic contract on the DataFusion spine: a `mode` config field, a hard feature-gate that errors (never silently ignores) on every unsupported surface, and a determinism gate proving identical pair set / cluster partition / id_prep edges across `target_partitions`.

**Architecture:** Add `mode: Literal["standard","scale"]` to `GoldenMatchConfig`. `run_spine` (the spine entry) calls a new `_validate_scale_mode_supported(config)` guard before doing any work: it requires `config.mode == "scale"` and raises `NotImplementedError` for LLM scorer/cluster, LLM boost, LLM auto, rerank/cross-encoder, negative-evidence, domain extraction, and any non-single-weighted matchkey surface. A new determinism test runs `run_spine` at `target_partitions ∈ {1,3,17}` and asserts the emitted pair SET, the cluster PARTITION, the id_prep edge sets, and `avg_edge` are identical/ε-equal. Sign-off content (feature matrix, customer statement, MAX-vs-last) is finalized in the parent roadmap doc.

**Tech Stack:** Python 3.12, pydantic v2, polars, Python `datafusion>=53,<54`, pytest. The spine's relational stages run inside one `datafusion.SessionContext`; clustering/id_prep/golden mirror the in-memory frames-out path.

---

## Critical context for the executor (read before starting)

- **This box HANGS on `import goldenmatch` / `import polars` / `import datafusion`.** You CANNOT run pytest locally. Validate every Python change with `ruff check` (exit 0) and `python -m py_compile` ONLY. **CI is the only test verifier.** Each task's "run the test" step means: push the branch and read the `python (goldenmatch)` lane result. Batch tasks, then push once and watch CI.
- **The `python (goldenmatch)` CI lane** installs `datafusion>=53,<54` and builds the `goldenmatch_datafusion_udf` FFI wheel, so `run_spine` runs there with the real FFI scorers. New tests under `packages/python/goldenmatch/tests/` are picked up automatically — no CI YAML change needed for Stage D.
- **`ruff check` (I001 import ordering) is enforced.** Run `uv run ruff check packages/python/goldenmatch` — or if `uv` hangs, `ruff check packages/python/goldenmatch` — to **exit 0** before EVERY commit. Never pipe through `tail` (masks the exit code). `ruff check --fix` if it fires.
- **GitHub auth:** `GH_TOKEN=$(gh auth token --user benzsevern)` for every push/PR/merge. NEVER `benzsevern-mjh`. Repo is `benseverndev-oss/goldenmatch`.
- **Branch off `origin/main`, NOT local `main`** — local `main` is stale (it predates the whole DataFusion-spine series; `datafusion_spine.py`/`datafusion_backend.py` are NOT on local `main`). Stage C (PR #700) is only reachable from `origin/main`. Do `git fetch origin && git checkout -b feat/datafusion-spine-stage-d origin/main`. (See Task 0.)
- Subagents may NOT import/pytest/pyright/uv (zombie-python box pathology). Static checks only; the parent runs pyright bounded if needed.

## Grounding references (all on `main`)

- `goldenmatch/backends/datafusion_spine.py` — `run_spine(blocked_candidates, config, *, memory_limit=None, target_partitions=None)`; `_resolve_single_weighted_matchkey`; imports `_validate_matchkey` from `datafusion_backend`.
- `goldenmatch/backends/datafusion_backend.py:62` — `_validate_matchkey` is the NotImplementedError-on-out-of-scope pattern to mirror. `_SUPPORTED_SCORERS = ("jaro_winkler","levenshtein","token_sort")` at line 32.
- `goldenmatch/config/schemas.py` — `GoldenMatchConfig` top-level fields (~line 709). Feature-gate signals (verified):
  - `llm_boost: bool = False` — boost.
  - `llm_scorer: LLMScorerConfig | None = None` — `.enabled` flag.
  - `llm_auto: bool = False` — LLM auto.
  - `domain: DomainConfig | None = None` — `.enabled` flag (exotic/domain matchkeys).
  - per-matchkey (`MatchkeyConfig`): `rerank: bool = False`; `negative_evidence: list | None = None`; `type ∈ {"exact","weighted","probabilistic"}` (only single-field weighted w/ supported scorer is in scope).
  - `get_matchkeys()` (~line 797) returns top-level `matchkeys` or `match_settings.matchkeys` or `[]`.
- `tests/test_datafusion_spine_parity.py` — `_fixture_df`, `_config(*, max_cluster_size)`, `_prepared_blocks(df, config)`, `_partition(assignments)`, `_edge_sets_by_partition(assignments, raw_pairs)`, `_run_spine`. The Stage D determinism test ADDS to this file and reuses these helpers.
- `tests/test_config.py` — where `mode`-field schema tests go.
- Parent roadmap: `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md` — §"The scale-mode contract" (~line 324), §"Feature matrix … DRAFT for sign-off" (~line 392), §sign-off (~line 431). Already lists APPROVED customer statement + MAX-vs-last decision; Stage D finalizes/links them.
- Local gating notes: `docs/superpowers/plans/_stage-d-gating-notes.md` (delete after Stage D lands).
- Spec: `docs/superpowers/specs/2026-06-03-datafusion-spine-design.md` §"Stage D".

---

## File Structure

- **Modify** `goldenmatch/config/schemas.py` — add `mode: Literal["standard","scale"] = "standard"` to `GoldenMatchConfig`.
- **Modify** `goldenmatch/backends/datafusion_spine.py` — add `_validate_scale_mode_supported(config)`; call it first in `run_spine`.
- **Modify** `tests/test_datafusion_spine_parity.py` — set `mode="scale"` in `_config`; add the determinism test reusing existing helpers.
- **Create** `tests/test_datafusion_spine_scale_mode.py` — feature-gate tests (self-contained; assert each unsupported surface raises).
- **Modify** `tests/test_config.py` — `mode`-field schema tests.
- **Modify** `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md` — finalize feature matrix + customer statement + MAX-vs-last sign-off, cross-link to Stage D.

---

## Task 0: Setup — branch off `origin/main`

**Files:** none (git only)

- [ ] **Step 1: Fetch and branch off the REMOTE main** (local `main` is stale and lacks the spine files):

```bash
git fetch origin
git checkout -b feat/datafusion-spine-stage-d origin/main
```

- [ ] **Step 2: Verify Stage C is present** (so the Modify steps have files to touch):

```bash
test -f packages/python/goldenmatch/goldenmatch/backends/datafusion_spine.py && \
test -f packages/python/goldenmatch/tests/test_datafusion_spine_parity.py && echo OK
```
Expected: `OK`. If not, you branched off the wrong ref — redo Step 1.

---

## Task 1: Add `mode` field to `GoldenMatchConfig`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (the `GoldenMatchConfig` class, ~line 709)
- Test: `packages/python/goldenmatch/tests/test_config.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_config.py` (append; ensure `from goldenmatch.config.schemas import GoldenMatchConfig` and `import pytest` / `from pydantic import ValidationError` are imported — reuse existing imports, add only what's missing):

```python
def test_config_mode_defaults_to_standard():
    cfg = GoldenMatchConfig()
    assert cfg.mode == "standard"


def test_config_mode_accepts_scale():
    cfg = GoldenMatchConfig(mode="scale")
    assert cfg.mode == "scale"


def test_config_mode_rejects_unknown_value():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        GoldenMatchConfig(mode="turbo")


def test_config_mode_round_trips_through_model_dump():
    cfg = GoldenMatchConfig(mode="scale")
    assert GoldenMatchConfig(**cfg.model_dump()).mode == "scale"
```

- [ ] **Step 2: Add the field.** In `schemas.py`, `GoldenMatchConfig`, add next to the other top-level mode-like flags (e.g. right after `backend: str | None = None`):

```python
    # Execution mode. "standard" (default) = the in-memory/Ray pipeline,
    # bit-identical artifacts. "scale" = the DataFusion spine
    # (out-of-core, deterministic + semantically correct but NOT
    # bit-identical to standard; MAX dedup, reduced feature surface).
    # The spine entry (backends/datafusion_spine.run_spine) enforces the
    # scale-mode feature gate; this field is the opt-in signal.
    mode: Literal["standard", "scale"] = "standard"
```

  `Literal` is already imported in `schemas.py` (used by `MatchkeyConfig.type`). Verify the import line — do NOT add a duplicate.

- [ ] **Step 3: Static-validate.** `ruff check packages/python/goldenmatch/goldenmatch/config/schemas.py` (exit 0) and `python -m py_compile packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_config.py`.

- [ ] **Step 4: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_config.py
git commit -m "feat(spine): add mode={standard,scale} field to GoldenMatchConfig (Stage D)"
```

---

## Task 2: Scale-mode feature-gate at the spine entry

The gate raises `NotImplementedError` for every unsupported surface and `ValueError` when invoked on a non-scale config. It mirrors `_validate_matchkey`'s message style. Errors are explicit — NEVER silently ignore an unsupported feature.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/backends/datafusion_spine.py`
- Test (gate): `packages/python/goldenmatch/tests/test_datafusion_spine_scale_mode.py` (create)
- Test (parity fixup): `packages/python/goldenmatch/tests/test_datafusion_spine_parity.py`

- [ ] **Step 1: Write the failing gate tests** — create `tests/test_datafusion_spine_scale_mode.py`. These assert raises only; they build configs and call `run_spine([], config)` (empty blocks is fine — the gate runs BEFORE any block work, so it raises before touching DataFusion; an FFI/datafusion install is not required for the gate tests to pass, but the lane has it anyway). Helper `_base_config()` builds a valid single-weighted scale config; each test mutates one surface.

```python
"""Stage D: scale-mode feature-gate. ``run_spine`` must raise an explicit
error (never silently ignore) when ``mode="scale"`` is paired with an
unsupported surface, and must refuse a non-scale config."""
from __future__ import annotations

import pytest

from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    NegativeEvidenceField,
)


def _base_config(**overrides) -> GoldenMatchConfig:
    """Valid single-field weighted scale-mode config (the supported shape).
    ``overrides`` patch top-level fields; matchkey-level surfaces are set by
    mutating the returned config's matchkey in the test."""
    cfg = GoldenMatchConfig(
        mode="scale",
        blocking=BlockingConfig(
            strategy="static", keys=[BlockingKeyConfig(fields=["zip"])],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fuzzy_last",
                fields=[MatchkeyField(
                    column="last_name", scorer="jaro_winkler", weight=1.0,
                )],
                comparison="weighted",
                threshold=0.85,
            ),
        ],
    )
    for k, v in overrides.items():
        setattr(cfg, k, v)
    return cfg


def _run(cfg):
    from goldenmatch.backends.datafusion_spine import run_spine
    return run_spine([], cfg)


def test_gate_rejects_non_scale_mode():
    cfg = _base_config()
    cfg.mode = "standard"
    with pytest.raises(ValueError, match="mode='scale'"):
        _run(cfg)


def test_gate_rejects_llm_boost():
    with pytest.raises(NotImplementedError, match="boost"):
        _run(_base_config(llm_boost=True))


def test_gate_rejects_llm_auto():
    with pytest.raises(NotImplementedError, match="LLM"):
        _run(_base_config(llm_auto=True))


def test_gate_rejects_llm_scorer_enabled():
    from goldenmatch.config.schemas import LLMScorerConfig
    with pytest.raises(NotImplementedError, match="LLM"):
        _run(_base_config(llm_scorer=LLMScorerConfig(enabled=True)))


def test_gate_allows_llm_scorer_present_but_disabled():
    # A disabled LLM scorer is inert -> must NOT trip the gate. (It will
    # proceed past the gate and hit the empty-blocks no-op path, returning
    # normally.)
    from goldenmatch.config.schemas import LLMScorerConfig
    _run(_base_config(llm_scorer=LLMScorerConfig(enabled=False)))


def test_gate_rejects_domain_enabled():
    from goldenmatch.config.schemas import DomainConfig
    with pytest.raises(NotImplementedError, match="domain"):
        _run(_base_config(domain=DomainConfig(enabled=True)))


def test_gate_rejects_rerank():
    cfg = _base_config()
    cfg.get_matchkeys()[0].rerank = True
    with pytest.raises(NotImplementedError, match="rerank"):
        _run(cfg)


def test_gate_rejects_negative_evidence():
    cfg = _base_config()
    cfg.get_matchkeys()[0].negative_evidence = [
        NegativeEvidenceField(
            field="phone", scorer="exact", threshold=0.9, penalty=0.5,
        )
    ]
    with pytest.raises(NotImplementedError, match="negative.evidence"):
        _run(cfg)


def test_gate_rejects_probabilistic_matchkey():
    cfg = _base_config()
    cfg.matchkeys.append(
        MatchkeyConfig(
            name="prob_mk",
            fields=[MatchkeyField(column="last_name", scorer="jaro_winkler")],
            comparison="probabilistic",
        )
    )
    with pytest.raises(NotImplementedError, match="weighted"):
        _run(cfg)
```

- [ ] **Step 2: Verify the tests fail** (in CI). Expected: all fail because `run_spine` does not gate yet (currently `_resolve_single_weighted_matchkey` would pick the weighted mk and proceed; several would raise the wrong error type or none).

- [ ] **Step 3: Implement `_validate_scale_mode_supported`** in `datafusion_spine.py`. Add the function above `run_spine`, and call it as the FIRST statement in `run_spine` (before `_resolve_single_weighted_matchkey`).

```python
def _validate_scale_mode_supported(config) -> None:
    """Hard-gate the scale-mode feature surface. Raise EXPLICITLY (never
    silently ignore) when ``mode="scale"`` is paired with an unsupported
    feature, so a customer never gets a silently-degraded result. Mirrors
    ``datafusion_backend._validate_matchkey``'s NotImplementedError-on-
    out-of-scope contract.

    Supported scale-mode surface: a single-field ``weighted`` matchkey
    with a supported scorer (jaro_winkler / levenshtein / token_sort),
    static blocking, golden rules. Everything below is OUT and errors.
    """
    if getattr(config, "mode", "standard") != "scale":
        raise ValueError(
            "DataFusion spine requires mode='scale' (the opt-in to the "
            "out-of-core, MAX-dedup, reduced-feature path). Set "
            "config.mode='scale' to use run_spine; standard mode runs the "
            "in-memory pipeline."
        )

    if getattr(config, "llm_boost", False):
        raise NotImplementedError(
            "DataFusion spine (mode='scale') does not support LLM boost "
            "(config.llm_boost=True). Drop it or use standard mode."
        )
    if getattr(config, "llm_auto", False):
        raise NotImplementedError(
            "DataFusion spine (mode='scale') does not support LLM auto "
            "(config.llm_auto=True). Drop it or use standard mode."
        )
    llm_scorer = getattr(config, "llm_scorer", None)
    if llm_scorer is not None and getattr(llm_scorer, "enabled", False):
        raise NotImplementedError(
            "DataFusion spine (mode='scale') does not support the LLM "
            "scorer/cluster (config.llm_scorer.enabled=True). Drop it or "
            "use standard mode."
        )
    domain = getattr(config, "domain", None)
    if domain is not None and getattr(domain, "enabled", False):
        raise NotImplementedError(
            "DataFusion spine (mode='scale') does not support domain "
            "extraction / exotic domain matchkeys (config.domain.enabled="
            "True). Drop it or use standard mode."
        )

    for mk in config.get_matchkeys():
        if getattr(mk, "type", None) != "weighted":
            raise NotImplementedError(
                "DataFusion spine (mode='scale') supports only single-field "
                f"weighted matchkeys; matchkey {mk.name!r} has "
                f"type={mk.type!r} (probabilistic/exact/exotic out of scope)."
            )
        if getattr(mk, "rerank", False):
            raise NotImplementedError(
                "DataFusion spine (mode='scale') does not support "
                f"rerank/cross-encoder (matchkey {mk.name!r} rerank=True)."
            )
        if getattr(mk, "negative_evidence", None):
            raise NotImplementedError(
                "DataFusion spine (mode='scale') does not support "
                f"negative-evidence post-filters (matchkey {mk.name!r})."
            )
```

  Then in `run_spine`, immediately after the docstring / before `mk = _resolve_single_weighted_matchkey(config)`:

```python
    _validate_scale_mode_supported(config)
```

  Note: the gate iterates ALL matchkeys (so a stray probabilistic/rerank matchkey errors even though `_resolve_single_weighted_matchkey` would pick a different one) — that's the point: never silently ignore.

- [ ] **Step 4: Fix the Stage C parity fixture.** In `tests/test_datafusion_spine_parity.py`, `_config(...)`, add `mode="scale"` to the `GoldenMatchConfig(...)` constructor (otherwise the gate's `mode='scale'` check fails the now-passing parity tests):

```python
    return GoldenMatchConfig(
        mode="scale",
        blocking=BlockingConfig(
        ...
```

- [ ] **Step 5: Static-validate.** `ruff check packages/python/goldenmatch` (exit 0); `python -m py_compile` on all three changed files.

- [ ] **Step 6: Commit.**

```bash
git add packages/python/goldenmatch/goldenmatch/backends/datafusion_spine.py \
        packages/python/goldenmatch/tests/test_datafusion_spine_scale_mode.py \
        packages/python/goldenmatch/tests/test_datafusion_spine_parity.py
git commit -m "feat(spine): scale-mode feature gate at run_spine entry (Stage D)"
```

---

## Task 3: Determinism across `target_partitions`

Prove the scale-mode output is identical across partition counts. Compare SETS/PARTITIONS (label- and order-independent), NOT raw f32 float equality. The fixture (`_fixture_df`) is all-identical-strings-within-block → every pair scores exactly 1.0, threshold 0.85 → 0.15 margin, so NO pair is within ε(1e-6) of the cutoff: the gate measures partition determinism, not threshold flapping (spec §Stage D, lines 90-92). The multi-member blocks (5-dense, 3-chain) exercise multi-partition join + GROUP-BY aggregation; tp=17 over-partitions the tiny input deliberately.

**Files:**
- Test: `packages/python/goldenmatch/tests/test_datafusion_spine_parity.py` (add; reuse `_fixture_df`, `_config`, `_prepared_blocks`, `_partition`, `_edge_sets_by_partition`)

- [ ] **Step 1: Add a `target_partitions`-aware spine runner + the determinism test** to `test_datafusion_spine_parity.py`. Paste EXACTLY this (no `monkeypatch` param, no `_avg_edge_by_partition` helper — see the avg_edge note below for why it's omitted):

```python
def _run_spine_tp(blocks, config, target_partitions):
    from goldenmatch.backends.datafusion_spine import run_spine
    return run_spine(blocks, config, target_partitions=target_partitions)


def test_spine_deterministic_across_target_partitions():
    """Stage D gate: the emitted pair SET, the cluster PARTITION, and the
    id_prep edge sets are identical across target_partitions {1,3,17}.

    Compared as SETS/PARTITIONS (label- and order-independent), NOT raw f32
    float equality. The fixture's within-block strings are identical, so every
    pair scores exactly 1.0 (threshold 0.85 -> 0.15 margin): no pair is within
    1e-6 of the cutoff, so this measures partition determinism, not threshold
    flapping (spec Stage D). avg_edge is intentionally NOT asserted: run_spine
    does not return cluster metadata, and with uniform 1.0 scores avg_edge is a
    deterministic function of the (proven-identical) edge sets, so it cannot
    drift independently.
    """
    df = _fixture_df()
    config = _config(max_cluster_size=100)

    results = {}
    for tp in (1, 3, 17):
        # Rebuild blocks per run so each run is independent (build_blocks may
        # yield a lazy frame consumed once).
        blocks_tp = _prepared_blocks(df, config)
        _golden, assign, raw_pairs = _run_spine_tp(blocks_tp, config, tp)
        results[tp] = {
            "pairset": frozenset((min(a, b), max(a, b)) for a, b, _ in raw_pairs),
            "partition": _partition(assign),
            "edges": _edge_sets_by_partition(assign, raw_pairs),
        }

    base = results[1]
    for tp in (3, 17):
        assert results[tp]["pairset"] == base["pairset"], (
            f"pair set diverged at target_partitions={tp}"
        )
        assert results[tp]["partition"] == base["partition"], (
            f"cluster partition diverged at target_partitions={tp}"
        )
        assert results[tp]["edges"] == base["edges"], (
            f"id_prep edge sets diverged at target_partitions={tp}"
        )
```

  **Why avg_edge is omitted:** `run_spine` returns only `(golden_df, assignments, raw_pairs)` — not `cluster_frames.metadata` — so `avg_edge` is not observable from the return without changing the API. With the all-1.0 fixture it is a deterministic function of the proven-identical edge sets, so a separate avg_edge assert adds no coverage. If a reviewer later insists on an explicit avg_edge gate, derive it test-side via `build_cluster_frames(raw_pairs, sorted({i for p in raw_pairs for i in p[:2]}), ...)` and compare per-cluster — but do NOT add that unless asked (keeps the test free of an unused helper).

- [ ] **Step 2: Verify in CI** the determinism test passes at all three partition counts. If it FAILS (pair set or partition diverges), that's a real nondeterminism bug — STOP and apply the spec's contingency: pin the reduction (e.g. sort the score self-join output deterministically before the GROUP BY, or add `ORDER BY id_a, id_b` to the dedup SQL) and re-run. Surface to the human if a pin doesn't resolve it.

- [ ] **Step 3: Static-validate** + **Commit.**

```bash
git add packages/python/goldenmatch/tests/test_datafusion_spine_parity.py
git commit -m "test(spine): determinism gate across target_partitions {1,3,17} (Stage D)"
```

---

## Task 4: Finalize scale-mode sign-off in the parent roadmap

The roadmap already drafts the feature matrix + APPROVED customer statement + MAX-vs-last decision. Stage D promotes "DRAFT for sign-off" to "shipped" and cross-links the enforcing code.

**Files:**
- Modify: `docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md`

- [ ] **Step 1:** In the "Feature matrix (scale mode supported / dropped) — DRAFT for sign-off" section (~line 392), change the heading to drop "DRAFT for sign-off" → "(SHIPPED Stage D — enforced by `datafusion_spine._validate_scale_mode_supported`)". Verify each DROPPED row matches the actual gate (LLM boost/auto/scorer, domain, rerank, negative-evidence, non-weighted matchkeys). Add any gated surface the matrix is missing.

- [ ] **Step 2:** In the sign-off section (~line 431-448), under the customer-facing statement, add a one-line pointer: the statement now lives in code as the `ValueError`/`NotImplementedError` messages in `_validate_scale_mode_supported`, and the `mode` field's docstring in `schemas.py`. Confirm the MAX-vs-last note (R1=0 on the default single-weighted path) is stated as the dedup contract for scale mode.

- [ ] **Step 3: Commit** (specs are gitignored — use `-f`).

```bash
git add -f docs/superpowers/specs/2026-06-01-arrow-native-finish-line-design.md
git commit -m "docs(spine): finalize scale-mode sign-off + feature matrix (Stage D)"
```

---

## Task 5: Push, green CI, merge

- [ ] **Step 1:** Delete the scratch gating notes: `git rm -f docs/superpowers/plans/_stage-d-gating-notes.md` (or `rm` if untracked) — it was a working aid.

- [ ] **Step 2: Push the branch and open the PR.**

```bash
GH_TOKEN=$(gh auth token --user benzsevern) git push -u origin feat/datafusion-spine-stage-d
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --base main \
  --title "feat(spine): scale-mode contract — mode field, feature gate, determinism gate (Stage D)" \
  --body "Stage D of the DataFusion spine. Adds the mode={standard,scale} config field, a hard feature-gate at run_spine entry (errors on LLM boost/auto/scorer, domain, rerank, negative-evidence, non-weighted matchkeys, and non-scale mode), and a determinism gate across target_partitions {1,3,17}. Finalizes the scale-mode sign-off in the roadmap. Spec: docs/superpowers/specs/2026-06-03-datafusion-spine-design.md (Stage D)."
```

- [ ] **Step 3: Watch the `python (goldenmatch)` lane go green** (and `ci-required`). Poll: `while gh pr checks <N> | grep -qE "\bpending\b|in_progress"; do sleep 30; done`. If the branch falls behind main, `gh pr update-branch <N>` and re-poll.

- [ ] **Step 4: Merge** once green: `gh pr merge <N> --squash --delete-branch` (the worktree-delete cosmetic failure is expected and safe to ignore).

---

## Definition of done

- `GoldenMatchConfig.mode` exists, defaults `"standard"`, rejects unknown values, round-trips.
- `run_spine` raises `ValueError` on non-scale mode and `NotImplementedError` (explicit, matched message) on every unsupported surface; a disabled LLM scorer does NOT trip the gate.
- Determinism test green at `target_partitions ∈ {1,3,17}` (pair set + partition + id_prep edges identical).
- Stage C parity tests still green (fixture sets `mode="scale"`).
- Roadmap feature matrix + sign-off finalized and cross-linked to the enforcing code.
- `python (goldenmatch)` + `ci-required` green; PR merged.

## Out of scope (Stage E and beyond)

- The out-of-core spill bench (Stage E — separate plan).
- Flipping the `mode` default to `"scale"` (do NOT — gated on Stage E's spill verdict + the recorded sign-off).
- Sail / distributed routing; view-elimination; multi-field weighted; the in-memory golden custom-field-rules fallback.
