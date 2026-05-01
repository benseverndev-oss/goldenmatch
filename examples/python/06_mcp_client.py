"""06 — Connect to goldensuite-mcp from a Python MCP client.

Demonstrates calling tools over MCP rather than the in-process API. Useful
when you've deployed the aggregator container and want to access it from
agents, notebooks, or services that aren't co-located with the Suite.

Spin up the server first:
    docker run -p 8300:8300 ghcr.io/benzsevern/goldensuite-mcp:latest

Then:
    pip install mcp httpx
    python 06_mcp_client.py
"""
from __future__ import annotations

import asyncio

from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession


SERVER_URL = "http://localhost:8300/mcp"


async def main() -> None:
    async with streamablehttp_client(SERVER_URL) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools_response = await session.list_tools()
            tools = tools_response.tools
            print(f"connected to {SERVER_URL}")
            print(f"got {len(tools)} tools from goldensuite-mcp")

            # Group by source for visibility — every tool's description starts
            # with its origin in goldensuite-mcp.
            by_first_word: dict[str, int] = {}
            for t in tools:
                first = (t.description or "").split()[0:2]
                key = " ".join(first) or "unlabeled"
                by_first_word[key] = by_first_word.get(key, 0) + 1
            print("tool descriptions by leading words:")
            for key, n in sorted(by_first_word.items(), key=lambda kv: -kv[1])[:10]:
                print(f"  {n:3d}  {key}")

            # Call a known goldenmatch tool — this is the same tool you'd get
            # from goldenmatch's standalone MCP server, but routed through the
            # aggregator. The aggregator picked it under first-wins ordering.
            for tool_name in ("get_stats", "list_domains", "list_stages", "list_transforms"):
                if tool_name in {t.name for t in tools}:
                    print(f"\ncalling {tool_name!r}...")
                    result = await session.call_tool(tool_name, {})
                    text = result.content[0].text if result.content else "(empty)"
                    print(f"  → {text[:200]}{'...' if len(text) > 200 else ''}")
                    break


if __name__ == "__main__":
    asyncio.run(main())
