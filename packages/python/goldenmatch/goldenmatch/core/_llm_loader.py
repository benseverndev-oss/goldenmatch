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
2. the Hugging Face cache (already downloaded).
3. download-on-first-use via ``huggingface_hub`` from the pinned
   ``(repo_id, revision, filename)``.
4. abstain (``None``).

Pin + verify: when :data:`PINNED_MODEL` carries a ``sha256`` the resolved file is
checksum-verified (never-black-box — the exact bytes are pinned). The model
artifact itself is NOT in the git tree; it lives on the Hugging Face Hub (see the
companion spec ``2026-07-26-oss-er-matcher-llm-boost-design.md`` §5).

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
    """A pinned local model artifact (never-black-box: exact bytes are pinned)."""

    repo_id: str
    revision: str
    filename: str
    sha256: str | None = None  # None -> not yet pinned; verification skipped.


# The default self-hosted ER-matcher (companion spec §4: Qwen2.5-3B, Apache-2.0,
# 4-bit GGUF). Placeholder coordinates until the trained model is published to the
# Hub (companion spec P3); `sha256=None` skips verification until the real bytes
# are pinned. The LOADER LOGIC below is complete and tested regardless of whether
# this artifact exists yet — a missing repo simply abstains.
PINNED_MODEL = LocalModelSpec(
    repo_id="benseverndev-oss/goldenmatch-er-matcher-3b",
    revision="main",
    filename="goldenmatch-er-matcher-3b.q4_k_m.gguf",
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
    ) -> tuple[bool, float]: ...


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


def resolve_model_path(spec: LocalModelSpec = PINNED_MODEL) -> str | None:
    """Resolve the GGUF path via the discover order, or ``None`` to abstain.

    Never raises for a plain "not reachable" (missing extra, offline, unknown
    repo) — those all abstain. Only a genuine integrity failure (checksum
    mismatch) raises, because a corrupt pinned artifact is an anomaly, not an
    absence.
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

    # 2/3. HF cache hit, else download-on-first-use. Both go through
    # huggingface_hub.hf_hub_download, which returns a cached path when present
    # and downloads otherwise. Absence of the extra / network -> abstain.
    try:
        from huggingface_hub import hf_hub_download  # pyright: ignore[reportMissingImports]
    except Exception:  # noqa: BLE001 - extra not installed -> abstain
        logger.info(
            "huggingface_hub not installed; local LLM abstains "
            "(pip install goldenmatch[local-llm])."
        )
        return None

    try:
        path = hf_hub_download(
            repo_id=spec.repo_id, revision=spec.revision, filename=spec.filename
        )
    except Exception as e:  # noqa: BLE001 - unreachable repo / offline -> abstain
        logger.info("local model not reachable (%s); abstaining.", e)
        return None

    if spec.sha256:
        _verify_sha256(path, spec.sha256)
    return path


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
