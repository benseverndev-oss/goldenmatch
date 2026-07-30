"""Tests for the self-hosted local LLM adapter + loader (D1 Path B).

No model or network: the loader's gate/discover/verify logic is exercised with a
fake path + monkeypatched download, and the ``provider="local"`` scoring path is
driven by an injected stub adapter.
"""

from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.core import _llm_loader as L
from goldenmatch.core.llm_scorer import llm_score_pairs


class _StubAdapter:
    """Deterministic in-process adapter: matches when both names are equal."""

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def score_pair(self, row_a, row_b, columns):
        self.calls.append((row_a["__row_id__"], row_b["__row_id__"]))
        return (row_a["name"] == row_b["name"], 0.99)


def _df():
    return pl.DataFrame({
        "__row_id__": [0, 1, 2, 3],
        "name": ["Ann", "Ann", "Bob", "Zed"],
    })


# ── serializer + verdict parsing ───────────────────────────────────────────────


class TestSerializerAndParse:
    def test_serialize_pair_v1_is_deterministic_field_order(self):
        s = L.serialize_pair_v1({"b": 2, "a": 1}, {"a": 9, "b": 8}, ["a", "b"])
        assert s == "Record A:\na: 1\nb: 2\n\nRecord B:\na: 9\nb: 8"

    def test_parse_verdict_plain_json(self):
        assert L.parse_verdict('{"match": true, "confidence": 0.9}') == (True, 0.9)

    def test_parse_verdict_embedded_json(self):
        assert L.parse_verdict('Sure! {"match": false, "confidence": 0.2} done') == (False, 0.2)

    def test_parse_verdict_clamps_confidence(self):
        assert L.parse_verdict('{"match": true, "confidence": 5}') == (True, 1.0)

    def test_parse_verdict_garbage_abstains(self):
        assert L.parse_verdict("not json at all") == (False, 0.0)


# ── loader gate + discover order ───────────────────────────────────────────────


class TestLoader:
    def test_mode_0_abstains(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM", "0")
        assert L.load_local_adapter() is None

    def test_override_path_missing_abstains(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_PATH", "/no/such/model.gguf")
        assert L.resolve_model_path() is None

    def test_override_path_present_no_sha_returns_it(self, monkeypatch, tmp_path):
        p = tmp_path / "model.gguf"
        p.write_bytes(b"gguf-bytes")
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_PATH", str(p))
        assert L.resolve_model_path() == str(p)

    def test_override_path_sha_mismatch_raises(self, monkeypatch, tmp_path):
        p = tmp_path / "model.gguf"
        p.write_bytes(b"gguf-bytes")
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_PATH", str(p))
        spec = L.LocalModelSpec(url="https://example.test/m.gguf", filename="m.gguf", sha256="deadbeef")
        with pytest.raises(L.LocalLLMUnavailableError):
            L.resolve_model_path(spec)

    def test_override_path_sha_match_returns_it(self, monkeypatch, tmp_path):
        import hashlib
        p = tmp_path / "model.gguf"
        p.write_bytes(b"gguf-bytes")
        digest = hashlib.sha256(b"gguf-bytes").hexdigest()
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_PATH", str(p))
        spec = L.LocalModelSpec(url="https://example.test/m.gguf", filename="m.gguf", sha256=digest)
        assert L.resolve_model_path(spec) == str(p)

    def test_mode_1_raises_when_unresolvable(self, monkeypatch):
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM", "1")
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_PATH", "/no/such/model.gguf")
        with pytest.raises(L.LocalLLMUnavailableError):
            L.load_local_adapter()

    # ── GitHub Release cache + download path ───────────────────────────────────

    def test_cache_hit_returns_cached(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GOLDENMATCH_LOCAL_LLM_PATH", raising=False)
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_CACHE", str(tmp_path))
        (tmp_path / "m.gguf").write_bytes(b"cached-bytes")
        spec = L.LocalModelSpec(url="https://example.test/m.gguf", filename="m.gguf")
        assert L.resolve_model_path(spec) == str(tmp_path / "m.gguf")

    def _fake_urlopen(self, monkeypatch, payload: bytes):
        import contextlib
        import io

        @contextlib.contextmanager
        def _open(url, *a, **k):
            yield io.BytesIO(payload)

        monkeypatch.setattr("urllib.request.urlopen", _open)

    def test_download_verifies_and_atomically_caches(self, monkeypatch, tmp_path):
        import hashlib

        monkeypatch.delenv("GOLDENMATCH_LOCAL_LLM_PATH", raising=False)
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_CACHE", str(tmp_path))
        payload = b"downloaded-gguf-bytes"
        self._fake_urlopen(monkeypatch, payload)
        spec = L.LocalModelSpec(
            url="https://example.test/m.gguf", filename="m.gguf",
            sha256=hashlib.sha256(payload).hexdigest(),
        )
        path = L.resolve_model_path(spec)
        assert path == str(tmp_path / "m.gguf")
        assert (tmp_path / "m.gguf").read_bytes() == payload
        # no leftover .part temp files
        assert not list(tmp_path.glob("*.part"))

    def test_download_sha_mismatch_raises_and_leaves_no_cache(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GOLDENMATCH_LOCAL_LLM_PATH", raising=False)
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_CACHE", str(tmp_path))
        self._fake_urlopen(monkeypatch, b"downloaded-gguf-bytes")
        spec = L.LocalModelSpec(url="https://example.test/m.gguf", filename="m.gguf", sha256="deadbeef")
        with pytest.raises(L.LocalLLMUnavailableError):
            L.resolve_model_path(spec)
        assert not (tmp_path / "m.gguf").exists()
        assert not list(tmp_path.glob("*.part"))

    def test_download_unreachable_abstains(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GOLDENMATCH_LOCAL_LLM_PATH", raising=False)
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM_CACHE", str(tmp_path))

        def _boom(url, *a, **k):
            raise OSError("no network")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        spec = L.LocalModelSpec(url="https://example.test/m.gguf", filename="m.gguf")
        assert L.resolve_model_path(spec) is None
        assert not list(tmp_path.glob("*.part"))


# ── provider="local" scoring path ──────────────────────────────────────────────


class TestLocalScoringPath:
    def test_injected_adapter_scores_the_band(self):
        stub = _StubAdapter()
        pairs = [
            (0, 1, 0.80),  # in band, same name -> promoted to 1.0
            (2, 3, 0.80),  # in band, different name -> keeps 0.80
            (0, 2, 0.99),  # auto-accept -> 1.0 (no adapter call)
            (1, 3, 0.50),  # below band -> untouched
        ]
        out = llm_score_pairs(pairs, _df(), provider="local", local_adapter=stub)
        assert out[0] == (0, 1, 1.0)
        assert out[1] == (2, 3, 0.80)
        assert out[2] == (0, 2, 1.0)
        assert out[3] == (1, 3, 0.50)
        # Only the two in-band pairs hit the adapter; auto-accept/below did not.
        assert set(stub.calls) == {(0, 1), (2, 3)}

    def test_no_adapter_abstains_gracefully(self, monkeypatch):
        # Force the loader off so provider='local' finds no adapter.
        monkeypatch.setenv("GOLDENMATCH_LOCAL_LLM", "0")
        pairs = [(0, 1, 0.80), (0, 2, 0.99)]
        out = llm_score_pairs(pairs, _df(), provider="local")
        assert out == pairs  # unchanged, no crash
