import logging

import pytest
from golden_diagnostics import (
    environment_report,
    is_expected,
    issue_url,
    report_anomaly,
    report_exception,
    reset,
)


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Prompts on by default; clear the warn-once guard between tests.
    monkeypatch.delenv("GOLDEN_DIAGNOSTICS", raising=False)
    reset()
    yield
    reset()


def test_environment_report_is_pii_safe():
    env = environment_report("goldenmatch", "3.0.0",
                             extra={"native": "installed", "path": "/home/user/secret.csv",
                                    "blob": "x" * 300})
    assert env["package"] == "goldenmatch"
    assert env["version"] == "3.0.0"
    assert env["native"] == "installed"
    # path-like and over-long values are scrubbed
    assert env["path"] == "<omitted>"
    assert env["blob"] == "<omitted>"
    assert "python" in env and "platform" in env


def test_issue_url_encodes_and_caps():
    url = issue_url("t" * 400, "b" * 10000, labels=["native-slow-path"])
    assert url.startswith("https://github.com/benseverndev-oss/goldenmatch/issues/new?")
    assert "title=" in url and "body=" in url and "labels=native-slow-path" in url
    # body is capped well under browser URL limits
    assert len(url) < 8000


def test_report_anomaly_fires_once_per_key(caplog):
    with caplog.at_level(logging.WARNING):
        m1 = report_anomaly("native-slow-path", "hot path fell back", once_key="k")
        m2 = report_anomaly("native-slow-path", "hot path fell back", once_key="k")
    assert m1 is not None and "please report it" in m1
    assert "github.com/benseverndev-oss/goldenmatch/issues/new" in m1
    assert m2 is None  # warn-once


def test_kill_switch_silences(monkeypatch):
    monkeypatch.setenv("GOLDEN_DIAGNOSTICS", "0")
    assert report_anomaly("x", "y") is None


def test_report_exception_skips_expected():
    class ControllerNotConfidentError(Exception):
        pass

    exc = ControllerNotConfidentError("red config")
    # expected -> no prompt (returns None so caller can `report(...); raise`)
    assert report_exception(exc, category="dedupe", summary="dedupe failed",
                            expected=[ControllerNotConfidentError]) is None
    reset()
    # unexpected -> prompt (the exception text is embedded in the prefilled URL body)
    boom = RuntimeError("kernel segfault")
    msg = report_exception(boom, category="dedupe", summary="dedupe crashed",
                           expected=[ControllerNotConfidentError])
    assert msg is not None
    assert "issues/new" in msg
    assert "kernel+segfault" in msg  # url-encoded traceback in the issue body


def test_is_expected():
    assert is_expected(ValueError("x"), [ValueError, KeyError])
    assert not is_expected(RuntimeError("x"), [ValueError, KeyError])


def test_report_anomaly_never_raises(monkeypatch):
    # even if logging blows up, report_anomaly swallows it
    class BadLogger:
        def warning(self, *a, **k):
            raise RuntimeError("logging down")

    assert report_anomaly("x", "y", logger=BadLogger()) is None
