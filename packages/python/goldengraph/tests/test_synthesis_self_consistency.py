"""Synthesis self-consistency: sample N times + majority-vote (stage-2-B). Pure, no live LLM."""
from __future__ import annotations

from goldengraph.llm import OpenAIClient


class _FakeChat:
    """Minimal stand-in for openai's client: records every create() call's kwargs and
    returns a canned completion with a usage object (so budget accounting doesn't crash)."""
    def __init__(self):
        self.calls = []

        class _Msg:  # resp.choices[0].message.content
            content = "hello"

        class _Choice:
            message = _Msg()

        class _Usage:
            prompt_tokens = 1
            completion_tokens = 1

        class _Resp:
            choices = [_Choice()]
            usage = _Usage()

        self._resp = _Resp()

    # mimic client.chat.completions.create(**kwargs)
    class _Completions:
        def __init__(self, outer):
            self._outer = outer

        def create(self, **kwargs):
            self._outer.calls.append(kwargs)
            return self._outer._resp

    @property
    def chat(self):
        outer = self

        class _Chat:
            completions = _FakeChat._Completions(outer)

        return _Chat()


def test_complete_many_issues_n_calls_at_temperature():
    fake = _FakeChat()
    client = OpenAIClient(model="m", client=fake)
    out = client.complete_many("p", n=3, temperature=0.7)
    assert out == ["hello", "hello", "hello"]
    assert len(fake.calls) == 3
    assert all(c["temperature"] == 0.7 for c in fake.calls)


def test_complete_unchanged_temperature_zero():
    fake = _FakeChat()
    client = OpenAIClient(model="m", client=fake)
    client.complete("p")
    assert fake.calls[-1]["temperature"] == 0
