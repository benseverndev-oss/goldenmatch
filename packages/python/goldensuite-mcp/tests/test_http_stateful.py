"""The HTTP transport must run STATEFUL, not stateless.

The 8 stateful goldenmatch tools (list_clusters/get_cluster/get_golden_record/
explain_match/evaluate/export_results/match_record/find_duplicates) carry run
state across calls via a session-keyed store (PR #1713) keyed on the
per-connection ServerSession. That only persists when the HTTP layer keeps one
ServerSession alive across a client's requests -- i.e. stateful mode. Under
stateless=True a live smoke test showed `dedupe_file` then `list_clusters` in the
same client session returned "No run loaded". This locks the fix in.
"""
from unittest import mock

from goldensuite_mcp import cli


def test_serve_http_builds_stateful_session_manager():
    captured = {}

    class _FakeMgr:
        def __init__(self, app, stateless):
            captured["stateless"] = stateless

        def run(self):  # pragma: no cover - lifespan not entered in this test
            raise AssertionError("lifespan should not run")

        def handle_request(self, *a, **k):  # pragma: no cover
            pass

    with mock.patch(
        "mcp.server.streamable_http_manager.StreamableHTTPSessionManager", _FakeMgr
    ), mock.patch("uvicorn.run") as run:
        cli._serve_http("127.0.0.1", 8300)

    assert captured["stateless"] is False, "HTTP transport must be stateful (see PR #1713)"
    run.assert_called_once()
