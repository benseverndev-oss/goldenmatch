"""Tests for A2A server."""
import pytest

try:
    from aiohttp import web  # noqa: F401
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


class TestRunPipelineSkill:
    async def test_inline_records_dispatch(self, a2a_client):
        # A2A dispatches run-pipeline to the SAME run_pipeline_tool the MCP server
        # uses, so it inherits the enriched surface: inline `records` input and the
        # full per-stage result. `stages: []` keeps it hermetic (load only).
        client = await a2a_client
        resp = await client.post("/tasks", json={
            "skill": "run-pipeline",
            "params": {"records": [{"a": 1}, {"a": 2}], "stages": []},
        })
        assert resp.status == 200
        body = await resp.json()
        assert body["status"] == "completed"
        result = body["result"]
        assert result["status"] == "success"
        assert result["input_rows"] == 2
        assert "load" in result["stages"]
