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


# The default self-hosted ER-matcher: Qwen2.5-1.5B-Instruct (Apache-2.0,
# redistribution-clean), 4-bit GGUF, published as a GitHub Release asset by the
# ``publish-er-matcher`` workflow. NOTE: Qwen2.5-3B is Qwen-Research-licensed
# (non-commercial), so the 3B fine-tune was WITHDRAWN -- 1.5B is the shipped base
# (measured stronger zero-shot anyway: walmart F1 0.795 vs the 3B's 0.721).
PINNED_MODEL = LocalModelSpec(
    url=(
        "https://github.com/benseverndev-oss/goldenmatch/releases/download/"
        "er-matcher-1.5b-v1.0.0/goldenmatch-er-matcher-1.5b-q4_k_m.gguf"
    ),
    filename="goldenmatch-er-matcher-1.5b-q4_k_m.gguf",
    sha256="64564eefd68373f5d1eddc064d21d24e9e172dd8d003e03255d10dfb09ce4ed0",
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


def _project(row: dict, columns: list[str] | None) -> dict:
    """Restrict a pipeline row to the caller's data columns (dropping ``__row_id__``
    and other internals) before serialization — the model was trained on the record
    fields only, so leaking internal columns into the prompt is out-of-distribution."""
    if columns is None:
        return row
    return {c: row.get(c) for c in columns}


def serialize_pair_v1(row_a: dict, row_b: dict, columns: list[str] | None = None) -> str:
    """The pinned ``serialize_pair_v1`` — SINGLE-SOURCED from
    ``core.er_matcher.prompt.serialize_pair_v1`` (the exact rendering the model was
    fine-tuned on) so Path A (served) and Path B (in-process) are byte-identical.

    ``columns`` selects which fields to render (the canonical serializer then orders
    them by the sorted key union); pass ``None`` to render every key. Having ONE v1
    rendering is what makes the registry serializer-version guard meaningful — a
    second hand-rolled ``v1`` that disagreed would feed the model OOD prompts the
    version-string check cannot catch.
    """
    from goldenmatch.core.er_matcher.prompt import serialize_pair_v1 as _canonical

    return _canonical(_project(row_a, columns), _project(row_b, columns))


def parse_verdict(text: str) -> tuple[bool, float]:
    """Lenient parse of the model's JSON verdict -> ``(is_match, confidence)``.

    Delegates to the canonical ``core.er_matcher.prompt.parse_verdict`` (one parser
    contract). A parse failure returns ``(False, 0.0)`` — abstain, never crash
    (mirrors the hosted fallback contract). ``confidence`` is clamped to ``[0, 1]``.
    """
    from goldenmatch.core.er_matcher.prompt import parse_verdict as _canonical

    v = _canonical(text)
    if v is None:
        return False, 0.0
    return bool(v["match"]), max(0.0, min(1.0, float(v["confidence"])))


class LocalLlamaAdapter:
    """In-process adapter over a llama.cpp GGUF model (lazy — only built when a
    model actually loads). Implements :class:`LocalLLMAdapter`."""

    def __init__(self, llm) -> None:  # llm: llama_cpp.Llama (untyped — optional dep)
        self._llm = llm

    def score_pair(
        self, row_a: dict, row_b: dict, columns: list[str]
    ) -> tuple[bool, float]:
        # build_chat single-sources BOTH the system rubric and the user-turn
        # serialization from core.er_matcher.prompt — the same messages the model
        # was fine-tuned on (project to the data columns first, see _project).
        from goldenmatch.core.er_matcher.prompt import build_chat

        messages = build_chat(_project(row_a, columns), _project(row_b, columns))
        try:
            resp = self._llm.create_chat_completion(
                messages=messages,
                max_tokens=64,
                temperature=0.0,
            )
            text = resp["choices"][0]["message"]["content"]
        except Exception as e:  # noqa: BLE001 - inference failure -> abstain
            logger.warning("local LLM inference failed: %s", e)
            return False, 0.0
        return parse_verdict(text)

    def score_and_explain(
        self, row_a: dict, row_b: dict, columns: list[str],
        *, weights: dict[str, float] | None = None,
        counterfactuals: bool = False,
    ):
        """Score the pair AND attach a field-grounded rationale.

        Returns ``(is_match, confidence, PairExplanation)``. The explanation is
        built from the model's OWN learned field-importance (the Layer-2 abstraction
        of the causally-validated match direction — see
        ``core.er_matcher.explainer``), so it reflects what actually moves the
        model's decision, to a stated faithfulness bound.

        ``counterfactuals=True`` additionally re-scores the pair once per column
        with that column blanked on BOTH records, attaching measured
        :class:`Counterfactual` entries — "removing birth_place would reverse this
        verdict". That is a direct causal claim about this decision rather than a
        corpus statistic, but it costs ``len(columns)`` extra inference calls, so
        it is OFF by default and intended for a human review queue. Measured on
        person data, only ~19% of pairs have any single-field flip; the model
        integrates redundant evidence, so "no single field decides this" is the
        normal answer, not a failure.
        """
        from goldenmatch.core.er_matcher.explainer import explain_pair

        match, conf = self.score_pair(row_a, row_b, columns)
        p_without: dict[str, float] | None = None
        if counterfactuals:
            p_without = {}
            for f in columns:
                # blank on BOTH sides -> the prompt's "(missing)" sentinel, which
                # the system rubric trains the model to ignore rather than treat
                # as a conflict. Removes the evidence in-distribution.
                a_wo = dict(row_a) | {f: None}
                b_wo = dict(row_b) | {f: None}
                m_wo, c_wo = self.score_pair(a_wo, b_wo, columns)
                # score_pair returns (verdict, confidence-in-that-verdict); convert
                # to a comparable P(match) so deltas are on one axis.
                p_without[f] = c_wo if m_wo else 1.0 - c_wo
        explanation = explain_pair(
            row_a, row_b, columns, match=match, confidence=conf, weights=weights,
            p_without=p_without,
        )
        return match, conf, explanation
