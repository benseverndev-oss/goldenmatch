"""Tests for A2A server."""
import pytest

try:
    from aiohttp import web
    from goldenpipe.a2a.server import create_app
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")


@pytest.fixture
def a2a_client(aiohttp_client):
    return aiohttp_client(create_app())


class TestAgentCard:
    async def test_agent_card(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/.well-known/agent.json")
        assert resp.status == 200
        data = await resp.json()
        assert data["name"] == "GoldenPipe"
        assert "skills" in data


class TestHealthEndpoint:
    async def test_health(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/health")
        assert resp.status == 200
