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


from goldengraph.synthesize import (
    _synth_samples,
    _synth_temperature,
    _vote_answer,
    synthesize_local,
)


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


_SUB = {
    "entities": [{"entity_id": 0, "canonical_name": "Acme", "typ": "org"}],
    "edges": [],
}


class _ManyStub:
    """LLM stub with complete_many returning a CANNED list of completions (already in the
    'show hops then Answer: X' shape). Records whether complete_many vs complete was used."""
    def __init__(self, samples: list[str], single: str = "Answer: SingleFallback"):
        self._samples = samples
        self._single = single
        self.many_calls = 0
        self.single_calls = 0

    def complete(self, prompt: str) -> str:
        self.single_calls += 1
        return self._single

    def complete_many(self, prompt: str, *, n: int, temperature: float) -> list[str]:
        self.many_calls += 1
        return list(self._samples)


def test_self_consistency_votes_when_enabled(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")
    llm = _ManyStub(["Answer: Firefox", "Answer: Firefox", "Answer: Chrome"])
    assert synthesize_local("q?", _SUB, llm) == "Firefox"
    assert llm.many_calls == 1 and llm.single_calls == 0


def test_default_off_uses_single_complete(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_SYNTH_SAMPLES", raising=False)
    llm = _ManyStub(["Answer: X"])
    out = synthesize_local("q?", _SUB, llm)
    assert out == "SingleFallback"           # the single-call path
    assert llm.single_calls == 1 and llm.many_calls == 0


def test_stub_without_complete_many_falls_back(monkeypatch):
    from conftest import RecordingLLM
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")   # enabled, but stub lacks complete_many
    llm = RecordingLLM("Answer: Y")
    assert synthesize_local("q?", _SUB, llm) == "Y"
    assert len(llm.prompts) == 1                            # single call, no crash


def test_all_samples_empty_falls_back(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_SYNTH_SAMPLES", "3")
    llm = _ManyStub(["", "   ", ""], single="Answer: Recovered")
    assert synthesize_local("q?", _SUB, llm) == "Recovered"
    assert llm.many_calls == 1 and llm.single_calls == 1   # sampled, all empty -> one fallback
