from __future__ import annotations
from collections.abc import Callable

Dispatch = dict[str, Callable[[str, dict], dict]]


def run_step(dispatch: Dispatch, tool_name: str, args: dict) -> tuple[bool, dict]:
    """Run one composite step. Returns (ok, result). A missing tool, a raised
    exception, or a returned {"error": ...} are all failures."""
    handler = dispatch.get(tool_name)
    if handler is None:
        return False, {"error": f"tool {tool_name!r} not available in this suite build"}
    try:
        result = handler(tool_name, args or {})
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"{type(exc).__name__}: {exc}"}
    if isinstance(result, dict) and "error" in result:
        return False, result
    return True, result
