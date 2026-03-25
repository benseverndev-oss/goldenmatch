"""Tests for goldencheck.agent.review_queue."""
from __future__ import annotations



from goldencheck.agent.review_queue import ReviewItem, ReviewQueue
from goldencheck.models.finding import Finding, Severity


def _make_item(
    job_name: str = "job1",
    item_id: str = "item-1",
    column: str = "col_a",
    check: str = "null_check",
    severity: str = "WARNING",
    confidence: float = 0.7,
    message: str = "found nulls",
    status: str = "pending",
) -> ReviewItem:
    return ReviewItem(
        job_name=job_name,
        item_id=item_id,
        column=column,
        check=check,
        severity=severity,
        confidence=confidence,
        message=message,
        status=status,
    )


def _make_finding(
    severity: Severity = Severity.WARNING,
    confidence: float = 0.7,
    column: str = "col_a",
    check: str = "null_check",
    message: str = "found nulls",
) -> Finding:
    return Finding(
        severity=severity,
        column=column,
        check=check,
        message=message,
        confidence=confidence,
    )


# -------------------------------------------------------------------
# Memory backend tests
# -------------------------------------------------------------------


def test_memory_backend_add_and_pending():
    q = ReviewQueue(backend="memory")
    q.add(_make_item(item_id="a1"))
    q.add(_make_item(item_id="a2"))
    q.add(_make_item(item_id="a3", job_name="other_job"))

    pending = q.pending("job1")
    assert len(pending) == 2
    ids = {it.item_id for it in pending}
    assert ids == {"a1", "a2"}


def test_memory_backend_approve():
    q = ReviewQueue(backend="memory")
    q.add(_make_item(item_id="a1"))
    q.approve("a1", decided_by="human")

    pending = q.pending("job1")
    assert len(pending) == 0

    stats = q.stats("job1")
    assert stats["pinned"] == 1


def test_memory_backend_reject():
    q = ReviewQueue(backend="memory")
    q.add(_make_item(item_id="a1"))
    q.reject("a1", decided_by="human", reason="false positive")

    pending = q.pending("job1")
    assert len(pending) == 0

    stats = q.stats("job1")
    assert stats["dismissed"] == 1


def test_memory_backend_stats():
    q = ReviewQueue(backend="memory")
    q.add(_make_item(item_id="a1"))
    q.add(_make_item(item_id="a2"))
    q.add(_make_item(item_id="a3"))
    q.approve("a1", decided_by="human")
    q.reject("a2", decided_by="human")

    stats = q.stats("job1")
    assert stats == {"pending": 1, "pinned": 1, "dismissed": 1}


# -------------------------------------------------------------------
# SQLite backend tests
# -------------------------------------------------------------------


def test_sqlite_backend(tmp_path):
    db_path = tmp_path / "reviews.db"
    q = ReviewQueue(backend="memory")
    # Directly construct with sqlite backend via internal path
    from goldencheck.agent.review_queue import _SQLiteBackend

    backend = _SQLiteBackend(db_path=db_path)
    q._backend = backend

    q.add(_make_item(item_id="s1"))
    q.add(_make_item(item_id="s2"))
    q.approve("s1", decided_by="bot")

    pending = q.pending("job1")
    assert len(pending) == 1
    assert pending[0].item_id == "s2"

    stats = q.stats("job1")
    assert stats["pinned"] == 1
    assert stats["pending"] == 1


def test_sqlite_persistence(tmp_path):
    db_path = tmp_path / "reviews.db"
    from goldencheck.agent.review_queue import _SQLiteBackend

    # First queue: add items
    q1 = ReviewQueue(backend="memory")
    q1._backend = _SQLiteBackend(db_path=db_path)
    q1.add(_make_item(item_id="p1"))
    q1.add(_make_item(item_id="p2"))
    q1.approve("p1", decided_by="human")

    # Second queue: same db, items should persist
    q2 = ReviewQueue(backend="memory")
    q2._backend = _SQLiteBackend(db_path=db_path)

    pending = q2.pending("job1")
    assert len(pending) == 1
    assert pending[0].item_id == "p2"

    stats = q2.stats("job1")
    assert stats["pinned"] == 1
    assert stats["pending"] == 1


# -------------------------------------------------------------------
# classify_findings tests
# -------------------------------------------------------------------


def test_classify_findings_auto_pin():
    q = ReviewQueue(backend="memory")
    findings = [
        _make_finding(severity=Severity.ERROR, confidence=0.9),
        _make_finding(severity=Severity.WARNING, confidence=0.85),
    ]
    result = q.classify_findings(findings, "job1")

    assert len(result["pinned"]) == 2
    assert len(result["review"]) == 0
    assert len(result["dismissed"]) == 0
    assert all(it.status == "pinned" for it in result["pinned"])


def test_classify_findings_review():
    q = ReviewQueue(backend="memory")
    findings = [
        _make_finding(severity=Severity.WARNING, confidence=0.6),
        _make_finding(severity=Severity.ERROR, confidence=0.55),
    ]
    result = q.classify_findings(findings, "job1")

    assert len(result["pinned"]) == 0
    assert len(result["review"]) == 2
    assert len(result["dismissed"]) == 0
    assert all(it.status == "pending" for it in result["review"])

    # Items should be in the queue
    pending = q.pending("job1")
    assert len(pending) == 2


def test_classify_findings_dismiss():
    q = ReviewQueue(backend="memory")
    findings = [
        # Low confidence WARNING -> dismissed
        _make_finding(severity=Severity.WARNING, confidence=0.3),
        # INFO severity (even high confidence) -> dismissed
        _make_finding(severity=Severity.INFO, confidence=0.95),
    ]
    result = q.classify_findings(findings, "job1")

    assert len(result["pinned"]) == 0
    assert len(result["review"]) == 0
    assert len(result["dismissed"]) == 2
    assert all(it.status == "dismissed" for it in result["dismissed"])


# -------------------------------------------------------------------
# Auto-detect backend tests
# -------------------------------------------------------------------


def test_auto_detect_memory(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    # No .goldencheck dir, no DATABASE_URL -> memory
    q = ReviewQueue(backend="auto")
    from goldencheck.agent.review_queue import _MemoryBackend

    assert isinstance(q._backend, _MemoryBackend)


def test_auto_detect_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".goldencheck").mkdir()
    q = ReviewQueue(backend="auto")
    from goldencheck.agent.review_queue import _SQLiteBackend

    assert isinstance(q._backend, _SQLiteBackend)
