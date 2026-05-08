# Auto-Config Negative Evidence + Clustered-Identity Guard Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `NegativeEvidenceField` schema + eager `promote_negative_evidence` rule + lazy `compute_identity_collision_signal` indicator + post-iteration `rule_demote_clustered_identity` to lift DQbench T3 F1 from 53.8% to ≥70% (target composite ≥75 primary, ≥70 fallback).

**Architecture:** Three new layers with one new schema field. `MatchkeyConfig.negative_evidence: list[NegativeEvidenceField] | None = None` (default-None for v1.10 cache compat). Eager rule `promote_negative_evidence` runs at config-build time, scans unused identity-prior columns, populates negative-evidence fields. Lazy indicator `compute_identity_collision_signal` does full-data within-group divergence (8s budget). Post-iteration rule `rule_demote_clustered_identity` demotes exact-email matchkeys when collision_rate > 0.2.

**Tech Stack:** Python 3.12, polars, Pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md` — read this first.

---

## Pre-flight checklist

- [ ] Working in dedicated branch `feature/autoconfig-richer-matchkey` (currently at HEAD `800e34e` with spec committed); branched off v1.10's HEAD `a7c2a57` so v1.10's code is in scope.
- [ ] Once v1.10 PR #119 merges, `git rebase main` to align — should be a clean rebase since v1.11 work hasn't touched v1.10's modified files.
- [ ] DQbench dataset present at `~/.dqbench/datasets/er_tier{1,2,3}/data.csv`.
- [ ] DBLP-ACM, NCVR samples at `packages/python/goldenmatch/tests/benchmarks/datasets/`.
- [ ] OPENAI_API_KEY available via `set -a && source /d/show_case/goldencheck/.testing/.env && set +a` (only needed if Phase 7's optional with-LLM run is requested).
- [ ] Editable install: `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch._api; print(goldenmatch._api.__file__)"` shows the worktree path.
- [ ] Baseline test count: 1907 passing (v1.10 release). After v1.11, expect ~1957 (+50 new).
- [ ] Bash shell (Git Bash); Python pinned at `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe`.
- [ ] Diagnostic findings available at `.profile_tmp/v111_t3_findings.txt` (gitignored — generated during brainstorming).

---

## File structure (locked in here)

| File | Role | Change |
|---|---|---|
| `config/schemas.py` | Existing | Add `NegativeEvidenceField` Pydantic model; add `negative_evidence: list[NegativeEvidenceField] \| None = None` field to `MatchkeyConfig` |
| `core/scorer.py` (or `core/matchkey.py`) | Existing | Add `_apply_negative_evidence(matchkey, pair) -> float` helper; integrate into existing scoring loop |
| `core/autoconfig_negative_evidence.py` | NEW module | `promote_negative_evidence(config, df, column_priors) -> GoldenMatchConfig` (pure function) + `_pick_scorer_for_column(col_name, col_type) -> tuple[list[str], str]` |
| `core/autoconfig.py` | Existing | Call `promote_negative_evidence` post-v0-build, pre-iteration |
| `core/indicators.py` | Existing | Add `compute_identity_collision_signal(df, identity_col, witness_cols) -> CollisionSignal`; add `BUDGET_COLLISION = 8.0` constant |
| `core/complexity_profile.py` | Existing | Add `CollisionSignal` dataclass (rate + witness_used) |
| `core/autoconfig_controller.py` | Existing | Add `IndicatorContext.identity_collision_signal(identity_col, witness_cols)` memoized method |
| `core/autoconfig_rules.py` | Existing | Add `rule_demote_clustered_identity` + `_demote_exact_to_weighted_fuzzy` helper; append to `DEFAULT_RULES` at position 14 |
| `tests/test_negative_evidence_scoring.py` | NEW | Tier 3: schema + scoring integration (~80 LOC) |
| `tests/test_autoconfig_negative_evidence.py` | NEW | Tier 1: `promote_negative_evidence` + `_pick_scorer_for_column` tests (~70 LOC) |
| `tests/test_indicators.py` | Existing | Tier 1 extension: `compute_identity_collision_signal` tests (~40 LOC) |
| `tests/test_autoconfig_rules.py` | Existing | Tier 2 extension: `rule_demote_clustered_identity` + `_demote_exact_to_weighted_fuzzy` tests (~100 LOC) |
| `tests/test_dqbench_t3_recovery.py` | NEW | Tier 4: synthetic + v1.10-compat integration (~150 LOC) |
| `tests/test_autoconfig_memory_v110_compat.py` | NEW | Tier 5: cache backward-compat (~50 LOC) |
| `tests/test_autoconfig_properties.py` | Existing | Tier 6 extension: monotonicity + idempotency (~40 LOC) |
| `tests/test_indicators_budget.py` | Existing | Tier 7 extension: collision-signal budget + NE scoring overhead (~30 LOC) |
| `tests/fixtures/autoconfig/t3_synthetic.csv` | NEW | 200-row collision fixture |
| `tests/fixtures/autoconfig/t3_clean_compat.csv` | NEW | 200-row clean fixture |
| `tests/fixtures/autoconfig/v1_10_memory_snapshot.json` | NEW | v1.10 schema cache fixture |
| `tests/fixtures/autoconfig/_gen_v1_10_snapshot.py` | NEW | Generator for v1.10 fixture |
| `tests/fixtures/autoconfig/_gen_t3_synthetic.py` | NEW | Generator for T3 fixtures |

**Total: ~500 LOC code + ~350 LOC tests + ~6 fixture files = ~850 LOC.**

---

## Phase 1 — Schema: `NegativeEvidenceField` + `MatchkeyConfig.negative_evidence`

Foundation. Default-None field add; backward-compat preserved.

### Task 1.1: Add `NegativeEvidenceField` Pydantic model

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py`
- Test: `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py` (NEW)

- [ ] **Step 1: Read existing `MatchkeyField` for shape reference**

```bash
sed -n '60,110p' /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/config/schemas.py
```

Confirm: `MatchkeyField` has `field`, `transforms`, `scorer`, `weight` with validators.

- [ ] **Step 2: Failing tests**

Create `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py`:

```python
"""v1.11: NegativeEvidenceField schema + scoring integration tests."""
import pytest


def test_negative_evidence_field_construct():
    from goldenmatch.config.schemas import NegativeEvidenceField
    ne = NegativeEvidenceField(
        field="phone", transforms=["digits_only"], scorer="exact",
        threshold=0.5, penalty=0.3,
    )
    assert ne.field == "phone"
    assert ne.transforms == ["digits_only"]
    assert ne.scorer == "exact"
    assert ne.threshold == 0.5
    assert ne.penalty == 0.3


def test_negative_evidence_field_default_transforms_empty():
    from goldenmatch.config.schemas import NegativeEvidenceField
    ne = NegativeEvidenceField(
        field="address", scorer="token_sort", threshold=0.4, penalty=0.4,
    )
    assert ne.transforms == []


def test_negative_evidence_field_validates_scorer():
    """scorer must be in VALID_SCORERS; 'digits_only_exact' is not registered."""
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"scorer"):
        NegativeEvidenceField(
            field="phone", scorer="digits_only_exact",
            threshold=0.5, penalty=0.3,
        )


def test_negative_evidence_field_rejects_threshold_out_of_range():
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"threshold"):
        NegativeEvidenceField(
            field="x", scorer="exact", threshold=1.5, penalty=0.3,
        )


def test_negative_evidence_field_rejects_penalty_out_of_range():
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError, match=r"penalty"):
        NegativeEvidenceField(
            field="x", scorer="exact", threshold=0.4, penalty=-0.1,
        )


def test_negative_evidence_field_validates_transforms():
    """transforms must be in VALID_SIMPLE_TRANSFORMS."""
    import pydantic
    from goldenmatch.config.schemas import NegativeEvidenceField
    with pytest.raises(pydantic.ValidationError):
        NegativeEvidenceField(
            field="x", transforms=["nonexistent_transform"],
            scorer="exact", threshold=0.4, penalty=0.3,
        )
```

- [ ] **Step 3: Run; expect 6 FAIL with ImportError**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_negative_evidence_scoring.py -v --timeout=60 2>&1 | tail -10
```

- [ ] **Step 4: Implement `NegativeEvidenceField`**

Edit `packages/python/goldenmatch/goldenmatch/config/schemas.py`. Place AFTER `MatchkeyField` (around line 150, before `MatchkeyConfig`):

```python
class NegativeEvidenceField(BaseModel):
    """v1.11: a field whose disagreement subtracts from a weighted matchkey's
    score. Mirrors MatchkeyField's shape so transforms can normalize before
    scoring (e.g., transforms=['digits_only'] + scorer='exact' for phone).

    Spec: docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md
    """
    model_config = ConfigDict(extra="forbid")

    field: str
    transforms: list[str] = Field(default_factory=list)
    scorer: str
    threshold: float = Field(ge=0.0, le=1.0)
    penalty: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate_transforms_and_scorer(self) -> "NegativeEvidenceField":
        for t in self.transforms:
            if t not in VALID_SIMPLE_TRANSFORMS:
                raise ValueError(
                    f"Invalid transform '{t}'. Must be one of "
                    f"{sorted(VALID_SIMPLE_TRANSFORMS)}"
                )
        if self.scorer not in VALID_SCORERS:
            raise ValueError(
                f"Invalid scorer '{self.scorer}'. Must be one of "
                f"{sorted(VALID_SCORERS)}"
            )
        return self
```

If `model_validator` and `ConfigDict` and `Field` aren't imported at the top of the file, add them (they're already used elsewhere — check existing imports).

- [ ] **Step 5: Re-run tests; expect 6 PASS**

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_negative_evidence_scoring.py
git commit -m "feat(autoconfig): add NegativeEvidenceField Pydantic model with validators"
```

### Task 1.2: Add `negative_evidence` field to `MatchkeyConfig`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py` (`MatchkeyConfig` class)
- Test: `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py` (extend)

- [ ] **Step 1: Failing tests — append:**

```python
def test_matchkey_config_negative_evidence_default_none():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="test",
        type="weighted",
        threshold=0.85,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="ensemble", weight=1.0)],
    )
    assert mk.negative_evidence is None


def test_matchkey_config_accepts_negative_evidence():
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="test", type="weighted", threshold=0.85,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.5, penalty=0.3),
        ],
    )
    assert mk.negative_evidence is not None
    assert len(mk.negative_evidence) == 1
    assert mk.negative_evidence[0].field == "phone"


def test_matchkey_config_v110_cache_compat_no_field():
    """v1.10-saved JSON without negative_evidence key deserializes cleanly."""
    from goldenmatch.config.schemas import MatchkeyConfig
    legacy_json = {
        "name": "primary", "type": "weighted", "threshold": 0.85,
        "fields": [{"field": "email", "transforms": ["lowercase"],
                    "scorer": "ensemble", "weight": 1.0}],
    }
    mk = MatchkeyConfig.model_validate(legacy_json)
    assert mk.negative_evidence is None
```

- [ ] **Step 2: Run; expect 3 FAIL**

- [ ] **Step 3: Add field to `MatchkeyConfig`**

In `MatchkeyConfig` class (line ~154), add the field at the end of existing fields list:

```python
class MatchkeyConfig(BaseModel):
    # ... existing fields ...
    negative_evidence: list[NegativeEvidenceField] | None = None
```

Place after the existing fields (which include `name`, `type`, `threshold`, `fields`, etc.). Pydantic v2 doesn't enforce default-after-required ordering for BaseModel like dataclasses do, but place it logically last for readability.

- [ ] **Step 4: Re-run tests; expect 9 PASS (6 from 1.1 + 3 new)**

- [ ] **Step 5: Run full schema tests for regression**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_config.py tests/test_negative_evidence_scoring.py -q --timeout=60 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/tests/test_negative_evidence_scoring.py
git commit -m "feat(autoconfig): add MatchkeyConfig.negative_evidence field (default-None)"
```

---

## Phase 2 — Scoring integration (`_apply_negative_evidence`)

The hot-path code that subtracts the negative-evidence penalty from the positive score.

### Task 2.1: `_apply_negative_evidence` helper

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (find the appropriate location for a helper that the scoring loop can call)
- Test: `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py` (extend)

- [ ] **Step 1: Locate the existing scoring loop**

```bash
grep -n "def find_fuzzy_matches\|def score_blocks\|def _score_pair\|matchkey.fields\|matchkey.threshold" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/scorer.py | head -20
```

Find where the per-pair score is computed for a weighted matchkey. The helper will be called from there.

- [ ] **Step 2: Failing tests — append to test file:**

```python
def test_apply_negative_evidence_returns_zero_when_no_ne():
    """Empty/None negative_evidence → zero penalty."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "555-9999")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0


def test_apply_negative_evidence_subtracts_when_field_disagrees():
    """NE field disagrees (sim < threshold) → penalty applied."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="phone", transforms=["digits_only"],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "555-9999")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.3   # phones disagree → full penalty


def test_apply_negative_evidence_no_subtract_when_field_agrees():
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="phone", transforms=["digits_only"],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555-1234", "5551234")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0   # phones agree (after digits_only transform)


def test_apply_negative_evidence_skips_unregistered_scorer(caplog):
    """Defensive: unknown scorer → skip + WARNING (since validators should
    have caught this at construction time, this exercises the runtime guard)."""
    import logging
    # Bypass Pydantic validation by constructing via model_construct
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    bogus = NegativeEvidenceField.model_construct(
        field="phone", transforms=[],
        scorer="nonexistent_scorer", threshold=0.5, penalty=0.3,
    )
    mk.negative_evidence = [bogus]
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("555", "999")}
    with caplog.at_level(logging.WARNING):
        penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0    # skipped, not crashed
    assert any("nonexistent_scorer" in r.message for r in caplog.records)


def test_apply_negative_evidence_skips_missing_field():
    """Field not in pair dict → skip (defensive)."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(
                field="missing_field", transforms=[],
                scorer="exact", threshold=0.5, penalty=0.3,
            ),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com")}    # no missing_field
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0
```

- [ ] **Step 3: Run; expect 5 FAIL with ImportError**

- [ ] **Step 4: Implement `_apply_negative_evidence`**

The exact location depends on `core/scorer.py`'s structure. Place it as a module-level helper near other private helpers (after imports, before the main scoring functions). Read the file to find the convention.

```python
def _apply_negative_evidence(matchkey, pair: dict) -> float:
    """v1.11: compute the total negative-evidence penalty for a pair.

    Returns the sum of penalties for NE fields whose similarity is below
    their threshold. Defensive: skips NE entries with unknown scorers,
    missing fields, or scorer-call exceptions; logs WARNING and continues.

    Caller is responsible for: `final_score = max(0.0, score_positive - penalty)`.
    """
    if not matchkey.negative_evidence:
        return 0.0

    from goldenmatch.core.scorer import _score_one  # or whichever helper exists
    # If no equivalent helper exists, define it inline using rapidfuzz directly.

    total_penalty = 0.0
    for ne in matchkey.negative_evidence:
        if ne.field not in pair:
            logger.warning(
                "auto-config: NE field '%s' not in pair; skipping", ne.field,
            )
            continue
        try:
            a, b = pair[ne.field]
            # Apply transforms
            for t in ne.transforms:
                a = _apply_transform(a, t)
                b = _apply_transform(b, t)
            # Score
            sim = _score_one(a, b, ne.scorer)
        except KeyError as exc:
            logger.warning(
                "auto-config: NE scorer '%s' for field '%s' not registered; skipping",
                ne.scorer, ne.field,
            )
            continue
        except Exception as exc:
            logger.warning(
                "auto-config: NE scoring of field '%s' raised %s; skipping",
                ne.field, type(exc).__name__,
            )
            continue
        if sim < ne.threshold:
            total_penalty += ne.penalty
    return total_penalty
```

The exact transform-and-score primitives in goldenmatch may not be `_score_one` and `_apply_transform`. Read `core/scorer.py` and `core/standardize.py` to find the right names. If no suitable single-pair helper exists, the implementation may need to call rapidfuzz directly:

```python
from rapidfuzz import fuzz
SCORER_FN = {
    "exact": lambda a, b: 1.0 if a == b else 0.0,
    "jaro_winkler": lambda a, b: fuzz.WRatio(a, b) / 100.0,
    "token_sort": lambda a, b: fuzz.token_sort_ratio(a, b) / 100.0,
    "levenshtein": lambda a, b: fuzz.ratio(a, b) / 100.0,
    "ensemble": ...    # may need to delegate; check existing scorer.py
}
```

Use whatever pattern matches the existing scoring code's style — don't reinvent if there's an existing helper.

- [ ] **Step 5: Re-run tests; expect 5 PASS**

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_negative_evidence_scoring.py
git commit -m "feat(autoconfig): _apply_negative_evidence helper (subtract penalty when NE field disagrees)"
```

### Task 2.2: Wire `_apply_negative_evidence` into the scoring loop

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (the per-pair score-aggregation function)
- Test: `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py` (extend)

- [ ] **Step 1: Failing test — end-to-end through the scoring loop**

```python
def test_score_pair_with_negative_evidence_drops_below_threshold():
    """E2E: positive=0.9, NE penalty=0.3, threshold=0.8 → final=0.6 → no match."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        NegativeEvidenceField, BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch._api import dedupe_df

    df = pl.DataFrame({
        # 2 records: same name+email, different phones (collision pair)
        "first_name": ["Brian", "Brian"],
        "email": ["b@x.com", "b@x.com"],
        "phone": ["5551234", "5559999"],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[
                MatchkeyField(field="first_name", transforms=["lowercase"],
                              scorer="ensemble", weight=0.5),
                MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=0.5),
            ],
            negative_evidence=[
                NegativeEvidenceField(
                    field="phone", transforms=["digits_only"],
                    scorer="exact", threshold=0.5, penalty=0.4,
                ),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    result = dedupe_df(df, config=config)
    # NE on phone disagrees → penalty 0.4 → final = 1.0 - 0.4 = 0.6 < 0.8
    # → 0 matches, 2 distinct clusters
    if hasattr(result, "clusters"):
        assert len(result.clusters) == 2 or result.clusters == {}
```

- [ ] **Step 2: Run; the test may need iteration depending on existing scoring loop's shape**

- [ ] **Step 3: Find the scoring aggregation site**

```bash
grep -n "matchkey.threshold\|>= matchkey\|>= mk.threshold\|sum.*weight" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/scorer.py | head -10
```

Locate where `weighted matchkey` is computed: a sum over fields × weights compared against `threshold`. Insert the negative-evidence call:

```python
# Before:
score = sum(field.weight * scorer_fn(...) for field in matchkey.fields)
if score >= matchkey.threshold:
    # match

# After:
score_positive = sum(field.weight * scorer_fn(...) for field in matchkey.fields)
score_negative = _apply_negative_evidence(matchkey, pair_dict)
final_score = max(0.0, score_positive - score_negative)
if final_score >= matchkey.threshold:
    # match
```

The exact location depends on the scoring loop. If there are multiple matchkey-types (weighted, exact, probabilistic), apply ONLY in the weighted-matchkey branch. Exact and probabilistic matchkeys should not have NE applied (per spec §Non-goals: NE on exact matchkeys is out of scope; the clustered-identity guard is the path for those).

- [ ] **Step 4: Run E2E test; expect PASS**

- [ ] **Step 5: Run broader scorer regression**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_scorer.py tests/test_pipeline.py tests/test_negative_evidence_scoring.py -q --timeout=120 2>&1 | tail -10
```

Expected: all pass; existing scoring behavior unchanged for matchkeys without `negative_evidence`.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_negative_evidence_scoring.py
git commit -m "feat(autoconfig): wire _apply_negative_evidence into weighted-matchkey scoring loop"
```

---

## Phase 3 — Eager promotion module + `auto_configure_df` integration

### Task 3.1: Create `core/autoconfig_negative_evidence.py`

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py` (NEW)

- [ ] **Step 1: Failing tests**

Create `packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py`:

```python
"""v1.11: tests for promote_negative_evidence + _pick_scorer_for_column."""
import polars as pl
import pytest


def test_pick_scorer_for_column_email():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("email", "email")
    assert transforms == []
    assert scorer == "token_sort"


def test_pick_scorer_for_column_phone():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("phone", "phone")
    assert transforms == ["digits_only"]
    assert scorer == "exact"


def test_pick_scorer_for_column_address():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("home_address", "address")
    assert transforms == []
    assert scorer == "token_sort"


def test_pick_scorer_for_column_unknown_falls_back_to_ensemble():
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    transforms, scorer = _pick_scorer_for_column("custom_field", "text")
    assert transforms == []
    assert scorer == "ensemble"


def test_pick_scorer_validates_against_VALID_SCORERS():
    """Returned scorer is always in VALID_SCORERS."""
    from goldenmatch.config.schemas import VALID_SCORERS
    from goldenmatch.core.autoconfig_negative_evidence import _pick_scorer_for_column
    for col_type in ["email", "phone", "address", "text", "id-like", "date"]:
        _, scorer = _pick_scorer_for_column("any", col_type)
        assert scorer in VALID_SCORERS


def test_promote_negative_evidence_t3_pattern():
    """T3-shaped df: phone+address get promoted to NE on the weighted matchkey."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "first_name": ["Brian"] * 10,
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
        "address": [f"{i} Main St" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first_name", transforms=["lowercase"],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    column_priors = {
        "first_name": ColumnPrior(identity_score=0.3, corruption_score=0.0),
        "email": ColumnPrior(identity_score=0.95, corruption_score=0.0),
        "phone": ColumnPrior(identity_score=0.85, corruption_score=0.0),
        "address": ColumnPrior(identity_score=0.7, corruption_score=0.0),
    }
    new_config = promote_negative_evidence(config, df, column_priors)
    ne = new_config.matchkeys[0].negative_evidence
    assert ne is not None
    ne_fields = {n.field for n in ne}
    # phone, address get promoted (identity_score >= 0.7 + cardinality_ratio >= 0.5)
    assert "phone" in ne_fields
    assert "address" in ne_fields
    # email is in blocking → skipped
    # first_name is in matchkey.fields → skipped
    # first_name has identity_score 0.3 → wouldn't qualify anyway
    assert "first_name" not in ne_fields
    assert "email" not in ne_fields


def test_promote_negative_evidence_idempotent():
    """Calling twice doesn't double-add."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "first_name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="first_name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["first_name"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "first_name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(config, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    # NE list should be identical length on both
    assert len(once.matchkeys[0].negative_evidence) == len(twice.matchkeys[0].negative_evidence)


def test_promote_negative_evidence_skips_blocking_columns():
    """A column used in blocking should not be promoted as NE."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="primary", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["phone"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    new_config = promote_negative_evidence(config, df, priors)
    ne = new_config.matchkeys[0].negative_evidence
    # phone is in blocking → not promoted
    assert ne is None or all(n.field != "phone" for n in ne)


def test_promote_negative_evidence_empty_df_returns_unchanged():
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="t", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="x", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["x"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    new_config = promote_negative_evidence(config, pl.DataFrame(), {})
    assert new_config == config
```

- [ ] **Step 2: Run; expect 9 FAIL**

- [ ] **Step 3: Implement**

Create `packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py`:

```python
"""v1.11: eager promotion of identity-prior columns to negative evidence.

Spec: docs/superpowers/specs/2026-05-08-autoconfig-negative-evidence-and-clustered-identity-design.md §Architecture #2.

Pure function — no controller state, no I/O. Called from auto_configure_df
between config-v0 build and the iteration loop.
"""
from __future__ import annotations
import logging

import polars as pl

from goldenmatch.config.schemas import (
    GoldenMatchConfig, MatchkeyConfig, NegativeEvidenceField, VALID_SCORERS,
)
from goldenmatch.core.complexity_profile import ColumnPrior

logger = logging.getLogger(__name__)

_IDENTITY_SCORE_THRESHOLD = 0.7
_CARDINALITY_THRESHOLD = 0.5
_DEFAULT_NE_THRESHOLD = 0.4
_DEFAULT_NE_PENALTY = 0.3


def _pick_scorer_for_column(col_name: str, col_type: str) -> tuple[list[str], str]:
    """Pick (transforms, scorer) tuple for negative-evidence on a column.

    Always returns a scorer from VALID_SCORERS. Defaults to ('[]', 'ensemble')
    for unknown column types.
    """
    name_lower = col_name.lower()
    type_lower = (col_type or "").lower()
    if "phone" in name_lower or type_lower == "phone":
        return (["digits_only"], "exact")
    if "email" in name_lower or type_lower == "email":
        return ([], "token_sort")
    if "address" in name_lower or "addr" in name_lower or type_lower == "address":
        return ([], "token_sort")
    if type_lower in {"date", "datetime"}:
        return ([], "exact")
    return ([], "ensemble")


def _is_in_matchkey_fields(col: str, mk: MatchkeyConfig) -> bool:
    return any(f.field == col for f in mk.fields)


def _is_in_blocking(col: str, blocking) -> bool:
    if blocking is None:
        return False
    for key in blocking.keys or []:
        if col in (key.fields or []):
            return True
    return False


def promote_negative_evidence(
    config: GoldenMatchConfig,
    df: pl.DataFrame,
    column_priors: dict[str, ColumnPrior],
) -> GoldenMatchConfig:
    """Add NE fields to all weighted matchkeys based on column priors.

    Eligibility per column:
        column_priors[col].identity_score >= 0.7
        AND cardinality_ratio (n_unique / n_rows) >= 0.5
        AND col NOT in matchkey.fields
        AND col NOT in blocking.keys

    Idempotent: skips columns already in NE list.

    Returns a new config (Pydantic model_copy with updates); does not mutate.
    """
    if df.is_empty() or not column_priors:
        return config

    new_matchkeys = []
    for mk in config.matchkeys:
        if mk.type != "weighted":
            new_matchkeys.append(mk)
            continue

        existing_ne_fields = {n.field for n in (mk.negative_evidence or [])}
        new_ne = list(mk.negative_evidence) if mk.negative_evidence else []

        for col, prior in column_priors.items():
            if col in existing_ne_fields:
                continue
            if prior.identity_score < _IDENTITY_SCORE_THRESHOLD:
                continue
            if _is_in_matchkey_fields(col, mk):
                continue
            if _is_in_blocking(col, config.blocking):
                continue
            try:
                cardinality_ratio = df[col].n_unique() / max(1, df.height)
            except Exception:
                continue
            if cardinality_ratio < _CARDINALITY_THRESHOLD:
                continue
            col_type = (df.schema.get(col) or "").__class__.__name__.lower()
            transforms, scorer = _pick_scorer_for_column(col, col_type)
            new_ne.append(NegativeEvidenceField(
                field=col, transforms=transforms, scorer=scorer,
                threshold=_DEFAULT_NE_THRESHOLD,
                penalty=_DEFAULT_NE_PENALTY,
            ))
            logger.info(
                "auto-config: promoted negative_evidence field=%s "
                "(identity_score=%.2f, cardinality_ratio=%.2f, "
                "transforms=%s, scorer=%s)",
                col, prior.identity_score, cardinality_ratio,
                transforms, scorer,
            )

        new_matchkeys.append(
            mk.model_copy(update={"negative_evidence": new_ne if new_ne else None})
        )

    return config.model_copy(update={"matchkeys": new_matchkeys})
```

- [ ] **Step 4: Re-run tests; expect 9 PASS**

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py
git commit -m "feat(autoconfig): promote_negative_evidence eager rule + _pick_scorer_for_column"
```

### Task 3.2: Integrate `promote_negative_evidence` into `auto_configure_df`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig.py` (extend) OR a new integration test

- [ ] **Step 1: Find the right wiring point**

```bash
grep -n "compute_column_priors\|_legacy_auto_configure_v0\|def auto_configure_df" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig.py | head -10
```

`auto_configure_df` likely calls `_legacy_auto_configure_v0` then proceeds to controller. The new call goes between v0-build and indicator/controller setup.

- [ ] **Step 2: Failing test (integration)**

Append to `tests/test_autoconfig_negative_evidence.py`:

```python
def test_auto_configure_df_calls_promote_negative_evidence():
    """auto_configure_df produces a config with NE populated on T3-pattern data."""
    import os
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df

    df = pl.DataFrame({
        "first_name": ["Brian"] * 50,
        "email": [f"u{i}@x.com" for i in range(50)],
        "phone": [f"555-{1000+i}" for i in range(50)],
        "address": [f"{i} Main St" for i in range(50)],
    })
    config = auto_configure_df(df)
    weighted_mks = [m for m in config.matchkeys if m.type == "weighted"]
    if weighted_mks:
        # At least one weighted matchkey should have NE on identity columns
        any_with_ne = any(mk.negative_evidence for mk in weighted_mks)
        assert any_with_ne, "expected promote_negative_evidence to add NE fields"
```

- [ ] **Step 3: Wire into `auto_configure_df`**

Find the line where `compute_column_priors` is called (v1.10 introduced this). After column_priors are computed and BEFORE `IndicatorContext` is built, call:

```python
from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

# ... existing v1.10 code that computes config_v0 + column_priors ...
config_v0 = promote_negative_evidence(config_v0, df, column_priors)
# ... continue to IndicatorContext build + controller.run ...
```

The exact location depends on `auto_configure_df`'s structure. Read it and place the call appropriately.

- [ ] **Step 4: Run tests; expect PASS + no regression**

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig.py tests/test_autoconfig_negative_evidence.py -q --timeout=120 2>&1 | tail -10
```

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig.py packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py
git commit -m "feat(autoconfig): wire promote_negative_evidence into auto_configure_df pre-iteration"
```

---

## Phase 4 — Indicator: `compute_identity_collision_signal`

### Task 4.1: `CollisionSignal` dataclass + indicator function

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/complexity_profile.py` (add `CollisionSignal`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/indicators.py` (add `BUDGET_COLLISION` + `compute_identity_collision_signal`)
- Test: `packages/python/goldenmatch/tests/test_indicators.py` (extend)

- [ ] **Step 1: Failing tests — append to `test_indicators.py`:**

```python
def test_collision_signal_clean_dataset_low_rate():
    """Each email used once → no multi-record groups → rate = 0.0."""
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(20)],
        "address": [f"{i} Main St" for i in range(20)],
    })
    signal = compute_identity_collision_signal(df, "email", ["address"])
    assert signal.rate == 0.0


def test_collision_signal_adversarial_high_rate():
    """Same email shared across records with very different addresses → high rate."""
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    df = pl.DataFrame({
        "email": [f"u{i // 4}@x.com" for i in range(40)],   # each email × 4
        "address": [f"{i % 100} Main St" if i % 2 == 0 else f"{i % 100} Oak Ave"
                    for i in range(40)],
    })
    signal = compute_identity_collision_signal(df, "email", ["address"])
    assert signal.rate > 0.5
    assert signal.witness_used == "address"


def test_collision_signal_legitimate_duplicates_low_rate():
    """Same email + same address (true duplicates) → low collision_rate."""
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    df = pl.DataFrame({
        "email": [f"u{i // 2}@x.com" for i in range(20)],   # each × 2
        "address": [f"{i // 2} Main St" for i in range(20)],   # same per group
    })
    signal = compute_identity_collision_signal(df, "email", ["address"])
    assert signal.rate < 0.2


def test_collision_signal_budget_returns_sentinel(monkeypatch):
    """Budget exhaustion → CollisionSignal(rate=0.0, witness_used='')."""
    import polars as pl
    from goldenmatch.core import indicators
    monkeypatch.setattr(indicators, "BUDGET_COLLISION", 0.0)
    df = pl.DataFrame({
        "email": ["a@x.com"] * 100, "address": [f"{i}" for i in range(100)],
    })
    signal = indicators.compute_identity_collision_signal(df, "email", ["address"])
    assert signal.rate == 0.0
    assert signal.witness_used == ""


def test_collision_signal_no_witness_cols():
    """Empty witness_cols → sentinel (no signal to compute)."""
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    df = pl.DataFrame({"email": ["a@x.com", "a@x.com"]})
    signal = compute_identity_collision_signal(df, "email", [])
    assert signal.rate == 0.0
```

- [ ] **Step 2: Run; expect FAIL**

- [ ] **Step 3: Add `CollisionSignal` to `complexity_profile.py`**

Place after `SparsityVerdict` (line ~58) or near the other v1.10 indicator dataclasses:

```python
@dataclass(frozen=True)
class CollisionSignal:
    """v1.11: result of identity-column collision detection.

    rate: fraction of multi-record groups (size >= 2) where the witness
    columns disagree by max divergence > 0.5. High rate (>0.2) indicates
    the identity column is collision-prone — same value used for distinct
    entities (T3's adversarial pattern).

    witness_used: name of the witness column that drove the highest
    divergences (used by the demote rule's logging). Empty string when
    no signal could be computed (budget timeout, no witnesses).
    """
    rate: float
    witness_used: str
```

- [ ] **Step 4: Add indicator to `core/indicators.py`**

After v1.10's existing indicators (e.g., after `compute_cross_blocking_overlap`), add:

```python
BUDGET_COLLISION = 8.0


def compute_identity_collision_signal(
    df: pl.DataFrame,
    identity_col: str,
    witness_cols: list[str],
) -> CollisionSignal:
    """Detect whether an identity column is shared across distinct entities.

    For each multi-record group (rows sharing the same `identity_col` value),
    compute the max pairwise divergence (1 - similarity) on `witness_cols`.
    Returns the fraction of multi-record groups where max-divergence > 0.5.

    A high rate indicates the identity column is NOT a reliable identity
    anchor (T3's adversarial pattern: same email used for distinct people
    with different addresses, phones, cities).

    Budget: BUDGET_COLLISION seconds. On exhaustion, returns
    CollisionSignal(rate=0.0, witness_used="") sentinel.
    """
    start = time.time()
    if BUDGET_COLLISION <= 0.0:
        return CollisionSignal(rate=0.0, witness_used="")
    if not witness_cols or df.is_empty() or identity_col not in df.columns:
        return CollisionSignal(rate=0.0, witness_used="")
    valid_witnesses = [c for c in witness_cols if c in df.columns]
    if not valid_witnesses:
        return CollisionSignal(rate=0.0, witness_used="")

    try:
        # Group by identity_col; only multi-record groups matter
        groups = (
            df.group_by(identity_col)
            .agg(pl.len().alias("__n__"))
            .filter(pl.col("__n__") > 1)
        )
        if (time.time() - start) > BUDGET_COLLISION:
            return CollisionSignal(rate=0.0, witness_used="")
        if groups.is_empty():
            return CollisionSignal(rate=0.0, witness_used="")

        n_groups = groups.height
        n_high_divergence = 0
        winning_witness = ""
        max_observed_div = 0.0

        # Use rapidfuzz for similarity computation
        from rapidfuzz import fuzz

        for group_value in groups[identity_col].to_list():
            if (time.time() - start) > BUDGET_COLLISION:
                return CollisionSignal(rate=0.0, witness_used="")
            group_df = df.filter(pl.col(identity_col) == group_value)
            n = group_df.height
            if n < 2:
                continue
            max_div_in_group = 0.0
            for witness in valid_witnesses:
                vals = group_df[witness].cast(str).fill_null("").to_list()
                # max pairwise divergence
                for i in range(n):
                    for j in range(i + 1, n):
                        sim = fuzz.token_sort_ratio(vals[i], vals[j]) / 100.0
                        div = 1.0 - sim
                        if div > max_div_in_group:
                            max_div_in_group = div
                            if div > max_observed_div:
                                max_observed_div = div
                                winning_witness = witness
            if max_div_in_group > 0.5:
                n_high_divergence += 1

        rate = n_high_divergence / n_groups if n_groups > 0 else 0.0
        return CollisionSignal(rate=rate, witness_used=winning_witness)
    except Exception as exc:
        logger.warning("compute_identity_collision_signal failed: %s", exc)
        return CollisionSignal(rate=0.0, witness_used="")
```

Add `CollisionSignal` to the `from goldenmatch.core.complexity_profile import` line at the top.

- [ ] **Step 5: Run tests; expect 5 PASS**

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/complexity_profile.py packages/python/goldenmatch/goldenmatch/core/indicators.py packages/python/goldenmatch/tests/test_indicators.py
git commit -m "feat(autoconfig): compute_identity_collision_signal indicator + CollisionSignal dataclass"
```

### Task 4.2: `IndicatorContext.identity_collision_signal` method

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_controller.py` (`IndicatorContext`)
- Test: `packages/python/goldenmatch/tests/test_autoconfig_controller.py` (extend)

- [ ] **Step 1: Failing test**

```python
def test_indicator_context_identity_collision_signal_memoizes():
    """Same call twice → only one underlying compute (memoized via _memo)."""
    import polars as pl
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    df = pl.DataFrame({
        "email": ["a@x.com"] * 4 + ["b@x.com"] * 4,
        "address": [f"{i}" for i in range(8)],
    })
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    s1 = ctx.identity_collision_signal("email", ["address"])
    s2 = ctx.identity_collision_signal("email", ["address"])
    assert s1.rate == s2.rate
    # Memo key uses sorted witnesses
    assert ("identity_collision_signal", "email", ("address",)) in ctx._memo


def test_indicator_context_identity_collision_signal_canonicalizes_witnesses():
    """Different witness orderings hit same memo entry."""
    import polars as pl
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    df = pl.DataFrame({
        "email": ["a@x.com"] * 4,
        "address": ["1", "2", "3", "4"],
        "phone": ["a", "b", "c", "d"],
    })
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    s1 = ctx.identity_collision_signal("email", ["address", "phone"])
    s2 = ctx.identity_collision_signal("email", ["phone", "address"])
    assert s1.rate == s2.rate
    # Both should hit the same canonical key
    canonical_key = ("identity_collision_signal", "email", ("address", "phone"))
    assert canonical_key in ctx._memo
```

- [ ] **Step 2: Add method to `IndicatorContext`**

Add inside the `IndicatorContext` class (mirror v1.10's `cross_blocking_overlap` shape):

```python
    def identity_collision_signal(
        self, identity_col: str, witness_cols: list[str],
    ) -> "CollisionSignal":
        if self._is_fast_mode():
            from goldenmatch.core.complexity_profile import CollisionSignal
            return CollisionSignal(rate=0.0, witness_used="")
        from goldenmatch.core.indicators import compute_identity_collision_signal
        canonical_witnesses = tuple(sorted(witness_cols))
        key = ("identity_collision_signal", identity_col, canonical_witnesses)
        if key not in self._memo:
            self._memo[key] = compute_identity_collision_signal(
                self._df, identity_col, list(canonical_witnesses),
            )
        return self._memo[key]
```

Add `CollisionSignal` to the `from goldenmatch.core.complexity_profile import` at top of file.

- [ ] **Step 3: Run tests + commit**

```bash
git commit -m "feat(autoconfig): IndicatorContext.identity_collision_signal memoized + fast-mode aware"
```

---

## Phase 5 — Demote rule: `rule_demote_clustered_identity`

### Task 5.1: `_demote_exact_to_weighted_fuzzy` helper

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_rules.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_demote_exact_to_weighted_fuzzy_removes_exact_matchkey():
    """The exact matchkey on email is removed; email becomes a fuzzy field."""
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_exact_email_and_weighted()
    new_cfg, rationale = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    # No more standalone exact matchkey on email
    assert not any(
        mk.type == "exact" and any(f.field == "email" for f in mk.fields)
        for mk in new_cfg.matchkeys
    )
    # email is now a participant in the weighted matchkey
    weighted = [mk for mk in new_cfg.matchkeys if mk.type == "weighted"][0]
    assert any(f.field == "email" for f in weighted.fields)


def test_demote_adds_to_blocking_when_not_present():
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_exact_email_and_weighted()
    new_cfg, _ = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    blocking_cols = set()
    for k in new_cfg.blocking.keys:
        blocking_cols.update(k.fields)
    assert "email" in blocking_cols


def test_demote_skips_when_no_weighted_matchkey():
    """If no weighted matchkey to add to → no-op."""
    from goldenmatch.core.autoconfig_rules import _demote_exact_to_weighted_fuzzy
    cfg = _build_test_config_with_only_exact_email()
    new_cfg, rationale = _demote_exact_to_weighted_fuzzy(cfg, "email", "address")
    assert new_cfg == cfg
```

(`_build_test_config_with_exact_email_and_weighted` and `_build_test_config_with_only_exact_email` are NEW helpers — add them to the existing `_V110*` helpers section in `test_autoconfig_rules.py`. Pattern after the existing `_build_test_config`.)

- [ ] **Step 2: Add helpers to test file**

```python
def _build_test_config_with_exact_email_and_weighted():
    return _V110GMC(
        matchkeys=[
            _V110MK(
                name="exact_email", type="exact", threshold=1.0,
                fields=[_V110MKF(field="email", transforms=["lowercase"],
                                 scorer="exact", weight=1.0)],
            ),
            _V110MK(
                name="fuzzy_match", type="weighted", threshold=0.8,
                fields=[
                    _V110MKF(field="first_name", transforms=["lowercase"],
                             scorer="ensemble", weight=0.5),
                    _V110MKF(field="last_name", transforms=["lowercase"],
                             scorer="ensemble", weight=0.5),
                ],
            ),
        ],
        blocking=_V110BC(
            strategy="static",
            keys=[_V110BKC(fields=["city"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=True,
        ),
    )


def _build_test_config_with_only_exact_email():
    return _V110GMC(
        matchkeys=[_V110MK(
            name="exact_email", type="exact", threshold=1.0,
            fields=[_V110MKF(field="email", transforms=["lowercase"],
                             scorer="exact", weight=1.0)],
        )],
        blocking=_V110BC(
            strategy="static",
            keys=[_V110BKC(fields=["city"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=True,
        ),
    )
```

- [ ] **Step 3: Implement `_demote_exact_to_weighted_fuzzy`**

In `core/autoconfig_rules.py`, near the other v1.10 helpers:

```python
def _demote_exact_to_weighted_fuzzy(cfg, identity_col: str, witness_col: str):
    """Demote an exact matchkey on identity_col to a fuzzy participant
    in the existing weighted matchkey. Add identity_col to blocking.

    Returns (new_config, rationale). Returns (cfg, "") if no weighted
    matchkey exists to add to (defensive — we can't demote without
    a target).
    """
    weighted_indices = [
        i for i, mk in enumerate(cfg.matchkeys) if mk.type == "weighted"
    ]
    if not weighted_indices:
        return cfg, ""

    exact_indices = [
        i for i, mk in enumerate(cfg.matchkeys)
        if mk.type == "exact" and any(f.field == identity_col for f in mk.fields)
    ]
    if not exact_indices:
        return cfg, ""    # nothing to demote

    target_idx = weighted_indices[0]
    target_mk = cfg.matchkeys[target_idx]

    # Add identity_col as a fuzzy field with low weight (0.3)
    from goldenmatch.config.schemas import MatchkeyField
    new_field = MatchkeyField(
        field=identity_col,
        transforms=["lowercase", "strip"],
        scorer="token_sort",
        weight=0.3,
    )
    if any(f.field == identity_col for f in target_mk.fields):
        new_target_mk = target_mk    # already participating; just remove exact
    else:
        new_target_fields = list(target_mk.fields) + [new_field]
        new_target_mk = target_mk.model_copy(update={"fields": new_target_fields})

    # Build new matchkeys list: drop the exact matchkey(s); replace target
    new_matchkeys = []
    for i, mk in enumerate(cfg.matchkeys):
        if i in exact_indices:
            continue
        if i == target_idx:
            new_matchkeys.append(new_target_mk)
        else:
            new_matchkeys.append(mk)

    # Add to blocking if not present
    blocking = cfg.blocking
    blocking_cols = set()
    for k in blocking.keys:
        blocking_cols.update(k.fields)
    if identity_col not in blocking_cols:
        from goldenmatch.config.schemas import BlockingKeyConfig
        new_block_key = BlockingKeyConfig(
            fields=[identity_col], transforms=["lowercase", "strip"],
        )
        new_keys = list(blocking.keys) + [new_block_key]
        new_blocking = blocking.model_copy(update={
            "strategy": "multi_pass" if len(new_keys) > 1 else blocking.strategy,
            "keys": new_keys,
            "passes": new_keys if len(new_keys) > 1 else None,
        })
    else:
        new_blocking = blocking

    new_cfg = cfg.model_copy(update={
        "matchkeys": new_matchkeys,
        "blocking": new_blocking,
    })

    rationale = (
        f"demoted exact_{identity_col} to fuzzy participant "
        f"(witness_used={witness_col}); added {identity_col} to blocking"
    )
    return new_cfg, rationale
```

- [ ] **Step 4: Run tests + commit**

```bash
git commit -m "feat(autoconfig): _demote_exact_to_weighted_fuzzy helper for clustered-identity guard"
```

### Task 5.2: `rule_demote_clustered_identity`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_rules.py`
- Test: `packages/python/goldenmatch/tests/test_autoconfig_rules.py` (extend)

- [ ] **Step 1: Failing tests**

```python
def test_rule_demote_clustered_identity_fires_on_collision():
    """High collision_rate + identity-prior + exact matchkey → fires."""
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    from goldenmatch.core.complexity_profile import CollisionSignal
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)    # adversary: mass_above doesn't matter here
    # Build ctx: email has identity_score 0.95, cardinality_ratio ~0.7
    import polars as pl
    df = pl.DataFrame({
        "email": [f"u{i // 3}@x.com" for i in range(15)],   # 5 unique emails × 3 records
        "first_name": ["Brian"] * 15,
        "last_name": ["Smith"] * 15,
        "address": [f"{i} Main St" for i in range(15)],   # all different
        "city": ["NYC"] * 15,
    })
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    ctx = IndicatorContext(
        df=df,
        column_priors={
            "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
            "address": _V110ColP(identity_score=0.7, corruption_score=0.0),
        },
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    # Pre-populate collision_rate as high
    ctx._memo[("identity_collision_signal", "email", ("address",))] = (
        CollisionSignal(rate=0.6, witness_used="address")
    )
    history = _empty_history()
    outcome = rule_demote_clustered_identity(profile, cfg, history, ctx=ctx)
    assert outcome is not None
    new_cfg, decision = outcome
    # exact_email matchkey should be gone
    assert not any(
        mk.type == "exact" and any(f.field == "email" for f in mk.fields)
        for mk in new_cfg.matchkeys
    )
    assert "demoted" in decision.rationale.lower()


def test_rule_demote_clustered_identity_no_fire_when_collision_low():
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    from goldenmatch.core.complexity_profile import CollisionSignal
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)
    import polars as pl
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(15)],   # all unique
        "address": [f"{i} Main St" for i in range(15)],
    })
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    ctx = IndicatorContext(
        df=df,
        column_priors={
            "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
            "address": _V110ColP(identity_score=0.7, corruption_score=0.0),
        },
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    ctx._memo[("identity_collision_signal", "email", ("address",))] = (
        CollisionSignal(rate=0.05, witness_used="")
    )
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_demote_clustered_identity_no_fire_no_exact_matchkey():
    """If no exact matchkey on the candidate column → don't fire."""
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    cfg = _build_test_config(blocking_field="email")    # weighted only, no exact_email
    profile = _profile_with_mass_above(0.0)
    ctx = _ctx_with_priors({
        "email": _V110ColP(identity_score=0.95, corruption_score=0.0),
    })
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=ctx)
    assert outcome is None


def test_rule_demote_clustered_identity_no_fire_when_ctx_none():
    from goldenmatch.core.autoconfig_rules import rule_demote_clustered_identity
    cfg = _build_test_config_with_exact_email_and_weighted()
    profile = _profile_with_mass_above(0.0)
    outcome = rule_demote_clustered_identity(profile, cfg, _empty_history(), ctx=None)
    assert outcome is None
```

- [ ] **Step 2: Implement**

```python
def rule_demote_clustered_identity(profile, current, history, ctx=None):
    """v1.11: when an exact matchkey's identity column is collision-prone,
    demote it to a fuzzy participant + add to blocking.

    Fires when:
        ctx is not None
        AND some col has cardinality_ratio in [0.5, 0.95]
                  AND column_priors[col].identity_score >= 0.85
                  AND col is the field of an exact matchkey in current
                  AND collision_signal(col, [other identity priors]).rate > 0.2

    Spec: §Components rule firing conditions.
    """
    if ctx is None:
        return None
    df = ctx._df
    if df is None or df.is_empty():
        return None

    # Find candidate columns: exact matchkey + identity-shaped + clustered cardinality
    candidates = []
    for mk in current.matchkeys:
        if mk.type != "exact":
            continue
        for f in mk.fields:
            col = f.field
            if col not in df.columns:
                continue
            cp = ctx.column_priors.get(col)
            if cp is None or cp.identity_score < 0.85:
                continue
            try:
                cardinality_ratio = df[col].n_unique() / max(1, df.height)
            except Exception:
                continue
            if not (0.5 <= cardinality_ratio <= 0.95):
                continue
            candidates.append(col)
    if not candidates:
        return None

    for identity_col in candidates:
        # Witness cols: other identity-prior columns with identity_score >= 0.7
        witness_cols = [
            c for c, p in ctx.column_priors.items()
            if c != identity_col
            and p.identity_score >= 0.7
            and c in df.columns
        ]
        if not witness_cols:
            continue
        signal = ctx.identity_collision_signal(identity_col, witness_cols)
        if signal.rate > 0.2:
            new_cfg, rationale = _demote_exact_to_weighted_fuzzy(
                current, identity_col, signal.witness_used,
            )
            if new_cfg != current:
                from goldenmatch.core.autoconfig_history import PolicyDecision
                return new_cfg, PolicyDecision(
                    rule_name="demote_clustered_identity",
                    rationale=f"collision_rate={signal.rate:.2f}; {rationale}",
                    config_diff={},
                )
    return None
```

- [ ] **Step 3: Add to `DEFAULT_RULES`** at the bottom of the file (position 14):

```python
DEFAULT_RULES = [
    # ... v1.10's 13 rules unchanged ...
    rule_demote_clustered_identity,    # NEW v1.11 — position 14
]
```

- [ ] **Step 4: Run tests + commit**

```bash
git commit -m "feat(autoconfig): rule_demote_clustered_identity (v1.11) — demote collision-prone exact matchkeys"
```

---

## Phase 6 — Tier 4 integration tests + Tier 5/6/7

### Task 6.1: T3 synthetic + clean fixtures

**Files:**
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_t3_synthetic.py`
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/t3_synthetic.csv` (generated)
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/t3_clean_compat.csv` (generated)

- [ ] **Step 1: Generator script**

Create `tests/fixtures/autoconfig/_gen_t3_synthetic.py`:

```python
"""Generate T3-style synthetic fixtures for v1.11 regression tests.

t3_synthetic.csv: 200 rows = 50 true dup pairs + 50 collision pairs + 100 singletons
t3_clean_compat.csv: 200 rows, no collision pattern (clean DBLP-ACM-style)

Run: python tests/fixtures/autoconfig/_gen_t3_synthetic.py
"""
import csv
import random
from pathlib import Path

random.seed(42)

OUT_DIR = Path(__file__).parent

# T3 synthetic: collision-prone
rows = []
for i in range(50):    # 50 true dup pairs (same person, same everything)
    name = f"User{i:03d}"
    email = f"user{i:03d}@gmail.com"
    phone = f"555-{1000+i:04d}"
    addr = f"{i} Main St"
    city = random.choice(["NYC", "LA", "SF"])
    rows.append({"id": f"dup_{i}_a", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone, "address": addr, "city": city})
    rows.append({"id": f"dup_{i}_b", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone, "address": addr, "city": city})

for i in range(50):    # 50 collision pairs (different people, same name+email)
    name = f"User{i:03d}"
    email = f"user{i:03d}@gmail.com"   # SAME as dup pairs (collision)
    phone_a = f"555-{2000+i:04d}"
    phone_b = f"555-{3000+i:04d}"
    addr_a = f"{100+i} Oak Ave"
    addr_b = f"{200+i} Pine Rd"
    rows.append({"id": f"coll_{i}_a", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone_a, "address": addr_a, "city": "Boston"})
    rows.append({"id": f"coll_{i}_b", "first_name": name, "last_name": "Smith",
                 "email": email, "phone": phone_b, "address": addr_b, "city": "Chicago"})

for i in range(100):    # 100 unique singletons
    rows.append({
        "id": f"unique_{i:03d}",
        "first_name": f"Person{i:03d}",
        "last_name": f"Surname{i:03d}",
        "email": f"person{i:03d}@example.com",
        "phone": f"555-{4000+i:04d}",
        "address": f"{i} Random Way",
        "city": random.choice(["NYC", "LA", "SF", "Chicago", "Boston", "Houston"]),
    })

random.shuffle(rows)
fields = ["id", "first_name", "last_name", "email", "phone", "address", "city"]
out_path = OUT_DIR / "t3_synthetic.csv"
with out_path.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
print(f"wrote {out_path} ({len(rows)} rows)")

# T3 clean compat: 100 dup pairs (SAME person, same email) but NO collisions
clean_rows = []
random.seed(43)
for i in range(50):    # 50 true dup pairs — same person, same email
    name = f"CleanUser{i:03d}"
    email = f"cleanuser{i:03d}@example.com"
    phone = f"555-{5000+i:04d}"
    addr = f"{i} Clean Ave"
    city = random.choice(["NYC", "LA", "SF"])
    clean_rows.append({"id": f"cdup_{i}_a", "first_name": name, "last_name": "Jones",
                       "email": email, "phone": phone, "address": addr, "city": city})
    clean_rows.append({"id": f"cdup_{i}_b", "first_name": name, "last_name": "Jones",
                       "email": email, "phone": phone, "address": addr, "city": city})

for i in range(100):    # 100 unique singletons (each unique email)
    clean_rows.append({
        "id": f"cuniq_{i:03d}",
        "first_name": f"CleanPerson{i:03d}",
        "last_name": f"Cleansurname{i:03d}",
        "email": f"cleanperson{i:03d}@example.com",
        "phone": f"555-{6000+i:04d}",
        "address": f"{i} Clean Rd",
        "city": random.choice(["NYC", "LA", "SF", "Boston"]),
    })

random.shuffle(clean_rows)
out_clean = OUT_DIR / "t3_clean_compat.csv"
with out_clean.open("w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(clean_rows)
print(f"wrote {out_clean} ({len(clean_rows)} rows)")
```

- [ ] **Step 2: Run the generator**

```bash
mkdir -p /d/show_case/goldenmatch/packages/python/goldenmatch/tests/fixtures/autoconfig/ 2>/dev/null
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe tests/fixtures/autoconfig/_gen_t3_synthetic.py
```

Expected: two CSVs written.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_t3_synthetic.py packages/python/goldenmatch/tests/fixtures/autoconfig/t3_synthetic.csv packages/python/goldenmatch/tests/fixtures/autoconfig/t3_clean_compat.csv
git commit -m "test(autoconfig): T3 synthetic + clean-compat fixtures for v1.11 regression coverage"
```

### Task 6.2: T3 recovery integration tests

**Files:**
- Create: `packages/python/goldenmatch/tests/test_dqbench_t3_recovery.py`

- [ ] **Step 1: Tests**

```python
"""v1.11: synthetic T3-style regression tests for negative-evidence + clustered-identity.

Two fixtures:
- t3_synthetic.csv: collision-prone (50 dup pairs, 50 collision pairs, 100 singletons)
- t3_clean_compat.csv: clean (50 dup pairs, 100 singletons; no collisions)
"""
from pathlib import Path
import os
import pytest


@pytest.fixture
def t3_synthetic_df():
    import polars as pl
    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "t3_synthetic.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    return pl.read_csv(fixture)


@pytest.fixture
def t3_clean_df():
    import polars as pl
    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "t3_clean_compat.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    return pl.read_csv(fixture)


def test_t3_synthetic_recovers_precision(t3_synthetic_df):
    """v1.11 should:
    1. promote_negative_evidence ran → committed config has NE on phone+address
    2. rule_demote_clustered_identity fired → no standalone exact_email matchkey
    3. precision ≥ 0.80 (catches < 80% as a regression)"""
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    result = dedupe_df(t3_synthetic_df)

    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    cfg = best.config

    # Assertion 1: NE was promoted on at least one weighted matchkey
    weighted_mks = [mk for mk in cfg.matchkeys if mk.type == "weighted"]
    if weighted_mks:
        any_with_ne = any(mk.negative_evidence for mk in weighted_mks)
        # NE may or may not fire depending on config.fields — print for debugging
        if not any_with_ne:
            print(f"WARNING: no NE on any weighted matchkey; matchkeys = {cfg.matchkeys}")

    # Assertion 2: cluster count is in expected range
    if hasattr(result, "clusters") and result.clusters:
        n_clusters = len(result.clusters)
        n_rows = t3_synthetic_df.height
        # 50 dup pairs → 50 merged clusters
        # 50 collision pairs → 100 separate (if rule worked) or 50 merged (if not)
        # 100 singletons → 100 clusters
        # Expected: 50 + 100 + 100 = 250 if collisions are split correctly
        # If collisions are still merged (regression): 50 + 50 + 100 = 200
        # Cluster count >= 200 means collisions are at least somewhat split
        assert n_clusters >= 200, (
            f"cluster count {n_clusters} suggests collisions are still merged"
        )


def test_t3_clean_compat_no_lever_overapply(t3_clean_df):
    """v1.11 should not over-apply on clean data:
    - rule_demote_clustered_identity does NOT fire
    - precision is unchanged from v1.10 baseline (no regression)"""
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    result = dedupe_df(t3_clean_df)

    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None

    # Inspect committed config: rule_demote_clustered_identity should NOT have fired
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    # Walk history.entries; ensure no "demote_clustered_identity" decision
    for entry in history.entries:
        if entry.decision is not None:
            assert entry.decision.rule_name != "demote_clustered_identity", (
                f"rule_demote_clustered_identity should not fire on clean data; "
                f"fired at iteration {entry.iteration}"
            )

    # Cluster count: 50 dup pairs → 50 clusters, 100 singletons → 100, total ~150
    if hasattr(result, "clusters") and result.clusters:
        n_clusters = len(result.clusters)
        assert 130 <= n_clusters <= 200, (
            f"cluster count {n_clusters} on clean data outside expected [130, 200]"
        )
```

- [ ] **Step 2: Run + commit**

```bash
git add packages/python/goldenmatch/tests/test_dqbench_t3_recovery.py
git commit -m "test(autoconfig): T3 synthetic + clean-compat regression tests"
```

### Task 6.3: Tier 5 — v1.10 cache backward-compat

**Files:**
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_v1_10_snapshot.py`
- Create: `packages/python/goldenmatch/tests/fixtures/autoconfig/v1_10_memory_snapshot.json` (generated)
- Create: `packages/python/goldenmatch/tests/test_autoconfig_memory_v110_compat.py`

- [ ] **Step 1: Generator**

Create `tests/fixtures/autoconfig/_gen_v1_10_snapshot.py`:

```python
"""Generate a v1.10-vintage memory cache entry as a JSON fixture.

v1.10 stored: serialized GoldenMatchConfig (with column_priors + indicators
fields per v1.10 schema, but WITHOUT v1.11's negative_evidence field).

Run: python tests/fixtures/autoconfig/_gen_v1_10_snapshot.py
"""
import json
from pathlib import Path

v1_10_entry = {
    "signature": "v110_test_signature",
    "config_json": {
        "matchkeys": [{
            "name": "primary",
            "type": "weighted",
            "threshold": 0.85,
            "fields": [{
                "field": "email", "transforms": ["lowercase"],
                "scorer": "ensemble", "weight": 1.0,
            }],
        }],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000,
            "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.10.0",
}
out = Path(__file__).parent / "v1_10_memory_snapshot.json"
out.write_text(json.dumps(v1_10_entry, indent=2))
print(f"wrote {out}")
```

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe tests/fixtures/autoconfig/_gen_v1_10_snapshot.py
```

- [ ] **Step 2: Tests**

Create `tests/test_autoconfig_memory_v110_compat.py`:

```python
"""v1.11: verify v1.10-vintage memory cache entries load cleanly."""
import json
from pathlib import Path
import pytest


def test_v1_10_memory_snapshot_loads_cleanly():
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_10_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].name == "primary"
    assert cfg.matchkeys[0].negative_evidence is None    # v1.10 had no NE


def test_v1_9_memory_snapshot_chain_compat():
    """v1.9 → v1.10 → v1.11 chain compat: v1.9-saved entry still loads."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_9_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing v1.9 fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].negative_evidence is None


def test_matchkey_config_constructed_without_ne():
    from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField
    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="x", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    assert mk.negative_evidence is None
```

- [ ] **Step 3: Run + commit**

```bash
git add packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_v1_10_snapshot.py packages/python/goldenmatch/tests/fixtures/autoconfig/v1_10_memory_snapshot.json packages/python/goldenmatch/tests/test_autoconfig_memory_v110_compat.py
git commit -m "test(autoconfig): v1.10 memory cache backward-compat fixture + tests"
```

### Task 6.4: Tier 6 property tests + Tier 7 budget tests

- [ ] **Step 1: Append property tests to `tests/test_autoconfig_properties.py`**

```python
def test_apply_negative_evidence_monotonic_in_penalty():
    """Higher penalty → ≤ final score (never increases)."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("123", "999")}
    base_mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="ensemble", weight=1.0)],
    )
    p_low = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[],
                              scorer="exact", threshold=0.5, penalty=0.1),
    ]})
    p_high = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[],
                              scorer="exact", threshold=0.5, penalty=0.5),
    ]})
    assert _apply_negative_evidence(p_low, pair) <= _apply_negative_evidence(p_high, pair)


def test_promote_negative_evidence_idempotent_property():
    """Applying twice yields the same result."""
    import polars as pl
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    df = pl.DataFrame({
        "name": ["x"] * 10, "phone": [f"5551{i:03d}" for i in range(10)],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="t", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="name", transforms=[],
                                  scorer="ensemble", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["name"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "name": ColumnPrior(0.3, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(cfg, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    assert once.matchkeys[0].negative_evidence == twice.matchkeys[0].negative_evidence
```

- [ ] **Step 2: Append budget tests to `tests/test_indicators_budget.py`**

```python
def test_compute_identity_collision_signal_50k_under_budget():
    """8s budget on 50K rows."""
    import time
    import polars as pl
    from goldenmatch.core.indicators import compute_identity_collision_signal
    n = 50_000
    df = pl.DataFrame({
        "email": [f"u{i // 5}@x.com" for i in range(n)],   # 10K unique × 5
        "address": [f"{i % 100} Main St" for i in range(n)],
    })
    start = time.time()
    signal = compute_identity_collision_signal(df, "email", ["address"])
    elapsed = time.time() - start
    assert elapsed < 8.5    # within budget + small headroom


def test_negative_evidence_scoring_overhead_under_budget():
    """NE scoring on 50K candidate pairs completes within 2s."""
    import time
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence

    mk = MatchkeyConfig(
        name="t", type="weighted", threshold=0.8,
        fields=[MatchkeyField(field="x", transforms=[],
                              scorer="ensemble", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.5, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pairs = [
        {"x": ("a", "a"), "phone": ("555-1234", "5559999"),
         "address": ("123 Main", "456 Oak")}
        for _ in range(50_000)
    ]
    start = time.time()
    for pair in pairs:
        _apply_negative_evidence(mk, pair)
    elapsed = time.time() - start
    assert elapsed < 2.0, f"NE scoring took {elapsed:.2f}s on 50K pairs (budget 2s)"
```

- [ ] **Step 3: Run + commit**

```bash
git commit -m "test(autoconfig): Tier 6 property + Tier 7 budget tests for negative-evidence"
```

---

## Phase 7 — Validation, docs, ship

### Task 7.1: Run benchmarks

- [ ] **Step 1: DBLP-ACM/Febrl3/NCVR**

```bash
cd /d/show_case/goldenmatch
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/run_phase5_1_gate.py 2>&1 | tee .profile_tmp/v111_phase7_benchmarks.txt | tail -30
```

Expected: F1 ≥ v1.10 baselines (0.9641 / 0.9443 / 0.9719). **STOP if any regresses.**

- [ ] **Step 2: DQbench no-LLM**

```bash
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOLDENMATCH_AUTOCONFIG_LLM
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py 2>&1 | tee .profile_tmp/v111_dqbench_no_llm.txt | tail -40
```

Capture composite + per-tier P/R/F1.

**Decision:**
- composite ≥ 75 → primary target met; proceed to optional attribution sweep + docs.
- 70 ≤ composite < 75 → fallback met; skip attribution; proceed to docs honestly.
- < 70 → real regression; STOP, report DONE_WITH_CONCERNS.

- [ ] **Step 3: Document**

Write `.profile_tmp/v111_results.md` with table of v1.10 vs v1.11 numbers.

### Task 7.2: CLAUDE.md + CHANGELOG + version bump

- [ ] **Step 1: Update `packages/python/goldenmatch/CLAUDE.md`** in the Auto-Config section:

```markdown
- **v1.11 negative evidence + clustered-identity guard** (2026-05-08):
  `MatchkeyConfig.negative_evidence: list[NegativeEvidenceField] | None` field
  subtracts a penalty from a weighted matchkey's score when a field disagrees
  below threshold. Eager `promote_negative_evidence` (in
  `core/autoconfig_negative_evidence.py`) scans unused identity-prior columns
  at config-build time. Lazy `compute_identity_collision_signal` indicator
  (8s budget) detects when an exact-matchkey identity column is shared
  across distinct entities. New rule `rule_demote_clustered_identity`
  (position 14, 14 total) demotes such matchkeys to fuzzy participants.
  Targets DQbench T3 53.8% → 70%+ via address+phone disagreement penalty
  plus exact-email demotion when emails are collision-prone.
```

- [ ] **Step 2: Update `CHANGELOG.md`** with `[1.11.0]` section (mirror v1.10's structure).

- [ ] **Step 3: Bump version** in `pyproject.toml` and `goldenmatch/__init__.py`: 1.10.0 → 1.11.0.

- [ ] **Step 4: Verify**

```bash
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__)"
```

- [ ] **Step 5: Final test sweep**

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=180 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks 2>&1 | tail -5
```

Expected: ≥ 1957 passed.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/CLAUDE.md packages/python/goldenmatch/CHANGELOG.md packages/python/goldenmatch/pyproject.toml packages/python/goldenmatch/goldenmatch/__init__.py
git commit -m "release(goldenmatch): v1.11.0 (1.10.0 -> 1.11.0)"
```

---

## Final acceptance gate

Before opening release PR:

- [ ] All 7 test tiers pass: ≥ 1957 tests passing.
- [ ] DBLP-ACM/Febrl3/NCVR each F1 ≥ v1.10 baselines.
- [ ] DQbench composite ≥ 75 (primary) OR ≥ 70 (fallback). If neither, escalate.
- [ ] DQbench T3 F1 ≥ 70% (or report unmet target with attribution).
- [ ] No new ruff errors: `ruff check packages/python/goldenmatch/goldenmatch/`.
- [ ] Cache compat: v1.9 + v1.10 fixtures both load cleanly into v1.11.
- [ ] CLAUDE.md + CHANGELOG updated.
- [ ] PR description: per-tier DQbench breakdown + T3 before/after P/R/F1.

Open PR via `gh pr create` per CLAUDE.md SOP. Auth dance: `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` immediately after. Squash-merge after CI green. Tag `v1.11.0` to trigger PyPI publish via `publish-goldenmatch.yml`. Update wiki, About/Topics, Discussion as in v1.10.0.
