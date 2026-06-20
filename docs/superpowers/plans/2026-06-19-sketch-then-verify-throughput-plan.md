# Sketch-then-verify throughput execution plan (#1083) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in throughput tier where the controller blocks with LSH/sketch and confirms candidate pairs by cheap sketch distance (no per-field fuzzy/FS scoring), tuned by a recall knob, reporting an honest LSH-theoretic recall/precision posture.

**Architecture:** A new isolated module `core/throughput_verify.py` owns the sketch-distance scoring, banding selection, the LSH S-curve, and the posture object. An opt-in `throughput` config field threads `dedupe_df → auto_configure_df → AutoConfigController.run` exactly like `planning_effort`. When enabled, auto-config forces `lsh`/`simhash` blocking on the longest text column; the pipeline routes those candidate pairs to the sketch-distance verifier instead of the fuzzy/FS scorer; the planner records the posture for telemetry. Default-off is byte-identical to today.

**Tech Stack:** Python 3.11+, Polars, NumPy, Pydantic v2, pytest. Reuses `core/sketch.py` (MinHash/LSH + SimHash primitives from #1081/#1082) and `core/simhash_blocker.py` / `core/lsh_blocker.py`.

**Spec:** `docs/superpowers/specs/2026-06-19-sketch-then-verify-throughput-plan-design.md`

**Worktree:** `D:/show_case/goldenmatch/.worktrees/1083-throughput-plan` (branch `feat/1083-throughput-plan`, off `origin/main`). All paths below are relative to `packages/python/goldenmatch/` unless noted.

---

## Verified APIs (from `core/sketch.py`, do not re-derive)

- `signature(shingles: list[int], num_perms, seed) -> list[int]` — MinHash sig of a shingle set.
- `signature_batch(texts: list[str], mode="char", k=3, num_perms=128, seed=0) -> list[list[int]]` — all rows' MinHash sigs in one call.
- `estimate_jaccard(sig_a: list[int], sig_b: list[int]) -> float` — fraction of equal signature positions (the lexical pair score).
- `shingle(text, mode="char", k=3) -> list[int]`.
- `optimal_bands(num_perms, threshold, steps=1000) -> tuple[int, int]` — `(num_bands, rows_per_band)`; accuracy-tier default. **Throughput uses recall-target-driven `select_banding` instead (Task 4).**
- `simhash_signature(vector: list[float], num_planes, seed) -> list[int]` — 0/1 bits.
- `simhash_band_hashes_batch(vectors, num_planes=128, num_bands=32, seed=0) -> list[list[int]]`.

## Verified integration coordinates

- `_api.py:387` `dedupe_df(...)`; passes kwargs to `auto_configure_df` at `_api.py:492`. `DedupeResult` dataclass at `_api.py:122-167`.
- `core/autoconfig.py` `auto_configure_df(..., planning_effort=...)`; calls `build_blocking`; `_text_corpus_blocking` at `~1945`, `_is_text_corpus` at `~1895`.
- `core/autoconfig_controller.py:516-526` `AutoConfigController.run(..., planning_effort="normal")`; `resolve_planning_effort` + `_PLANNING_EFFORTS` at `471-486`.
- `core/execution_plan.py:14-69` `ExecutionPlan` (frozen dataclass) + `apply_to(config)` (~`59`).
- `core/autoconfig_planner.py:70-116` `apply_planner_rules(...) -> ExecutionPlan`.
- `core/pipeline.py:87-119` `_get_block_scorer(config)`; pipeline reads `config`, NOT `ExecutionPlan`. **So the operative throughput signal lives on `config.throughput`.**
- `core/lsh_blocker.py:23-97` `MinHashLSHBlocker.candidate_pairs(texts) -> set[tuple[int,int]]`.
- `core/simhash_blocker.py:26-100` `SimHashLSHBlocker.candidate_pairs(embeddings) -> set[tuple[int,int]]`.
- `config/schemas.py:379-447` `LSHKeyConfig` / `SimHashKeyConfig`; `GoldenMatchConfig` + `planning_effort` field at `937-976`.
- `core/autoconfig_verify.py:247-282` `PostflightReport.__str__` + `_render_plan_line`/`_render_blocking_line`.
- `web/controller_telemetry.py:252-293` `serialize_telemetry(...) -> dict`.

## Key design decisions locked in (read before starting)

1. **Operative signal = `config.throughput`** (a resolved `ThroughputConfig`), because the pipeline reads `config`, not `ExecutionPlan`. `ExecutionPlan.verify_mode`/`sketch_*` fields are the planner's *record* for telemetry/posture, written onto config via `apply_to`.
2. **Self-contained throughput dispatch; no blocker surgery.** The normal pipeline builds *blocks (LazyFrames)* and only scores `weighted` matchkeys — the throughput tier has none. So throughput runs as a **dedicated branch** in the scoring stage that owns its own block+verify: it embeds the text column ONCE (semantic) or builds the signatures ONCE (lexical), passes that to BOTH `SimHashLSHBlocker.candidate_pairs(emb)` / `MinHashLSHBlocker.candidate_pairs(texts)` AND `score_sketch_pairs(...)`, then feeds the scored pairs into the existing cluster stage. Note: `build_simhash_blocks` does NOT retain its embeddings, which is exactly why the throughput branch does its own single embed rather than trying to reuse them. The blockers themselves are not modified.
3. **Banding is recall-target-driven** (`select_banding`), choosing among divisor splits `b*r == signature_len`: the fewest bands (best precision) whose expected recall still meets `recall_target`; if none meets it, the max-recall split. Monotonic and honest.
4. **Metric-specific math.** Lexical = Jaccard (`s`); semantic = cosine, per-band bit-match prob `p = 1 - arccos(s)/pi`. Expected recall S-curve: `1-(1-x**r)**b` with `x=s` (jaccard) or `x=p` (cosine).
5. **Default-off byte-identical.** `throughput=None`/absent ⇒ `verify_mode="full"`, every existing path unchanged (tested fence, Task 12).

---

## Task 1: `ThroughputConfig` Pydantic model

**Files:**
- Modify: `config/schemas.py` (add model near `LSHKeyConfig`/`SimHashKeyConfig` ~`379`; add field on `GoldenMatchConfig` after `planning_effort` ~`976`)
- Test: `tests/test_throughput_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_throughput_config.py
import pytest
from pydantic import ValidationError
from goldenmatch.config.schemas import ThroughputConfig, GoldenMatchConfig


def test_defaults():
    c = ThroughputConfig()
    assert c.enabled is False
    assert c.recall_target == 0.95
    assert c.similarity_threshold is None


def test_recall_target_must_be_in_open_unit_interval():
    for bad in (0.0, 1.0, -0.1, 1.5):
        with pytest.raises(ValidationError):
            ThroughputConfig(recall_target=bad)


def test_similarity_threshold_bounds():
    ThroughputConfig(similarity_threshold=0.8)  # ok
    for bad in (0.0, 1.0, 1.2):
        with pytest.raises(ValidationError):
            ThroughputConfig(similarity_threshold=bad)


def test_goldenmatch_config_has_throughput_field_defaulting_none():
    assert GoldenMatchConfig().throughput is None


def test_config_accepts_runtime_throughput_plan_private_attr():
    # The pipeline reads a runtime-only resolved plan off the config; Pydantic v2
    # rejects undeclared private attrs, so it MUST be a declared PrivateAttr.
    c = GoldenMatchConfig()
    c._throughput_plan = object()          # must not raise
    assert c._throughput_plan is not None
```

- [ ] **Step 2: Run test, verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_throughput_config.py -v`
Expected: FAIL — `ImportError: cannot import name 'ThroughputConfig'` (and the private-attr test errors).

- [ ] **Step 3: Implement the model + field + private attr**

In `config/schemas.py`, add near the other key configs:

```python
class ThroughputConfig(BaseModel):
    """Opt-in sketch-then-verify throughput tier (#1083).

    A high-recall, low-cost dedup posture: LSH/sketch blocking + a light
    sketch-distance verify instead of per-field fuzzy/FS scoring. ``recall_target``
    is the primary knob; ``similarity_threshold`` overrides the default near-dup
    similarity (Jaccard 0.8 lexical / cosine 0.85 semantic, chosen by metric).
    """

    enabled: bool = False
    recall_target: float = Field(default=0.95, gt=0.0, lt=1.0)
    similarity_threshold: float | None = Field(default=None, gt=0.0, lt=1.0)
```

On `GoldenMatchConfig`, immediately after the `planning_effort` field:

```python
    throughput: ThroughputConfig | None = None
```

**AND** declare the runtime-only resolved-plan holder as a `PrivateAttr` (Pydantic v2
rejects assignment to an undeclared `_`-prefixed attr — confirmed; there is an
existing precedent in this same model at ~`schemas.py:1034`: `_preflight_report` /
`_strict_autoconfig` / `_domain_profile`). Add alongside those:

```python
    _throughput_plan: Any = PrivateAttr(default=None)
```

(`PrivateAttr` and `Any` are already imported in `schemas.py`; confirm and add to the
import line if not. This is why `config._throughput_plan = ...` in Tasks 7/9/10 works.)

- [ ] **Step 4: Run test, verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_throughput_config.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_throughput_config.py
git commit -m "feat(throughput): ThroughputConfig schema + GoldenMatchConfig field (#1083)"
```

---

## Task 2: `resolve_throughput_config` + `ThroughputNotApplicableError`

Mirrors `resolve_planning_effort` (kwarg → env → default). Accepts `True` (enable w/ defaults), a `float` (recall_target), a `ThroughputConfig`, or `None`.

**Files:**
- Create: `core/throughput_verify.py`
- Test: `tests/test_throughput_config.py` (extend)

- [ ] **Step 1: Write the failing test** (append)

```python
from goldenmatch.core.throughput_verify import (
    resolve_throughput_config, ThroughputNotApplicableError,
)


def test_resolve_none_returns_none(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    assert resolve_throughput_config(None) is None


def test_resolve_true_enables_defaults(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    c = resolve_throughput_config(True)
    assert c.enabled and c.recall_target == 0.95


def test_resolve_float_is_recall_target(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_THROUGHPUT", raising=False)
    c = resolve_throughput_config(0.9)
    assert c.enabled and c.recall_target == 0.9


def test_env_enables_when_kwarg_absent(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT", "1")
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT_RECALL", "0.8")
    c = resolve_throughput_config(None)
    assert c.enabled and c.recall_target == 0.8


def test_kwarg_beats_env(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_THROUGHPUT", "1")
    c = resolve_throughput_config(0.7)
    assert c.recall_target == 0.7


def test_error_type_exists():
    assert issubclass(ThroughputNotApplicableError, Exception)
```

- [ ] **Step 2: Run, verify fail** — `ImportError`.

- [ ] **Step 3: Implement** (start `core/throughput_verify.py`)

```python
"""Sketch-then-verify throughput tier (#1083): banding, sketch-distance verify,
and the honest LSH-theoretic posture. Isolated from the accuracy scorer."""
from __future__ import annotations

import os
from goldenmatch.config.schemas import ThroughputConfig


class ThroughputNotApplicableError(Exception):
    """Raised when the throughput tier is requested but the data has no text
    column to sketch on. Explicit refuse — no silent fall-back to the accuracy
    tier (mirrors ControllerNotConfidentError)."""


def resolve_throughput_config(arg=None, config=None) -> ThroughputConfig | None:
    """Resolve throughput posture: kwarg -> env -> off.

    ``arg`` accepts True (enable w/ defaults), a float (recall_target), a
    ThroughputConfig, or None. If None and config.throughput is set, that wins.
    Env: GOLDENMATCH_THROUGHPUT (truthy), GOLDENMATCH_THROUGHPUT_RECALL,
    GOLDENMATCH_THROUGHPUT_SIMILARITY.
    """
    if isinstance(arg, ThroughputConfig):
        return arg
    if arg is True:
        return ThroughputConfig(enabled=True)
    if isinstance(arg, (int, float)) and not isinstance(arg, bool):
        return ThroughputConfig(enabled=True, recall_target=float(arg))
    if arg is False:
        return None
    # arg is None: fall back to an explicit config.throughput, then env.
    if config is not None and getattr(config, "throughput", None) is not None:
        return config.throughput
    env = os.environ.get("GOLDENMATCH_THROUGHPUT")
    if env and env.strip().lower() in ("1", "true", "yes", "on"):
        recall = os.environ.get("GOLDENMATCH_THROUGHPUT_RECALL")
        sim = os.environ.get("GOLDENMATCH_THROUGHPUT_SIMILARITY")
        return ThroughputConfig(
            enabled=True,
            recall_target=float(recall) if recall else 0.95,
            similarity_threshold=float(sim) if sim else None,
        )
    return None
```

- [ ] **Step 4: Run, verify pass.**

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/throughput_verify.py packages/python/goldenmatch/tests/test_throughput_config.py
git commit -m "feat(throughput): resolve_throughput_config + error type (#1083)"
```

---

## Task 3: `ExecutionPlan` throughput fields (telemetry record)

**Files:**
- Modify: `core/execution_plan.py`
- Test: `tests/test_execution_plan.py` (or new `tests/test_throughput_planner.py`)

- [ ] **Step 1: Failing test**

```python
import dataclasses
from goldenmatch.core.execution_plan import ExecutionPlan


def test_verify_mode_defaults_to_full():
    assert ExecutionPlan().verify_mode == "full"
    assert ExecutionPlan().sketch_bands is None


def test_replace_overlays_verify_fields_preserving_backend():
    base = ExecutionPlan(backend="bucket", max_workers=8)
    p = dataclasses.replace(base, verify_mode="sketch_distance",
                            sketch_bands=16, sketch_rows=8, sketch_similarity=0.8)
    assert p.backend == "bucket" and p.max_workers == 8
    assert p.verify_mode == "sketch_distance" and p.sketch_bands == 16
```

- [ ] **Step 2: Run, verify fail** — `TypeError: unexpected keyword 'verify_mode'`.

- [ ] **Step 3: Implement** — add to `ExecutionPlan` (after `routing_decisions`):

```python
    verify_mode: Literal["full", "sketch_distance"] = "full"
    sketch_bands: int | None = None
    sketch_rows: int | None = None
    sketch_similarity: float | None = None
```

(Import `Literal` if not already imported.)

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): ExecutionPlan verify_mode + sketch banding fields (#1083)`.

---

## Task 4: Banding selection + LSH S-curve (`select_banding`, `expected_recall_lsh`)

**Files:**
- Modify: `core/throughput_verify.py`
- Test: `tests/test_throughput_banding.py`

- [ ] **Step 1: Failing test**

```python
import math
import pytest
from goldenmatch.core.throughput_verify import (
    expected_recall_lsh, select_banding, DEFAULT_SIMILARITY,
)


def test_expected_recall_jaccard_matches_formula():
    b, r, s = 16, 8, 0.8
    assert expected_recall_lsh("jaccard", s, b, r) == pytest.approx(1 - (1 - s**r)**b)


def test_expected_recall_cosine_uses_bit_match_prob():
    b, r, s = 16, 8, 0.85
    p = 1 - math.acos(s) / math.pi
    assert expected_recall_lsh("cosine", s, b, r) == pytest.approx(1 - (1 - p**r)**b)


def test_select_banding_respects_divisor_invariant():
    b, r = select_banding("jaccard", 128, 0.8, 0.95)
    assert b * r == 128 and 128 % b == 0


def test_select_banding_picks_fewest_bands_meeting_target():
    # more bands -> higher recall; want the smallest b that still hits target.
    b, r = select_banding("jaccard", 128, 0.8, 0.95)
    assert expected_recall_lsh("jaccard", 0.8, b, r) >= 0.95
    # one fewer divisor-band must fall short (precision-optimal)
    divisors = [d for d in range(1, 128) if 128 % d == 0 and d < b]
    if divisors:
        b_lower = max(divisors)
        assert expected_recall_lsh("jaccard", 0.8, b_lower, 128 // b_lower) < 0.95


def test_default_similarity_per_metric():
    assert DEFAULT_SIMILARITY["jaccard"] == 0.8
    assert DEFAULT_SIMILARITY["cosine"] == 0.85
```

- [ ] **Step 2: Run, verify fail** — `ImportError`.

- [ ] **Step 3: Implement** (append to `throughput_verify.py`)

```python
import math

DEFAULT_SIMILARITY = {"jaccard": 0.8, "cosine": 0.85}


def _band_match_prob(metric: str, s: float) -> float:
    """Per-band single-row collision base prob at similarity ``s``.

    Jaccard: a MinHash row matches with prob s. Cosine (SimHash): a single
    hyperplane bit matches with prob ``1 - arccos(s)/pi``.
    """
    if metric == "cosine":
        return 1.0 - math.acos(max(-1.0, min(1.0, s))) / math.pi
    return s


def expected_recall_lsh(metric: str, s: float, bands: int, rows: int) -> float:
    """LSH S-curve: probability a pair at similarity ``s`` shares >=1 band.

    ``1 - (1 - x**rows)**bands`` with x the per-row band-match prob for the
    metric. Ground-truth-free expected recall over pairs at similarity ``s``.
    """
    x = _band_match_prob(metric, s)
    return 1.0 - (1.0 - x**rows) ** bands


def select_banding(metric: str, signature_len: int, similarity: float,
                   recall_target: float) -> tuple[int, int]:
    """Choose (bands, rows) among divisor splits of ``signature_len``.

    Picks the fewest bands (best precision) whose expected recall still meets
    ``recall_target`` at ``similarity``; if none meets it, the max-recall split.
    Divisor invariant: bands * rows == signature_len.
    """
    splits = [(b, signature_len // b) for b in range(1, signature_len + 1)
              if signature_len % b == 0]
    scored = [(b, r, expected_recall_lsh(metric, similarity, b, r)) for b, r in splits]
    meeting = [c for c in scored if c[2] >= recall_target]
    if meeting:
        b, r, _ = min(meeting, key=lambda c: c[0])
    else:
        b, r, _ = max(scored, key=lambda c: c[2])
    return b, r
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): recall-target banding + LSH S-curve (#1083)`.

---

## Task 5: `ThroughputPosture` + `build_posture`

**Files:**
- Modify: `core/throughput_verify.py`
- Test: `tests/test_throughput_posture.py`

- [ ] **Step 1: Failing test**

```python
import pytest
from goldenmatch.core.throughput_verify import ThroughputPosture, build_posture


def test_build_posture_fields():
    p = build_posture(metric="jaccard", recall_target=0.95, similarity=0.8,
                      bands=16, rows=8, n_rows=1000, candidate_pairs=500,
                      verified_pairs=480, semantic_fell_back=False)
    assert isinstance(p, ThroughputPosture)
    assert p.metric == "jaccard" and p.bands == 16 and p.rows_per_band == 8
    assert p.candidate_pairs == 500 and p.verified_pairs == 480
    assert 0.0 <= p.expected_recall <= 1.0
    # reduction_ratio = candidate_pairs / (n*(n-1)/2)
    assert p.reduction_ratio == pytest.approx(500 / (1000 * 999 / 2))
    assert "not a measured F1" in p.notes


def test_posture_notes_flags_semantic_fallback():
    p = build_posture(metric="jaccard", recall_target=0.95, similarity=0.8,
                      bands=16, rows=8, n_rows=10, candidate_pairs=1,
                      verified_pairs=1, semantic_fell_back=True)
    assert "fell back to lexical" in p.notes
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (append)

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ThroughputPosture:
    recall_target: float
    similarity_threshold: float
    metric: str
    bands: int
    rows_per_band: int
    expected_recall: float
    reduction_ratio: float
    candidate_pairs: int
    verified_pairs: int
    notes: str

    def to_dict(self) -> dict:
        from dataclasses import asdict
        return asdict(self)


def build_posture(*, metric: str, recall_target: float, similarity: float,
                 bands: int, rows: int, n_rows: int, candidate_pairs: int,
                 verified_pairs: int, semantic_fell_back: bool) -> ThroughputPosture:
    total = n_rows * (n_rows - 1) / 2 if n_rows > 1 else 1.0
    notes = (
        f"expected_recall is an LSH-theoretic estimate over pairs at/above "
        f"similarity {similarity} ({metric}); it is not a measured F1. Precision "
        f"is traded for throughput and is not directly measured here."
    )
    if semantic_fell_back:
        notes += " Semantic embedder unreachable; fell back to lexical lsh."
    if candidate_pairs / total > 0.5:
        notes += " WARNING: reduction_ratio > 0.5 — banding is near-degenerate."
    return ThroughputPosture(
        recall_target=recall_target, similarity_threshold=similarity, metric=metric,
        bands=bands, rows_per_band=rows,
        expected_recall=expected_recall_lsh(metric, similarity, bands, rows),
        reduction_ratio=candidate_pairs / total,
        candidate_pairs=candidate_pairs, verified_pairs=verified_pairs, notes=notes,
    )
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): ThroughputPosture + build_posture (#1083)`.

---

## Task 6: `score_sketch_pairs` (the sketch-distance verifier)

**Files:**
- Modify: `core/throughput_verify.py`
- Test: `tests/test_throughput_verify.py`

- [ ] **Step 1: Failing test**

```python
import numpy as np
from goldenmatch.core.throughput_verify import score_sketch_pairs


def test_jaccard_keeps_only_above_threshold():
    texts = ["the quick brown fox", "the quick brown fox", "a totally different string here"]
    pairs = {(0, 1), (0, 2)}
    out = score_sketch_pairs(pairs, metric="jaccard", threshold=0.8, texts=texts,
                             mode="word", k=2, num_perms=128, seed=0)
    ids = {(a, b) for a, b, _ in out}
    assert (0, 1) in ids            # identical -> jaccard ~1.0
    assert (0, 2) not in ids        # disjoint -> below 0.8
    assert all(0.0 <= sc <= 1.0 for _, _, sc in out)


def test_cosine_uses_supplied_embeddings():
    emb = np.array([[1.0, 0.0], [0.99, 0.01], [0.0, 1.0]])
    pairs = {(0, 1), (0, 2)}
    out = score_sketch_pairs(pairs, metric="cosine", threshold=0.85, embeddings=emb)
    ids = {(a, b) for a, b, _ in out}
    assert (0, 1) in ids and (0, 2) not in ids


def test_output_is_canonical_min_max_triples():
    texts = ["aa", "aa"]
    out = score_sketch_pairs({(1, 0)}, metric="jaccard", threshold=0.1, texts=texts,
                             mode="char", k=1, num_perms=64, seed=0)
    assert out and out[0][0] < out[0][1]
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** (append)

```python
import numpy as np
from goldenmatch.core import sketch


def score_sketch_pairs(candidate_pairs, *, metric, threshold,
                      texts=None, embeddings=None,
                      mode="char", k=3, num_perms=128, seed=0):
    """Confirm candidate pairs by sketch distance. Returns [(id_a, id_b, score)]
    canonical (a<b), keeping pairs with score >= threshold.

    Lexical (jaccard): MinHash signatures via signature_batch(texts, ...) computed
    ONCE, then estimate_jaccard per pair. Semantic (cosine): cosine over the
    supplied ``embeddings`` (reused from the pipeline; never re-embedded here).
    """
    out: list[tuple[int, int, float]] = []
    if metric == "jaccard":
        if texts is None:
            raise ValueError("jaccard verify requires texts=")
        sigs = sketch.signature_batch(texts, mode=mode, k=k, num_perms=num_perms, seed=seed)
        for a, b in candidate_pairs:
            a, b = (a, b) if a < b else (b, a)
            score = sketch.estimate_jaccard(sigs[a], sigs[b])
            if score >= threshold:
                out.append((a, b, float(score)))
    elif metric == "cosine":
        if embeddings is None:
            raise ValueError("cosine verify requires embeddings=")
        emb = np.asarray(embeddings, dtype=np.float64)
        norms = np.linalg.norm(emb, axis=1)
        for a, b in candidate_pairs:
            a, b = (a, b) if a < b else (b, a)
            denom = norms[a] * norms[b]
            score = float(emb[a] @ emb[b] / denom) if denom > 0 else 0.0
            if score >= threshold:
                out.append((a, b, score))
    else:
        raise ValueError(f"unknown metric {metric!r}")
    return out
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): score_sketch_pairs sketch-distance verify (#1083)`.

---

## Task 7: Planner overlay (`apply_throughput_overlay`) + `ExecutionPlan.apply_to`

**Files:**
- Modify: `core/autoconfig_planner.py` (add overlay; call after base plan is built)
- Modify: `core/execution_plan.py` (`apply_to` writes verify bits onto config)
- Test: `tests/test_throughput_planner.py`

- [ ] **Step 1: Failing test**

```python
import dataclasses
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.autoconfig_planner import apply_throughput_overlay
from goldenmatch.config.schemas import ThroughputConfig, GoldenMatchConfig


def test_overlay_sets_sketch_distance_preserving_backend():
    base = ExecutionPlan(backend="bucket", max_workers=8)
    cfg = ThroughputConfig(enabled=True, recall_target=0.95)
    plan = apply_throughput_overlay(base, cfg, metric="jaccard", signature_len=128)
    assert plan.verify_mode == "sketch_distance"
    assert plan.backend == "bucket" and plan.max_workers == 8
    assert plan.sketch_bands * plan.sketch_rows == 128
    assert plan.sketch_similarity == 0.8  # jaccard default


def test_overlay_honors_similarity_override():
    cfg = ThroughputConfig(enabled=True, similarity_threshold=0.9)
    plan = apply_throughput_overlay(ExecutionPlan(), cfg, metric="jaccard", signature_len=128)
    assert plan.sketch_similarity == 0.9


def test_apply_to_writes_verify_mode_onto_config():
    plan = ExecutionPlan(verify_mode="sketch_distance", sketch_bands=16,
                         sketch_rows=8, sketch_similarity=0.8)
    cfg = GoldenMatchConfig(throughput=ThroughputConfig(enabled=True))
    plan.apply_to(cfg)
    assert cfg.throughput.verify_mode == "sketch_distance"  # see Step 3 note
```

> **Step 3 note:** rather than add mutable runtime fields to the Pydantic `ThroughputConfig`, store the resolved verify bits where the pipeline already looks. Simplest: have `apply_to` set private attrs on the config object the pipeline reads (`config._throughput_plan = plan`). Adjust the assertion to `assert cfg._throughput_plan.verify_mode == "sketch_distance"`. Pick ONE mechanism and make the test match; do not add Pydantic fields that round-trip to YAML for runtime-only state.

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement**

In `core/autoconfig_planner.py`:

```python
def apply_throughput_overlay(plan, cfg, *, metric, signature_len):
    """Overlay the sketch-then-verify posture onto a base ExecutionPlan.

    Orthogonal to backend selection: backend/workers/clustering are preserved.
    metric in {"jaccard","cosine"}; signature_len = num_perms (lexical) or
    num_planes (semantic).
    """
    import dataclasses
    from goldenmatch.core.throughput_verify import select_banding, DEFAULT_SIMILARITY
    similarity = cfg.similarity_threshold or DEFAULT_SIMILARITY[metric]
    bands, rows = select_banding(metric, signature_len, similarity, cfg.recall_target)
    return dataclasses.replace(
        plan, verify_mode="sketch_distance",
        sketch_bands=bands, sketch_rows=rows, sketch_similarity=similarity,
    )
```

In `core/execution_plan.py::apply_to`, after the backend write, record the plan for the pipeline:

```python
        if self.verify_mode != "full":
            config._throughput_plan = self  # runtime-only; pipeline reads this
```

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): planner overlay + apply_to records verify plan (#1083)`.

---

## Task 8: Blocking forcing + kwarg threading in auto-config

When `throughput.enabled`: route the longest text/string column to `lsh` (or `simhash` if an embedder is reachable), **bypassing `_is_text_corpus`**. Raise `ThroughputNotApplicableError` if no text column exists.

**Files:**
- Modify: `core/autoconfig.py` (`auto_configure_df` gains `throughput=None`; new `_throughput_blocking(profiles, config)` helper; called in `build_blocking` when throughput on)
- Test: `tests/test_throughput_autoconfig.py`

- [ ] **Step 1: Failing test**

```python
import polars as pl
import pytest
from goldenmatch.core import autoconfig
from goldenmatch.core.throughput_verify import ThroughputNotApplicableError
from goldenmatch.config.schemas import ThroughputConfig


def _corpus_df():
    return pl.DataFrame({"body": ["the cat sat", "the cat sat on the mat",
                                  "an entirely separate sentence about dogs"] * 5})


def test_throughput_forces_lsh_on_text_column(monkeypatch):
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)
    cfg = autoconfig.auto_configure_df(_corpus_df(), throughput=0.95)
    assert cfg.blocking.strategy == "lsh"
    assert cfg.blocking.lsh.column == "body"


def test_throughput_uses_simhash_when_embedder_available(monkeypatch):
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: True)
    cfg = autoconfig.auto_configure_df(_corpus_df(), throughput=True)
    assert cfg.blocking.strategy == "simhash"


def test_throughput_raises_without_text_column():
    df = pl.DataFrame({"zip": [10001, 10002, 10003], "age": [20, 30, 40]})
    with pytest.raises(ThroughputNotApplicableError):
        autoconfig.auto_configure_df(df, throughput=True)
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** Add `throughput=None` to `auto_configure_df` signature; resolve it via `resolve_throughput_config(throughput, ...)`; when enabled, stash on the returned `config.throughput`. In `build_blocking` (or right after it), when throughput is enabled call:

```python
def _throughput_blocking(profiles, config):
    """Force lsh/simhash blocking on the longest text column (throughput tier).

    Bypasses _is_text_corpus — the user opted in. Reuses _text_corpus_blocking's
    column-pick + embedder-aware routing. Raises if no text column exists.
    """
    from goldenmatch.core.throughput_verify import ThroughputNotApplicableError
    text_cols = [p for p in profiles if p.col_type in ("description", "string", "name", "multi_name")]
    if not text_cols:
        raise ThroughputNotApplicableError(
            "throughput tier requires a text column to sketch on; none found")
    # delegate to the existing embedder-aware routing (simhash if available, else lsh)
    return _text_corpus_blocking(profiles, df=None, config=config)
```

> **Implementation note:** confirm `_text_corpus_blocking`'s exact signature and that it selects the longest-avg-len text column among `description`-typed profiles; if it only considers `description`, broaden the candidate set here (string/name) before delegating, or inline its `lsh`/`simhash` build. The three tests pin the required behavior.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): force lsh/simhash blocking + threading in auto-config (#1083)`.

---

## Task 9: Controller threading + overlay call

**Files:**
- Modify: `core/autoconfig_controller.py` (`run(..., throughput=None)`; resolve; after the base `ExecutionPlan` is built, call `apply_throughput_overlay` with the right `metric`/`signature_len` from the committed blocking config; store on `RunHistory`)
- Modify: `core/autoconfig.py` (`auto_configure_df` passes `throughput` into `controller.run`)
- Test: `tests/test_throughput_planner.py` (extend with a controller-level test using a small corpus)

- [ ] **Step 1: Failing test**

```python
def test_controller_emits_sketch_distance_plan_for_throughput(monkeypatch):
    import polars as pl
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)
    df = pl.DataFrame({"body": ["the cat sat", "the cat sat on the mat",
                                "a different sentence"] * 20})
    cfg = autoconfig.auto_configure_df(df, throughput=0.95)
    plan = getattr(cfg, "_throughput_plan", None)
    assert plan is not None and plan.verify_mode == "sketch_distance"
    assert plan.sketch_similarity == 0.8 and plan.sketch_bands * plan.sketch_rows == cfg.blocking.lsh.num_perms
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement.** Thread `throughput` through `run`; derive `metric` from the committed blocking strategy (`lsh` → `jaccard` w/ `signature_len = lsh.num_perms`; `simhash` → `cosine` w/ `signature_len = simhash.num_planes`); call `apply_throughput_overlay`; `plan.apply_to(committed_config)` so `_throughput_plan` lands on the config.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): controller threads + overlays the throughput plan (#1083)`.

---

## Task 10: Pipeline dispatch + `dedupe_df` kwarg + `DedupeResult.throughput_posture`

**Files:**
- Modify: `_api.py` (`dedupe_df(..., throughput=None)`; pass to `auto_configure_df`; add `DedupeResult.throughput_posture: dict | None = None`)
- Modify: `core/pipeline.py` (when `config._throughput_plan` present, route lsh/simhash candidate pairs to `score_sketch_pairs`; build posture)
- Test: `tests/test_throughput_integration.py`

- [ ] **Step 1: Failing test**

```python
import polars as pl
from goldenmatch import dedupe_df


def test_dedupe_df_throughput_finds_near_dups_and_reports_posture(monkeypatch):
    from goldenmatch.core import autoconfig
    monkeypatch.setattr(autoconfig, "_embedder_available", lambda config=None: False)
    base = ["the quick brown fox jumps over the lazy dog"]
    near = ["the quick brown fox jumps over the lazy dogs"]   # near-dup
    far = ["completely unrelated text about quantum computing"]
    df = pl.DataFrame({"body": (base * 3) + (near * 3) + (far * 3)})
    res = dedupe_df(df, throughput=0.95)
    assert res.throughput_posture is not None
    assert res.throughput_posture["metric"] == "jaccard"
    assert 0.0 <= res.throughput_posture["expected_recall"] <= 1.0
    # the base/near cluster should merge; far stays separate
    assert res.clusters  # at least one multi-member cluster formed
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement a self-contained throughput branch.** The normal pipeline
builds blocks (LazyFrames) via `build_blocks` (~`pipeline.py:1545`) and only scores
`mk.type == "weighted"` matchkeys (the loop at ~`pipeline.py:1513`). The throughput
tier has **no weighted matchkey**, so it gets its OWN branch that produces candidate
pairs directly and hands the scored pairs to the same cluster stage — bypassing the
weighted loop. In `_run_dedupe_pipeline`, before the normal block+score stages:

```python
plan = getattr(config, "_throughput_plan", None)
if plan is not None and plan.verify_mode == "sketch_distance":
    from goldenmatch.core import throughput_verify as tv
    from goldenmatch.core.lsh_blocker import MinHashLSHBlocker
    from goldenmatch.core.simhash_blocker import SimHashLSHBlocker

    n_rows = collected.height
    if config.blocking.strategy == "simhash":
        sc = config.blocking.simhash
        texts = collected[sc.column].cast(pl.Utf8).fill_null("").to_list()
        emb = _embed_text_column(texts, model=sc.model)   # ONE embed; see note
        blocker = SimHashLSHBlocker(num_planes=sc.num_planes,
                                    num_bands=plan.sketch_bands, seed=sc.seed)
        pairs = blocker.candidate_pairs(emb)
        scored = tv.score_sketch_pairs(pairs, metric="cosine",
                                       threshold=plan.sketch_similarity, embeddings=emb)
        metric = "cosine"
    else:  # lsh
        lc = config.blocking.lsh
        texts = collected[lc.column].cast(pl.Utf8).fill_null("").to_list()
        blocker = MinHashLSHBlocker(mode=lc.mode, k=lc.k, num_perms=lc.num_perms,
                                    num_bands=plan.sketch_bands, seed=lc.seed)
        pairs = blocker.candidate_pairs(texts)
        scored = tv.score_sketch_pairs(pairs, metric="jaccard",
                                       threshold=plan.sketch_similarity, texts=texts,
                                       mode=lc.mode, k=lc.k, num_perms=lc.num_perms, seed=lc.seed)
        metric = "jaccard"

    _throughput_posture = tv.build_posture(
        metric=metric, recall_target=config.throughput.recall_target,
        similarity=plan.sketch_similarity, bands=plan.sketch_bands, rows=plan.sketch_rows,
        n_rows=n_rows, candidate_pairs=len(pairs), verified_pairs=len(scored),
        semantic_fell_back=False)
    # hand `scored` (list[(id_a,id_b,score)]) to the SAME cluster stage the normal
    # path uses, then golden, then return — skipping the weighted-matchkey loop.
```

> **Implementation notes (trace first, then wire):**
> - **Constructors are real** — `MinHashLSHBlocker(mode,k,num_perms,num_bands,seed)` (`lsh_blocker.py:23`) and `SimHashLSHBlocker(num_planes,num_bands,seed)` (`simhash_blocker.py:26`); `.candidate_pairs(...)` returns `set[tuple[int,int]]`. Pass `plan.sketch_bands` as `num_bands` (the recall-tuned banding).
> - **Embed exactly once (write the wrapper — there is NO pre-existing `_embed_text_column`).** `build_simhash_blocks` embeds the column internally and DISCARDS the array (`simhash_blocker.py:109-125`); do NOT call it. Write a tiny wrapper around the SAME idiom it uses at `simhash_blocker.py:118-123`:
>   ```python
>   from goldenmatch.core.embedder import get_embedder   # get_embedder: embedder.py:173
>   def _embed_text_column(texts, model=None):
>       emb = get_embedder(model).embed_column(texts, cache_key="throughput")  # embed_column: embedder.py:34/103
>       return np.asarray(emb, dtype=np.float64)
>   ```
>   This embeds once and feeds both the blocker and verify.
> - **Cluster hand-off + positional-index remap.** The normal path passes scored pairs into `build_cluster_frames(all_pairs, all_ids, ...)` at `pipeline.py:1990`, where `all_ids = collected["__row_id__"].to_list()` (`pipeline.py:1939`). **`.candidate_pairs(...)` returns POSITIONAL indices into `texts`/`emb`, NOT `__row_id__` values** — they coincide only for single-source dedupe (row-index offset 0; offset applied at `pipeline.py:726-728`). For multi-source, remap each positional pair `(i, j)` to `(all_ids[i], all_ids[j])` before clustering (or build `texts`/`emb` in `__row_id__` order). Then route the throughput `scored` into the same `build_cluster_frames(...)` call and reuse golden + return assembly unchanged.
> - **`semantic_fell_back=False`** here: by pipeline time `config.blocking.strategy` already reflects the auto-config lsh-vs-simhash decision. If you want the "wanted semantic but no embedder → lexical" note, set a bool on `config.throughput` at auto-config time (Task 8) and read it here instead of hardcoding False.
> - Thread `_throughput_posture.to_dict()` onto `DedupeResult.throughput_posture` where `_api.py` assembles the result.

In `_api.py`, add the `throughput=None` kwarg, pass it to `auto_configure_df(..., throughput=throughput)`, and add the `DedupeResult` field.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): pipeline sketch-verify dispatch + dedupe_df knob + result posture (#1083)`.

---

## Task 11: Posture surfacing (PostflightReport + telemetry)

**Files:**
- Modify: `core/autoconfig_verify.py` (`_render_throughput_line` + call in `__str__`; thread the posture into `PostflightReport`)
- Modify: `web/controller_telemetry.py` (`_throughput_summary` + `"throughput"` key)
- Test: `tests/test_throughput_posture.py` (extend) + `tests/web/test_controller_telemetry.py` (extend if present)

- [ ] **Step 1: Failing test**

```python
def test_telemetry_has_throughput_block_when_present():
    from goldenmatch.web.controller_telemetry import _throughput_summary
    posture = {"metric": "jaccard", "expected_recall": 0.97, "reduction_ratio": 0.01,
               "bands": 16, "rows_per_band": 8, "candidate_pairs": 100, "verified_pairs": 90}
    assert _throughput_summary(posture)["metric"] == "jaccard"
    assert _throughput_summary(None) is None


def test_postflight_renders_throughput_line():
    from goldenmatch.core.autoconfig_verify import _render_throughput_line
    line = _render_throughput_line({"metric": "jaccard", "expected_recall": 0.97,
                                    "reduction_ratio": 0.01})
    assert "throughput" in line.lower() and "0.97" in line
    assert _render_throughput_line(None) == ""
```

- [ ] **Step 2: Run, verify fail.**

- [ ] **Step 3: Implement** the two helpers; wire `_throughput_summary(posture)` into `serialize_telemetry`'s return dict under `"throughput"` (pass the posture through, however the telemetry path receives the run result), and `_render_throughput_line` into `PostflightReport.__str__` after the blocking line.

- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit** — `feat(throughput): surface posture on PostflightReport + telemetry (#1083)`.

---

## Task 12: Off-by-default fence + docs + CHANGELOG

**Files:**
- Test: `tests/test_throughput_integration.py` (extend)
- Modify: `docs-site/goldenmatch/blocking.mdx`, `docs-site/goldenmatch/configuration.mdx`, `docs-site/goldenmatch/tuning.mdx`
- Modify: `packages/python/goldenmatch/CHANGELOG.md`

- [ ] **Step 1: Off-by-default regression fence**

```python
def test_throughput_off_is_byte_identical(monkeypatch):
    import polars as pl
    from goldenmatch import dedupe_df
    df = pl.DataFrame({"name": ["alice", "alicia", "bob", "bobby", "carol"] * 10,
                       "zip": ["10001", "10001", "20002", "20002", "30003"] * 10})
    a = dedupe_df(df)
    b = dedupe_df(df, throughput=None)
    assert a.throughput_posture is None and b.throughput_posture is None
    assert a.clusters.keys() == b.clusters.keys()
```

- [ ] **Step 2: Run, verify pass** (no impl needed — it asserts the invariant).

- [ ] **Step 3: Docs.** Add a "Throughput tier (sketch-then-verify)" section to `tuning.mdx` (the `GOLDENMATCH_THROUGHPUT*` env vars + `throughput=` kwarg + the recall knob + the honest-posture caveat). Add `throughput` to the `GoldenMatchConfig` reference in `configuration.mdx`. Add a one-paragraph note + the posture explanation to `blocking.mdx` near the lsh/simhash sections (cross-link: "for high-recall low-cost corpus dedup, see the throughput tier"). Keep ASCII; follow existing mdx style.

- [ ] **Step 4: CHANGELOG.** Add an `### Added` entry under the next version in `packages/python/goldenmatch/CHANGELOG.md`: opt-in `throughput` tier (sketch-then-verify), `dedupe_df(throughput=...)`, recall knob, honest LSH posture; note default-off byte-identical; reference #1083.

- [ ] **Step 5: Commit + push + PR**

```bash
git add -A
git commit -m "test(throughput): off-by-default fence + docs + changelog (#1083)"
git push -u origin feat/1083-throughput-plan
gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "feat: sketch-then-verify throughput execution plan (#1083)" \
  --body "Implements #1083 (epic #1080). Opt-in throughput tier: lsh/simhash blocking + sketch-distance verify, recall knob, honest LSH posture. Default-off byte-identical. Spec: docs/superpowers/specs/2026-06-19-sketch-then-verify-throughput-plan-design.md"
```

---

## Verification checklist (run before opening the PR)

- [ ] `.venv/Scripts/python.exe -m pytest tests/test_throughput_*.py -v` all green locally (targeted run only — never the full suite locally; xdist OOMs this box).
- [ ] `python -m ruff check --select E9,F63,F7,F82 goldenmatch/core/throughput_verify.py` clean.
- [ ] `python -c "import ast; ast.parse(open('goldenmatch/core/throughput_verify.py').read())"` parses.
- [ ] Grep the diff for an accidental default flip: `throughput` must default `None`/off everywhere.
- [ ] Confirm `dedupe_df(df)` (no throughput) path is untouched — the fence test in Task 12.
- [ ] Arm auto-merge once CI is green: `gh pr merge <N> --auto --squash` then STOP (merge queue lands it).

## Scope reminder (do NOT build here)

- Distributed/billion-scale throughput → #1084. The planner overlay composes with backend rules, but `score_sketch_pairs` is single-node; do not distribute it.
- Corpus parquet/jsonl adapters + a `corpus-dedupe` CLI/product surface → #1085.
- Throughput benchmark + CI perf gate, and any auto-selection default-on flip → #1086.
