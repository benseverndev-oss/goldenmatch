"""SessionStore: bounded, TTL'd per-session AgentSession cache."""
from goldenmatch.mcp._session_store import SessionStore


def test_put_get_roundtrip():
    s = SessionStore(max_sessions=8, ttl_seconds=100, clock=lambda: 0.0)
    s.put("a", "SESSION_A")
    assert s.get("a") == "SESSION_A"
    assert s.get("missing") is None


def test_ttl_expiry():
    now = {"t": 0.0}
    s = SessionStore(max_sessions=8, ttl_seconds=10, clock=lambda: now["t"])
    s.put("a", "SA")
    now["t"] = 9.9
    assert s.get("a") == "SA"      # still fresh
    now["t"] = 10.1
    assert s.get("a") is None       # expired


def test_lru_eviction_at_max():
    now = {"t": 0.0}
    s = SessionStore(max_sessions=2, ttl_seconds=1000, clock=lambda: now["t"])
    s.put("a", "SA"); now["t"] += 1
    s.put("b", "SB"); now["t"] += 1
    s.get("a"); now["t"] += 1        # touch a -> a is now MRU, b is LRU
    s.put("c", "SC")                 # over max -> evict LRU (b)
    assert s.get("a") == "SA"
    assert s.get("c") == "SC"
    assert s.get("b") is None


def test_put_same_key_updates_not_grows():
    s = SessionStore(max_sessions=2, ttl_seconds=1000, clock=lambda: 0.0)
    s.put("a", "SA1"); s.put("a", "SA2")
    assert s.get("a") == "SA2"
