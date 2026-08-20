"""goldensuite-mcp CLI — `goldensuite-mcp serve [--transport stdio|http] [--port N]`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import secrets
import sys


def _serve_stdio() -> None:
    from mcp.server.stdio import stdio_server

    from goldensuite_mcp.server import create_server

    async def main() -> None:
        server = create_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())


def resolve_http_auth_token(host: str) -> str | None:
    """Return the MCP HTTP bearer token, enforcing the fail-closed bind rule.

    Raises ``RuntimeError`` when binding to a non-loopback host without
    ``GOLDENSUITE_MCP_TOKEN`` set, so an exposed server is never started
    unauthenticated by accident. Returns the token (or ``None`` for an
    intentionally-open loopback bind). Escape hatch: set
    ``GOLDENSUITE_MCP_ALLOW_PUBLIC=1`` to intentionally run an open public server.

    This server matters more than its siblings, not less: it aggregates EVERY
    Golden Suite tool -- goldenmatch, goldencheck, goldenflow, goldenpipe,
    infermap -- behind one endpoint, so an unauthenticated bind exposes the
    union of five tool surfaces rather than one.
    """
    token = os.environ.get("GOLDENSUITE_MCP_TOKEN")
    is_loopback = host in ("127.0.0.1", "localhost", "::1")
    allow_public = os.environ.get("GOLDENSUITE_MCP_ALLOW_PUBLIC", "").strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not token and not is_loopback and not allow_public:
        raise RuntimeError(
            f"Refusing to start an unauthenticated MCP HTTP server on host {host!r}. "
            "Set GOLDENSUITE_MCP_TOKEN, bind to 127.0.0.1 for local use, or set "
            "GOLDENSUITE_MCP_ALLOW_PUBLIC=1 to intentionally run an open public server."
        )
    return token


def _serve_http(host: str, port: int) -> None:
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.middleware import Middleware
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from goldensuite_mcp.server import create_server

    token = resolve_http_auth_token(host)  # fail closed before any server setup
    server = create_server()
    # Stateful sessions (NOT stateless): the 8 stateful goldenmatch tools
    # (list_clusters/get_cluster/get_golden_record/explain_match/evaluate/
    # export_results/match_record/find_duplicates) carry run state across calls
    # via a session-keyed store (see goldenmatch.mcp._session_ctx / _session_store,
    # PR #1713). That store is keyed on the per-connection ServerSession, so it
    # only persists when the HTTP layer keeps one ServerSession alive across a
    # client's requests -- i.e. stateful mode, which issues an Mcp-Session-Id the
    # client echoes. Under stateless=True every POST built a fresh ServerSession,
    # so `dedupe_file` then `list_clusters` in the same client session missed the
    # store and returned "No run loaded". Bounded by the store's LRU(64)+TTL(1h);
    # single Railway replica so no cross-instance session affinity concern.
    session_manager = StreamableHTTPSessionManager(app=server, stateless=False)

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with session_manager.run():
            yield

    async def server_card(request):
        return JSONResponse(
            {
                "name": "goldensuite-mcp",
                "description": (
                    "One MCP server exposing every Golden Suite tool — "
                    "goldenmatch, goldencheck, goldenflow, goldenpipe, infermap. "
                    "First-wins on tool-name collisions; collisions are logged at startup."
                ),
                "homepage": "https://github.com/benseverndev-oss/goldenmatch",
                "iconUrl": "https://avatars.githubusercontent.com/u/192581748",
            }
        )

    class _BearerAuthMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            # `/.well-known/` stays public: it is the discovery card, and health
            # checks read it. Everything else needs the bearer.
            if request.url.path.startswith("/.well-known/"):
                return await call_next(request)
            if token:
                header = request.headers.get("Authorization", "")
                if not header.startswith("Bearer ") or not secrets.compare_digest(
                    header[7:], token
                ):
                    return JSONResponse({"error": "Unauthorized"}, status_code=401)
            return await call_next(request)

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
        middleware=[Middleware(_BearerAuthMiddleware)],
    )

    uvicorn.run(app, host=host, port=port)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="goldensuite-mcp")
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="Run the aggregated MCP server.")
    s.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="Transport (default: stdio).",
    )
    s.add_argument("--host", default="0.0.0.0", help="HTTP host (default 0.0.0.0).")
    s.add_argument("--port", type=int, default=8300, help="HTTP port (default 8300).")

    args = parser.parse_args(argv)

    if args.cmd == "serve":
        if args.transport == "http":
            _serve_http(args.host, args.port)
        else:
            _serve_stdio()
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
