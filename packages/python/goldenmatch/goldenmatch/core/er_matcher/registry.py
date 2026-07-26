"""Pinned model registry for the OSS ER-matcher (P6).

The repo holds ONLY the pin (URL + sha256), never the weights (spec §5). On first
use, ``resolve_model`` downloads the GGUF to a local cache and verifies its
sha256 -- never-black-box: the exact bytes are pinned and checked. The URL is a
direct-download asset (a GitHub Release asset in the project's own org, or an HF
resolve URL); ghcr/OCI is an alternative that needs an OCI puller (deferred).

Weights are published only after the P5 eval gate passes; until then the specs
carry ``sha256=None`` (a `PENDING` pin) and ``resolve_model`` refuses to download
(you can't verify what isn't pinned).
"""
from __future__ import annotations

import hashlib
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from goldenmatch.core.er_matcher.prompt import SERIALIZER_VERSION


@dataclass(frozen=True)
class ModelSpec:
    tier: str            # "3b" (default) | "7b" (optional)
    filename: str        # local cache filename, e.g. goldenmatch-er-3b-q4_k_m.gguf
    url: str | None      # direct-download asset URL (Release/HF); None until published
    sha256: str | None   # pinned digest; None = PENDING (not yet published/gated)
    base_model: str      # provenance (e.g. Qwen/Qwen2.5-3B-Instruct)
    serializer_version: str  # must match the runtime serializer, else refuse

    @property
    def published(self) -> bool:
        return bool(self.url) and bool(self.sha256)


# Pins are PENDING until P3b trains + P5's gate passes + P4 publishes. The shapes
# are real; url/sha256 get filled by the publish step (spec §5, plan P4).
MODELS: dict[str, ModelSpec] = {
    "3b": ModelSpec(
        tier="3b",
        filename="goldenmatch-er-matcher-3b-q4_k_m.gguf",
        url=None,
        sha256=None,
        base_model="Qwen/Qwen2.5-3B-Instruct",
        serializer_version="v1",
    ),
    "7b": ModelSpec(
        tier="7b",
        filename="goldenmatch-er-matcher-7b-q4_k_m.gguf",
        url=None,
        sha256=None,
        base_model="Qwen/Qwen2.5-7B-Instruct",
        serializer_version="v1",
    ),
}

DEFAULT_TIER = "3b"


def cache_dir() -> Path:
    """Where downloaded GGUFs live (override with GOLDENMATCH_ER_MODEL_DIR)."""
    root = os.environ.get("GOLDENMATCH_ER_MODEL_DIR")
    if root:
        return Path(root)
    return Path.home() / ".cache" / "goldenmatch" / "er-matcher"


def sha256_file(path: Path, _chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_chunk), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, expected_sha256: str) -> None:
    """Raise if the file's digest doesn't match the pin (tamper/corruption guard)."""
    actual = sha256_file(path)
    if actual != expected_sha256:
        raise ValueError(
            f"sha256 mismatch for {path.name}: expected {expected_sha256}, got {actual}"
        )


def resolve_model(tier: str = DEFAULT_TIER, *, cache: Path | None = None) -> Path:
    """Return a local path to the verified GGUF, downloading on first use.

    Refuses (ValueError) if the tier isn't published yet (no pin to verify) or if
    the serializer version drifted from the runtime (the model was trained on a
    different rendering). Re-verifies an existing cached file against the pin.
    """
    spec = MODELS.get(tier)
    if spec is None:
        raise ValueError(f"unknown ER-matcher tier {tier!r}; choices: {sorted(MODELS)}")
    if spec.serializer_version != SERIALIZER_VERSION:
        raise ValueError(
            f"serializer drift: model {tier} was built for serializer "
            f"{spec.serializer_version} but runtime is {SERIALIZER_VERSION} -- retrain/republish"
        )
    if not spec.published:
        raise ValueError(
            f"ER-matcher tier {tier!r} is not published yet (pin is PENDING). "
            "It ships after the P5 eval gate passes (spec/plan 2026-07-26)."
        )
    dest = (cache or cache_dir()) / spec.filename
    if dest.exists():
        verify(dest, spec.sha256)  # re-check cached bytes
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(spec.url, tmp)  # noqa: S310 (pinned https asset)
    try:
        verify(tmp, spec.sha256)
    except ValueError:
        tmp.unlink(missing_ok=True)
        raise
    tmp.replace(dest)
    return dest
