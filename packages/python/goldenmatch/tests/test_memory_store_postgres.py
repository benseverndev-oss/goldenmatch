import pytest

from tests._pg_helpers import HAS_POSTGRES, pg_url_fixture

pytestmark = pytest.mark.skipif(not HAS_POSTGRES, reason="no test postgres")

@pytest.fixture
def pg():
    # pg_url_fixture is a generator (NOT a context manager) — mirror the existing
    # pattern in tests/test_db.py: `yield from`. The yielded holder exposes .url().
    yield from pg_url_fixture()

def _mk(a, b, ds, dec="reject", score=0.5, trust=1.0, src="steward"):
    from goldenmatch.core.memory.store import Correction
    return Correction(id=f"{a}-{b}-{ds}", id_a=a, id_b=b, decision=dec, source=src,
                      trust=trust, field_hash="", record_hash="", original_score=score,
                      matchkey_name="mk", dataset=ds)

def test_pg_correction_roundtrip_and_trust_wins(pg):
    from goldenmatch.core.memory.store import MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.add_correction(_mk(1, 2, "A"))
    assert s.count_corrections(dataset="A") == 1
    # lower-trust does not overwrite higher-trust
    s.add_correction(_mk(1, 2, "A", dec="approve", trust=0.5, src="agent"))
    got = s.get_pair_correction(1, 2, dataset="A")
    assert got.decision == "reject"           # steward (1.0) kept
    s.close()

def test_pg_null_dataset_upsert(pg):
    from goldenmatch.core.memory.store import MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.add_correction(_mk(1, 2, None)); s.add_correction(_mk(1, 2, None, dec="approve"))
    assert s.count_corrections() == 1          # upsert, not duplicate
    s.close()

def test_pg_adjustments_tenant_isolation(pg):
    from datetime import datetime

    from goldenmatch.core.memory.store import LearnedAdjustment, MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    s.save_adjustment(LearnedAdjustment("mk", threshold=0.8, learned_at=datetime.now()), dataset="A")
    s.save_adjustment(LearnedAdjustment("mk", threshold=0.6, learned_at=datetime.now()), dataset="B")
    assert s.get_adjustment("mk", dataset="A").threshold == 0.8
    assert s.get_adjustment("mk", dataset="B").threshold == 0.6
    got = s.get_all_adjustments(dataset="A")
    assert len(got) == 1 and got[0].dataset == "A"
    s.close()

def test_pg_missing_extra_message(monkeypatch):
    # Force `import psycopg` to fail → the actionable ImportError, without a DB.
    import sys
    monkeypatch.setitem(sys.modules, "psycopg", None)
    from goldenmatch.core.memory.store import MemoryStore
    with pytest.raises(ImportError, match=r"goldenmatch\[postgres\]"):
        MemoryStore(backend="postgres", connection="postgresql://unused")

def test_pg_learn_per_dataset_isolation(pg):
    from goldenmatch.core.memory.learner import MemoryLearner
    from goldenmatch.core.memory.store import MemoryStore
    s = MemoryStore(backend="postgres", connection=pg.url(), table_prefix="goldenmatch_")
    for i in range(10): s.add_correction(_mk(i, 100+i, "A", "approve", 0.9))
    for i in range(10): s.add_correction(_mk(200+i, 300+i, "A", "reject", 0.2))
    for i in range(10): s.add_correction(_mk(i, 100+i, "B", "approve", 0.3))
    for i in range(10): s.add_correction(_mk(200+i, 300+i, "B", "reject", 0.25))
    MemoryLearner(s, dataset="A").learn()
    MemoryLearner(s, dataset="B").learn()
    a, b = s.get_adjustment("mk", dataset="A"), s.get_adjustment("mk", dataset="B")
    assert a and b and a.threshold != b.threshold   # not pooled
    s.close()
