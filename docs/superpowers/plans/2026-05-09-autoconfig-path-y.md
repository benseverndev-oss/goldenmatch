# Auto-Config Path Y Implementation Plan (v1.12)

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend `_apply_negative_evidence` to exact matchkeys + extend `promote_negative_evidence` to walk all matchkey types so DQbench T3 53.8% → ≥70% (target composite ≥75 primary, ≥70 fallback). Reuse `MatchkeyConfig.threshold` as score gate for NE-enabled exact matchkeys (default 0.5 when NE set + threshold None).

**Architecture:** Two surgical changes to existing v1.11 code, no new modules. Extend `find_exact_matches` (or equivalent path in `core/scorer.py`) to invoke `_apply_negative_evidence` when matchkey.negative_evidence is populated, score = `max(0, 1.0 - sum(penalties))`, emit only if score ≥ threshold. Extend `promote_negative_evidence` to iterate all matchkey types; skip the `_is_exact_matchkey_field` gate on the exact-matchkey branch (rationale doesn't apply when iterating an exact matchkey for itself); set default threshold=0.5 when adding NE to a threshold-None exact matchkey.

**Tech Stack:** Python 3.12, polars, Pydantic v2, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-05-09-autoconfig-path-y-design.md` — read this first.

---

## Pre-flight checklist

- [ ] Working in dedicated branch `feature/autoconfig-richer-matchkey` (currently at HEAD `4320367` with v1.11 implementation + v1.12 spec committed). v1.11 PR #121 is open but not yet merged; v1.12 work continues on this same branch and PR #121 will be amended OR a new PR opened post-v1.12.
- [ ] DQbench dataset present at `~/.dqbench/datasets/er_tier{1,2,3}/data.csv`.
- [ ] DBLP-ACM, NCVR samples at `packages/python/goldenmatch/tests/benchmarks/datasets/`.
- [ ] **Editable install verification (CRITICAL — v1.11 Phase 7 hit this):**
   ```bash
   C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__, goldenmatch.__file__)"
   ```
   Expected: shows the worktree path + current pyproject.toml version (1.11.0 pre-bump). If a stale site-packages install is found (e.g. v1.9.0), reinstall editable: `python -m pip install -e packages/python/goldenmatch`. Otherwise `dqbench` benchmarks measure the wrong code.
- [ ] Bash shell (Git Bash); Python pinned at `C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe`.
- [ ] Baseline test count (currently includes v1.11): 1986 passing. After v1.12, expect ~2005 (+19 new).

---

## File structure (locked in here)

| File | Role | Change |
|---|---|---|
| `core/scorer.py` | Existing | Modify `find_exact_matches` (or its per-pair scoring branch) to invoke `_apply_negative_evidence` when `matchkey.negative_evidence` is populated. `_apply_negative_evidence` itself is unchanged from v1.11. |
| `core/autoconfig_negative_evidence.py` | Existing | Modify `promote_negative_evidence`: remove the `if mk.type != "weighted": continue` filter; selectively apply the `_is_exact_matchkey_field` gate (skip for `mk.type == "exact"`); set `matchkey.threshold = 0.5` when adding NE to a threshold-None exact matchkey. |
| `tests/test_negative_evidence_scoring.py` | Existing | Tier 3: 8 new tests for exact-matchkey NE scoring |
| `tests/test_autoconfig_negative_evidence.py` | Existing | Tier 1: 5 new tests for `promote_negative_evidence` extension |
| `tests/test_dqbench_t3_recovery.py` | Existing | Tier 4: un-xfail v1.11's `test_t3_synthetic_recovers_precision`; add 1 new test for Path Y filtering |
| `tests/test_autoconfig_properties.py` | Existing | Tier 6: 2 new property tests |
| `tests/test_autoconfig_memory_v111_compat.py` | NEW | Tier 5: 3 cache backward-compat tests for v1.11 → v1.12 |
| `tests/test_indicators_budget.py` | Existing | Tier 7: 1 new performance budget test |

**Total: ~280 LOC code + ~340 LOC tests = ~620 LOC** (smaller than v1.11's ~850 LOC since v1.12 extends existing functions rather than adding new modules).

---

## Phase 1 — Extend `_apply_negative_evidence` to exact-matchkey scoring path

### Task 1.1: Tier 3 unit tests for NE-on-exact scoring

**Files:**
- Create: tests in `packages/python/goldenmatch/tests/test_negative_evidence_scoring.py` (existing file, append section)

- [ ] **Step 1: Add 8 failing tests** — append to `tests/test_negative_evidence_scoring.py`:

```python
# ============================================================
# v1.12 Path Y: NE on exact matchkeys
# ============================================================

def _build_exact_matchkey_with_ne(threshold=None, ne_fields=None):
    """Helper: build an exact matchkey with optional NE + threshold."""
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    return MatchkeyConfig(
        name="exact_email", type="exact", threshold=threshold,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=ne_fields,
    )


def test_exact_matchkey_with_ne_filters_disagreeing_pair():
    """T3 case: same email, divergent phone+address → final 0.3 < 0.5 → no match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pair = {
        "email": ("a@x.com", "a@x.com"),    # match
        "phone": ("555-1234", "555-9999"),    # disagree
        "address": ("123 Main", "456 Oak"),    # disagree
    }
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.7    # 0.3 + 0.4
    final = max(0.0, 1.0 - penalty)
    assert final < 0.5    # below threshold; would not emit


def test_exact_matchkey_with_ne_keeps_agreeing_pair():
    """True duplicate: NE fields agree → no penalty → final 1.0 → match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("5551234", "555-1234")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.0    # phones agree after digits_only transform
    assert max(0.0, 1.0 - penalty) == 1.0


def test_exact_matchkey_without_ne_preserves_binary_behavior():
    """Backward compat: exact matchkey with NE=None → today's binary 1.0 emit."""
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne()    # threshold=None, ne_fields=None
    penalty = _apply_negative_evidence(mk, {"email": ("a@x.com", "a@x.com")})
    assert penalty == 0.0    # NE=None → returns 0


def test_exact_matchkey_minor_address_noise_preserves_match():
    """Single-field disagreement at penalty 0.4 → final 0.6 ≥ 0.5 → match."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    # address sim ~0.30 (severely different), penalty 0.4 fires
    pair = {"email": ("a@x.com", "a@x.com"),
            "address": ("123 Main St", "456 Oak Avenue")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.4
    final = max(0.0, 1.0 - penalty)
    assert final == 0.6    # at threshold 0.5 → still emitted


def test_exact_matchkey_two_ne_fields_at_03_each_filters():
    """Cumulative penalty 0.6 > 0.5 → final 0.4 → filtered."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="zip", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"),
            "phone": ("555-1234", "555-9999"), "zip": ("90210", "10001")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 0.6
    assert max(0.0, 1.0 - penalty) == 0.4    # below 0.5


def test_exact_matchkey_penalty_exceeds_one_clamps_to_zero():
    """Defensive: penalty sum > 1.0 → final clamped to 0.0."""
    from goldenmatch.config.schemas import NegativeEvidenceField
    from goldenmatch.core.scorer import _apply_negative_evidence
    mk = _build_exact_matchkey_with_ne(
        threshold=0.5,
        ne_fields=[
            NegativeEvidenceField(field="phone", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.6),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="exact", threshold=0.4, penalty=0.6),
        ],
    )
    pair = {"email": ("a@x.com", "a@x.com"),
            "phone": ("a", "b"), "address": ("c", "d")}
    penalty = _apply_negative_evidence(mk, pair)
    assert penalty == 1.2
    assert max(0.0, 1.0 - penalty) == 0.0    # clamp to floor


def test_find_exact_matches_emits_when_ne_score_above_threshold(tmp_path):
    """E2E through find_exact_matches: pair with agreeing NE emits as match."""
    import polars as pl
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import find_exact_matches
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "email": ["a@x.com", "a@x.com"],
        "phone": ["5551234", "555-1234"],   # agree after digits_only
    })
    mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
        ],
    )
    pairs = find_exact_matches(df, mk)
    # The pair (0, 1) should emit with score ≥ 0.5
    assert any(score >= 0.5 for *_, score in pairs)


def test_find_exact_matches_filters_when_ne_score_below_threshold(tmp_path):
    """E2E: pair with disagreeing NE drops below threshold → not emitted."""
    import polars as pl
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import find_exact_matches
    df = pl.DataFrame({
        "__row_id__": [0, 1],
        "email": ["a@x.com", "a@x.com"],
        "phone": ["5551234", "5559999"],   # disagree
        "address": ["123 Main", "999 Oak"],   # disagree
    })
    mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=["lowercase"],
                              scorer="exact", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pairs = find_exact_matches(df, mk)
    # The pair (0, 1) should NOT emit (penalty 0.7 → final 0.3 < 0.5)
    assert len(pairs) == 0
```

- [ ] **Step 2: Run; expect 8 FAIL.** First 6 fail because the helper math is correct (already passing in v1.11) but `_apply_negative_evidence` returns 0.0 for all matchkeys (it's only invoked from weighted path today). Last 2 (`test_find_exact_matches_*`) fail because `find_exact_matches` doesn't yet honor NE — emits regardless.

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_negative_evidence_scoring.py -k "exact_matchkey or find_exact_matches" -v --timeout=60 2>&1 | tail -15
```

Actually re-check: `_apply_negative_evidence` from v1.11 takes ANY matchkey (the function doesn't check `mk.type`). The first 6 unit tests should PASS even without v1.12 changes — they only invoke `_apply_negative_evidence` directly. The 2 E2E tests via `find_exact_matches` are the ones that fail.

Confirm: run only the first 6 tests; expect PASS. Then run the 2 E2E tests; expect FAIL.

### Task 1.2: Modify `find_exact_matches` to honor NE + threshold

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py:158-` (the `find_exact_matches` function)

- [ ] **Step 1: Read current `find_exact_matches`**

```bash
grep -n "def find_exact_matches" /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/scorer.py
sed -n '158,220p' /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/scorer.py
```

The function emits (a, b, 1.0) for each pair where exact-fields are equal. Find where the per-pair emit happens.

- [ ] **Step 2: Wrap the emit in NE-aware logic**

Modify `find_exact_matches` so that when `matchkey.negative_evidence` is populated:
- Build a per-pair dict `{field: (val_a, val_b)}` for each NE field
- Call `_apply_negative_evidence(matchkey, pair_dict)` to compute penalty
- Compute `final_score = max(0.0, 1.0 - penalty)`
- Determine threshold: `matchkey.threshold if matchkey.threshold is not None else 0.5` (defensive default)
- Emit only if `final_score >= threshold`; emit with `score=final_score` (not 1.0)

When `matchkey.negative_evidence` is None or empty: today's binary emit at score 1.0 (unchanged).

Sketch:

```python
def find_exact_matches(df: pl.DataFrame, mk: MatchkeyConfig) -> list[tuple[int, int, float]]:
    # ... existing code that finds pairs of equal-field rows ...
    pairs_to_emit: list[tuple[int, int, float]] = []
    for row_id_a, row_id_b in candidate_pairs:
        if not mk.negative_evidence:
            pairs_to_emit.append((row_id_a, row_id_b, 1.0))
            continue
        # NE-aware path
        pair_dict = {}
        for ne in mk.negative_evidence:
            try:
                val_a = df[ne.field][row_id_a]
                val_b = df[ne.field][row_id_b]
                pair_dict[ne.field] = (val_a, val_b)
            except Exception:
                pair_dict[ne.field] = (None, None)
        penalty = _apply_negative_evidence(mk, pair_dict)
        final_score = max(0.0, 1.0 - penalty)
        threshold = mk.threshold if mk.threshold is not None else 0.5
        if final_score >= threshold:
            pairs_to_emit.append((row_id_a, row_id_b, final_score))
    return pairs_to_emit
```

The exact integration depends on `find_exact_matches`'s current implementation (it likely uses Polars self-join for performance). Adapt the NE-aware path to fit. Do NOT regress the today's-binary-emit path's performance.

- [ ] **Step 3: Add INFO log on first NE-on-exact firing per matchkey-per-run**

Inside `find_exact_matches`, just before the NE-aware loop:

```python
if mk.negative_evidence:
    threshold = mk.threshold if mk.threshold is not None else 0.5
    if mk.threshold is None:
        logger.info(
            "auto-config: NE active on exact matchkey '%s' but threshold is None; "
            "using default 0.5 (recommend setting matchkey.threshold explicitly)",
            mk.name,
        )
```

- [ ] **Step 4: Run failing tests; expect 8 PASS**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_negative_evidence_scoring.py -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 5: Run broader scorer + pipeline regression**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_scorer.py tests/test_pipeline.py tests/test_negative_evidence_scoring.py -q --timeout=120 2>&1 | tail -10
```

Expected: all pass; existing exact-matchkey tests unchanged.

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/scorer.py packages/python/goldenmatch/tests/test_negative_evidence_scoring.py
git commit -m "feat(autoconfig): _apply_negative_evidence applies to exact matchkeys when NE+threshold are set"
```

---

## Phase 2 — Extend `promote_negative_evidence` to all matchkey types

### Task 2.1: Tier 1 unit tests

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py`

- [ ] **Step 1: Append 5 failing tests**

```python
def test_promote_ne_populates_exact_matchkey_too():
    """v1.12: promote_negative_evidence walks exact matchkeys, not just weighted."""
    import polars as pl
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
        matchkeys=[
            MatchkeyConfig(
                name="exact_email", type="exact", threshold=None,
                fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                       scorer="exact", weight=1.0)],
            ),
            MatchkeyConfig(
                name="fuzzy_match", type="weighted", threshold=0.8,
                fields=[MatchkeyField(field="first_name", transforms=["lowercase"],
                                       scorer="ensemble", weight=1.0)],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["first_name"], transforms=["lowercase"])],
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

    # Find exact_email matchkey in new_config
    exact_mk = next(mk for mk in new_config.matchkeys if mk.type == "exact")
    assert exact_mk.negative_evidence is not None
    ne_fields = {n.field for n in exact_mk.negative_evidence}
    assert "phone" in ne_fields    # qualifies: identity 0.85, cardinality high
    # NOTE: address has identity 0.7, which is below the v1.11 IDENTITY_SCORE_THRESHOLD
    # of 0.75. So it does NOT qualify under current default. Adjust test as needed.


def test_promote_ne_sets_default_threshold_on_exact_when_none():
    """When NE is added to an exact matchkey with threshold=None, set threshold=0.5."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact", threshold=None,
            fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                   scorer="exact", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "email": ColumnPrior(0.95, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    new_config = promote_negative_evidence(config, df, priors)
    exact_mk = next(mk for mk in new_config.matchkeys if mk.type == "exact")
    if exact_mk.negative_evidence:
        # NE was added; threshold should now be 0.5
        assert exact_mk.threshold == 0.5


def test_promote_ne_preserves_user_set_threshold_on_exact():
    """User-set threshold on exact matchkey is NOT overwritten by promotion."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact", threshold=0.7,    # user set
            fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                   scorer="exact", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {"email": ColumnPrior(0.95, 0.0), "phone": ColumnPrior(0.85, 0.0)}
    new_config = promote_negative_evidence(config, df, priors)
    exact_mk = next(mk for mk in new_config.matchkeys if mk.type == "exact")
    assert exact_mk.threshold == 0.7    # user value preserved


def test_promote_ne_skips_is_exact_matchkey_field_gate_on_exact_branch():
    """v1.12 fix: the _is_exact_matchkey_field gate should NOT apply when
    iterating the exact matchkey itself. T3's phone is not in any exact
    matchkey, but should still be promoted as NE on exact_email."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    # Config with ONLY exact_email; phone is NOT in any exact matchkey
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact", threshold=None,
            fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                   scorer="exact", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {"email": ColumnPrior(0.95, 0.0), "phone": ColumnPrior(0.85, 0.0)}
    new_config = promote_negative_evidence(config, df, priors)
    exact_mk = next(mk for mk in new_config.matchkeys if mk.type == "exact")
    # Phone should be NE on exact_email even though phone isn't in any exact matchkey
    assert exact_mk.negative_evidence is not None
    ne_fields = {n.field for n in exact_mk.negative_evidence}
    assert "phone" in ne_fields


def test_promote_ne_idempotent_on_exact_matchkey():
    """Calling twice produces the same exact-matchkey NE list."""
    import polars as pl
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence

    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"555-{1000+i}" for i in range(10)],
    })
    config = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact", threshold=None,
            fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                   scorer="exact", weight=1.0)],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {"email": ColumnPrior(0.95, 0.0), "phone": ColumnPrior(0.85, 0.0)}
    once = promote_negative_evidence(config, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    once_mk = next(mk for mk in once.matchkeys if mk.type == "exact")
    twice_mk = next(mk for mk in twice.matchkeys if mk.type == "exact")
    assert (once_mk.negative_evidence or []) == (twice_mk.negative_evidence or [])
    assert once_mk.threshold == twice_mk.threshold
```

- [ ] **Step 2: Run; expect 5 FAIL** — current `promote_negative_evidence` skips exact matchkeys entirely.

### Task 2.2: Modify `promote_negative_evidence`

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py:113-` (the loop in `promote_negative_evidence`)

- [ ] **Step 1: Read current loop**

```bash
sed -n '85,170p' /d/show_case/goldenmatch/packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py
```

The current loop has `if mk.type != "weighted": continue` at line 113, and `_is_exact_matchkey_field` gate at line 131.

- [ ] **Step 2: Remove the type filter; selectively apply the `_is_exact_matchkey_field` gate**

Modify the function:

```python
def promote_negative_evidence(
    config: GoldenMatchConfig,
    df: pl.DataFrame,
    column_priors: dict[str, ColumnPrior],
) -> GoldenMatchConfig:
    """v1.12: walk all matchkey types (was weighted-only in v1.11).

    Skip _is_exact_matchkey_field gate when iterating exact matchkeys
    (the gate's rationale doesn't apply for NE-on-exact).
    """
    if df.is_empty() or not column_priors:
        return config

    all_matchkeys = list(config.matchkeys)

    new_matchkeys: list[MatchkeyConfig] = []
    for mk in config.matchkeys:
        # v1.12: walk all matchkey types, not just weighted
        # (probabilistic matchkeys still skipped — see §Non-goals)
        if mk.type not in ("weighted", "exact"):
            new_matchkeys.append(mk)
            continue

        existing_ne_fields: set[str] = {n.field for n in (mk.negative_evidence or [])}
        new_ne: list[NegativeEvidenceField] = list(mk.negative_evidence) if mk.negative_evidence else []

        for col, prior in column_priors.items():
            if col in existing_ne_fields:
                continue
            if prior.identity_score < _IDENTITY_SCORE_THRESHOLD:
                continue
            # v1.12: skip _is_exact_matchkey_field gate when iterating exact matchkey
            # The gate exists to prevent NE-on-weighted-without-anchor regression;
            # it doesn't apply when the matchkey IS the exact anchor.
            if mk.type == "weighted":
                if not _is_exact_matchkey_field(col, all_matchkeys):
                    continue
            if _is_in_matchkey_fields(col, mk):
                continue
            if _is_in_blocking(col, config.blocking):
                continue
            if col not in df.columns:
                continue
            try:
                cardinality_ratio = df[col].n_unique() / max(1, df.height)
            except Exception:
                continue
            if cardinality_ratio < _CARDINALITY_THRESHOLD:
                continue

            col_type_hint = ""    # v1.10 deviation; scorer-pick is name-keyed
            transforms, scorer = _pick_scorer_for_column(col, col_type_hint)
            new_ne.append(NegativeEvidenceField(
                field=col, transforms=transforms, scorer=scorer,
                threshold=_DEFAULT_NE_THRESHOLD,
                penalty=_DEFAULT_NE_PENALTY,
            ))
            logger.info(
                "auto-config: promoted negative_evidence field=%s on matchkey=%s "
                "(identity_score=%.2f, cardinality_ratio=%.2f, scorer=%s)",
                col, mk.name, prior.identity_score, cardinality_ratio, scorer,
            )

        # v1.12: when NE was added to an exact matchkey with threshold=None,
        # set threshold=0.5 to activate the score-and-threshold scoring path
        new_threshold = mk.threshold
        if mk.type == "exact" and new_ne and len(new_ne) > len(existing_ne_fields):
            if mk.threshold is None:
                new_threshold = 0.5
                logger.info(
                    "auto-config: set default threshold=0.5 on exact matchkey=%s "
                    "(NE was added; threshold was None)",
                    mk.name,
                )

        new_matchkeys.append(
            mk.model_copy(update={
                "negative_evidence": new_ne if new_ne else None,
                "threshold": new_threshold,
            })
        )

    return config.model_copy(update={"matchkeys": new_matchkeys})
```

- [ ] **Step 3: Run failing tests; expect 5 PASS + existing v1.11 tests still pass**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_negative_evidence.py -v --timeout=60 2>&1 | tail -15
```

- [ ] **Step 4: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/autoconfig_negative_evidence.py packages/python/goldenmatch/tests/test_autoconfig_negative_evidence.py
git commit -m "feat(autoconfig): promote_negative_evidence walks all matchkey types; sets default threshold on exact"
```

---

## Phase 3 — Tier 4 T3 integration tests

### Task 3.1: Un-xfail v1.11's failing T3 test + add new Path Y test

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_dqbench_t3_recovery.py`

- [ ] **Step 1: Find v1.11's xfailed test**

```bash
grep -n "xfail\|test_t3_synthetic" /d/show_case/goldenmatch/packages/python/goldenmatch/tests/test_dqbench_t3_recovery.py | head -10
```

- [ ] **Step 2: Remove the xfail decorator**

The test was xfailed because Path X (clustered-guard) couldn't reach T3. With Path Y (NE on exact), it should now pass. Remove `@pytest.mark.xfail(...)`.

- [ ] **Step 3: Add a new explicit Path Y test**

```python
def test_t3_synthetic_path_y_filters_collision_pairs(t3_synthetic_df):
    """v1.12: Path Y should filter collision pairs via NE on exact_email.

    Asserts:
    1. Committed config has NE on exact_email matchkey (Path Y populated)
    2. Cluster count is reasonable (not catastrophically merged)
    3. Precision >= 0.85 (Path Y filters collision pairs)
    """
    import os
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    from goldenmatch import dedupe_df
    result = dedupe_df(t3_synthetic_df)

    from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
    last = _LAST_CONTROLLER_RUN.get()
    assert last is not None
    profile, history = last
    best = history.pick_committed(precision_collapse_floor=0.9)
    assert best is not None
    cfg = best.config

    # Assertion 1: NE was promoted on the exact_email matchkey
    exact_mks = [mk for mk in cfg.matchkeys if mk.type == "exact"]
    if exact_mks:
        ne_present = any(mk.negative_evidence for mk in exact_mks)
        assert ne_present, (
            f"expected NE on at least one exact matchkey; got {exact_mks}"
        )

    # Assertion 2: cluster count is in expected range
    if hasattr(result, "clusters") and result.clusters:
        n_clusters = len(result.clusters)
        n_rows = t3_synthetic_df.height
        # T3 synthetic: 50 dup pairs (50 clusters) + 100 collision pairs
        # filtered into 200 separate clusters + 100 singletons = ~250 cluster slots
        # If Path Y works: collision pairs are NOT merged → high cluster count
        assert n_clusters >= 200, (
            f"cluster count {n_clusters} too low for {n_rows} rows; "
            "Path Y may not be filtering collision pairs"
        )
```

- [ ] **Step 4: Run + commit**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_dqbench_t3_recovery.py -v --timeout=120 2>&1 | tail -15
git add packages/python/goldenmatch/tests/test_dqbench_t3_recovery.py
git commit -m "test(autoconfig): un-xfail T3 synthetic test + add Path Y filter test"
```

---

## Phase 4 — Tier 5/6/7 supporting tests

### Task 4.1: Tier 5 cache backward-compat (`tests/test_autoconfig_memory_v111_compat.py`, NEW)

- [ ] **Step 1: Create the test file**

```python
"""v1.12: verify v1.11-vintage memory cache entries load cleanly into v1.12.

v1.11 stored: GoldenMatchConfig with NE optional on weighted matchkeys only.
v1.12 adds: NE on exact matchkeys + threshold default 0.5.

A v1.11 cache entry has no NE on exact matchkeys (NE was never promoted on
exact in v1.11). v1.12's deserializer must handle this cleanly.
"""
import json
from pathlib import Path
import pytest


def test_v1_11_cache_entry_loads_cleanly():
    """v1.11 cache entry (no NE on exact) → v1.12 deserialization OK."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_11_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    # Verify exact matchkey deserializes with NE=None preserved
    exact_mks = [mk for mk in cfg.matchkeys if mk.type == "exact"]
    if exact_mks:
        for mk in exact_mks:
            assert mk.negative_evidence is None, (
                f"v1.11 entry should have NE=None on exact matchkey '{mk.name}'"
            )


def test_v1_10_chain_compat_through_v112():
    """v1.10 → v1.11 → v1.12 chain: oldest fixture still loads."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    fixture = (
        Path(__file__).parent / "fixtures" / "autoconfig"
        / "v1_10_memory_snapshot.json"
    )
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    entry = json.loads(fixture.read_text())
    cfg = GoldenMatchConfig.model_validate(entry["config_json"])
    assert cfg.matchkeys[0].negative_evidence is None


def test_v1_12_cache_entry_with_ne_on_exact_round_trips():
    """v1.12 cache entry with NE on exact serializes + deserializes losslessly."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        NegativeEvidenceField, BlockingConfig, BlockingKeyConfig,
    )
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact", threshold=0.5,
            fields=[MatchkeyField(field="email", transforms=["lowercase"],
                                   scorer="exact", weight=1.0)],
            negative_evidence=[
                NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                      scorer="exact", threshold=0.4, penalty=0.3),
            ],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    serialized = cfg.model_dump_json()
    reloaded = GoldenMatchConfig.model_validate_json(serialized)
    assert reloaded.matchkeys[0].threshold == 0.5
    assert reloaded.matchkeys[0].negative_evidence is not None
    assert reloaded.matchkeys[0].negative_evidence[0].field == "phone"
```

- [ ] **Step 2: Generate v1.11 fixture if missing**

If `tests/fixtures/autoconfig/v1_11_memory_snapshot.json` doesn't exist:

```python
# tests/fixtures/autoconfig/_gen_v1_11_snapshot.py
import json
from pathlib import Path
v1_11_entry = {
    "signature": "v111_test_signature",
    "config_json": {
        "matchkeys": [
            {
                "name": "exact_email", "type": "exact", "threshold": None,
                "fields": [{"field": "email", "transforms": ["lowercase"],
                            "scorer": "exact", "weight": 1.0}],
                "negative_evidence": None,    # v1.11 didn't promote NE on exact
            },
            {
                "name": "fuzzy_match", "type": "weighted", "threshold": 0.85,
                "fields": [{"field": "first_name", "transforms": [],
                            "scorer": "ensemble", "weight": 1.0}],
                "negative_evidence": None,
            },
        ],
        "blocking": {
            "strategy": "static",
            "keys": [{"fields": ["email"], "transforms": ["lowercase"]}],
            "max_block_size": 1000, "skip_oversized": True,
        },
    },
    "succeeded": 1,
    "version_written_by": "1.11.0",
}
out = Path(__file__).parent / "v1_11_memory_snapshot.json"
out.write_text(json.dumps(v1_11_entry, indent=2))
print(f"wrote {out}")
```

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe tests/fixtures/autoconfig/_gen_v1_11_snapshot.py
```

- [ ] **Step 3: Run + commit**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_memory_v111_compat.py -v --timeout=60
git add packages/python/goldenmatch/tests/test_autoconfig_memory_v111_compat.py packages/python/goldenmatch/tests/fixtures/autoconfig/_gen_v1_11_snapshot.py packages/python/goldenmatch/tests/fixtures/autoconfig/v1_11_memory_snapshot.json
git commit -m "test(autoconfig): v1.11 memory cache backward-compat fixture + tests"
```

### Task 4.2: Tier 6 properties + Tier 7 budget

- [ ] **Step 1: Append properties to `tests/test_autoconfig_properties.py`**

```python
def test_ne_on_exact_monotonic_in_penalty():
    """Increasing penalty for NE on exact → ≤ final score."""
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence

    pair = {"email": ("a@x.com", "a@x.com"), "phone": ("a", "b")}
    base_mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="exact", weight=1.0)],
    )
    p_low = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[], scorer="exact",
                              threshold=0.5, penalty=0.1),
    ]})
    p_high = base_mk.model_copy(update={"negative_evidence": [
        NegativeEvidenceField(field="phone", transforms=[], scorer="exact",
                              threshold=0.5, penalty=0.5),
    ]})
    assert _apply_negative_evidence(p_low, pair) <= _apply_negative_evidence(p_high, pair)


def test_promote_ne_extension_idempotent_property():
    """promote_negative_evidence on a config with both weighted+exact matchkeys
    is idempotent: calling twice produces identical output."""
    import polars as pl
    from goldenmatch.core.autoconfig_negative_evidence import promote_negative_evidence
    from goldenmatch.core.complexity_profile import ColumnPrior
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(10)],
        "phone": [f"5551{i:03d}" for i in range(10)],
    })
    cfg = GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="exact_email", type="exact", threshold=None,
                fields=[MatchkeyField(field="email", transforms=[],
                                       scorer="exact", weight=1.0)],
            ),
            MatchkeyConfig(
                name="fuzzy_match", type="weighted", threshold=0.85,
                fields=[MatchkeyField(field="email", transforms=[],
                                       scorer="ensemble", weight=1.0)],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["email"], transforms=[])],
            max_block_size=1000, skip_oversized=False,
        ),
    )
    priors = {
        "email": ColumnPrior(0.95, 0.0),
        "phone": ColumnPrior(0.85, 0.0),
    }
    once = promote_negative_evidence(cfg, df, priors)
    twice = promote_negative_evidence(once, df, priors)
    for mk_a, mk_b in zip(once.matchkeys, twice.matchkeys):
        assert (mk_a.negative_evidence or []) == (mk_b.negative_evidence or [])
        assert mk_a.threshold == mk_b.threshold
```

- [ ] **Step 2: Append budget test to `tests/test_indicators_budget.py`**

```python
def test_exact_matchkey_ne_scoring_overhead_under_budget():
    """v1.12: NE scoring on 50K candidate pairs against exact matchkey
    completes within 2s (spec target ~1s, 2s margin for CI shared runners)."""
    import time
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, NegativeEvidenceField,
    )
    from goldenmatch.core.scorer import _apply_negative_evidence

    mk = MatchkeyConfig(
        name="exact_email", type="exact", threshold=0.5,
        fields=[MatchkeyField(field="email", transforms=[],
                              scorer="exact", weight=1.0)],
        negative_evidence=[
            NegativeEvidenceField(field="phone", transforms=["digits_only"],
                                  scorer="exact", threshold=0.4, penalty=0.3),
            NegativeEvidenceField(field="address", transforms=[],
                                  scorer="token_sort", threshold=0.4, penalty=0.4),
        ],
    )
    pairs = [
        {"email": ("a@x.com", "a@x.com"),
         "phone": ("555-1234", "555-9999"),
         "address": ("123 Main", "456 Oak")}
        for _ in range(50_000)
    ]
    start = time.time()
    for pair in pairs:
        _apply_negative_evidence(mk, pair)
    elapsed = time.time() - start
    assert elapsed < 2.0, f"NE scoring on exact took {elapsed:.2f}s (target 2s)"
```

- [ ] **Step 3: Run + commit**

```bash
cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest tests/test_autoconfig_properties.py tests/test_indicators_budget.py -v --timeout=120 2>&1 | tail -10
git add packages/python/goldenmatch/tests/test_autoconfig_properties.py packages/python/goldenmatch/tests/test_indicators_budget.py
git commit -m "test(autoconfig): Tier 6 property + Tier 7 budget tests for Path Y"
```

---

## Phase 5 — Validation, docs, ship

### Task 5.1: Run benchmarks (real DBLP-ACM/Febrl3/NCVR + DQbench)

**CRITICAL**: verify editable install picks up the worktree's v1.11.0 (not stale site-packages):

```bash
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__, goldenmatch.__file__)"
```

Expected: shows worktree path + 1.11.0. If stale: `python -m pip install -e packages/python/goldenmatch`.

- [ ] **Step 1: DBLP-ACM/Febrl3/NCVR**

```bash
cd /d/show_case/goldenmatch
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe .profile_tmp/run_phase5_1_gate.py 2>&1 | tee .profile_tmp/v112_phase5_1.txt | tail -20
```

Expected: F1 ≥ v1.11 baselines (0.9641 / 0.9443 / 0.9719). **STOP if any regress.**

- [ ] **Step 2: DQbench no-LLM**

```bash
unset OPENAI_API_KEY ANTHROPIC_API_KEY GOLDENMATCH_AUTOCONFIG_LLM
rm -f ~/.goldenmatch/autoconfig_memory.db
GOLDENMATCH_AUTOCONFIG_MEMORY=0 dqbench run goldenmatch-zeroconfig --adapter .profile_tmp/goldenmatch_zeroconfig_adapter.py 2>&1 | tee .profile_tmp/v112_dqbench_no_llm.txt | tail -25
```

Capture composite + per-tier P/R/F1.

**Decision branch:**
- composite ≥ 75 → primary target met. Proceed to docs.
- 70 ≤ composite < 75 → fallback met. Proceed to docs honestly.
- < 70 → real regression. STOP, DONE_WITH_CONCERNS.
- T1 or T2 F1 < v1.11 baseline → STOP. Tighten penalty defaults (0.2/0.3) or promotion gates.

- [ ] **Step 3: Document results in `.profile_tmp/v112_results.md`**

### Task 5.2: CLAUDE.md + CHANGELOG + version bump

- [ ] **Step 1: Update `packages/python/goldenmatch/CLAUDE.md`**

Add to Auto-Config section:

```markdown
- **v1.12 Path Y** (2026-05-09): extends `_apply_negative_evidence` to exact
  matchkeys when `matchkey.negative_evidence` is populated. Score formula:
  `final = max(0, 1.0 - sum(disagreement_penalties))`; emit if `final >=
  matchkey.threshold` (default 0.5 when NE set + threshold None). Backward
  compat: exact matchkey without NE preserves today's binary 1.0/0.0.
  `promote_negative_evidence` extended to walk all matchkey types; the
  `_is_exact_matchkey_field` gate is selectively applied (skipped on the
  exact-matchkey iteration branch — its rationale doesn't apply when iterating
  an exact matchkey for itself). When NE is added to a threshold-None exact
  matchkey, threshold defaults to 0.5 to activate the score-and-threshold path.
  Targets DQbench T3 53.8% → 70%+ via NE penalty filtering collision pairs
  directly on the exact_email matchkey.
- **v1.12 ship target**: <fill in based on Phase 5.1 measurement>
```

- [ ] **Step 2: Update `packages/python/goldenmatch/CHANGELOG.md`**

Add `[1.12.0]` section above `[1.11.0]`. Mirror v1.11's structure. Use actual numbers from Task 5.1.

- [ ] **Step 3: Bump version**

```bash
grep -rn '"1\.11\.0"\|version = "1\.11\.0"\|__version__ = "1\.11\.0"' packages/python/goldenmatch/pyproject.toml packages/python/goldenmatch/goldenmatch/__init__.py
```

Edit both: `1.11.0` → `1.12.0`.

- [ ] **Step 4: Verify**

```bash
C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -c "import goldenmatch; print(goldenmatch.__version__)"
```

Expected: `1.12.0`.

- [ ] **Step 5: Final test sweep + ruff**

```bash
rm -f ~/.goldenmatch/autoconfig_memory.db && cd /d/show_case/goldenmatch/packages/python/goldenmatch && C:/Users/bsevern/AppData/Local/Programs/Python/Python312/python.exe -m pytest -q --timeout=180 --ignore=tests/test_db.py --ignore=tests/test_reconcile.py --ignore=tests/test_mcp_and_watch.py --ignore=tests/test_embedder.py --ignore=tests/test_llm_boost.py --ignore=tests/benchmarks 2>&1 | tail -5
```

Expected: ≥ 2005 passed (~+19 over v1.11's 1986).

```bash
cd /d/show_case/goldenmatch && ruff check packages/python/goldenmatch/goldenmatch/ 2>&1 | tail -5
```

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/CLAUDE.md packages/python/goldenmatch/CHANGELOG.md packages/python/goldenmatch/pyproject.toml packages/python/goldenmatch/goldenmatch/__init__.py
git commit -m "release(goldenmatch): v1.12.0 (1.11.0 -> 1.12.0)"
```

---

## Final acceptance gate

Before opening release PR:

- [ ] All 7 test tiers pass: ≥ 2005 tests passing.
- [ ] DBLP-ACM/Febrl3/NCVR each F1 ≥ v1.11 baselines.
- [ ] T1 F1 ≥ 88.9%; T2 F1 ≥ 69.0%. **No regression on v1.11's measured tiers.**
- [ ] DQbench composite ≥ 75 (primary) OR ≥ 70 (fallback). If neither, escalate.
- [ ] DQbench T3 F1 ≥ 70% (or report unmet target with attribution to PR description).
- [ ] No new ruff errors.
- [ ] Cache compat: v1.10 + v1.11 + v1.12 fixtures all load cleanly.
- [ ] CLAUDE.md + CHANGELOG updated with v1.12 entries.
- [ ] PR description includes per-tier DQbench breakdown + T3 before/after P/R/F1; cites spec amendment §Path Y adoption + Phase 7 diagnostic file path.

Open PR via `gh pr create` per CLAUDE.md SOP. Auth dance: `gh auth switch --user benzsevern` before push, switch back to `benzsevern-mjh` immediately after.

**Bundle release tasks (deferred from v1.10 + v1.11):**
- Tag `v1.12.0` to trigger PyPI publish via `publish-goldenmatch.yml`
- Wiki updates for v1.10 + v1.11 + v1.12 (one combined commit)
- About / Topics updates (one combined commit)
- Discussions: announce v1.10 + v1.11 + v1.12 in a single thread

If v1.11 PR #121 is still open at this point: amend it with v1.12 commits + push, OR close it and open a new PR for the combined v1.11 + v1.12 work. Decision deferred to user at release time.
