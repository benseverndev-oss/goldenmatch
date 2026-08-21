"""Refusal exceptions must say where the answer lives.

These three are refusals BY DESIGN, which is exactly why they get misread as
malfunctions: a caller (or an AI agent) sees "committed a RED config" or
"refusing slow-path" and starts looking for a way around it in the source,
rather than reading the documented reason and the named escape hatch.

The pointer is cheap to add and easy to drop by accident during a message
reword, so it is pinned here.
"""

from __future__ import annotations

import pytest
from goldenmatch.core._docs import AGENT_DOCS_HINT, with_docs_hint
from goldenmatch.core.autoconfig_controller import ControllerNotConfidentError
from goldenmatch.core.config_lint.registry import ConfigLintError, Finding, Severity
from goldenmatch.core.distributed_routing_rules import SlowPathRefusedError


def _refusals() -> list[Exception]:
    return [
        ControllerNotConfidentError(
            n_rows=150_000, failing_sub_profile="blocking", stop_reason="BUDGET"
        ),
        ConfigLintError(
            [
                Finding(
                    rule_id="r1",
                    severity=Severity.ERROR,
                    message="m",
                    rationale="why",
                    doc_anchor="anchor",
                )
            ]
        ),
        SlowPathRefusedError(decisions=[], n_rows=200_000),
    ]


@pytest.mark.parametrize("exc", _refusals(), ids=lambda e: type(e).__name__)
def test_refusal_names_both_the_shipped_file_and_the_site(exc: Exception) -> None:
    message = str(exc)
    # The in-wheel path works offline, which is the case that matters for an
    # agent inspecting an installed package with no network.
    assert "goldenmatch/llms.txt" in message
    assert "https://docs.bensevern.dev/docs/goldenmatch" in message


@pytest.mark.parametrize("exc", _refusals(), ids=lambda e: type(e).__name__)
def test_refusal_still_leads_with_its_own_cause(exc: Exception) -> None:
    """The pointer is a suffix, never a replacement for the actual reason."""
    message = str(exc)
    assert not message.startswith(AGENT_DOCS_HINT)
    cause = message[: message.index(AGENT_DOCS_HINT)].strip()
    assert len(cause) > 40, f"pointer crowded out the cause: {message!r}"


def test_hint_is_idempotent() -> None:
    """Double-wrapping must not stutter -- messages get composed and re-raised."""
    once = with_docs_hint("boom")
    assert with_docs_hint(once) == once
    assert once.count(AGENT_DOCS_HINT) == 1


def test_shipped_llms_txt_is_actually_there() -> None:
    """The pointer promises a file inside the package; prove it resolves."""
    from pathlib import Path

    import goldenmatch

    assert (Path(goldenmatch.__file__).parent / "llms.txt").is_file()
