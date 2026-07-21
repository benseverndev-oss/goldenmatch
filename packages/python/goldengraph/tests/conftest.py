"""Test fixtures: a deterministic stub LLM + a fresh store.

The stub returns canned extraction JSON regardless of prompt, so the pipeline is
deterministic without a real model (the goldenmatch-kg posture). goldenmatch's
resolution accuracy is covered by its own parity suite + SP6's eval, not here.
"""

from __future__ import annotations

import pytest


class StubLLM:
    """Returns a fixed completion for every prompt."""

    def __init__(self, response: str):
        self.response = response

    def complete(self, prompt: str) -> str:  # noqa: ARG002 - prompt ignored by design
        return self.response


class RecordingLLM:
    """Records every prompt it sees and returns a canned answer — lets a test
    assert WHAT the synthesizer was given (the subgraph), not free-form text."""

    def __init__(self, response: str = "ANSWER"):
        self.response = response
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.response


class StubEmbedder:
    """Deterministic one-hot embedder: each known string maps to a basis vector;
    unknown strings → the zero vector. Makes cosine ranking reproducible."""

    def __init__(self, vocab: dict[str, int]):
        self.vocab = vocab
        self.dim = max(vocab.values()) + 1 if vocab else 1

    def embed(self, texts: list[str]):
        import numpy as np

        m = np.zeros((len(texts), self.dim), dtype=float)
        for i, t in enumerate(texts):
            if t in self.vocab:
                m[i, self.vocab[t]] = 1.0
        return m


@pytest.fixture
def store():
    from goldengraph.core._native_loader import new_store

    return new_store()
