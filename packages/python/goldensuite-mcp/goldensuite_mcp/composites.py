from __future__ import annotations

import os
from collections.abc import Callable

from mcp.types import Tool

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


# ---------------------------------------------------------------------------
# Composite orchestrations
# ---------------------------------------------------------------------------

_FILE_INPUT_PROPS = {
    "file_content": {"type": "string", "description": "Inline file bytes (base64 or text)."},
    "filename": {"type": "string", "description": "Original filename for an inline upload."},
    "file_path": {"type": "string", "description": "Server path to an already-uploaded file."},
    "encoding": {"type": "string", "description": "Inline content encoding: 'base64' (default) or 'text'."},
}


def _gen_output_path(src_path: str, suffix: str) -> str:
    stem, _ = os.path.splitext(src_path)
    return f"{stem}.{suffix}.csv"


def _upload(dispatch, args):
    """Resolve the input to a server path: pass inline bytes or an existing path."""
    up = {k: args[k] for k in ("file_content", "filename", "file_path", "encoding") if k in args}
    if args.get("file_path") and "file_content" not in args:
        return True, {"path": args["file_path"], "filename": os.path.basename(args["file_path"])}, "upload"
    ok, res = run_step(dispatch, "upload_dataset", up)
    return ok, res, "upload"


def orchestrate_dedupe_file(dispatch, args):
    steps = []
    ok, res, label = _upload(dispatch, args)
    # NOTE: upload_dataset returns {path, bytes, filename} -- no row count; don't record "rows".
    steps.append({"step": label, "ok": ok, **({"path": res.get("path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("dedupe_file", steps)
    path = res["path"]

    excl = {"exclude_columns": args["exclude_columns"]} if args.get("exclude_columns") else {}
    ok, res = run_step(dispatch, "auto_configure", {"file_path": path, **excl})
    steps.append({"step": "auto_configure", "ok": ok, **({"config": res.get("config") or res} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("dedupe_file", steps)
    config = res.get("config") or res

    golden_path = _gen_output_path(path, "golden")
    ok, res = run_step(dispatch, "agent_deduplicate",
                       {"file_path": path, "config": config, "output_path": golden_path, **excl})
    cd = res.get("confidence_distribution", {}) if ok else {}
    steps.append({"step": "deduplicate", "ok": ok,
                  **({"auto_merge": cd.get("auto_merged"), "review": cd.get("review"),
                      "reject": cd.get("auto_rejected"), "golden_path": res.get("golden_path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("dedupe_file", steps)

    outputs = {"golden_path": res.get("golden_path"),
               "golden_records": res.get("golden_records"),
               "total_records": (res.get("results") or {}).get("total_records")}
    return _finish("dedupe_file", steps, config=config, outputs=outputs)


def _finish(workflow, steps, config=None, outputs=None, degraded_steps=frozenset()):
    ok = all(s["ok"] for s in steps if s["step"] not in degraded_steps)
    out = {"workflow": workflow, "ok": ok, "summary": _summarize(workflow, steps, outputs, degraded_steps), "steps": steps}
    if config is not None:
        out["config"] = config
    if outputs is not None:
        out["outputs"] = outputs
    return out


def _summarize(workflow, steps, outputs, degraded_steps=frozenset()):
    if not all(s["ok"] for s in steps if s["step"] not in degraded_steps):
        bad = next(s for s in steps if not s["ok"] and s["step"] not in degraded_steps)
        return f"{workflow} failed at step '{bad['step']}': {bad.get('error')}"
    if workflow == "dedupe_file" and outputs:
        d = next((s for s in steps if s["step"] == "deduplicate"), {})
        return (f"{outputs.get('total_records')} records -> {outputs.get('golden_records')} golden; "
                f"{d.get('auto_merge')} merged, {d.get('review')} to review. Written to {outputs.get('golden_path')}.")
    return f"{workflow} ok"


_COMPOSITE_SPECS = [
    {"name": "dedupe_file",
     "description": "One call: dedupe a single CSV. Uploads the file, auto-configures matching, runs entity resolution with confidence gating, and writes golden (deduplicated) records to a CSV. Returns a summary + the golden file path.",
     "inputSchema": {"type": "object", "properties": {**_FILE_INPUT_PROPS,
        "exclude_columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to exclude from matching."}}, "required": []},
     "orchestrate": orchestrate_dedupe_file},
]


def build_composites(dispatch):
    tools, table = [], {}
    for spec in _COMPOSITE_SPECS:
        tools.append(Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"]))
        fn = spec["orchestrate"]
        table[spec["name"]] = (lambda f: (lambda name, args: f(dispatch, args or {})))(fn)
    return tools, table
