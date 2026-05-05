"""Tests for MemoryStore CRUD operations."""
import pytest
from datetime import datetime
from goldenmatch.core.memory.store import (
    CorrectionSource,
    Decision,
    HIGH_TRUST_SOURCES,
    MemoryStore,
    Correction,
    LearnedAdjustment,
    trust_for_source,
)


@pytest.fixture
def store(tmp_path):
    return MemoryStore(backend="sqlite", path=str(tmp_path / "test_memory.db"))


def _make_correction(**kwargs) -> Correction:
    defaults = dict(
        id="test-1", id_a=1, id_b=2, decision="approve",
        source="steward", trust=1.0, field_hash="abc123",
        record_hash="def456", original_score=0.85,
        matchkey_name=None, reason=None, dataset="test",
        created_at=datetime.now(),
    )
    defaults.update(kwargs)
    return Correction(**defaults)


class TestAddAndGet:
    def test_add_and_get(self, store):
        c = _make_correction()
        store.add_correction(c)
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result is not None
        assert result.decision == "approve"
        assert result.trust == 1.0

    def test_get_missing_returns_none(self, store):
        assert store.get_pair_correction(99, 100) is None

    def test_get_corrections_list(self, store):
        store.add_correction(_make_correction(id="c1", id_a=1, id_b=2))
        store.add_correction(_make_correction(id="c2", id_a=3, id_b=4))
        result = store.get_corrections(dataset="test")
        assert len(result) == 2

    def test_count_corrections(self, store):
        store.add_correction(_make_correction(id="c1", id_a=1, id_b=2))
        store.add_correction(_make_correction(id="c2", id_a=3, id_b=4))
        assert store.count_corrections(dataset="test") == 2
        assert store.count_corrections(dataset="other") == 0


class TestUpsertAndTrust:
    def test_upsert_higher_trust_wins(self, store):
        store.add_correction(_make_correction(
            id="c1", id_a=1, id_b=2, decision="approve", trust=0.5, source="llm",
        ))
        store.add_correction(_make_correction(
            id="c2", id_a=1, id_b=2, decision="reject", trust=1.0, source="steward",
        ))
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result.decision == "reject"
        assert result.trust == 1.0

    def test_upsert_lower_trust_ignored(self, store):
        store.add_correction(_make_correction(
            id="c1", id_a=1, id_b=2, decision="approve", trust=1.0, source="steward",
        ))
        store.add_correction(_make_correction(
            id="c2", id_a=1, id_b=2, decision="reject", trust=0.5, source="llm",
        ))
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result.decision == "approve"

    def test_upsert_same_trust_latest_wins(self, store):
        store.add_correction(_make_correction(
            id="c1", id_a=1, id_b=2, decision="approve", trust=1.0,
            created_at=datetime(2026, 1, 1),
        ))
        store.add_correction(_make_correction(
            id="c2", id_a=1, id_b=2, decision="reject", trust=1.0,
            created_at=datetime(2026, 3, 1),
        ))
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result.decision == "reject"


class TestBulkLookup:
    def test_bulk_lookup(self, store):
        store.add_correction(_make_correction(id="c1", id_a=1, id_b=2))
        store.add_correction(_make_correction(id="c2", id_a=3, id_b=4))
        result = store.get_pair_corrections_bulk(
            [(1, 2), (3, 4), (5, 6)], dataset="test",
        )
        assert (1, 2) in result
        assert (3, 4) in result
        assert (5, 6) not in result


class TestAdjustments:
    def test_save_and_get_adjustment(self, store):
        adj = LearnedAdjustment(
            matchkey_name="mk1", threshold=0.82,
            field_weights=None, sample_size=15,
            learned_at=datetime.now(),
        )
        store.save_adjustment(adj)
        result = store.get_adjustment("mk1")
        assert result is not None
        assert result.threshold == 0.82

    def test_get_all_adjustments(self, store):
        store.save_adjustment(LearnedAdjustment(
            matchkey_name="mk1", threshold=0.8, field_weights=None,
            sample_size=10, learned_at=datetime.now(),
        ))
        store.save_adjustment(LearnedAdjustment(
            matchkey_name="mk2", threshold=0.9,
            field_weights={"name": 0.6, "zip": 0.4},
            sample_size=55, learned_at=datetime.now(),
        ))
        result = store.get_all_adjustments()
        assert len(result) == 2


class TestCorrectionsSince:
    def test_corrections_since(self, store):
        old = _make_correction(id="c1", id_a=1, id_b=2, created_at=datetime(2026, 1, 1))
        new = _make_correction(id="c2", id_a=3, id_b=4, created_at=datetime(2026, 3, 25))
        store.add_correction(old)
        store.add_correction(new)
        result = store.corrections_since(datetime(2026, 3, 1))
        assert len(result) == 1
        assert result[0].id_a == 3


class TestPairCanonicalization:
    def test_reversed_pair_finds_correction(self, store):
        """Correction stored as (2,1) should be found when looking up (1,2)."""
        store.add_correction(_make_correction(id="c1", id_a=2, id_b=1))
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result is not None
        assert result.decision == "approve"

    def test_reversed_pair_upsert(self, store):
        """Storing (1,2) then (2,1) should upsert the same logical pair."""
        store.add_correction(_make_correction(id="c1", id_a=1, id_b=2, decision="approve"))
        store.add_correction(_make_correction(id="c2", id_a=2, id_b=1, decision="reject"))
        result = store.get_pair_correction(1, 2, dataset="test")
        assert result.decision == "reject"
        assert store.count_corrections(dataset="test") == 1

    def test_bulk_lookup_reversed(self, store):
        store.add_correction(_make_correction(id="c1", id_a=2, id_b=1))
        result = store.get_pair_corrections_bulk([(1, 2)], dataset="test")
        assert (1, 2) in result


class TestUnsupportedBackend:
    def test_raises_not_implemented(self, tmp_path):
        import pytest
        with pytest.raises(NotImplementedError, match="postgres"):
            MemoryStore(backend="postgres", path=str(tmp_path / "x.db"))


class TestContextManager:
    def test_context_manager(self, tmp_path):
        with MemoryStore(backend="sqlite", path=str(tmp_path / "ctx.db")) as store:
            store.add_correction(_make_correction())
            assert store.count_corrections(dataset="test") == 1
        # Connection closed after with block


class TestLastLearnTime:
    def test_no_adjustments(self, store):
        assert store.last_learn_time() is None

    def test_with_adjustment(self, store):
        now = datetime.now()
        store.save_adjustment(LearnedAdjustment(
            matchkey_name="mk1", threshold=0.8, field_weights=None,
            sample_size=10, learned_at=now,
        ))
        result = store.last_learn_time()
        assert result is not None


class TestCorrectionSourceEnum:
    """StrEnum-based source/decision constants and trust mapping."""

    def test_strenum_equals_raw_string(self):
        assert CorrectionSource.STEWARD == "steward"
        assert CorrectionSource.BOOST == "boost"
        assert CorrectionSource.UNMERGE == "unmerge"
        assert CorrectionSource.AGENT == "agent"
        assert CorrectionSource.LLM == "llm"
        assert CorrectionSource.API == "api"
        assert Decision.APPROVE == "approve"
        assert Decision.REJECT == "reject"

    def test_high_trust_set_membership(self):
        assert CorrectionSource.STEWARD in HIGH_TRUST_SOURCES
        assert CorrectionSource.BOOST in HIGH_TRUST_SOURCES
        assert CorrectionSource.UNMERGE in HIGH_TRUST_SOURCES
        assert CorrectionSource.AGENT not in HIGH_TRUST_SOURCES
        assert CorrectionSource.LLM not in HIGH_TRUST_SOURCES
        assert CorrectionSource.API not in HIGH_TRUST_SOURCES
        # Raw-string membership also works (StrEnum is a str subclass)
        assert "steward" in HIGH_TRUST_SOURCES
        assert "agent" not in HIGH_TRUST_SOURCES

    def test_trust_for_source_human_tier(self):
        assert trust_for_source("steward") == 1.0
        assert trust_for_source("boost") == 1.0
        assert trust_for_source("unmerge") == 1.0
        assert trust_for_source(CorrectionSource.STEWARD) == 1.0

    def test_trust_for_source_agent_tier(self):
        assert trust_for_source("api") == 0.5
        assert trust_for_source("agent") == 0.5
        assert trust_for_source("llm") == 0.5
        assert trust_for_source(CorrectionSource.AGENT) == 0.5

    def test_all_sources_round_trip_through_store(self, store):
        """Every defined CorrectionSource value persists and reads back."""
        for i, src in enumerate(CorrectionSource):
            store.add_correction(_make_correction(
                id=f"src-{src.value}",
                id_a=i * 10,
                id_b=i * 10 + 1,
                source=src.value,
                trust=trust_for_source(src),
            ))
        items = store.get_corrections(dataset="test")
        sources_seen = {c.source for c in items}
        assert sources_seen == {s.value for s in CorrectionSource}


@pytest.mark.skip(
    reason="WAL mode required for reliable concurrent writes from two "
    "MemoryStore instances on the same SQLite file. Default journaling "
    "intermittently raises 'database is locked'. Tracked as a follow-up; "
    "test exists to surface the limitation rather than enforce it."
)
def test_concurrent_memory_store_writes_dont_lock(tmp_path):
    """Two MemoryStore instances pointing at the same DB; each writes one
    correction; both are visible to a third reader. Skipped by default --
    flips green only when WAL mode is enabled (TODO)."""
    db = str(tmp_path / "concurrent.db")
    s1 = MemoryStore(backend="sqlite", path=db)
    s2 = MemoryStore(backend="sqlite", path=db)
    try:
        s1.add_correction(_make_correction(id="c1", id_a=10, id_b=11))
        s2.add_correction(_make_correction(id="c2", id_a=20, id_b=21))
    finally:
        s1.close()
        s2.close()
    reader = MemoryStore(backend="sqlite", path=db)
    try:
        items = reader.get_corrections(dataset="test")
        ids = {c.id for c in items}
        assert ids == {"c1", "c2"}
    finally:
        reader.close()
