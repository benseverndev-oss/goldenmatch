"""goldengraph engine thread-safety: the per-question token counter and the 429/5xx
retry. No wheel / network -- the engine module imports goldengraph lazily inside its
methods, so `_CountingLLM` / `_with_retry` import free-standing."""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from urllib.error import HTTPError

_BENCH_ROOT = Path(__file__).resolve().parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from erkgbench.qa_e2e.engines import goldengraph as gg  # noqa: E402


class _InnerLLM:
    """Deterministic inner client: the reply length is a fixed function of the prompt
    length, so token counts are predictable."""

    def complete(self, prompt: str) -> str:
        return "r" * len(prompt)


def test_counting_llm_thread_local_isolates_per_call_tokens():
    # Each worker thread issues a DISTINCT-length prompt many times; reset->calls->read
    # on that thread must report ONLY that thread's tokens (no cross-contamination),
    # while the global counters sum every thread's calls.
    llm = gg._CountingLLM(_InnerLLM())
    n_threads, n_calls = 8, 20
    results: dict[int, tuple[int, int]] = {}
    barrier = threading.Barrier(n_threads)
    lock = threading.Lock()

    def worker(tid: int):
        prompt = "p" * (10 * (tid + 1))  # thread tid's prompt length = 10*(tid+1)
        barrier.wait()  # maximise interleaving
        llm.reset_thread_tokens()
        for _ in range(n_calls):
            llm.complete(prompt)
        got = llm.thread_tokens()
        with lock:
            results[tid] = got

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Expected per-thread: n_calls * (max(1, len(prompt)//4), max(1, len(reply)//4)).
    total_in = total_out = 0
    for tid in range(n_threads):
        plen = 10 * (tid + 1)
        exp_in = n_calls * max(1, plen // 4)
        exp_out = n_calls * max(1, plen // 4)  # reply length == prompt length
        assert results[tid] == (exp_in, exp_out), tid
        total_in += exp_in
        total_out += exp_out

    # Global counters are the order-independent SUM across all threads.
    assert llm.input_tokens == total_in
    assert llm.output_tokens == total_out


def test_counting_llm_reset_clears_previous_call_residue():
    # A pooled worker thread is reused across questions: reset must zero the prior
    # question's residue so the next delta is exact.
    llm = gg._CountingLLM(_InnerLLM())
    llm.reset_thread_tokens()
    llm.complete("xxxx")
    first = llm.thread_tokens()
    assert first[0] >= 1
    llm.reset_thread_tokens()
    llm.complete("yyyyyyyy")
    second = llm.thread_tokens()
    # second reflects ONLY the second call, not the sum of both
    assert second == (max(1, 8 // 4), max(1, 8 // 4))


def _http_error(code: int) -> HTTPError:
    return HTTPError("http://x", code, "err", None, None)


def test_with_retry_retries_rate_limit_then_succeeds(monkeypatch):
    # A 429 the first 3 attempts, then success: _with_retry must swallow the 429s and
    # return the eventual value, invoking fn exactly 4 times (retried, counted once by
    # the caller since only the successful return records tokens).
    monkeypatch.setattr(gg.time, "sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        if calls["n"] <= 3:
            raise _http_error(429)
        return "ok"

    assert gg._with_retry(fn) == "ok"
    assert calls["n"] == 4


def test_with_retry_reraises_non_retryable(monkeypatch):
    # A 400 is a real error -> re-raised immediately, no retries.
    monkeypatch.setattr(gg.time, "sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _http_error(400)

    try:
        gg._with_retry(fn)
        raise AssertionError("expected HTTPError")
    except HTTPError as e:
        assert e.code == 400
    assert calls["n"] == 1


def test_with_retry_gives_up_after_attempts(monkeypatch):
    # Persistent 429: exhausts attempts (default 6) then re-raises.
    monkeypatch.setattr(gg.time, "sleep", lambda *_a, **_k: None)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        raise _http_error(429)

    try:
        gg._with_retry(fn, attempts=6)
        raise AssertionError("expected HTTPError")
    except HTTPError as e:
        assert e.code == 429
    assert calls["n"] == 6
