"""Phase 4: Collection point tests for Learning Memory.

Each surface accepts an optional MemoryStore. When provided, calling the
surface's existing approve/reject/decide/label flow also writes a Correction
into the store.
"""
from __future__ import annotations

import polars as pl

from goldenmatch.core.memory.store import MemoryStore


def _new_store(tmp_path) -> MemoryStore:
    return MemoryStore(backend="sqlite", path=str(tmp_path / "mem.db"))


def _make_match_server_for_test(memory_store: MemoryStore, dataset: str):
    """Test factory that builds a MatchServer with memory plumbing only.

    Avoids reaching into private fields directly across the test surface;
    keeps the test honest if MatchServer.__init__ grows new defaults.
    """
    from goldenmatch.api.server import MatchServer

    server = MatchServer.__new__(MatchServer)
    # Mirror the public init shape (engine/config/result), then populate
    # memory plumbing.
    server.engine = None
    server.config = None
    server.result = None
    server._rows = []
    server._id_to_idx = {}
    server._review_queue = []
    server._review_decisions = []
    server._memory_store = memory_store
    server._memory_dataset = dataset
    return server


def _person_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "name": ["alice", "alice b", "bob", "carol"],
            "zip": ["10001", "10001", "10002", "10003"],
        }
    )


# ── 4.1 ReviewQueue ─────────────────────────────────────────────────────


class TestReviewQueueCollection:
    def test_approve_writes_correction(self, tmp_path):
        from goldenmatch.core.review_queue import ReviewQueue

        store = _new_store(tmp_path)
        df = _person_df()
        rq = ReviewQueue(
            backend="memory",
            memory_store=store,
            df=df,
            matchkey_fields=["name", "zip"],
            dataset="test",
        )
        rq.add(job_name="job1", id_a=0, id_b=1, score=0.85, explanation="x")
        rq.approve("job1", 0, 1, decided_by="alice")

        items = store.get_corrections(dataset="test")
        assert len(items) == 1
        c = items[0]
        assert (c.id_a, c.id_b) == (0, 1)
        assert c.decision == "approve"
        assert c.source == "steward"
        assert c.trust == 1.0
        assert len(c.field_hash) == 16
        assert ":" in c.record_hash and len(c.record_hash) == 33

    def test_reject_writes_correction(self, tmp_path):
        from goldenmatch.core.review_queue import ReviewQueue

        store = _new_store(tmp_path)
        df = _person_df()
        rq = ReviewQueue(
            backend="memory",
            memory_store=store,
            df=df,
            matchkey_fields=["name", "zip"],
            dataset="test",
        )
        rq.add(job_name="job1", id_a=2, id_b=3, score=0.80, explanation="x")
        rq.reject("job1", 2, 3, decided_by="bob", reason="diff")

        items = store.get_corrections(dataset="test")
        assert len(items) == 1
        assert items[0].decision == "reject"
        assert items[0].source == "steward"
        assert items[0].trust == 1.0
        assert items[0].reason == "diff"

    def test_no_store_unchanged(self, tmp_path):
        from goldenmatch.core.review_queue import ReviewQueue

        rq = ReviewQueue(backend="memory")
        rq.add(job_name="j", id_a=0, id_b=1, score=0.85, explanation="x")
        rq.approve("j", 0, 1, decided_by="alice")
        # No exception, default behavior preserved.
        assert rq.stats("j")["approved"] == 1


# ── 4.2 unmerge_record + unmerge_cluster ─────────────────────────────────


class TestUnmergeCollection:
    def _two_clusters(self):
        from goldenmatch.core.cluster import build_clusters

        pairs = [(0, 1, 0.95), (1, 2, 0.92), (0, 2, 0.93)]
        return build_clusters(pairs, [0, 1, 2, 3])

    def test_unmerge_record_writes_rejects(self, tmp_path):
        from goldenmatch.core.cluster import unmerge_record

        store = _new_store(tmp_path)
        clusters = self._two_clusters()
        unmerge_record(0, clusters, memory_store=store, dataset="ds")

        items = store.get_corrections(dataset="ds")
        # Record 0 was paired with 1 and 2 in the cluster — both rejects.
        assert len(items) == 2
        for c in items:
            assert c.decision == "reject"
            assert c.source == "unmerge"
            assert c.trust == 1.0
            assert c.field_hash == ""
            assert c.record_hash == ""

    def test_unmerge_cluster_writes_rejects(self, tmp_path):
        from goldenmatch.core.cluster import unmerge_cluster

        store = _new_store(tmp_path)
        clusters = self._two_clusters()
        # Find the multi-member cluster
        cid = next(cid for cid, ci in clusters.items() if ci["size"] > 1)
        unmerge_cluster(cid, clusters, memory_store=store, dataset="ds")

        items = store.get_corrections(dataset="ds")
        # 3 pairs from a 3-member cluster
        assert len(items) == 3
        for c in items:
            assert c.decision == "reject"
            assert c.source == "unmerge"
            assert c.field_hash == ""
            assert c.record_hash == ""

    def test_unmerge_no_store_unchanged(self):
        from goldenmatch.core.cluster import unmerge_record, unmerge_cluster

        clusters = self._two_clusters()
        result = unmerge_record(0, clusters)
        assert isinstance(result, dict)
        clusters2 = self._two_clusters()
        cid = next(cid for cid, ci in clusters2.items() if ci["size"] > 1)
        result2 = unmerge_cluster(cid, clusters2)
        assert isinstance(result2, dict)


# ── 4.3 llm_score_pairs ──────────────────────────────────────────────────


class TestLLMScorerCollection:
    def test_writes_corrections_for_each_decision(self, tmp_path, monkeypatch):
        from goldenmatch.core import llm_scorer as mod

        # Stub provider detection so no real network calls.
        monkeypatch.setattr(mod, "_detect_provider", lambda: ("openai", "sk-test"))

        # Stub _batch_score: return is_match=True for first candidate, False for next.
        def fake_batch_score(candidate_indices, pairs, *a, **kw):
            return {idx: (i == 0) for i, idx in enumerate(candidate_indices)}

        monkeypatch.setattr(mod, "_batch_score", fake_batch_score)

        store = _new_store(tmp_path)
        df = _person_df()
        # Both pairs in candidate range [0.75, 0.95]
        pairs = [(0, 1, 0.80), (2, 3, 0.82)]

        mod.llm_score_pairs(
            pairs,
            df,
            auto_threshold=0.95,
            candidate_lo=0.75,
            candidate_hi=0.95,
            memory_store=store,
            dataset="dsl",
        )

        items = store.get_corrections(dataset="dsl")
        assert len(items) == 2
        decisions = sorted(c.decision for c in items)
        assert decisions == ["approve", "reject"]
        for c in items:
            assert c.source == "llm"
            assert c.trust == 0.5
            assert len(c.field_hash) == 16
            assert ":" in c.record_hash


# ── 4.4 agent_approve_reject ─────────────────────────────────────────────


class TestAgentApproveRejectCollection:
    def test_approve_writes_correction(self, tmp_path):
        from goldenmatch.mcp.agent_tools import _dispatch
        from goldenmatch.core.agent import AgentSession

        store = _new_store(tmp_path)
        # Need to enqueue first via the session's queue.
        session = AgentSession()
        session.review_queue.add(
            job_name="j", id_a=5, id_b=7, score=0.8, explanation="x"
        )

        def session_factory():
            return session

        result = _dispatch(
            "agent_approve_reject",
            {
                "job_name": "j",
                "id_a": 5,
                "id_b": 7,
                "decision": "approve",
                "decided_by": "agent",
            },
            session_factory,
            memory_store=store,
            dataset="dsa",
        )
        assert result["status"] == "ok"
        items = store.get_corrections(dataset="dsa")
        assert len(items) == 1
        c = items[0]
        assert (c.id_a, c.id_b) == (5, 7)
        assert c.decision == "approve"
        assert c.source == "agent"
        assert c.trust == 0.5


# ── 4.5 REST POST /reviews/decide ────────────────────────────────────────


class TestRestDecideCollection:
    def test_review_decision_writes_correction(self, tmp_path):
        from goldenmatch.api.server import MatchServer

        store = _new_store(tmp_path)
        server = _make_match_server_for_test(store, "dsr")
        server._review_queue = [
            {"pair_id": "p1", "row_id_a": 1, "row_id_b": 2, "status": "pending"}
        ]

        result = server.review_decision("p1", "approve", reviewer="steward")
        assert result["status"] == "recorded"

        items = store.get_corrections(dataset="dsr")
        assert len(items) == 1
        c = items[0]
        assert (c.id_a, c.id_b) == (1, 2)
        assert c.decision == "approve"
        assert c.source == "steward"
        assert c.trust == 1.0
        # Empty hashes (REST has no df in scope).
        assert c.field_hash == ""
        assert c.record_hash == ""


# ── 4.6 BoostTab y/n ─────────────────────────────────────────────────────


class TestBoostTabCollection:
    def test_record_label_writes_correction(self, tmp_path):
        """Targets the testable seam (record_boost_label) instead of poking
        private BoostTab attrs. Real Textual setup is too heavy for unit tests,
        and the seam is what BoostTab._record_memory_correction calls anyway."""
        from goldenmatch.tui.tabs.boost_tab import record_boost_label

        store = _new_store(tmp_path)
        df = _person_df()
        pairs = [(0, 1, 0.82), (2, 3, 0.78)]

        # Match (approve).
        a, b, score = pairs[0]
        record_boost_label(
            memory_store=store, df=df,
            id_a=a, id_b=b, score=score, is_match=True,
            matchkey_fields=["name", "zip"], dataset="dsb",
        )
        # Non-match (reject).
        a, b, score = pairs[1]
        record_boost_label(
            memory_store=store, df=df,
            id_a=a, id_b=b, score=score, is_match=False,
            matchkey_fields=["name", "zip"], dataset="dsb",
        )

        items = store.get_corrections(dataset="dsb")
        assert len(items) == 2
        decisions = sorted(c.decision for c in items)
        assert decisions == ["approve", "reject"]
        for c in items:
            assert c.source == "boost"
            assert c.trust == 1.0
            assert len(c.field_hash) == 16
            assert ":" in c.record_hash
