"""Opt-in, PII-safe product analytics.

NOT the controller "telemetry" surface (auto-config introspection, see
``goldenmatch.web.controller_telemetry``). This module sends anonymous USAGE
events to PostHog so maintainers can see real engagement -- the signal PyPI
download counts can't show (retention, which surfaces/features get used).

Hard guarantees (this is a privacy-positioned tool that ships PPRL -- the bar
is non-negotiable):

* OFF BY DEFAULT. Emits only when ``GOLDENMATCH_ANALYTICS`` is truthy AND a
  ``POSTHOG_API_KEY`` is set. Owned hosted services (docs, the Railway MCP
  deployment) set the flag; a user's machine stays silent unless they opt in.
* PII-FREE BY CONSTRUCTION. ``capture()`` accepts only a fixed allow-list of
  scalar property keys, and drops any value that looks like a path or is
  over-long. It NEVER receives row values, column names, file paths, queries,
  or data of any kind -- callers pass pre-bucketed scalars (scale band, backend
  name, version). There is no code path that forwards user data.
* FAIL-OPEN. Never raises, never blocks. The HTTP post is fire-and-forget on a
  daemon thread with a short timeout; every error is swallowed.
* ANONYMOUS. A random UUID4 at ``~/.goldenmatch/analytics_id`` (no machine
  fingerprinting). Delete it to reset; ``GOLDENMATCH_ANALYTICS=0`` stops it.
"""
from __future__ import annotations

import json
import os
import platform
import threading
import urllib.request
import uuid
from pathlib import Path

_TRUTHY = frozenset({"1", "true", "on", "yes"})

# The ONLY property keys allowed off the wire. Each is a non-PII scalar produced
# by goldenmatch itself, never a value derived from the user's records. Anything
# not in this set is dropped by _build_payload -- adding a key here is a
# deliberate privacy decision, reviewed in tests/test_analytics.py.
_ALLOWED_PROPS = frozenset({
    "surface",         # library | cli | mcp | web | tui
    "command",         # CLI command name (static, from our own command table)
    "tool",            # MCP tool name (static, from our own tool table)
    "backend",         # polars-direct | bucket | chunked | duckdb | ray
    "row_bucket",      # scale band, e.g. "10K-100K" (never the exact count)
    "duration_bucket", # wall band, e.g. "1-10s"
    "result_bucket",   # cluster-count band
    "scorer_count",    # int: number of scorers configured
    "matchkey_count",  # int: number of matchkeys
    "native_available",# bool: is the rust kernel present
    "planning_effort", # fast | normal | thinking | einstein
    "config_source",   # auto | explicit
    "had_reference",   # bool: dedupe vs match
    "mode",            # free scalar for small enums set by us
})

_MAX_STR = 64


def _api_key() -> str | None:
    return os.environ.get("POSTHOG_API_KEY", "").strip() or None


def analytics_enabled() -> bool:
    """True only when explicitly opted in AND a key is configured."""
    if os.environ.get("GOLDENMATCH_ANALYTICS", "").strip().lower() not in _TRUTHY:
        return False
    return _api_key() is not None


def _host() -> str:
    return os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com").rstrip("/")


def _version() -> str:
    try:
        from goldenmatch import __version__  # late import; avoids cycle
        return str(__version__)
    except Exception:
        return "unknown"


def _distinct_id() -> str:
    """Stable, anonymous, regenerable install id. No hardware fingerprinting."""
    p = Path.home() / ".goldenmatch" / "analytics_id"
    try:
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or "anonymous"
        p.parent.mkdir(parents=True, exist_ok=True)
        new = str(uuid.uuid4())
        p.write_text(new, encoding="utf-8")
        return new
    except Exception:
        return "anonymous"


def scale_bucket(n: int) -> str:
    """Bucket a row/pair count so an exact (potentially identifying) size never leaves."""
    for hi, label in (
        (100, "<100"), (1_000, "100-1K"), (10_000, "1K-10K"),
        (100_000, "10K-100K"), (1_000_000, "100K-1M"), (10_000_000, "1M-10M"),
    ):
        if n < hi:
            return label
    return "10M+"


def duration_bucket(seconds: float) -> str:
    for hi, label in ((1, "<1s"), (10, "1-10s"), (60, "10-60s"), (600, "1-10m")):
        if seconds < hi:
            return label
    return "10m+"


def _safe_value(v):
    """Coerce to a non-PII scalar or drop. Belt-and-suspenders over the key
    allow-list: cap strings and reject anything path-like."""
    if isinstance(v, bool) or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        if len(v) > _MAX_STR or "/" in v or "\\" in v:
            return None
        return v
    return None


def _build_payload(event: str, properties: dict | None) -> dict:
    props: dict = {}
    for k, v in (properties or {}).items():
        if k not in _ALLOWED_PROPS:
            continue
        sv = _safe_value(v)
        if sv is not None:
            props[k] = sv
    # Auto props are produced here, not by callers -- always non-PII.
    props["gm_version"] = _version()
    props["python_version"] = platform.python_version()
    props["os"] = platform.system()
    props["$process_person_profile"] = False  # event-only; no person profile bloat
    return {
        "api_key": _api_key(),
        "event": event,
        "distinct_id": _distinct_id(),
        "properties": props,
    }


def _post(payload: dict) -> None:
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{_host()}/capture/", data=data,
            headers={"Content-Type": "application/json", "User-Agent": "goldenmatch-analytics"},
        )
        urllib.request.urlopen(req, timeout=2.0).close()
    except Exception:
        pass  # fail-open: analytics never affects the user


def _emit(payload: dict) -> None:
    threading.Thread(target=_post, args=(payload,), daemon=True).start()


def capture(event: str, properties: dict | None = None) -> None:
    """Record an anonymous usage event. No-op unless opted in. Never raises."""
    try:
        if not analytics_enabled():
            return
        _emit(_build_payload(event, properties))
    except Exception:
        pass
