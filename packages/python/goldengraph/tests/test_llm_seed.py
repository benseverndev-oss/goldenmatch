"""GOLDENGRAPH_LLM_SEED: pass a fixed `seed` to the completion request for reproducible decoding."""
from goldengraph.llm import OpenAIClient


class _CaptureClient:
    """Fake OpenAI client capturing the create() kwargs; returns a minimal response."""
    def __init__(self):
        self.kwargs = None

        class _Resp:
            class _Choice:
                class _Msg:
                    content = "ok"
                message = _Msg()
            choices = [_Choice()]
            usage = None

        class _Completions:
            def __init__(self, outer):
                self._outer = outer

            def create(self, **kwargs):
                self._outer.kwargs = kwargs
                return _Resp()

        class _Chat:
            def __init__(self, outer):
                self.completions = _Completions(outer)

        self.chat = _Chat(self)


def test_seed_absent_by_default(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_LLM_SEED", raising=False)
    cap = _CaptureClient()
    OpenAIClient(model="m", client=cap).complete("hi")
    assert "seed" not in cap.kwargs


def test_seed_present_when_set(monkeypatch):
    monkeypatch.setenv("GOLDENGRAPH_LLM_SEED", "42")
    cap = _CaptureClient()
    OpenAIClient(model="m", client=cap).complete("hi")
    assert cap.kwargs.get("seed") == 42
    assert cap.kwargs.get("temperature") == 0


def test_seed_empty_or_garbage_ignored(monkeypatch):
    for bad in ("", " ", "abc"):
        monkeypatch.setenv("GOLDENGRAPH_LLM_SEED", bad)
        cap = _CaptureClient()
        OpenAIClient(model="m", client=cap).complete("hi")
        assert "seed" not in cap.kwargs, bad
