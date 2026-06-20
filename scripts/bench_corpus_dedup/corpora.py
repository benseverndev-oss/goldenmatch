"""Pluggable corpus adapters: load_corpus(name, n_docs, seed) -> (doc_id, text) stream.

Adapters:
  offline   - vendored data/offline_corpus.jsonl (network-free; the per-PR gate fixture)
  fineweb   - HF HuggingFaceFW/fineweb (ODC-By), streaming   [headline default]
  c4        - HF allenai/c4 'en', streaming
  wikipedia - HF wikimedia/wikipedia, streaming

All adapters stream and are bounded by n_docs. The offline adapter is deterministic for a
fixed (n_docs, seed); the HF adapters use datasets' seeded shuffle buffer.
"""
from __future__ import annotations

import json
import random
from collections.abc import Iterator
from pathlib import Path

HERE = Path(__file__).resolve().parent
OFFLINE_PATH = HERE / "data" / "offline_corpus.jsonl"

# (repo, config, split, text-field) for each streamed HF corpus.
_HF_SPECS = {
    "fineweb":   ("HuggingFaceFW/fineweb", "sample-10BT", "train", "text"),
    "c4":        ("allenai/c4", "en", "train", "text"),
    "wikipedia": ("wikimedia/wikipedia", "20231101.en", "train", "text"),
}


def _offline(n_docs: int, seed: int) -> Iterator[tuple[str, str]]:
    docs = [
        json.loads(line)
        for line in OFFLINE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    rng = random.Random(seed)
    rng.shuffle(docs)
    for d in docs[:n_docs]:
        yield str(d["doc_id"]), str(d["text"])


def _hf(name: str, n_docs: int, seed: int) -> Iterator[tuple[str, str]]:
    from datasets import load_dataset

    repo, config, split, field = _HF_SPECS[name]
    ds = load_dataset(repo, config, split=split, streaming=True)
    ds = ds.shuffle(seed=seed, buffer_size=10_000)
    emitted = 0
    for row in ds:
        if emitted >= n_docs:
            break
        text = (row.get(field) or "").strip()
        if text:
            yield f"{name}-{emitted}", text
            emitted += 1


def load_corpus(name: str, n_docs: int, seed: int = 0) -> Iterator[tuple[str, str]]:
    if name == "offline":
        yield from _offline(n_docs, seed)
    elif name in _HF_SPECS:
        yield from _hf(name, n_docs, seed)
    else:
        raise ValueError(f"unknown corpus {name!r}")
