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


@pytest.fixture
def store():
    from goldengraph_native import _native as gg

    return gg.PyStore()
