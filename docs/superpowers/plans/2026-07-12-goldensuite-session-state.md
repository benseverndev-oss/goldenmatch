# Session-Keyed Run State for Aggregated Goldenmatch Tools — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 8 stateful goldenmatch MCP tools (`list_clusters`, `get_cluster`, `get_golden_record`, `explain_match`, `evaluate`, `export_results`, `match_record`, `find_duplicates`) work via the goldensuite-mcp aggregator by persisting the last run per MCP session and having the tools fall back to it — with per-session isolation and byte-identical standalone behavior.

**Architecture:** A `ContextVar` carries the active MCP session id (set at `call_tool` entry from `server.request_context.session`). A bounded session store keeps the last `AgentSession` per id; `agent_deduplicate`/`agent_match_sources`/`auto_configure` persist their session instead of discarding it. A shared `_resolve_run_state()` returns module globals when set (standalone, byte-identical) and falls back to the session's `AgentSession` when they're `None` (aggregator path). The 8 tools call the resolver instead of reading globals directly.

**Tech Stack:** Python 3.12, MCP SDK (`mcp.server.lowlevel`), polars, pyarrow, pytest. Worktree: `D:\show_case\gm-session-state` on `feat/goldensuite-session-state` off main-with-#1705 @ `5c3f26973`.

**Spec:** `docs/superpowers/specs/2026-07-12-goldensuite-session-state-design.md`

**Run-prefix (worktree tests via main .venv; run each package from its OWN dir to avoid the `tests` package-name collision):**
```bash
PP="D:/show_case/gm-session-state/packages/python/goldensuite-mcp;D:/show_case/gm-session-state/packages/python/goldenmatch;D:/show_case/gm-session-state/packages/python/goldencheck;D:/show_case/gm-session-state/packages/python/goldencheck-types;D:/show_case/gm-session-state/packages/python/goldenflow;D:/show_case/gm-session-state/packages/python/goldenpipe;D:/show_case/gm-session-state/packages/python/infermap"
PY="PYTHONPATH=$PP POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest"
```

---

## File Structure

- **Create** `packages/python/goldenmatch/goldenmatch/mcp/_session_store.py` — bounded LRU+TTL `SessionStore` + `_STORE` singleton.
- **Create** `packages/python/goldenmatch/goldenmatch/mcp/_session_ctx.py` — session-id `ContextVar` + `set_current_session_id`/`current_session_id`/`session_key_from_context`.
- **Modify** `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` — persist the `AgentSession` after the 3 write tools.
- **Modify** `packages/python/goldenmatch/goldenmatch/mcp/server.py` — `_resolve_run_state()` + rewire the 8 tool handlers + set contextvar in `call_tool`.
- **Modify** `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` — set contextvar in aggregator `call_tool`; drop the stale "non-functional" comment.
- **Tests** (new): `tests/test_session_store.py`, `tests/test_session_ctx.py`, `tests/test_mcp_session_state.py` under `packages/python/goldenmatch/tests/`; `tests/test_session_isolation.py` under `packages/python/goldensuite-mcp/tests/`.

---

## Task 1: `SessionStore` (bounded LRU + TTL)

**Files:** Create `packages/python/goldenmatch/goldenmatch/mcp/_session_store.py`; Test `packages/python/goldenmatch/tests/test_session_store.py`

- [ ] **Step 1: Failing test.** Create `tests/test_session_store.py`:

```python
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
```

- [ ] **Step 2: Run — must FAIL** (module missing). `cd /d/show_case/gm-session-state/packages/python/goldenmatch && eval $PY tests/test_session_store.py -q`

- [ ] **Step 3: Implement** `goldenmatch/mcp/_session_store.py`:

```python
"""Bounded, TTL'd store of the last AgentSession per MCP session id.

Lazy eviction (no background thread): expired entries drop on access; when the
store exceeds ``max_sessions`` the least-recently-used entry is evicted. The
clock is injectable so TTL/LRU are testable without real time.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Any, Callable


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


class SessionStore:
    def __init__(
        self,
        max_sessions: int | None = None,
        ttl_seconds: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max = max_sessions if max_sessions is not None else _env_int(
            "GOLDENMATCH_MCP_SESSION_MAX", 64)
        self._ttl = ttl_seconds if ttl_seconds is not None else _env_int(
            "GOLDENMATCH_MCP_SESSION_TTL", 3600)
        self._clock = clock
        self._lock = threading.Lock()
        # id -> (session, last_touch); OrderedDict preserves LRU order
        self._d: "OrderedDict[str, tuple[Any, float]]" = OrderedDict()

    def put(self, session_id: str, session: Any) -> None:
        with self._lock:
            now = self._clock()
            self._d[session_id] = (session, now)
            self._d.move_to_end(session_id)
            self._evict(now)

    def get(self, session_id: str) -> Any | None:
        with self._lock:
            now = self._clock()
            entry = self._d.get(session_id)
            if entry is None:
                return None
            session, touched = entry
            if now - touched > self._ttl:
                del self._d[session_id]
                return None
            self._d[session_id] = (session, now)  # refresh touch
            self._d.move_to_end(session_id)
            return session

    def _evict(self, now: float) -> None:
        # drop expired first
        for k in [k for k, (_, t) in self._d.items() if now - t > self._ttl]:
            del self._d[k]
        # then LRU until within cap
        while len(self._d) > self._max:
            self._d.popitem(last=False)


_STORE = SessionStore()
```

- [ ] **Step 4: Run — PASS.** `eval $PY tests/test_session_store.py -q`
- [ ] **Step 5: Commit** (explicit paths, NOT `git add -A`):
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/_session_store.py packages/python/goldenmatch/tests/test_session_store.py
git commit -m "feat(mcp): bounded LRU+TTL SessionStore for per-session run state"
```

---

## Task 2: Session-id `ContextVar`

**Files:** Create `packages/python/goldenmatch/goldenmatch/mcp/_session_ctx.py`; Test `packages/python/goldenmatch/tests/test_session_ctx.py`

- [ ] **Step 1: Failing test.** Create `tests/test_session_ctx.py`:

```python
from goldenmatch.mcp import _session_ctx as ctx


def test_set_get_reset():
    assert ctx.current_session_id() is None
    tok = ctx.set_current_session_id("sess-1")
    try:
        assert ctx.current_session_id() == "sess-1"
    finally:
        ctx.reset_current_session_id(tok)
    assert ctx.current_session_id() is None


def test_key_from_context_with_session():
    class _Sess: ...
    class _Ctx:
        session = _Sess()
    class _Server:
        request_context = _Ctx()
    key = ctx.session_key_from_context(_Server())
    assert key is not None and key.startswith("sess-")


def test_key_from_context_absent_or_raising():
    class _NoCtx:
        @property
        def request_context(self):
            raise LookupError("no active request")
    assert ctx.session_key_from_context(_NoCtx()) is None

    class _NoneSession:
        class request_context:  # noqa: N801
            session = None
    assert ctx.session_key_from_context(_NoneSession()) is None
```

- [ ] **Step 2: Run — FAIL.**
- [ ] **Step 3: Implement** `goldenmatch/mcp/_session_ctx.py`:

```python
"""Request-scoped MCP session id, threaded to tool handlers via a ContextVar
(so dispatch signatures stay `(name, args)`)."""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "goldenmatch_mcp_session_id", default=None
)


def set_current_session_id(sid: str | None) -> Token:
    return _CURRENT_SESSION_ID.set(sid)


def reset_current_session_id(token: Token) -> None:
    _CURRENT_SESSION_ID.reset(token)


def current_session_id() -> str | None:
    return _CURRENT_SESSION_ID.get()


def session_key_from_context(server: Any) -> str | None:
    """Stable per-connection key for the active MCP session, or None.

    `id(session)` is stable for the life of one Streamable-HTTP connection and
    distinct across connections -- the isolation boundary we need. Returns None
    outside an active request (request_context raises LookupError) or when there
    is no session (stdio / standalone global path)."""
    try:
        ctx = server.request_context
        sess = getattr(ctx, "session", None)
        return f"sess-{id(sess)}" if sess is not None else None
    except Exception:
        return None
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit:**
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/_session_ctx.py packages/python/goldenmatch/tests/test_session_ctx.py
git commit -m "feat(mcp): request-scoped session-id ContextVar"
```

---

## Task 3: Persist the AgentSession after write tools

**Files:** Modify `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` (`_dispatch`, ~648); Test `packages/python/goldenmatch/tests/test_mcp_session_state.py` (new — grows across Tasks 3/5)

**Background:** `_dispatch` creates `session = session_cls()` at lines 687 (`auto_configure`), 715 (`agent_deduplicate`), 743 (`agent_match_sources`) and drops it. Persist ONLY `agent_deduplicate` + `agent_match_sources` — they set `session.result`. **Do NOT persist `auto_configure`** (plan-review finding): `AgentSession.autoconfigure()` sets `.config` but leaves `.result = None`, and the resolver's fallback guard (Task 4) requires `result is not None`; persisting it would overwrite a good prior dedupe under the same session id with an unusable `result=None` session, silently reverting all 8 tools to "no run loaded." `_persist_session` also guards on `result is not None` as belt-and-braces.

- [ ] **Step 1: Failing test.** Create `tests/test_mcp_session_state.py`:

```python
"""Session-fallback for the stateful goldenmatch MCP tools (aggregator path)."""
import csv

from goldenmatch.mcp import _session_ctx as ctx
from goldenmatch.mcp import _session_store as store
from goldenmatch.mcp.agent_tools import _dispatch
from goldenmatch.core.agent import AgentSession


def _fixture(tmp_path):
    p = tmp_path / "in.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["name", "email"])
        for r in [["John Smith", "j@x.com"], ["Jon Smith", "j@x.com"],
                  ["Mary Jones", "m@y.com"], ["Karen White", "k@z.com"]]:
            w.writerow(r)
    return str(p)


def test_agent_deduplicate_persists_session(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    tok = ctx.set_current_session_id("sess-test")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        saved = store._STORE.get("sess-test")
        assert isinstance(saved, AgentSession)
        assert saved.result is not None
    finally:
        ctx.reset_current_session_id(tok)


def test_no_session_id_does_not_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    # no contextvar set -> current_session_id() is None -> nothing stored
    _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
    assert store._STORE.get("sess-test") is None


def test_auto_configure_does_not_clobber_prior_dedupe(tmp_path, monkeypatch):
    """auto_configure has no .result; it must NOT overwrite a good dedupe session
    under the same id (the result-guard in _persist_session)."""
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    tok = ctx.set_current_session_id("sess-x")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        good = store._STORE.get("sess-x")
        assert good is not None and good.result is not None
        _dispatch("auto_configure", {"file_path": _fixture(tmp_path)}, AgentSession)
        # still the dedupe session (result intact), not a result-less autoconfig one
        assert store._STORE.get("sess-x") is good
        assert store._STORE.get("sess-x").result is not None
    finally:
        ctx.reset_current_session_id(tok)
```

- [ ] **Step 2: Run — FAIL** (session not persisted). `eval $PY tests/test_mcp_session_state.py -q`

- [ ] **Step 3: Implement.** In `agent_tools.py`, add a helper near the top of `_dispatch`'s module and call it in the 3 write branches. Add:

```python
def _persist_session(session) -> None:
    """Store the live AgentSession under the current MCP session id (if any),
    so the stateful server tools can read this run on later calls. No-op when
    there's no session id (stdio / standalone global path) or when the session
    has no result yet (never clobber a usable run with a result-less one)."""
    if getattr(session, "result", None) is None:
        return
    from goldenmatch.mcp._session_ctx import current_session_id
    from goldenmatch.mcp._session_store import _STORE
    sid = current_session_id()
    if sid is not None:
        _STORE.put(sid, session)
```

Then in `_dispatch`, add `_persist_session(session)` to the `agent_deduplicate` (~715-740) and `agent_match_sources` (~743-...) branches only, right before their `return out` (the `session` local is in scope, result already built). **Do NOT add it to the `auto_configure` branch** (~687-712) — that branch is `try: return session.autoconfigure(...) finally: ...` with no `out` var and no `.result`, and persisting it would clobber a prior dedupe (see Background). The `result is not None` guard makes an accidental call inert anyway.

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit:**
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py packages/python/goldenmatch/tests/test_mcp_session_state.py
git commit -m "feat(mcp): persist AgentSession per session id after dedupe/match/autoconfig"
```

---

## Task 4: `_resolve_run_state()` resolver

**Files:** Modify `packages/python/goldenmatch/goldenmatch/mcp/server.py` (add near the globals ~66 / before `_handle_tool` ~951); Test `tests/test_mcp_session_state.py` (append)

**Background:** Central resolver: globals-first (standalone, byte-identical), else the current session's AgentSession with a `__row_id__`-augmented frame (see spec §3 — raw `session.data` lacks `__row_id__`, which `match_one` needs).

- [ ] **Step 1: Failing tests.** Append to `tests/test_mcp_session_state.py`:

```python
def test_resolver_prefers_globals(monkeypatch):
    from goldenmatch.mcp import server as gm
    monkeypatch.setattr(gm, "_result", "GLOBAL_RESULT")
    monkeypatch.setattr(gm, "_config", "GLOBAL_CONFIG")
    monkeypatch.setattr(gm, "_engine", type("E", (), {"data": "GLOBAL_DATA"})())
    monkeypatch.setattr(gm, "_rows", [{"__row_id__": 0}])
    monkeypatch.setattr(gm, "_id_to_idx", {0: 0})
    rs = gm._resolve_run_state()
    assert rs.result == "GLOBAL_RESULT" and rs.config == "GLOBAL_CONFIG"
    assert rs.data == "GLOBAL_DATA"


def test_resolver_session_fallback_builds_row_ids(tmp_path, monkeypatch):
    import polars as pl
    from goldenmatch.mcp import server as gm
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import _session_store as store
    # globals all None (aggregator path)
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))

    sess = AgentSession()
    sess.result = "R"; sess.config = "C"
    sess.data = pl.DataFrame({"name": ["a", "b"]})  # NO __row_id__
    store._STORE.put("s1", sess)
    tok = ctx.set_current_session_id("s1")
    try:
        rs = gm._resolve_run_state()
        assert rs.result == "R" and rs.config == "C"
        assert "__row_id__" in rs.data.columns   # augmented
        assert rs.rows and rs.id_to_idx           # built from the augmented frame
    finally:
        ctx.reset_current_session_id(tok)


def test_resolver_nothing_loaded(monkeypatch):
    from goldenmatch.mcp import server as gm
    from goldenmatch.mcp import _session_ctx as ctx
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    tok = ctx.set_current_session_id("unknown")
    try:
        rs = gm._resolve_run_state()
        assert rs.result is None and rs.config is None and rs.data is None
    finally:
        ctx.reset_current_session_id(tok)
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement** in `server.py`:

```python
from dataclasses import dataclass


@dataclass
class _RunState:
    result: object | None
    config: object | None
    data: object | None
    rows: list
    id_to_idx: dict


def _resolve_run_state() -> "_RunState":
    """Active run state: module globals when set (standalone --file, byte-
    identical), else the current MCP session's AgentSession (aggregator path),
    else all-None (callers return a clean 'no run loaded' error)."""
    if _result is not None or _config is not None or _engine is not None:
        return _RunState(
            result=_result,
            config=_config,
            data=_engine.data if _engine is not None else None,
            rows=_rows,
            id_to_idx=_id_to_idx,
        )
    from goldenmatch.mcp._session_ctx import current_session_id
    from goldenmatch.mcp._session_store import _STORE
    sid = current_session_id()
    sess = _STORE.get(sid) if sid else None
    if sess is None or getattr(sess, "result", None) is None:
        return _RunState(None, None, None, [], {})
    df = sess.data
    if df is not None and "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__")
    rows = getattr(sess, "_mcp_rows", None)
    if rows is None or getattr(sess, "_mcp_data", None) is not df:
        sess._mcp_data = df
        rows = df.to_dicts() if df is not None else []
        sess._mcp_rows = rows
        sess._mcp_id_to_idx = {r["__row_id__"]: i for i, r in enumerate(rows)}
    return _RunState(sess.result, sess.config, sess._mcp_data,
                     sess._mcp_rows, sess._mcp_id_to_idx)
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit:**
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_mcp_session_state.py
git commit -m "feat(mcp): _resolve_run_state -- globals-first, session fallback (row-id augmented)"
```

---

## Task 5: Rewire the 8 tool handlers

**Files:** Modify `packages/python/goldenmatch/goldenmatch/mcp/server.py` (the 8 `_tool_*` handlers); Test `tests/test_mcp_session_state.py` (append the end-to-end cases)

**Background:** Each handler replaces direct global reads with `rs = _resolve_run_state()` + a "nothing loaded" guard, then uses `rs.result`/`rs.config`/`rs.data`/`rs.rows`/`rs.id_to_idx`. Behavior is byte-identical on the standalone path (globals win). Handler line numbers: `find_duplicates` 1040, `explain_match` 1063, `list_clusters` 1098, `get_cluster` 1111, `get_golden_record` 1126, `match_record` 1144, `export_results` 1367, `evaluate` 1591.

For each handler:
- Read the current body; note which globals it reads.
- Insert at the top: `rs = _resolve_run_state()`; if the fields it needs are None, `return {"error": "No run loaded. Run agent_deduplicate (or dedupe_file) in this session first."}`. (Match `evaluate`'s existing message style; `evaluate` already had this guard — swap its `_result` check for `rs.result`.)
- Replace `_result`→`rs.result`, `_config`→`rs.config`, `_engine.data`→`rs.data`, `_rows`→`rs.rows`, `_id_to_idx`→`rs.id_to_idx` in the body.
- `match_record` (1144): the call becomes `match_one(record, rs.data, mk_copy)` with `rs.data` the augmented frame (spec §4).

- [ ] **Step 1: Failing end-to-end test.** Append to `tests/test_mcp_session_state.py`:

```python
def test_eight_tools_work_via_session(tmp_path, monkeypatch):
    """Aggregator path (globals None): after agent_deduplicate under a session id,
    all 8 stateful tools return real data (no AttributeError)."""
    from goldenmatch.mcp import server as gm
    from goldenmatch.mcp import _session_store as store
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    monkeypatch.setattr(gm, "_rows", [])
    monkeypatch.setattr(gm, "_id_to_idx", {})
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))

    tok = ctx.set_current_session_id("s-e2e")
    try:
        _dispatch("agent_deduplicate", {"file_path": _fixture(tmp_path)}, AgentSession)
        # each tool via the server dispatch (aggregator uses this path)
        assert "error" not in gm.dispatch("list_clusters", {})
        clusters = gm.dispatch("list_clusters", {})
        assert isinstance(clusters, dict)
        assert "error" not in gm.dispatch("get_golden_record", {"cluster_id": next(iter(_first_cluster_ids(clusters)), 0)})
        out = tmp_path / "exp.csv"
        r_exp = gm.dispatch("export_results", {"output_path": str(out), "fmt": "csv"})
        assert "error" not in r_exp and out.exists()
        r_md = gm.dispatch("match_record", {"record": {"name": "John Smith", "email": "j@x.com"}})
        assert "error" not in r_md  # the __row_id__ regression guard
        r_fd = gm.dispatch("find_duplicates", {"record": {"name": "John Smith"}})
        assert "error" not in r_fd
    finally:
        ctx.reset_current_session_id(tok)


def _first_cluster_ids(clusters_payload):
    # tolerate whatever shape list_clusters returns; yield any int-ish ids
    import re
    return [int(m) for m in re.findall(r"\b\d+\b", str(clusters_payload))][:1]
```

(ADJUST assertions to the real return shapes after reading each handler — keep the intent: no `error`/no raise, real data. `get_cluster`/`explain_match`/`evaluate` may need specific args; add them once you know each handler's contract.)

- [ ] **Step 2: Run — FAIL** (tools still raise AttributeError via dispatch).
- [ ] **Step 3: Rewire all 8 handlers** as described. Do them one at a time, re-running the test after each to watch failures shrink.
- [ ] **Step 4: Run — PASS.** `eval $PY tests/test_mcp_session_state.py -q`
- [ ] **Step 5: Standalone regression.** Run the existing MCP tool suite (globals path unchanged): `eval $PY tests/test_mcp_and_watch.py tests/test_mcp_new_tools.py -q` (skip any that need `--file` state they don't set up; they must still pass). Also `eval $PY tests/test_agent.py tests/test_agent_output_path.py -q`.
- [ ] **Step 6: Commit:**
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_mcp_session_state.py
git commit -m "feat(mcp): 8 stateful tools fall back to session run state via _resolve_run_state"
```

---

## Task 6: Set the ContextVar in both `call_tool` handlers

**Files:** Modify `packages/python/goldenmatch/goldenmatch/mcp/server.py` (`call_tool` ~921), `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` (`call_tool` ~429)

**Background:** Both `call_tool` closures have `server` in scope (`server = Server(...)` in the enclosing `create_server`). Set the session id from `session_key_from_context(server)` before dispatch, reset in `finally`.

**Test-coverage note (plan-review advisory):** the Task 3/4/5 unit tests set the contextvar directly (`ctx.set_current_session_id`) and call `dispatch` — they exercise the store/resolver/tool logic but BYPASS the literal `call_tool` wiring changed here. Step 3 below adds one test that drives `session_key_from_context` through the SDK's real backing ContextVar to close that loop.

- [ ] **Step 1** (goldenmatch `call_tool`, ~921): wrap the body:
```python
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        from goldenmatch.mcp._session_ctx import set_current_session_id, reset_current_session_id, session_key_from_context
        _tok = set_current_session_id(session_key_from_context(server))
        try:
            name = _resolve_alias(name)
            ...  # existing body unchanged
        finally:
            reset_current_session_id(_tok)
```

- [ ] **Step 2** (goldensuite-mcp `call_tool`, ~429): same wrap, importing from `goldenmatch.mcp._session_ctx`:
```python
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        from goldenmatch.mcp._session_ctx import set_current_session_id, reset_current_session_id, session_key_from_context
        _tok = set_current_session_id(session_key_from_context(server))
        try:
            handler = dispatch_by_name.get(name)
            ...  # existing body unchanged
        finally:
            reset_current_session_id(_tok)
```

- [ ] **Step 3: Test — a fake server with a session drives the whole chain.** Add to `packages/python/goldensuite-mcp/tests/test_session_isolation.py` (new):

```python
"""Two MCP sessions are isolated through the aggregator dispatch."""
import csv

import pytest


def _fixture(tmp_path, names):
    p = tmp_path / f"{names[0]}.csv"
    with open(p, "w", newline="") as f:
        w = csv.writer(f); w.writerow(["name", "email"])
        for n in names:
            w.writerow([n, f"{n.split()[0].lower()}@x.com"])
    return str(p)


def test_sessions_isolated(tmp_path, monkeypatch):
    from goldenmatch.mcp import _session_ctx as ctx
    from goldenmatch.mcp import _session_store as store
    from goldenmatch.mcp import server as gm
    monkeypatch.setattr(store, "_STORE", store.SessionStore(clock=lambda: 0.0))
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)

    fa = _fixture(tmp_path, ["John Smith", "Jon Smith"])
    # session A runs a dedupe
    tokA = ctx.set_current_session_id("A")
    try:
        gm.dispatch("agent_deduplicate", {"file_path": fa})
        assert "error" not in gm.dispatch("list_clusters", {})
    finally:
        ctx.reset_current_session_id(tokA)
    # session B never ran anything -> clean error, NOT session A's clusters
    tokB = ctx.set_current_session_id("B")
    try:
        res = gm.dispatch("list_clusters", {})
        assert "error" in res
    finally:
        ctx.reset_current_session_id(tokB)
```

- [ ] **Step 3b: Test `session_key_from_context` through the SDK's real ContextVar.** Add to `packages/python/goldenmatch/tests/test_session_ctx.py` (it pushes onto the same ContextVar `Server.request_context` reads):

```python
def test_key_from_real_request_ctx():
    """Drive session_key_from_context through the SDK's actual request_ctx var,
    not just a fake server -- proves the call_tool wiring will resolve a key."""
    import mcp.server.lowlevel.server as low
    from mcp.server.lowlevel.server import Server
    from goldenmatch.mcp._session_ctx import session_key_from_context

    srv = Server("t")
    # No active request -> request_context raises LookupError -> None.
    assert session_key_from_context(srv) is None
    # Push a fake RequestContext carrying a session onto the SDK's ContextVar.
    class _Sess: ...
    class _Ctx:
        session = _Sess()
    tok = low.request_ctx.set(_Ctx())  # the var Server.request_context returns
    try:
        assert session_key_from_context(srv).startswith("sess-")
    finally:
        low.request_ctx.reset(tok)
```
(If the SDK's backing var isn't named `request_ctx`, introspect `Server.request_context`'s getter to find it; the intent is to exercise the real property, not a stub.)

- [ ] **Step 4: Run** (from each dir): `cd .../goldenmatch && eval $PY tests/test_session_ctx.py -q` and `cd .../goldensuite-mcp && eval $PY tests/test_session_isolation.py tests/test_curated_tools.py -q`
- [ ] **Step 5: Commit:**
```bash
git add packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_session_ctx.py packages/python/goldensuite-mcp/goldensuite_mcp/server.py packages/python/goldensuite-mcp/tests/test_session_isolation.py
git commit -m "feat(mcp): set session-id ContextVar at call_tool entry (goldenmatch + suite)"
```

---

## Task 7: Aggregator cleanup + acceptance test

**Files:** Modify `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` (remove the stale comment)

- [ ] **Step 1:** Remove the `_CURATED_DESCRIPTION_SUFFIXES` block's comment paragraph (added by #1705) that states the 8 tools "currently raise AttributeError via the suite endpoint ... Tracked as a separate bug". Replace with a one-line note that they are now session-backed (`_resolve_run_state`). Keep the 8 in `CURATED_TOOLS`.
- [ ] **Step 2: Acceptance test** — reproduce the ORIGINAL bug harness and prove it's fixed. Append to `test_session_isolation.py`:
```python
def test_cold_call_is_clean_error_not_crash(monkeypatch):
    from goldenmatch.mcp import server as gm
    from goldenmatch.mcp import _session_ctx as ctx
    for g in ("_result", "_config", "_engine"):
        monkeypatch.setattr(gm, g, None)
    tok = ctx.set_current_session_id("cold")
    try:
        res = gm.dispatch("list_clusters", {})   # no run yet
        assert "error" in res                     # clean error, NOT AttributeError
        assert "AttributeError" not in str(res)
    finally:
        ctx.reset_current_session_id(tok)
```
- [ ] **Step 3: Run + full goldensuite-mcp suite** (from its dir): `eval $PY tests/ -q` — all green (composites, curated, isolation, find_tools).
- [ ] **Step 4: Commit:**
```bash
git add packages/python/goldensuite-mcp/goldensuite_mcp/server.py packages/python/goldensuite-mcp/tests/test_session_isolation.py
git commit -m "chore(goldensuite-mcp): 8 stateful tools now session-backed; drop stale bug note"
```

---

## Task 8: Broad regression, lint, PR

**Files:** none (verification)

- [ ] **Step 1: Targeted regression** (each package from its own dir):
  - goldenmatch: `eval $PY tests/test_session_store.py tests/test_session_ctx.py tests/test_mcp_session_state.py tests/test_agent.py tests/test_agent_output_path.py tests/test_mcp_new_tools.py -q`
  - goldensuite-mcp: `eval $PY tests/ -q`
- [ ] **Step 2: Lint** the touched files: `.venv/Scripts/python.exe -m ruff check` on the 2 new modules + `server.py` (goldenmatch), `agent_tools.py`, goldensuite-mcp `server.py`, and the 3 new test files. Fix (use `--fix` for I001; re-run tests after).
- [ ] **Step 3: Version-consistency** (behavior fix, no bump expected): `.venv/Scripts/python.exe scripts/check_version_consistency.py` → exit 0.
- [ ] **Step 4: Push + PR** (`benzsevern` auth; explicit paths already committed):
```bash
unset GH_TOKEN; gh auth switch --user benzsevern; export GH_TOKEN=$(gh auth token --user benzsevern)
git push -u origin feat/goldensuite-session-state
gh pr create --repo benseverndev-oss/goldenmatch --base main --head feat/goldensuite-session-state \
  --title "fix(mcp): session-keyed run state so the 8 stateful tools work via the suite aggregator" \
  --body "Fixes the AttributeError on list_clusters/get_cluster/get_golden_record/explain_match/evaluate/export_results/match_record/find_duplicates via goldensuite-mcp. ContextVar session id + bounded per-session AgentSession store + _resolve_run_state fallback (globals-first, byte-identical standalone). Per-session isolation. Spec/plan local.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```
- [ ] **Step 5: Arm auto-merge** (merge-on-green standing authorization): `gh pr merge <N> --auto --squash` and STOP (do not poll CI).

---

## Task 9: Docs sweep

**Files:** `packages/python/goldenmatch/CLAUDE.md` (or the MCP docs), `docs-site/goldenmatch/tuning.mdx`

- [ ] **Step 1:** Note the session-state model + the two env tunables (`GOLDENMATCH_MCP_SESSION_MAX`, `GOLDENMATCH_MCP_SESSION_TTL`) in `tuning.mdx` (canonical runtime-config doc).
- [ ] **Step 2:** Add a short note in the goldenmatch package CLAUDE.md "Remote MCP Server" section that the aggregated stateful tools are session-backed (contextvar + SessionStore), standalone path unchanged.
- [ ] **Step 3: Commit + confirm** the PR includes the docs. (Per rollout-docs-sweep: sweep every surface at the end.)

---

## Definition of Done

- `SessionStore` + `_session_ctx` unit-tested (TTL, LRU, contextvar).
- `agent_deduplicate`/`match_sources`/`auto_configure` persist their session under the MCP session id.
- `_resolve_run_state()`: globals-first (standalone byte-identical), session fallback with `__row_id__`-augmented frame, clean-None otherwise.
- All 8 tools work via `gm.dispatch` under a session id (incl. `match_record` — no `ColumnNotFoundError`); cold call returns a clean error, never `AttributeError`.
- Two sessions isolated; unknown session → clean error.
- goldensuite-mcp suite green; standalone MCP/agent suites green.
- Stale "non-functional" comment removed; 8 tools stay curated.
- Docs swept (tuning.mdx tunables + CLAUDE.md note). PR merged via queue.
