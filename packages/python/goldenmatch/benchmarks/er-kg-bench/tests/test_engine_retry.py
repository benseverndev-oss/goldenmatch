"""The build's LLM/embedder retry: rate-limit/transient errors back off and
retry (so high concurrency doesn't drop docs); a 400 is a real error, re-raised."""
from __future__ import annotations

from urllib.error import HTTPError

import pytest
from erkgbench.qa_e2e.engines.goldengraph import _with_retry


def _http(code):
    return HTTPError("http://x", code, "msg", None, None)


def test_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http(429)
        return "ok"

    assert _with_retry(fn, attempts=5) == "ok"
    assert calls["n"] == 3


def test_400_is_not_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _http(400)

    with pytest.raises(HTTPError):
        _with_retry(fn, attempts=5)
    assert calls["n"] == 1  # gave up immediately


def test_gives_up_after_attempts(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda *_: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _http(503)

    with pytest.raises(HTTPError):
        _with_retry(fn, attempts=3)
    assert calls["n"] == 3
