"""End-to-end integration tests for Learning Memory (Phase 8).

Covers the full memory loop: seed corrections directly via MemoryStore,
run dedupe_df, assert on result.memory_stats, postflight rendering,
review queue persistence, trust conflicts, threshold learning, and the
deterministic explainer fallback.

Each test seeds via store.add_correction(...) rather than going through
collection points -- faster and isolates the integration surface from
the surface code under test.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path

import polars as pl
import pytest

from goldenmatch import dedupe_df
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
    MemoryConfig,
)
from goldenmatch.core.memory.store import Correction, MemoryStore


# ── Helpers ──────────────────────────────────────────────────────────


def _build_config(db_path: str, *, memory_enabled: bool = True) -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[
            MatchkeyConfig(
                name="identity",
                type="weighted",
                threshold=0.75,
                fields=[
                    MatchkeyField(
                        field="name",
                        scorer="jaro_winkler",
                        transforms=["lowercase"],
                        weight=1.0,
                    ),
                ],
            ),
        ],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["zip"], transforms=["lowercase"])],
            maxBlockSize=1000,
            skipOversized=True,
        ),
        memory=MemoryConfig(enabled=memory_enabled, path=db_path),
    )


def _basic_df() -> pl.DataFrame:
    """Three rows; rows 0 and 1 are the cluster pair (same zip + similar name)."""
    return pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )


def compute_field_hash_from_values(row_a_vals: tuple, row_b_vals: tuple) -> str:
    """Mirrors goldenmatch.core.memory.corrections.compute_field_hash so we
    can construct a matching field_hash for a seeded correction without
    needing the full df at seed time."""
    combined = "|".join(str(v) for v in row_a_vals + row_b_vals)
    return hashlib.sha256(combined.encode()).hexdigest()[:16]


def _capture_pipeline_df(input_df: pl.DataFrame, db_path: str) -> pl.DataFrame:
    """Run the pipeline once with no seeded corrections and capture the df
    as it appears at apply_corrections() time.

    The df at that point has extra columns (__source__, __xform_<sig>__) that
    feed into compute_record_hash. To produce hashes that re-anchor correctly
    in subsequent runs, we need to seed against this exact column set.
    """
    import goldenmatch.core.memory.corrections as corr_module

    captured: dict[str, pl.DataFrame] = {}
    orig = corr_module.apply_corrections

    def capture(scored, store, df, fields, **kw):
        captured["df"] = df
        return orig(scored, store, df, fields, **kw)

    corr_module.apply_corrections = capture
    # The pipeline imports apply_corrections inside its function body, so we
    # also have to patch that local binding via the module attribute -- the
    # internal `from ... import apply_corrections` resolves at call time
    # against corr_module, so a single attribute patch is sufficient.
    try:
        config = _build_config(db_path)
        # Ensure a memory.db exists so the hook fires (empty store is fine).
        store = MemoryStore(backend="sqlite", path=db_path)
        store.close()
        _ = dedupe_df(input_df, config=config)
    finally:
        corr_module.apply_corrections = orig

    assert "df" in captured, "pipeline did not invoke apply_corrections"
    return captured["df"]


def _record_hash_from_pipeline_df(pipeline_df: pl.DataFrame, row_id: int) -> str:
    """Compute record_hash exactly as apply_corrections does: sorted cols
    excluding __row_id__, joined by '|', sha256[:16]."""
    sorted_cols = sorted(c for c in pipeline_df.columns if c != "__row_id__")
    row = pipeline_df.filter(pl.col("__row_id__") == row_id).select(sorted_cols).row(0)
    return hashlib.sha256("|".join(str(v) for v in row).encode()).hexdigest()[:16]


def _seed_correction(
    db_path: str,
    *,
    id_a: int,
    id_b: int,
    decision: str,
    source: str = "steward",
    trust: float = 1.0,
    field_hash: str = "",
    record_hash: str = "",
    original_score: float = 0.95,
    matchkey_name: str | None = None,
    dataset: str | None = None,
) -> None:
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        store.add_correction(
            Correction(
                id=str(uuid.uuid4()),
                id_a=id_a,
                id_b=id_b,
                decision=decision,
                source=source,
                trust=trust,
                field_hash=field_hash,
                record_hash=record_hash,
                original_score=original_score,
                matchkey_name=matchkey_name,
                reason=None,
                dataset=dataset,
                created_at=datetime.now(),
            )
        )
    finally:
        store.close()


def _pair_score(scored_pairs, a: int, b: int) -> float | None:
    for pa, pb, s in scored_pairs:
        if (pa, pb) == (a, b) or (pa, pb) == (b, a):
            return s
    return None


# ── 1. Happy path ────────────────────────────────────────────────────


def test_e2e_happy_path_reject_overrides_score(tmp_path):
    """Seed a reject correction, run dedupe, assert pair score is 0.0."""
    df = _basic_df()
    db_path = str(tmp_path / "mem.db")
    _seed_correction(db_path, id_a=0, id_b=1, decision="reject")

    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
    score = _pair_score(result.scored_pairs, 0, 1)
    # Pair may have been filtered out below threshold, but if present, must be 0.0
    if score is not None:
        assert score == 0.0


# ── 2. Re-anchor on reorder ──────────────────────────────────────────


def test_e2e_reanchor_on_row_reorder(tmp_path):
    """Seed a correction with a real record_hash, shuffle df, re-run.
    Correction must still apply via record_hash re-anchor."""
    df = _basic_df()
    db_path = str(tmp_path / "mem.db")

    # Capture the df shape that apply_corrections will see (with __source__
    # and __xform_* columns) so we seed with the right record_hash recipe.
    pipeline_df = _capture_pipeline_df(df, db_path)
    # Original row IDs in pipeline_df: 0=Acme Corp, 1=Acme LLC.
    h0 = _record_hash_from_pipeline_df(pipeline_df, 0)
    h1 = _record_hash_from_pipeline_df(pipeline_df, 1)

    # field_hash matches what apply_corrections computes for matchkey field
    # values "name" -> ("Acme Corp", "Acme LLC").
    field_hash = compute_field_hash_from_values(("Acme Corp",), ("Acme LLC",))

    # Seed against synthetic row IDs that are NOT present in either the
    # original or shuffled df. This forces the re-anchor path via
    # record_hash (the production scenario for corrections persisted from
    # a prior run whose row IDs have since drifted).
    _seed_correction(
        db_path,
        id_a=100,
        id_b=101,
        decision="reject",
        field_hash=field_hash,
        record_hash=f"{h0}:{h1}",
    )

    # Reorder so Acme Corp and Acme LLC keep their relative order (Corp
    # before LLC) but a new row is inserted before them, shifting their
    # row IDs. This guarantees the canonical record_hash alignment used at
    # dual-hash check time matches the seeded record_hash.
    df_shuffled = pl.DataFrame(
        {
            "name": ["Beta Inc", "Acme Corp", "Acme LLC"],
            "zip": ["20002", "10001", "10001"],
        }
    )

    config = _build_config(db_path)
    result = dedupe_df(df_shuffled, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
    assert result.memory_stats.stale == 0


# ── 3. Re-anchor + edit on matchkey field => stale + review queue ───


def test_e2e_edit_on_matchkey_field_marks_stale_and_enqueues(tmp_path):
    """Edit a matchkey field on a corrected entity; correction must be
    classified as stale AND enqueued to the sibling review queue DB."""
    df = _basic_df()
    db_path = tmp_path / "mem.db"

    # Capture the pipeline-time df once with empty store so we can compute
    # record_hash exactly the way apply_corrections will. (The edited df in
    # this test will produce a DIFFERENT hash for row 0, which is the point
    # -- the correction goes stale because record_hash mismatches.)
    pipeline_df = _capture_pipeline_df(df, str(db_path))
    h0 = _record_hash_from_pipeline_df(pipeline_df, 0)
    h1 = _record_hash_from_pipeline_df(pipeline_df, 1)
    field_hash = compute_field_hash_from_values(("Acme Corp",), ("Acme LLC",))
    _seed_correction(
        str(db_path),
        id_a=0,
        id_b=1,
        decision="reject",
        field_hash=field_hash,
        record_hash=f"{h0}:{h1}",
    )

    # Edit the matchkey field on row 0.
    df_edited = pl.DataFrame(
        {
            "name": ["ACME CORPORATION", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )

    config = _build_config(str(db_path))
    result = dedupe_df(df_edited, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.stale >= 1

    # Sibling SQLite review queue should now contain the stale pair.
    queue_path = db_path.with_name("review_queue.db")
    assert queue_path.exists(), f"review queue not found at {queue_path}"

    from goldenmatch.core.review_queue import ReviewQueue

    rq = ReviewQueue(backend="sqlite", path=str(queue_path))
    pending = rq.list_pending("memory_stale")
    rq.close()
    pair_ids = {(it.id_a, it.id_b) for it in pending}
    # Original IDs are 0/1; canonicalization may flip order.
    assert (0, 1) in pair_ids or (1, 0) in pair_ids


# ── 4. Trust conflict ────────────────────────────────────────────────


def test_e2e_steward_overrides_llm_on_trust_conflict(tmp_path):
    """LLM (trust 0.5) rejects, then steward (trust 1.0) approves.
    Steward decision must win on next run -- pair survives as approved."""
    df = _basic_df()
    db_path = str(tmp_path / "mem.db")

    # First: LLM rejects (trust 0.5).
    _seed_correction(
        db_path,
        id_a=0,
        id_b=1,
        decision="reject",
        source="llm",
        trust=0.5,
    )
    # Then: steward approves (trust 1.0). Higher trust wins per upsert rule.
    _seed_correction(
        db_path,
        id_a=0,
        id_b=1,
        decision="approve",
        source="steward",
        trust=1.0,
    )

    # Verify the store has only the approve.
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        c = store.get_pair_correction(0, 1, dataset=None)
        assert c is not None
        assert c.decision == "approve"
        assert c.source == "steward"
        assert c.trust == 1.0
    finally:
        store.close()

    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
    # Approved override forces score to 1.0.
    score = _pair_score(result.scored_pairs, 0, 1)
    assert score == 1.0


# ── 5. Threshold learning ────────────────────────────────────────────


def test_e2e_threshold_learning_runs_and_updates_last_learn_time(tmp_path):
    """Seed 12 corrections covering a score range; pipeline triggers learn();
    last_learn_time advances and an adjustment is persisted."""
    df = _basic_df()
    db_path = str(tmp_path / "mem.db")

    # Seed 12 corrections with non-zero original_score, mixed approve/reject.
    # Approves cluster at higher scores, rejects at lower -- gives the learner
    # a real separating threshold to find.
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        for i in range(12):
            decision = "approve" if i >= 6 else "reject"
            score = 0.85 if decision == "approve" else 0.55
            store.add_correction(
                Correction(
                    id=str(uuid.uuid4()),
                    # Use disjoint synthetic row IDs so they don't conflict
                    # with the live df pair (0, 1).
                    id_a=100 + 2 * i,
                    id_b=101 + 2 * i,
                    decision=decision,
                    source="steward",
                    trust=1.0,
                    field_hash="",
                    record_hash="",
                    original_score=score,
                    matchkey_name=None,
                    reason=None,
                    dataset=None,
                    created_at=datetime.now(),
                )
            )
        # Sanity: no learn pass has run yet.
        assert store.last_learn_time() is None
    finally:
        store.close()

    config = _build_config(db_path)
    _ = dedupe_df(df, config=config)

    # Confirm the learner ran and persisted an adjustment.
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        last = store.last_learn_time()
        assert last is not None, "MemoryLearner.learn() should have advanced last_learn_time"
        adjustments = store.get_all_adjustments()
        assert any(a.threshold is not None for a in adjustments), \
            "expected at least one threshold adjustment from 12 seeded corrections"
    finally:
        store.close()


# ── 6. No API key, deterministic explainer ───────────────────────────


def test_e2e_explainer_deterministic_without_api_keys(monkeypatch, tmp_path):
    """With OPENAI_API_KEY and ANTHROPIC_API_KEY unset, review queue items
    still get a non-empty deterministic `why`."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    df = pl.DataFrame(
        {
            "__row_id__": [1, 2, 3],
            "name": ["Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "20002"],
        }
    )

    from goldenmatch.core.review_queue import ReviewQueue

    rq = ReviewQueue(df=df, matchkey_fields=["name", "zip"])
    rq.add("job", 1, 2, 0.85, explanation="legacy")
    items = rq.list_pending("job")
    assert len(items) == 1
    item = items[0]
    assert isinstance(item.why, str)
    assert item.why  # non-empty


# ── 7. Postflight surfaces stats ─────────────────────────────────────


def test_e2e_postflight_contains_memory_section(tmp_path):
    """Run with seeded correction; result.memory_stats populated and the
    rendered postflight string contains 'Memory:' and 'corrections applied'."""
    df = _basic_df()
    db_path = str(tmp_path / "mem.db")
    _seed_correction(db_path, id_a=0, id_b=1, decision="reject")

    config = _build_config(db_path)
    result = dedupe_df(df, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.applied == 1
    text = str(result.postflight_report) if result.postflight_report else ""
    assert "Memory:" in text, f"expected 'Memory:' in postflight, got: {text!r}"
    assert "corrections applied" in text or "correction applied" in text


# ── 8. Stale-ambiguous reported separately ───────────────────────────


def test_e2e_stale_ambiguous_surfaces_in_stats_and_postflight(tmp_path):
    """Seed correction with real record_hash, then re-run with a literal
    duplicate row that creates an ambiguous re-anchor. memory_stats must
    report stale_ambiguous == 1 and postflight must mention 'stale-ambiguous'."""
    db_path = str(tmp_path / "mem.db")

    # Build the duplicate df first; we need the record_hash that the pipeline
    # will compute for the duplicate row content -- both duplicates produce
    # the SAME hash, which is the ambiguity we want to trigger.
    df_dup = pl.DataFrame(
        {
            "name": ["Acme Corp", "Acme Corp", "Acme LLC", "Beta Inc"],
            "zip": ["10001", "10001", "10001", "20002"],
        }
    )

    # Capture the pipeline df for df_dup (no corrections yet -- just to
    # extract the actual hash for an Acme Corp row).
    pipeline_df = _capture_pipeline_df(df_dup, db_path)
    h_corp = _record_hash_from_pipeline_df(pipeline_df, 0)  # "Acme Corp" hash
    h_llc = _record_hash_from_pipeline_df(pipeline_df, 2)   # "Acme LLC" hash
    # Sanity: both Acme Corp duplicates produce the same hash.
    assert h_corp == _record_hash_from_pipeline_df(pipeline_df, 1)

    field_hash = compute_field_hash_from_values(("Acme Corp",), ("Acme LLC",))
    # Seed against IDs that AREN'T in the live df so direct lookup fails and
    # the system falls back to record_hash re-anchor -- where the duplicate
    # triggers the ambiguous branch.
    _seed_correction(
        db_path,
        id_a=999,
        id_b=998,
        decision="reject",
        field_hash=field_hash,
        record_hash=f"{h_corp}:{h_llc}",
    )

    config = _build_config(db_path)
    result = dedupe_df(df_dup, config=config)

    assert result.memory_stats is not None
    assert result.memory_stats.stale_ambiguous == 1
    text = str(result.postflight_report) if result.postflight_report else ""
    assert "stale-ambiguous" in text, \
        f"expected 'stale-ambiguous' in postflight, got: {text!r}"


def test_unmerge_correction_round_trips_empty_hash(tmp_path):
    """Integration test for the empty-hash collection path.

    Run dedupe -> call unmerge_record (which writes empty-hash reject
    corrections) -> re-run dedupe -> assert previously-merged pair has score
    0.0 (correction applied via empty-hash short-circuit).
    """
    from goldenmatch.core.cluster import unmerge_record

    df = _basic_df()
    db_path = str(tmp_path / "mem.db")
    config = _build_config(db_path)

    # First run: rows 0 and 1 should match into a cluster.
    result_1 = dedupe_df(df, config=config)
    assert result_1.clusters is not None
    multi_clusters = [
        c for c in result_1.clusters.values() if c.get("size", 0) > 1
    ]
    assert len(multi_clusters) >= 1, "expected at least one multi-member cluster"

    # Unmerge record 0 with a memory_store hooked up so a Correction is written.
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        unmerge_record(0, result_1.clusters, memory_store=store, dataset=None)
        items = store.get_corrections()
        assert any(
            c.decision == "reject" and c.source == "unmerge"
            and c.field_hash == "" and c.record_hash == ""
            for c in items
        ), f"expected an empty-hash unmerge correction, got: {items}"
    finally:
        store.close()

    # Re-run dedupe — the empty-hash correction should override the (0, 1)
    # score to 0.0.
    result_2 = dedupe_df(df, config=config)
    pair_scores_01 = [
        s for a, b, s in result_2.scored_pairs
        if {a, b} == {0, 1}
    ]
    assert pair_scores_01, "pair (0,1) should still appear in scored_pairs"
    assert all(s == 0.0 for s in pair_scores_01), (
        f"expected (0,1) score forced to 0.0, got: {pair_scores_01}"
    )
    assert result_2.memory_stats is not None
    assert result_2.memory_stats.applied >= 1


def test_trust_same_tier_latest_wins(tmp_path):
    """Two trust=1.0 corrections from steward for the same pair, opposing
    decisions: the second (latest) wins."""
    import time

    from goldenmatch.core.memory.store import Correction, MemoryStore

    db_path = str(tmp_path / "mem.db")
    store = MemoryStore(backend="sqlite", path=db_path)
    try:
        # First correction: approve.
        store.add_correction(Correction(
            id=str(uuid.uuid4()),
            id_a=0, id_b=1,
            decision="approve",
            source="steward",
            trust=1.0,
            field_hash="", record_hash="",
            original_score=0.85,
            matchkey_name=None,
            reason=None,
            dataset=None,
            created_at=datetime.now(),
        ))
        # Tiny sleep so created_at strictly differs even on fast clocks.
        time.sleep(0.01)
        # Second correction (same pair, same trust): reject. Should override.
        store.add_correction(Correction(
            id=str(uuid.uuid4()),
            id_a=0, id_b=1,
            decision="reject",
            source="steward",
            trust=1.0,
            field_hash="", record_hash="",
            original_score=0.85,
            matchkey_name=None,
            reason="changed my mind",
            dataset=None,
            created_at=datetime.now(),
        ))
        items = store.get_corrections()
        assert len(items) == 1, f"expected upsert (1 row), got {len(items)}"
        assert items[0].decision == "reject"
        assert items[0].reason == "changed my mind"
    finally:
        store.close()


def test_pipeline_memory_failure_renders_in_postflight(tmp_path):
    """When _apply_memory_post raises, CorrectionStats(failed=True) flows
    through and the postflight renderer surfaces 'Memory: failed (...)'."""
    from goldenmatch.core.autoconfig_verify import _render_memory_line
    from goldenmatch.core.memory.corrections import CorrectionStats

    stats = CorrectionStats(total_pairs=5, failed=True, error="boom")
    line = _render_memory_line(stats)
    assert "failed" in line
    assert "boom" in line
