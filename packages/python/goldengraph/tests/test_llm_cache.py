"""Unit tests for the prompt-hash `CachingLLMClient` (bench extraction cache).

The cache is a measurement-harness optimization: keying on the EXACT prompt (+
model + method) means a cached response IS the model's output for that prompt, so
serving it can never be stale as long as the prompt matches. Any extraction-code
change that alters a prompt yields a new key automatically.

`llm.py` imports only stdlib (os/typing + the cache's hashlib/json/threading), so
we load it by file path under a synthetic module name -- the same bypass
`test_native_loader` uses to dodge `goldengraph/__init__`'s heavy deps (numpy, the
native engine wheel). The unit under test is pure-Python and network-free.
"""

from __future__ import annotations

import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

_LLM_PATH = Path(__file__).parent.parent / "goldengraph" / "llm.py"


def _load_llm():
    spec = importlib.util.spec_from_file_location("_gg_llm_under_test", _LLM_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_LLM = _load_llm()
CachingLLMClient = _LLM.CachingLLMClient


class FakeInner:
    """Counts calls per method; returns a deterministic response per prompt so a
    cache HIT (which must NOT call this) is distinguishable from a MISS. Exposes
    `.model` + `.input_tokens`/`.output_tokens` like the real clients."""

    def __init__(self, model="fake-model"):
        self.model = model
        self.calls = {"complete": 0, "complete_json": 0, "complete_many": 0}
        self.input_tokens = 0
        self.output_tokens = 0

    def complete(self, prompt: str) -> str:
        self.calls["complete"] += 1
        self.input_tokens += len(prompt)
        self.output_tokens += 1
        return f"complete::{prompt}"

    def complete_json(self, prompt: str) -> str:
        self.calls["complete_json"] += 1
        self.input_tokens += len(prompt)
        self.output_tokens += 1
        return json.dumps({"prompt": prompt, "method": "complete_json"})

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        self.calls["complete_many"] += 1
        self.input_tokens += len(prompt) * n
        self.output_tokens += n
        return [f"many::{prompt}::{i}::t{temperature}" for i in range(n)]


def _cache(tmp_path, inner=None, name="cache.jsonl"):
    inner = inner or FakeInner()
    client = CachingLLMClient(inner, str(tmp_path / name))
    return client, inner


# 1. Miss then hit: same prompt twice -> inner called ONCE; both returns equal.
def test_miss_then_hit_complete(tmp_path):
    client, inner = _cache(tmp_path)
    a = client.complete("hello")
    b = client.complete("hello")
    assert a == b == "complete::hello"
    assert inner.calls["complete"] == 1


# 2. Different prompt or different model -> different key -> inner called again.
def test_different_prompt_is_a_new_key(tmp_path):
    client, inner = _cache(tmp_path)
    client.complete("p1")
    client.complete("p2")
    assert inner.calls["complete"] == 2


def test_different_model_is_a_new_key(tmp_path):
    path = str(tmp_path / "shared.jsonl")
    inner_a = FakeInner(model="model-a")
    inner_b = FakeInner(model="model-b")
    ca = CachingLLMClient(inner_a, path)
    cb = CachingLLMClient(inner_b, path)
    ca.complete("same-prompt")
    # cb reloads ca's on-disk entry but its model differs -> different key -> miss.
    cb.complete("same-prompt")
    assert inner_a.calls["complete"] == 1
    assert inner_b.calls["complete"] == 1


# 3. complete / complete_json / complete_many cache INDEPENDENTLY; temperature>0 not cached.
def test_methods_cache_independently(tmp_path):
    client, inner = _cache(tmp_path)
    # same prompt string, three different methods -> three distinct keys.
    client.complete("x")
    client.complete("x")
    client.complete_json("x")
    client.complete_json("x")
    client.complete_many("x", n=2, temperature=0.0)
    client.complete_many("x", n=2, temperature=0.0)
    assert inner.calls == {"complete": 1, "complete_json": 1, "complete_many": 1}


def test_complete_many_returns_cached_list(tmp_path):
    client, inner = _cache(tmp_path)
    first = client.complete_many("q", n=3, temperature=0.0)
    second = client.complete_many("q", n=3, temperature=0.0)
    assert first == second == ["many::q::0::t0.0", "many::q::1::t0.0", "many::q::2::t0.0"]
    assert inner.calls["complete_many"] == 1
    # a mutation of a returned list must not poison the cache.
    second.append("tampered")
    assert client.complete_many("q", n=3, temperature=0.0) == first


def test_complete_many_n_is_part_of_key(tmp_path):
    client, inner = _cache(tmp_path)
    client.complete_many("q", n=2, temperature=0.0)
    client.complete_many("q", n=3, temperature=0.0)
    assert inner.calls["complete_many"] == 2


def test_complete_many_positive_temperature_is_not_cached(tmp_path):
    client, inner = _cache(tmp_path)
    client.complete_many("q", n=2, temperature=0.7)
    client.complete_many("q", n=2, temperature=0.7)
    # nondeterministic sampling -> never frozen -> inner called EVERY time.
    assert inner.calls["complete_many"] == 2
    # and nothing was written to disk for the sampled calls.
    assert not Path(str(tmp_path / "cache.jsonl")).exists() or (
        Path(str(tmp_path / "cache.jsonl")).read_text(encoding="utf-8").strip() == ""
    )


# 4. Disabled (no path) -> passthrough, inner called every time, zero cache files.
def test_disabled_passthrough_no_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inner = FakeInner()
    client = CachingLLMClient(inner, None)
    client.complete("a")
    client.complete("a")
    client.complete_json("a")
    client.complete_json("a")
    assert inner.calls["complete"] == 2
    assert inner.calls["complete_json"] == 2
    # no file written anywhere under the cwd.
    assert list(Path(tmp_path).glob("**/*.jsonl")) == []


def test_disabled_empty_string_passthrough(tmp_path):
    inner = FakeInner()
    client = CachingLLMClient(inner, "")
    client.complete("a")
    client.complete("a")
    assert inner.calls["complete"] == 2


# 5. Persistence round-trip: a fresh instance on the same file serves from disk.
def test_persistence_round_trip(tmp_path):
    path = str(tmp_path / "persist.jsonl")
    inner1 = FakeInner()
    c1 = CachingLLMClient(inner1, path)
    r1 = c1.complete("persisted")
    assert inner1.calls["complete"] == 1

    inner2 = FakeInner()
    c2 = CachingLLMClient(inner2, path)
    r2 = c2.complete("persisted")
    assert r2 == r1
    assert inner2.calls["complete"] == 0  # served from disk, inner untouched


# 6. Corrupt cache file -> treated as empty, no crash, rebuilds.
def test_corrupt_cache_file_is_tolerated(tmp_path):
    path = tmp_path / "corrupt.jsonl"
    path.write_text("this is not json\n{also bad\n", encoding="utf-8")
    inner = FakeInner()
    client = CachingLLMClient(inner, str(path))  # must not raise
    out = client.complete("after-corruption")
    assert out == "complete::after-corruption"
    assert inner.calls["complete"] == 1
    # rebuilds cleanly: a fresh instance reads the rewritten file and hits.
    inner2 = FakeInner()
    client2 = CachingLLMClient(inner2, str(path))
    assert client2.complete("after-corruption") == out
    assert inner2.calls["complete"] == 0


def test_partial_last_line_is_tolerated(tmp_path):
    # A crash mid-append can leave a truncated final line.
    path = tmp_path / "partial.jsonl"
    good = json.dumps({"key": "abc", "resp": "cached-value"})
    path.write_text(good + "\n{\"key\": \"def\", \"resp\": \"tru", encoding="utf-8")
    inner = FakeInner()
    client = CachingLLMClient(inner, str(path))  # must not raise
    # rebuilt to empty, so a new call misses and repopulates without crashing.
    assert client.complete("z") == "complete::z"


# 7. Thread-safety: concurrent gets/puts don't lose entries or corrupt the file.
#    (The cache deliberately does NOT single-flight -- holding the lock across the
#    network call would serialize the parallel build. A rare concurrent double-miss
#    recomputes a key, but the file stays one-line-per-key and nothing is lost.)
def test_thread_safety_stress(tmp_path):
    path = str(tmp_path / "stress.jsonl")
    inner = FakeInner()
    client = CachingLLMClient(inner, path)
    prompts = [f"prompt-{i}" for i in range(200)]

    def hammer(p):
        return client.complete(p)

    with ThreadPoolExecutor(max_workers=16) as ex:
        futures = [ex.submit(hammer, p) for p in prompts for _ in range(4)]
        results = [f.result() for f in futures]

    assert all(r.startswith("complete::") for r in results)
    # every distinct prompt was computed at least once, and the cache stopped the
    # 800 concurrent calls from each hitting the model (well below one-per-call).
    assert len(prompts) <= inner.calls["complete"] < len(futures)

    # the file is well-formed (every line parses) and holds each key EXACTLY once,
    # covering every prompt -- no lost entries, no corruption despite the races.
    lines = [ln for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]
    keys = [json.loads(ln)["key"] for ln in lines]
    assert len(keys) == len(set(keys)) == len(prompts)

    # a fresh instance reloads all of them and never calls inner.
    inner2 = FakeInner()
    client2 = CachingLLMClient(inner2, path)
    for p in prompts:
        client2.complete(p)
    assert inner2.calls["complete"] == 0


def test_token_counters_passthrough_and_hits_are_free(tmp_path):
    client, inner = _cache(tmp_path)
    client.complete("costed")
    after_miss_in = client.input_tokens
    after_miss_out = client.output_tokens
    assert after_miss_in == inner.input_tokens == len("costed")
    assert after_miss_out == inner.output_tokens == 1
    # a HIT advances neither the inner counters nor the passthrough view.
    client.complete("costed")
    assert client.input_tokens == after_miss_in
    assert client.output_tokens == after_miss_out


def test_model_passthrough(tmp_path):
    client, inner = _cache(tmp_path, inner=FakeInner(model="gpt-4o-mini"))
    assert client.model == "gpt-4o-mini"


def test_complete_json_falls_back_when_inner_lacks_it(tmp_path):
    class OnlyComplete:
        model = "m"

        def __init__(self):
            self.n = 0

        def complete(self, prompt):
            self.n += 1
            return f"c::{prompt}"

    inner = OnlyComplete()
    client = CachingLLMClient(inner, str(tmp_path / "fb.jsonl"))
    assert client.complete_json("p") == "c::p"
    client.complete_json("p")  # cached
    assert inner.n == 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
