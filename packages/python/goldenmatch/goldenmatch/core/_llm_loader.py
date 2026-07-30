"""Loader + gate for the optional self-hosted local LLM adapter (D1 Path B).

Mirrors ``core/_native_loader.py``: one env gate, a discover order, an artifact
pin + verify, and **graceful abstain** — a missing extra / model returns ``None``
rather than raising into a pipeline run (the same contract as the LLM scorer's
"no API key -> unscored" fallback).

``GOLDENMATCH_LOCAL_LLM`` env:
- ``"0"``             -> force off; ``load_local_adapter`` returns ``None`` (abstain).
- ``"1"``             -> require; raise ``LocalLLMUnavailableError`` if it can't load.
- ``"auto"`` / unset  -> load if resolvable (extra installed + model reachable),
  else abstain.

Discover order for the GGUF model file (first hit wins):
1. ``GOLDENMATCH_LOCAL_LLM_PATH`` — an explicit local path override.
2. the local model cache (already downloaded; ``~/.cache/goldenmatch/models`` or
   ``GOLDENMATCH_LOCAL_LLM_CACHE``).
3. download-on-first-use from the pinned **GitHub Release asset URL** (stdlib
   ``urllib`` — no auth for the public repo), verified then atomically cached.
4. abstain (``None``).

Pin + verify: when :data:`PINNED_MODEL` carries a ``sha256`` the resolved file is
checksum-verified (never-black-box — the exact bytes are pinned). The model
artifact itself is NOT in the git tree; it is published as a **GitHub Release
asset** (uploaded from the training box via the ``publish-er-matcher``
workflow), which the ``goldenmatch[local-llm]`` install pulls from GitHub — no
Hugging Face Hub dependency. See the companion spec
``2026-07-26-oss-er-matcher-llm-boost-design.md`` §5.

The in-process adapter and its prompt/serializer are the same contract Path A's
local OpenAI-compatible server uses, so the model artifact is identical across
both paths.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class LocalLLMUnavailableError(RuntimeError):
    """Raised only under ``GOLDENMATCH_LOCAL_LLM=1`` when the adapter can't load."""


@dataclass(frozen=True)
class LocalModelSpec:
    """A pinned local model artifact (never-black-box: exact bytes are pinned).

    Hosted as a **GitHub Release asset**: ``url`` is the public asset download URL
    (``.../releases/download/<tag>/<file>``), ``filename`` the cached local name,
    ``sha256`` the integrity pin (``None`` until the artifact is published + pinned).
    """

    url: str
    filename: str
    sha256: str | None = None  # None -> not yet pinned; verification skipped.


# The default self-hosted ER-matcher (companion spec §4: Qwen2.5-3B, Apache-2.0,
# 4-bit GGUF), published as a GitHub Release asset by the
# ``publish-er-matcher`` workflow. Placeholder URL + `sha256=None` until the
# trained model is uploaded and pinned (companion spec P3); the LOADER LOGIC below
# is complete and tested regardless of whether this asset exists yet — an
# unreachable URL simply abstains.
PINNED_MODEL = LocalModelSpec(
    url=(
        "https://github.com/benseverndev-oss/goldenmatch/releases/download/"
        "er-matcher-3b-v1.0.0/goldenmatch-er-matcher-3b-q4_k_m.gguf"
    ),
    filename="goldenmatch-er-matcher-3b-q4_k_m.gguf",
    sha256=None,
)


@runtime_checkable
class LocalLLMAdapter(Protocol):
    """The in-process pairwise-adjudication contract.

    ``score_pair`` returns ``(is_match, confidence)`` for two records. Mirrors the
    hosted tier's per-pair yes/no + confidence, so the closed-loop refit (D2) and
    the review queue consume it identically.
    """

    def score_pair(
        self, row_a: dict, row_b: dict, columns: list[str]
    ) -> tuple[bool, float]:
        """Return ``(is_match, confidence)`` for the two records."""


def _local_llm_mode() -> str:
    return os.environ.get("GOLDENMATCH_LOCAL_LLM", "auto").strip().lower()


def _verify_sha256(path: str, expected: str) -> None:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    actual = h.hexdigest()
    if actual != expected:
        raise LocalLLMUnavailableError(
            f"local model checksum mismatch for {path}: expected {expected}, got {actual}"
        )


def _model_cache_dir() -> str:
    """Directory the downloaded GGUF is cached in (override: ``GOLDENMATCH_LOCAL_LLM_CACHE``)."""
    override = os.environ.get("GOLDENMATCH_LOCAL_LLM_CACHE")
    if override:
        return override
    return os.path.join(os.path.expanduser("~"), ".cache", "goldenmatch", "models")


def resolve_model_path(spec: LocalModelSpec = PINNED_MODEL) -> str | None:
    """Resolve the GGUF path via the discover order, or ``None`` to abstain.

    Never raises for a plain "not reachable" (offline, unknown asset) — those all
    abstain. Only a genuine integrity failure (checksum mismatch) raises, because
    a corrupt pinned artifact is an anomaly, not an absence.
    """
    # 1. Explicit local override.
    override = os.environ.get("GOLDENMATCH_LOCAL_LLM_PATH")
    if override:
        if os.path.exists(override):
            if spec.sha256:
                _verify_sha256(override, spec.sha256)
            return override
        logger.warning("GOLDENMATCH_LOCAL_LLM_PATH=%s does not exist; abstaining.", override)
        return None

    # 2. Cache hit (already downloaded).
    cache_dir = _model_cache_dir()
    cached = os.path.join(cache_dir, spec.filename)
    if os.path.exists(cached):
        if spec.sha256:
            _verify_sha256(cached, spec.sha256)
        return cached

    # 3. Download-on-first-use from the pinned GitHub Release asset URL. Stream to
    # a temp file, verify, then atomically publish into the cache — a partial or
    # corrupt download never poisons the cache. Any network error -> abstain.
    if not spec.url:
        return None
    import shutil
    import tempfile
    import urllib.request

    os.makedirs(cache_dir, exist_ok=True)
    tmp_path: str | None = None
    try:
        fd, tmp_path = tempfile.mkstemp(dir=cache_dir, suffix=".part")
        os.close(fd)
        with urllib.request.urlopen(spec.url) as resp, open(tmp_path, "wb") as out:  # noqa: S310 - pinned https asset URL
            shutil.copyfileobj(resp, out)
    except Exception as e:  # noqa: BLE001 - unreachable asset / offline -> abstain
        logger.info("local model not reachable (%s); abstaining.", e)
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return None

    if spec.sha256:
        try:
            _verify_sha256(tmp_path, spec.sha256)
        except LocalLLMUnavailableError:
            os.unlink(tmp_path)  # never keep corrupt bytes in the cache
            raise
    os.replace(tmp_path, cached)
    return cached


def load_local_adapter(
    spec: LocalModelSpec = PINNED_MODEL,
) -> LocalLLMAdapter | None:
    """Load the in-process local adapter, honoring ``GOLDENMATCH_LOCAL_LLM``.

    Returns an adapter, or ``None`` to abstain (mode ``0``, or ``auto`` with the
    model/extra unavailable). Raises :class:`LocalLLMUnavailableError` only under
    mode ``1`` when it cannot load.
    """
    mode = _local_llm_mode()
    if mode == "0":
        return None

    path = resolve_model_path(spec)
    if path is None:
        if mode == "1":
            raise LocalLLMUnavailableError(
                "GOLDENMATCH_LOCAL_LLM=1 but no local model could be resolved "
                "(install goldenmatch[local-llm] and ensure the pinned model is "
                "reachable, or set GOLDENMATCH_LOCAL_LLM_PATH)."
            )
        return None

    try:
        from llama_cpp import Llama  # pyright: ignore[reportMissingImports]
    except Exception as e:  # noqa: BLE001 - extra not installed
        if mode == "1":
            raise LocalLLMUnavailableError(
                "GOLDENMATCH_LOCAL_LLM=1 but llama-cpp-python is not installed "
                "(pip install goldenmatch[local-llm])."
            ) from e
        logger.info("llama-cpp-python not installed; local LLM abstains.")
        return None

    n_gpu_layers = int(os.environ.get("GOLDENMATCH_LOCAL_LLM_GPU_LAYERS", "0"))
    llm = Llama(model_path=path, n_gpu_layers=n_gpu_layers, verbose=False)
    return LocalLlamaAdapter(llm)


# ── serializer + adapter (shared contract with Path A) ─────────────────────────


def serialize_pair_v1(row_a: dict, row_b: dict, columns: list[str]) -> str:
    """Render two records for the model — the pinned ``serialize_pair_v1``.

    Deterministic field order (the caller's ``columns``), ``field: value`` lines.
    The serializer version is part of the model card; changing it means a new
    model revision (companion spec §8).
    """
    def _fmt(row: dict) -> str:
        return "\n".join(f"{c}: {row.get(c, '')}" for c in columns)

    return f"Record A:\n{_fmt(row_a)}\n\nRecord B:\n{_fmt(row_b)}"


_SYSTEM_PROMPT = (
    "You are an entity-resolution adjudicator. Decide whether Record A and "
    "Record B describe the SAME real-world entity. Weigh agreements and "
    "conflicts; a missing value is not a conflict. Reply with compact JSON only: "
    '{"match": true|false, "confidence": 0.0-1.0}.'
)


def parse_verdict(text: str) -> tuple[bool, float]:
    """Lenient parse of the model's JSON verdict -> ``(is_match, confidence)``.

    A parse failure returns ``(False, 0.0)`` — abstain, never crash (mirrors the
    hosted fallback contract). ``confidence`` is clamped to ``[0, 1]``.
    """
    import json
    import re

    try:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        obj = json.loads(m.group(0) if m else text)
        is_match = bool(obj.get("match", False))
        conf = float(obj.get("confidence", 0.0))
        return is_match, max(0.0, min(1.0, conf))
    except Exception:  # noqa: BLE001 - any malformed output -> abstain
        return False, 0.0


class LocalLlamaAdapter:
    """In-process adapter over a llama.cpp GGUF model (lazy — only built when a
    model actually loads). Implements :class:`LocalLLMAdapter`."""

    def __init__(self, llm) -> None:  # llm: llama_cpp.Llama (untyped — optional dep)
        self._llm = llm

    def score_pair(
        self, row_a: dict, row_b: dict, columns: list[str]
    ) -> tuple[bool, float]:
        prompt = serialize_pair_v1(row_a, row_b, columns)
        try:
            resp = self._llm.create_chat_completion(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=64,
                temperature=0.0,
            )
            text = resp["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 - inference failure -> abstain
            logger.warning("local LLM inference failed: %s", e)
            return False, 0.0
        return parse_verdict(text)
