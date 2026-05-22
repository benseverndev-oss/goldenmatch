# Cluster-decision tuner

**Target release:** v1.20.x (additive; minor bump)
**Effort:** ~½ day per RFC author estimate
**LOC:** ~150 production + ~30 schema/store + ~150 tests
**Source RFC:** Ben Severn (MJH Print Modernization, bsevern@mjhlifesciences.com),
  `goldenmatch-cluster-decision-tuner-rfc.md` (draft 2026-05-22)

## Problem

After v1.18.2 (#437) + Phase 1 (#445), goldenmatch tunes:

1. **Pair scoring** -- `MemoryLearner` ingests `approve`/`reject`
   corrections; learns matchkey thresholds + feature weights.
2. **Field strategy** -- `tune_field_strategy()` ingests
   `field_correct` corrections; proposes per-field merge strategies.

The **accept/reject decision that fires between those two layers** --
"is this entire cluster one person?" -- has no learning hook. Today
consumers hardcode a global confidence threshold (
print-modernization uses `HIGH_CONFIDENCE_FLOOR = 0.95`). As pair
scoring improves, the gray-zone shrinks asymmetrically across
datasets, but the threshold stays put.

Print-modernization has shipped a 30-line local sweeper that does
exactly the work this RFC asks goldenmatch to absorb. The RFC fires
**before a second consumer exists**, on the theory that the
abstraction is cheap and the API question is worth agreeing on now
rather than after two divergent implementations exist.

## Maintainer-decision answers

The RFC explicitly asks 4 decision questions. Answers:

| # | Question | Answer | Rationale |
|---|---|---|---|
| 1 | Nullable fields on `Correction` vs separate `ClusterDecision` dataclass | **Nullable fields** | Mirrors v1.18.2 field-level (`field_name`/`original_value`/`corrected_value`). Separate dataclass would need a parallel `MemoryStore.add_*` surface + parallel SQL schema. |
| 2 | Dataset namespace policy | **Single namespace + `decision` discriminator** (author's preference) | Matches existing `field_correct` pattern. Cluster + pair + field signals at the same `dataset` enable cross-feature analysis later. |
| 3 | Schema migration UX | **Auto-migrate on `MemoryStore.__init__`** | Exact pattern from PR #439 (`_migrate_field_correction_columns`). Already proven idempotent. |
| 4 | Release cadence | **v1.20.x minor** | Additive; backward compat preserved. Could ride alongside Phase 4 TUI in v1.20.0. |

## Naming clarification

The RFC author uses `decision="pair_correct"` for what we currently
store as `"approve"` / `"reject"`. We do NOT retroactively rename --
keep existing values; add `"cluster_decision"` as the 4th enum value.
The RFC code samples implicitly assume the rename; the implementation
will use `cluster_decision` alongside the existing string-literal
decision values.

## Design

### 1. `Correction` extension

`goldenmatch/core/memory/store.py::Correction` dataclass gains two
optional fields (mirroring the v1.18.2 field-level additions):

```python
@dataclass
class Correction:
    # ... existing fields ...
    # v1.18.2 field-level (#437):
    field_name: str | None = None
    original_value: str | None = None
    corrected_value: str | None = None
    # v1.20.x cluster-decision (this spec):
    cluster_score: float | None = None
    cluster_outcome: str | None = None  # "approve" | "reject"
```

`Decision` enum gains:

```python
class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    FIELD_CORRECT = "field_correct"
    CLUSTER_DECISION = "cluster_decision"  # NEW
```

`CorrectionSource` unchanged (existing tiers cover the cluster-decision case).

### 2. SQLite schema migration

`store.py::_SCHEMA` gains two columns. `_migrate_field_correction_columns`
renamed to `_migrate_field_and_cluster_columns` (or kept as a
single-purpose method + a new `_migrate_cluster_decision_columns`,
TBD on implementation taste). Both are idempotent ALTER TABLE.

```sql
ALTER TABLE corrections ADD COLUMN cluster_score REAL;
ALTER TABLE corrections ADD COLUMN cluster_outcome TEXT;
```

### 3. `MemoryStore.record_cluster_decision()` convenience

```python
def record_cluster_decision(
    self,
    dataset: str,
    cluster_id: int,
    score: float,
    outcome: str,  # "approve" | "reject"
    source: str = "steward",
) -> Correction:
    """Convenience: construct + add a cluster-decision Correction.

    Equivalent to:
        correction = Correction(
            id=uuid4(), id_a=cluster_id, id_b=0,
            decision="cluster_decision",
            source=source,
            trust=trust_for_source(source),
            ...
            cluster_score=score,
            cluster_outcome=outcome,
        )
        store.add_correction(correction)
    """
```

### 4. `tune_decision_threshold()` helper

New module: `goldenmatch/core/autoconfig_cluster_threshold_tuner.py`.

```python
@dataclass(frozen=True)
class ThresholdSuggestion:
    threshold: float | None
    n_total: int
    n_train: int
    n_heldout: int
    train_approve_rate: float | None
    heldout_approve_rate: float | None
    reason: str  # "ok" | "below_minimum" | "no_qualifying_band" | "overfit"


def tune_decision_threshold(
    store: MemoryStore,
    dataset: str,
    *,
    target_approve_rate: float = 0.99,
    min_band_n: int = 50,
    holdout_frac: float = 0.10,
    max_overfit_drop_pp: float = 1.0,
    seed: int | None = None,
) -> ThresholdSuggestion:
    """Sweep cluster-decision corrections for a threshold that hits
    target_approve_rate on the training set while remaining valid
    on a held-out 10%.

    Algorithm (verbatim from print-modernization's working impl):

    1. Load all decision='cluster_decision' Corrections for dataset.
       Skip with reason='below_minimum' if fewer than min_band_n * 2.
    2. Deterministic shuffle (seeded by sha256(dataset)[:8] -- avoids
       PYTHONHASHSEED non-determinism). Overridable via `seed` kwarg.
    3. Split 90/10 train/heldout.
    4. Sort train descending by cluster_score. Sweep: find the lowest
       threshold `t` such that approve-rate over scores >= t is
       >= target_approve_rate AND band has >= min_band_n samples.
    5. Compute heldout approve-rate at the same threshold.
    6. Reject (threshold=None, reason='overfit') if heldout < target
       OR heldout drops > max_overfit_drop_pp from train.
    7. Return ThresholdSuggestion.
    """
```

### 5. Tests

`tests/test_cluster_decision_tuner.py` (new file):

- `test_below_minimum_returns_none_with_reason` -- < 50 samples
- `test_no_qualifying_band_returns_none` -- no threshold meets target
- `test_overfit_guard_rejects_when_heldout_drops` -- train 99% but heldout 95%
- `test_ok_path_returns_valid_suggestion` -- 200-sample fixture
- `test_seed_determinism` -- same store + dataset + seed = same result
- `test_dataset_isolation` -- pub_48 corrections don't contaminate pub_99 suggestion
- `test_pair_level_corrections_ignored` -- approve/reject rows skipped
- `test_field_level_corrections_ignored` -- field_correct rows skipped

`tests/test_memory_store_cluster_decision.py`:

- `test_record_cluster_decision_round_trip`
- `test_record_cluster_decision_canonicalizes_cluster_outcome` -- "Approve"/"APPROVE"/"approved" normalize?
- `test_sqlite_migration_idempotent_on_v1.18.2_db`

### 6. Documentation

- CHANGELOG entry under `[Unreleased]` for v1.20.x
- README addition under "Learning Memory" section
- Brief note in `docs/api-quick-reference.md`
- CLAUDE.md update mentioning the 3rd decision variant

## Hand-off pattern

The RFC author commits to migrate their consumer (
`_persist_threshold_suggestion` in print-modernization) once we
publish the upstream release tag. Two ½-day chunks:

1. **Upstream PR** (this spec) -- ~½ day
2. **Consumer migration** (their side) -- ~½ day after release

## Acceptance criteria

- `Correction.cluster_score` + `cluster_outcome` round-trip
  end-to-end via MemoryStore
- `Decision.CLUSTER_DECISION` enum value exists + serializes
- `MemoryStore.record_cluster_decision()` convenience works
- `tune_decision_threshold()` returns the documented
  `ThresholdSuggestion` shape
- Schema migration on a pre-existing v1.18.2 SQLite file succeeds
  without data loss
- 8+ unit tests passing
- CHANGELOG + README updated

## Out of scope (per RFC author)

- Cross-dataset learning (each `dataset` tunes independently)
- Automatic threshold acceptance (tuner returns suggestion only)
- Cluster-level `MemoryLearner` analogue
- BigQuery / Snowflake / Redshift extensions
- Sub-namespaces (`pub_48/clusters` etc.)

## Risks acknowledged in RFC

- **Asymmetric ratchet**: threshold can move UP automatically but
  not DOWN (auto-approved bands drop out of the human-reviewed
  sample). Documented; operators move it down by clearing the
  override.
- **Generic `cluster_score`**: today the consumer feeds bottleneck
  pair score, but the API is float-agnostic. Doc the consumer's
  responsibility for the score semantic.
