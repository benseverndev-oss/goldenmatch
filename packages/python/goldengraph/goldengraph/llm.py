"""Provider-agnostic LLM boundary for goldengraph extraction.

Tests inject a deterministic stub; the optional `OpenAIClient` (behind the
`[openai]` extra) is the only real provider shipped. goldengraph owns this
minimal protocol rather than coupling to goldenmatch's internal LLM client, so
extraction stays testable + provider-swappable.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Any, Protocol


class LLMClient(Protocol):
    """A single-shot text completion: prompt in, raw model text out."""

    def complete(self, prompt: str) -> str: ...

    # Optional: a JSON-constrained completion for structured extraction. Callers MUST
    # feature-detect with `hasattr(llm, "complete_json")` and fall back to `complete`
    # -- test stubs and the pure protocol need not implement it.
    # def complete_json(self, prompt: str) -> str: ...

    # Optional: N independent samples at a temperature, for synthesis self-consistency.
    # Callers feature-detect with `hasattr(llm, "complete_many")` and fall back to `complete`.
    # def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]: ...


class OpenAIClient:
    """Minimal OpenAI adapter (optional `[openai]` extra). Reuses goldenmatch's
    `BudgetTracker` to cap spend when one is supplied."""

    def __init__(self, model: str = "gpt-4o-mini", *, budget=None, client=None):
        self.model = model
        self.budget = budget
        self._client = client  # injectable for tests; else lazily built

    def _ensure_client(self):
        if self._client is None:
            import os

            import openai  # lazy: only needed for the real provider

            # CHAT-specific provider overrides (GOLDENGRAPH_LLM_*), falling back to the
            # generic OPENAI_* env. This lets goldengraph's CHAT (extraction + synthesis)
            # target a different provider -- e.g. OpenRouter to dodge an OpenAI per-model
            # daily cap -- WITHOUT moving the embedder, which is a bare `OpenAI()` reading
            # OPENAI_BASE_URL/OPENAI_API_KEY (and OpenRouter serves no embeddings endpoint).
            # Unset GOLDENGRAPH_LLM_* -> byte-identical to the prior OPENAI_*-only behavior.
            #
            # The bench workflow sets OPENAI_BASE_URL='' on the OpenAI-API path (it
            # is only non-empty for a local Ollama run). The openai SDK treats an
            # empty-string base_url as a literal (invalid) URL -> APIConnectionError,
            # and passing base_url=None does NOT help -- the SDK re-reads the empty
            # env var itself. So pass an explicit default when the env is empty.
            base_url = (
                os.environ.get("GOLDENGRAPH_LLM_BASE_URL")
                or os.environ.get("OPENAI_BASE_URL")
                or "https://api.openai.com/v1"
            )
            api_key = (
                os.environ.get("GOLDENGRAPH_LLM_API_KEY")
                or os.environ.get("OPENAI_API_KEY")
                or None
            )
            self._client = openai.OpenAI(base_url=base_url, api_key=api_key)
        return self._client

    def complete(self, prompt: str) -> str:
        return self._chat(prompt, json_mode=False)

    def complete_json(self, prompt: str) -> str:
        """Like `complete` but constrains the model to a single JSON object
        (`response_format=json_object`). Forces valid extraction JSON from weaker
        models that otherwise emit prose/fenced/invalid output (the small-OSS-model
        failure mode). Honored by OpenAI + Ollama's OpenAI-compatible endpoint."""
        return self._chat(prompt, json_mode=True)

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        """N independent completions at `temperature` (for synthesis self-consistency).
        A LOOP of single calls -- Ollama's OpenAI-compatible endpoint does not reliably
        honor the `n=` param -- each token-tracked through `_chat`'s budget path."""
        return [self._chat(prompt, temperature=temperature) for _ in range(max(1, n))]

    def _chat(self, prompt: str, *, json_mode: bool = False, temperature: float = 0) -> str:
        client = self._ensure_client()
        kwargs = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
        }
        # Optional fixed seed for reproducible decoding (GOLDENGRAPH_LLM_SEED). Ollama's
        # OpenAI-compatible endpoint honors `seed` + temperature=0; unset/non-int -> omitted
        # (unchanged behavior). Reduces run-to-run extraction variance for bench determinism.
        _raw_seed = os.environ.get("GOLDENGRAPH_LLM_SEED", "").strip()
        if _raw_seed:
            try:
                kwargs["seed"] = int(_raw_seed)
            except ValueError:
                pass
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        resp = client.chat.completions.create(**kwargs)
        text = resp.choices[0].message.content or ""
        if self.budget is not None:
            # Best-effort spend accounting; BudgetTracker raises if over cap.
            usage = getattr(resp, "usage", None)
            if usage is not None:
                self.budget.record_call(
                    getattr(usage, "prompt_tokens", 0),
                    getattr(usage, "completion_tokens", 0),
                )
        return text


#: Sentinel distinguishing "no cache entry" from a legitimately cached falsy value
#: (a model can return "" and it must be served, not re-fetched).
_MISS = object()


class CachingLLMClient:
    """Persistent, prompt-hash-keyed response cache in front of any `LLMClient`.

    Why this is SAFE for a measurement harness: the key is a stable hash of the
    EXACT `(model, method, prompt, sorted(kwargs))` tuple, so a cached response IS
    the model's output for that exact prompt+model. Serving it can never be stale
    while the prompt matches, and any extraction-code change that alters a prompt
    yields a NEW key automatically (a miss that repopulates) -- so the cache cannot
    corrupt a re-run's measurement. All deterministic post-processing (parsing,
    resolution) still runs on the cached raw response.

    This is a CAPABILITY, not a library default: goldengraph never installs it on
    its own. Only the bench build path wraps its extraction client with it when the
    `GOLDENGRAPH_LLM_CACHE` env var points at a backing file. With no path it is a
    transparent passthrough (byte-identical to the unwrapped client, zero files).

    On a cache HIT the inner client is NOT called, so it incurs no API cost and the
    inner token counters do not advance; `.input_tokens`/`.output_tokens`/`.model`
    are read-through views of the inner client so callers keep working.

    `complete_many` with `temperature > 0` is nondeterministic sampling and is NOT
    cached (freezing a sampled distribution would be wrong) -- it always calls the
    inner client. Extraction runs `complete`/`complete_json` at temperature 0, so
    this only spares the self-consistency synthesis path.

    Backing store: JSON-lines (`{"key": <sha256>, "resp": <str|list[str]>}` per
    line), loaded on init and written through per put under a lock. Thread-safe --
    `ingest_corpus` parallelizes extraction across documents, so get/put and the
    file append are all guarded. A corrupt/partial file is treated as empty and
    overwritten rather than crashing the build.
    """

    def __init__(self, inner: Any, path: str | None):
        self._inner = inner
        # Empty/None path -> caching DISABLED (transparent passthrough, no file).
        self._path = str(path) if path else None
        # Model participates in the key so two runs against different models never
        # collide; captured once (the inner client's model is fixed per instance).
        self._model = getattr(inner, "model", "") or ""
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        if self._path:
            self._load()

    # --- read-through views onto the inner client (cache hits stay cost-free) ---
    @property
    def model(self) -> str:
        return self._model

    @property
    def input_tokens(self) -> int:
        return getattr(self._inner, "input_tokens", 0)

    @property
    def output_tokens(self) -> int:
        return getattr(self._inner, "output_tokens", 0)

    # --- LLMClient protocol ---
    def complete(self, prompt: str) -> str:
        return self._cached("complete", prompt, self._inner.complete)

    def complete_json(self, prompt: str) -> str:
        # Mirror `_CountingLLM`: fall back to `complete` (and its key) when the inner
        # client has no JSON-constrained method (test stubs / bare clients), so the
        # cached response and its key both reflect what was actually invoked.
        fn = getattr(self._inner, "complete_json", None)
        if fn is None:
            return self._cached("complete", prompt, self._inner.complete)
        return self._cached("complete_json", prompt, fn)

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        if self._path is None or (temperature and temperature > 0):
            # Disabled -> transparent passthrough; temperature>0 is nondeterministic
            # sampling and must never be frozen in the cache.
            return self._inner.complete_many(prompt, n=n, temperature=temperature)
        key = self._key("complete_many", prompt, n=n, temperature=temperature)
        hit = self._get(key)
        if hit is not _MISS:
            return list(hit)  # defensive copy so a caller can't mutate the cache
        resp = list(self._inner.complete_many(prompt, n=n, temperature=temperature))
        self._put(key, resp)
        return resp

    # --- internals ---
    def _cached(self, method: str, prompt: str, call) -> str:
        if self._path is None:
            # Disabled -> transparent passthrough (no hashing/locking, no file).
            return call(prompt)
        key = self._key(method, prompt)
        hit = self._get(key)
        if hit is not _MISS:
            return hit
        resp = call(prompt)
        self._put(key, resp)
        return resp

    def _key(self, method: str, prompt: str, **kwargs: Any) -> str:
        payload = json.dumps(
            [self._model, method, prompt, sorted(kwargs.items())],
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _get(self, key: str) -> Any:
        with self._lock:
            return self._cache.get(key, _MISS)

    def _put(self, key: str, resp: Any) -> None:
        with self._lock:
            if key in self._cache:
                return  # a concurrent writer already stored it -> one file line/key
            self._cache[key] = resp
            if self._path:
                try:
                    with open(self._path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps({"key": key, "resp": resp}, ensure_ascii=True) + "\n")
                except OSError:
                    pass  # never let a cache write failure break the build

    def _load(self) -> None:
        if not self._path or not os.path.exists(self._path):
            return
        try:
            cache: dict[str, Any] = {}
            with open(self._path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    rec = json.loads(line)
                    cache[rec["key"]] = rec["resp"]
            self._cache = cache
        except (OSError, ValueError, KeyError, TypeError):
            # Corrupt or partially-written file: treat as empty and overwrite it so
            # subsequent write-through appends start from a clean, well-formed file.
            self._cache = {}
            try:
                with open(self._path, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass
