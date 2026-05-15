"""goldensuite-mcp CLI — `goldensuite-mcp serve [--transport stdio|http] [--port N]`."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys


def _serve_stdio() -> None:
    from mcp.server.stdio import stdio_server

    from goldensuite_mcp.server import create_server

    async def main() -> None:
        server = create_server()
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(main())


def _serve_http(host: str, port: int) -> None:
    import contextlib
    from collections.abc import AsyncIterator

    import uvicorn
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount, Route

    from goldensuite_mcp.server import create_server

    server = create_server()
    session_manager = StreamableHTTPSessionManager(app=server, stateless=True)

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

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Route("/.well-known/mcp/server-card.json", server_card),
            Mount("/mcp", app=session_manager.handle_request),
        ],
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
