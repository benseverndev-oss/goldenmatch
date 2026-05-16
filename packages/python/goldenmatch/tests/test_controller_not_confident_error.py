"""Unit tests for ControllerNotConfidentError.

Spec §Design / Confidence gate -- exception construction, structured
fields, DOCS_URL class attribute. No suggested_config field (footgun).
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError


def test_exception_carries_structured_fields():
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    assert exc.n_rows == 500_000
    assert exc.failing_sub_profile == "scoring"
    assert exc.stop_reason == "BUDGET_TIME"


def test_exception_has_docs_url_class_attribute():
    """DOCS_URL is a class attribute so callers can reference it without
    catching the exception first."""
    assert hasattr(ControllerNotConfidentError, "DOCS_URL")
    assert isinstance(ControllerNotConfidentError.DOCS_URL, str)
    assert ControllerNotConfidentError.DOCS_URL.startswith("https://")


def test_exception_str_rendering_includes_diagnostic_fields():
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    rendered = str(exc)
    assert "500000" in rendered or "500_000" in rendered
    assert "scoring" in rendered
    assert "BUDGET_TIME" in rendered
    # The error tells the caller how to recover -- this is load-bearing
    # for users seeing the exception cold without context.
    assert "confidence_required=False" in rendered
    assert ControllerNotConfidentError.DOCS_URL in rendered


def test_exception_has_no_suggested_config_field():
    """Spec deliberately omits suggested_config (footgun: 'suggestion'
    derived from the config that just produced the RED commit). Verify
    the field is NOT present so a future refactor can't silently
    re-introduce it without the spec change."""
    exc = ControllerNotConfidentError(
        n_rows=500_000,
        failing_sub_profile="scoring",
        stop_reason="BUDGET_TIME",
    )
    assert not hasattr(exc, "suggested_config")


def test_exception_is_exception_subclass():
    """Plain Exception, not subclass of ValueError / RuntimeError. Caller
    catches by type, not by hierarchy."""
    assert issubclass(ControllerNotConfidentError, Exception)
