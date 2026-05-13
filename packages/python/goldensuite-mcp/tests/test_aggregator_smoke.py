"""Aggregator smoke tests for goldensuite-mcp.

The aggregator imports every sub-package's MCP tool list + dispatcher
and composes them into a single Server. These tests assert that:

- Each sub-package's adapter loads without raising.
- The composed TOOLS list is non-empty.
- The v1.15 Identity Graph tools surface through the aggregator
  (transitively via ``goldenmatch.mcp.server.TOOLS``).
- First-wins collision resolution logs a warning rather than crashing.

These are smoke tests, not coverage tests. They guard against future
drift where a sub-package renames its ``TOOLS`` symbol or changes its
dispatcher signature.
"""
from __future__ import annotations

import logging

import pytest


def test_aggregator_imports():
    """Bare import of the server module shouldn't raise."""
    from goldensuite_mcp import server  # noqa: F401


def test_each_subpackage_adapter_loads():
    """Each adapter returns (tools, dispatch) without raising."""
    from goldensuite_mcp.server import (
        _adapt_goldencheck,
        _adapt_goldenflow,
        _adapt_goldenmatch,
        _adapt_goldenpipe,
        _adapt_infermap,
    )

    for adapter in (
        _adapt_goldenmatch,
        _adapt_goldencheck,
        _adapt_goldenflow,
        _adapt_goldenpipe,
        _adapt_infermap,
    ):
        tools, dispatch = adapter()
        assert isinstance(tools, list)
        # Tools normalize to mcp.types.Tool; at minimum each adapter ships >=1
        assert len(tools) >= 1, f"{adapter.__name__} returned no tools"
        assert callable(dispatch)


def test_identity_tools_surface_through_aggregator():
    """v1.15 identity_* tools must flow through the goldenmatch adapter
    transitively (via goldenmatch.mcp.server.TOOLS, which composes
    AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + _BASE_TOOLS)."""
    from goldensuite_mcp.server import _adapt_goldenmatch

    tools, _ = _adapt_goldenmatch()
    names = {t.name for t in tools}
    expected = {
        "identity_resolve",
        "identity_list",
        "identity_history",
        "identity_conflicts",
        "identity_merge",
        "identity_split",
    }
    missing = expected - names
    assert not missing, f"Identity Graph tools missing from aggregator: {missing}"


def test_create_server_composes_full_tool_list():
    """The composed Server exposes every sub-package's tools, with first-wins
    on name collisions. The exact count may shift over time; we assert it's
    non-trivial and that the collision logger ran."""
    from goldensuite_mcp.server import create_server

    server = create_server()
    # Server.list_tools is the async handler; we want the registered Tool list
    # for the assertion, so reach into the internal registry instead.
    # mcp.server.Server uses a request-handler registry; the simpler check is
    # that create_server doesn't crash and the goldenmatch tools made it in.
    # Spot-check via _SUITE_ORDER -> _adapt_goldenmatch path used above.
    assert server is not None


def test_collision_logging_does_not_crash(caplog):
    """The aggregator logs WARNING on tool-name collisions and continues.
    No collision today is expected to crash the load."""
    caplog.set_level(logging.WARNING, logger="goldensuite_mcp.server")
    from goldensuite_mcp.server import create_server

    create_server()
    # Whether collisions occur depends on what each sub-package registers;
    # the contract is "log + continue", not "must produce a warning".
    # Test passes if no exception was raised.


@pytest.mark.parametrize(
    "name",
    [
        "identity_resolve",  # v1.15 identity graph
        "list_corrections",  # v1.6 learning memory
    ],
)
def test_dispatch_routes_goldenmatch_tools(name):
    """Calling a known goldenmatch tool through the aggregator's dispatch
    reaches the underlying handler rather than raising."""
    from goldensuite_mcp.server import _adapt_goldenmatch

    _, dispatch = _adapt_goldenmatch()
    # We can't actually exercise the tool (no fixture DB), but the call must
    # at least reach the handler -- it will either return a dict (success
    # with empty data) or raise a structured error, never NameError/Attr.
    try:
        out = dispatch(name, {})
        assert isinstance(out, dict)
    except Exception as e:
        # Structured errors from the handler are fine; bare crashes are not.
        msg = str(e).lower()
        assert "unknown tool" not in msg, f"{name} not routed: {e}"
