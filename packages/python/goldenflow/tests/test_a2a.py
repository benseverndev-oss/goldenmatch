"""Tests for A2A server."""
import pytest

try:
    import pytest_aiohttp  # noqa: F401
    from goldenflow.a2a.server import create_app  # noqa: F401
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = [
    pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed"),
    pytest.mark.asyncio,
]


@pytest.fixture
def a2a_client(aiohttp_client):
    return aiohttp_client(create_app())


class TestAgentCard:
    async def test_agent_card(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/.well-known/agent.json")
        assert resp.status == 200
        data = await resp.json()
        assert data["name"] == "GoldenFlow"
        assert "skills" in data
        assert len(data["skills"]) == 6

    async def test_skill_ids(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/.well-known/agent.json")
        data = await resp.json()
        skill_ids = {s["id"] for s in data["skills"]}
        assert "transform-data" in skill_ids
        assert "map-schemas" in skill_ids
        assert "discover" in skill_ids
        assert "diff-results" in skill_ids
        assert "configure" in skill_ids
        assert "handoff" in skill_ids

    async def test_skills_have_io_modes(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/.well-known/agent.json")
        data = await resp.json()
        for skill in data["skills"]:
            assert "inputModes" in skill
            assert "outputModes" in skill


class TestHealth:
    async def test_health(self, a2a_client):
        client = await a2a_client
        resp = await client.get("/health")
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "ok"


class TestDiscoverSkill:
    async def test_discover(self, a2a_client):
        client = await a2a_client
        resp = await client.post("/tasks", json={
            "id": "test-1",
            "skill": "discover",
            "params": {},
        })
        assert resp.status == 200
        data = await resp.json()
        assert data["status"] == "completed"
        assert "transforms" in data["result"]
        assert "domains" in data["result"]
