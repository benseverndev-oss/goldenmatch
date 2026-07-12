# Session-keyed run state for aggregated goldenmatch tools

**Date:** 2026-07-12
**Status:** Design approved, pending spec review
**Area:** `packages/python/goldenmatch/goldenmatch/mcp/{server.py,agent_tools.py}`, `packages/python/goldenmatch/goldenmatch/core/agent.py`, `packages/python/goldensuite-mcp/goldensuite_mcp/server.py`

## Problem

Eight goldenmatch MCP tools raise `AttributeError: 'NoneType' object has no
attribute 'clusters'/'golden'` when called via the goldensuite-mcp aggregator:
`list_clusters`, `get_cluster`, `get_golden_record`, `explain_match`, `evaluate`
(currently returns a clean error, not a crash), `export_results`, `match_record`,
`find_duplicates`.

Root cause (verified empirically + by code trace): these tools read module-global
run state — `_result`, `_config`, `_engine`, `_rows`, `_id_to_idx` in
`goldenmatch/mcp/server.py` — that is populated ONLY by `_initialize()`, called
once from `create_server(file_paths=...)` at standalone-server startup. The
aggregator's `_adapt_goldenmatch()` imports `gm.TOOLS`/`gm.dispatch` directly and
never calls `_initialize()`, so those globals stay `None` for the process lifetime.
`agent_deduplicate` builds a fresh `AgentSession` per call and **discards it** — no
run state survives across calls. So on the live suite endpoint these 8 headline
tools are non-functional.

## Goal

Make all 8 tools work via the aggregator by persisting the last dedupe/match run
per MCP session and having the tools fall back to it. Constraints:

- **Multi-user safe.** The suite endpoint is public (`GOLDENMATCH_MCP_ALLOW_PUBLIC`).
  State MUST be isolated per MCP session — user B's `list_clusters` must never see
  user A's clusters.
- **Standalone server byte-identical.** When the module globals ARE set (standalone
  `--file` startup), behavior is unchanged; the session fallback only engages when a
  global is `None`.
- **No dispatch-signature churn.** The `dispatch(name, args) -> dict` interface is
  shared across all 6 packages + composites; do not change it.

## Design

### 1. Session identity via a ContextVar

The MCP SDK exposes `server.request_context` (property on `mcp.server.lowlevel.Server`)
inside a `call_tool` handler, and `RequestContext` carries a `.session` field (the
`ServerSession`) plus `.request_id`. We derive a stable per-connection key from the
session object.

New module `goldenmatch/mcp/_session_ctx.py`:

```python
from contextvars import ContextVar

_CURRENT_SESSION_ID: ContextVar[str | None] = ContextVar(
    "goldenmatch_mcp_session_id", default=None
)

def set_current_session_id(sid: str | None) -> object:  # returns reset token
    return _CURRENT_SESSION_ID.set(sid)

def current_session_id() -> str | None:
    return _CURRENT_SESSION_ID.get()

def session_key_from_context(server) -> str | None:
    """Best-effort stable key for the active MCP session, or None."""
    try:
        ctx = server.request_context
        sess = getattr(ctx, "session", None)
        return f"sess-{id(sess)}" if sess is not None else None
    except Exception:
        return None
```

`id(session)` is stable for the life of one Streamable-HTTP connection (the same
`ServerSession` object handles every call from that client), and distinct per
connection — exactly the isolation boundary we need. It is process-local (fine: the
session store lives in the same process).

**Setting it.** At the top of each `call_tool` handler that can route into the
goldenmatch tools, set the contextvar and reset it in a `finally`:

- goldensuite-mcp `call_tool` (`goldensuite_mcp/server.py`): derive the key from the
  aggregator's own `server` and set it before `name_to_dispatch[name](name, args)`.
- goldenmatch standalone `call_tool` (`goldenmatch/mcp/server.py`): same, from its
  own `server`, so the standalone HTTP server also gets per-session isolation (a
  latent improvement; the `--file` global path still wins when set).

The contextvar flows into `dispatch` -> the tool handlers without any signature
change.

### 2. Session-keyed AgentSession store

New module `goldenmatch/mcp/_session_store.py` — a bounded, TTL'd store:

```python
class SessionStore:
    def __init__(self, max_sessions=64, ttl_seconds=3600): ...
    def put(self, session_id: str, agent_session) -> None: ...   # evict LRU/expired
    def get(self, session_id: str) -> AgentSession | None: ...    # None if absent/expired

_STORE = SessionStore()  # process singleton
```

- Bounds: `GOLDENMATCH_MCP_SESSION_MAX` (default 64), `GOLDENMATCH_MCP_SESSION_TTL`
  (default 3600s). Eviction: drop expired on access; when over `max`, evict the
  least-recently-used. (No background thread — eviction is lazy on put/get. Time
  source is injected/monotonic so it's testable without real clocks.)
- Thread-safety: a `threading.Lock` around put/get (block scoring uses threads
  elsewhere; be safe).

**Writing.** In `agent_tools._dispatch`, after a successful `agent_deduplicate` /
`agent_match_sources` / `auto_configure`, store the live `AgentSession` under
`current_session_id()` (when non-None): `_STORE.put(sid, session)`. The session is
no longer discarded. (These handlers currently create `session = session_cls()` and
drop it — keep the created session and store it.)

### 3. A resolver the 8 tools share

New helper in `goldenmatch/mcp/server.py`:

```python
def _resolve_run_state():
    """Return (result, config, data, rows, id_to_idx) for the active run.

    Prefers the module globals (standalone --file server, byte-identical path).
    Falls back to the current MCP session's stored AgentSession when a global is
    None (the aggregator path). Returns Nones when neither is available -- callers
    return a clean 'no run loaded' error, never raise.
    """
```

- When `_result`/`_config`/`_engine` are set (standalone), return them + `_rows`/
  `_id_to_idx` as today.
- Else look up `_STORE.get(current_session_id())`. From an `AgentSession`:
  `result = session.result`, `config = session.config`, and a **`__row_id__`-
  augmented frame** derived from `session.data`.

  **CRITICAL (spec-review finding): `session.data` is raw `pl.read_csv` with NO
  `__row_id__` column** — only `MatchEngine._load` adds it (`with_row_index`), which
  the aggregator path never runs. `match_one` (match_one.py:121) does
  `df["__row_id__"].to_list()` and would raise `ColumnNotFoundError` on raw
  `session.data`. So the resolver builds ONE augmented frame and reuses it for both
  `data` and `rows`/`id_to_idx`:

  ```python
  df = session.data
  if "__row_id__" not in df.columns:
      df = df.with_row_index("__row_id__")
  # cache all three on the session so repeat tool calls are cheap
  session._mcp_data = df
  session._mcp_rows = df.to_dicts()
  session._mcp_id_to_idx = {r["__row_id__"]: i for i, r in enumerate(session._mcp_rows)}
  ```

  The resolver returns `data = session._mcp_data` (augmented), NOT raw
  `session.data`. `rows`/`id_to_idx` come from that same frame so ids line up.

### 4. Rewire the 8 handlers

Each of the 8 `_tool_*` handlers starts by calling `_resolve_run_state()` and uses
the returned locals instead of reading the globals directly. Behavior when nothing
is loaded becomes a uniform clean error (matching `evaluate`'s existing
`{"error": "No dataset loaded ..."}`), so the AttributeError crash is gone
regardless of session availability.

- `list_clusters`, `get_cluster`, `get_golden_record`, `export_results`, `evaluate`:
  read `result` (+ `rows`/`id_to_idx` for `get_cluster`). `result` is the same
  `DedupeResult` shape from `AgentSession`, so the bodies are nearly unchanged.
- `find_duplicates`: reads `config` (+ `rows`). `explain_match`: reads `config`
  ONLY (it does not touch `_rows`; the resolver returning `rows` unconditionally is
  harmless).
- `match_record`: reads `config` + `data`. Instead of `match_one(record,
  _engine.data, mk)`, call `match_one(record, data, mk)` where `data` is the
  resolver's **`__row_id__`-augmented frame** (`_engine.data` standalone, or
  `session._mcp_data` on the session path -- see §3; passing raw `session.data`
  would `ColumnNotFoundError`). `match_one`'s contract is `(record, df, matchkey)`
  with `df` carrying `__row_id__`, so no `MatchEngine` object is required once the
  frame is augmented.

The mutating tools `unmerge_record`/`shatter_cluster` are OUT OF SCOPE (they call
`_engine.unmerge_*`, which mutates `MatchEngine._last_result`; there is no session
analog and they are not in the 8). They keep their existing `_engine is None`
guards.

### 5. goldensuite-mcp: drop the curate-out note, keep tools curated

These 8 stay in `CURATED_TOOLS`. Remove the code comment (added by #1705) that says
they're non-functional. No suffix changes needed.

## Testing

Unit (local, small fixtures; run each package from its own rootdir to avoid the
`tests` package-name collision):

1. `SessionStore`: put/get round-trip; TTL expiry (injected clock); LRU eviction at
   `max`; missing id -> None.
2. Contextvar: `session_key_from_context` returns a stable key for a fake server
   with a session, None when absent/raising.
3. `_resolve_run_state`: (a) globals set -> returns globals (standalone unchanged);
   (b) globals None + a stored session -> returns session-derived state; (c) neither
   -> all None.
4. End-to-end via the goldenmatch `dispatch` (aggregator path, globals None): set a
   session id in the contextvar, run `agent_deduplicate`, then each of the 8 tools
   returns real data (clusters listed, a cluster fetched, golden record present,
   export writes a CSV, evaluate scores, find_duplicates/explain_match return
   results) -- NOT an AttributeError. **`match_record` gets its OWN explicit case**
   (it's the one most likely to regress silently on the `__row_id__` augmentation):
   after a run, `match_record` on a probe record returns matches without a
   `ColumnNotFoundError`.
5. Isolation: two different session ids -> tool calls under id A never see id B's
   run; an unknown id -> clean "no run loaded" error.
6. Standalone byte-identical: with globals populated (simulate `_initialize`), the 8
   tools behave exactly as before (a regression guard on the existing behavior).

Aggregator integration (goldensuite-mcp): reproduce the original bug harness (call
`list_clusters` cold -> clean error; `agent_deduplicate` then `list_clusters` under
the SAME contextvar session id -> real clusters). This is the acceptance test that
the headline bug is fixed.

## Risks

- **Session-key stability.** `id(session)` reused after GC could alias a new session
  to an evicted slot; TTL + the fact that a live connection holds its session ref
  make collision negligible. Documented; acceptable for this endpoint.
- **Memory.** Stored `AgentSession`s hold a polars frame + result. Bounded by
  `max_sessions` (64) + TTL (1h); LRU eviction caps footprint. `agent_deduplicate`
  already materializes these; we retain, not duplicate.
- **Contextvar propagation across threads.** Tool handlers run in the request task;
  `match_one`/scoring may use a thread pool internally but the contextvar is read in
  the handler (main task) before any fan-out, so propagation isn't required.
- **Standalone regression.** Guarded by test 6 (globals-set path unchanged) and the
  "globals win when set" ordering in `_resolve_run_state`.

## Rollout

No API/schema change to any tool; no new tool. `agent_deduplicate`/`match_sources`
gain a side effect (store the session) that is inert when there's no session id
(standalone stdio, or the global path). The 8 tools stop crashing and start working
on the suite endpoint. No version bump required for behavior fixes unless a
package's release policy wants one; the version-consistency gate is unaffected.
The suite's `_CURATED_DESCRIPTION_SUFFIXES` need no change; remove the stale
"non-functional" comment from #1705.
