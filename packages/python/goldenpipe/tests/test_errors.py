import pytest
from goldenpipe.errors import PipeNotConfidentError


def test_pipe_not_confident_is_an_exception():
    with pytest.raises(PipeNotConfidentError):
        raise PipeNotConfidentError("nope")


def test_pipe_not_confident_carries_message():
    err = PipeNotConfidentError("rule=low_confidence on 200000 rows")
    assert "low_confidence" in str(err)
