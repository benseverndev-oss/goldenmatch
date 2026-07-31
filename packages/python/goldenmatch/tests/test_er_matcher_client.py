"""Tests for the local ER-matcher client + model registry (P6).

No server, no weights: the HTTP transport is stubbed and the registry is exercised
on its verify/refuse logic. Locks the Path-A contract (chat -> POST -> verdict)
and the abstain-on-failure guarantee."""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from goldenmatch.core.er_matcher import registry
from goldenmatch.core.er_matcher.client import LocalMatcherClient


def _completion(content: str) -> dict:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


def test_client_builds_chat_and_parses_verdict():
    seen = {}

    def stub_post(url, payload, timeout):
        seen["url"] = url
        seen["payload"] = payload
        return _completion('{"match": true, "confidence": 0.91, "reason": "emails agree"}')

    c = LocalMatcherClient(base_url="http://x/v1", post_fn=stub_post)
    v = c.score_pair({"email": "a@b.com"}, {"email": "a@b.com"})
    assert v == {"match": True, "confidence": 0.91, "reason": "emails agree"}
    # It hit the OpenAI-compatible endpoint with a system+user chat.
    assert seen["url"] == "http://x/v1/chat/completions"
    roles = [m["role"] for m in seen["payload"]["messages"]]
    assert roles == ["system", "user"]
    assert "Record A:" in seen["payload"]["messages"][1]["content"]


def test_client_abstains_on_transport_error():
    def boom(url, payload, timeout):
        raise ConnectionError("server down")

    assert LocalMatcherClient(post_fn=boom).score_pair({}, {}) is None


def test_client_abstains_on_garbage_completion():
    c = LocalMatcherClient(post_fn=lambda u, p, t: _completion("I think they match, probably"))
    assert c.score_pair({"x": "1"}, {"x": "1"}) is None


def test_client_abstains_on_malformed_response():
    c = LocalMatcherClient(post_fn=lambda u, p, t: {"unexpected": "shape"})
    assert c.score_pair({}, {}) is None


def test_score_pairs_batch():
    c = LocalMatcherClient(post_fn=lambda u, p, t: _completion('{"match": false, "confidence": 0.1}'))
    out = c.score_pairs([({"x": "1"}, {"x": "2"}), ({"x": "3"}, {"x": "4"})])
    assert len(out) == 2 and all(v["match"] is False for v in out)


# --- registry ---------------------------------------------------------------
def test_registry_3b_published_7b_pending():
    # 3b is published (er-matcher-3b-v1.0.0 GitHub Release, pinned url+sha256);
    # 7b weights still ship only after its eval gate, so it stays PENDING.
    assert registry.MODELS["3b"].published is True
    assert registry.MODELS["7b"].published is False


def test_resolve_refuses_unpublished():
    # 7b is still PENDING (no url/sha256) -> resolve refuses (can't verify what
    # isn't pinned). 3b is published now, so use 7b for the refusal check.
    with pytest.raises(ValueError, match="not published"):
        registry.resolve_model("7b")


def test_resolve_unknown_tier():
    with pytest.raises(ValueError, match="unknown"):
        registry.resolve_model("999b")


def test_verify_detects_tamper(tmp_path: Path):
    f = tmp_path / "m.gguf"
    f.write_bytes(b"hello weights")
    good = hashlib.sha256(b"hello weights").hexdigest()
    registry.verify(f, good)  # no raise
    with pytest.raises(ValueError, match="sha256 mismatch"):
        registry.verify(f, "0" * 64)


def test_serializer_drift_refused(monkeypatch):
    # A model built for a different serializer version must be refused (retrain).
    spec = registry.MODELS["3b"]
    drifted = registry.ModelSpec(
        tier=spec.tier, filename=spec.filename, url="https://x/m.gguf",
        sha256="a" * 64, base_model=spec.base_model, serializer_version="v0",
    )
    monkeypatch.setitem(registry.MODELS, "3b", drifted)
    with pytest.raises(ValueError, match="serializer drift"):
        registry.resolve_model("3b")


# --- serve_local launcher ---------------------------------------------------
def test_serve_argv_shape():
    from goldenmatch.core.er_matcher import serve_local
    argv = serve_local.build_server_argv(Path("/models/m.gguf"), "127.0.0.1", 8080)
    assert "llama_cpp.server" in argv
    assert argv[argv.index("--model") + 1] == "/models/m.gguf"
    assert argv[argv.index("--port") + 1] == "8080"
    assert argv[argv.index("--chat_format") + 1] == "chatml"  # Qwen2.5


def test_serve_main_hints_when_server_absent():
    # No llama-cpp-python in the dev sandbox -> main returns 2 with an install hint
    # BEFORE touching the (still-PENDING) model download.
    from goldenmatch.core.er_matcher import serve_local
    if serve_local._have_server():
        pytest.skip("llama_cpp installed; the no-server path isn't exercised here")
    assert serve_local.main(["--tier", "3b"]) == 2
