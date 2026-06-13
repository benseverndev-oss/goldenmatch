"""Retirement-runway guards for the cluster frames-out cutover.

Locks the GOLDENMATCH_CLUSTER_FRAMES_OUT gate default to ON and asserts the
legacy-opt-out deprecation warning. The SEMANTIC frames-vs-dict parity guard
lives in tests/test_cluster_frames_out_parity.py + test_pipeline_frames_out_parity.py
(already in CI) and is not duplicated here.
"""
import logging
import warnings

import goldenmatch.core.cluster as C
import pytest
from goldenmatch.core.cluster import _cluster_frames_out_enabled


def test_default_on(monkeypatch):
    # Locks the cutover: if anyone reverts the gate default to "0", this fails.
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    monkeypatch.setattr(C, "_LEGACY_CLUSTER_PATH_WARNED", False)
    assert _cluster_frames_out_enabled() is True


def test_silent_on_default(monkeypatch, recwarn, caplog):
    monkeypatch.delenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", raising=False)
    monkeypatch.setattr(C, "_LEGACY_CLUSTER_PATH_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="goldenmatch.cluster"):
        assert _cluster_frames_out_enabled() is True
    assert not any("CLUSTER_FRAMES_OUT" in r.message for r in caplog.records)
    assert not any(issubclass(w.category, DeprecationWarning) for w in recwarn.list)


def test_warns_once_on_legacy_optout(monkeypatch, caplog):
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_FRAMES_OUT", "0")
    monkeypatch.setattr(C, "_LEGACY_CLUSTER_PATH_WARNED", False)
    with caplog.at_level(logging.WARNING, logger="goldenmatch.cluster"):
        # First call: warns on the DeprecationWarning channel.
        with pytest.warns(DeprecationWarning, match="GOLDENMATCH_CLUSTER_FRAMES_OUT=0"):
            assert _cluster_frames_out_enabled() is False
        # Second call: once-per-process -> no NEW warning (promote to error to prove it).
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert _cluster_frames_out_enabled() is False
    # Logging channel fired exactly once.
    log_warns = [r for r in caplog.records if "CLUSTER_FRAMES_OUT" in r.message]
    assert len(log_warns) == 1
