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


from goldengraph.synthesize import _synth_samples, _synth_temperature, _vote_answer


def test_vote_majority_returns_raw_form():
    # 'Firefox' and 'firefox.' share a normalized key -> 2 votes; raw winner keeps casing
    assert _vote_answer(["Firefox", "firefox.", "Chrome"]) == "Firefox"


def test_vote_skips_empty_and_handles_single():
    assert _vote_answer(["", "Acme", ""]) == "Acme"
    assert _vote_answer(["Solo"]) == "Solo"
    assert _vote_answer([]) == ""
    assert _vote_answer(["", "  "]) == ""


def test_vote_tie_breaks_first_seen():
    # one each -> the key seen first wins
    assert _vote_answer(["Beta", "Alpha"]) == "Beta"


def test_synth_env_parsers_defensive(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_SAMPLES", raising=False)
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_TEMPERATURE", raising=False)
    assert _synth_samples() == 1 and _synth_temperature() == 0.7
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "5")
    assert _synth_samples() == 5
    for bad in ("abc", "0", "-3", "1"):  # non-int / <=1 -> single call
        monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", bad)
        assert _synth_samples() == 1
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_TEMPERATURE", "xyz")
    assert _synth_temperature() == 0.7
