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


def _upload_named(dispatch, args, content_key, name_key, path_key, label):
    """Resolve one named input to a server path. Maps the caller's per-file keys
    (e.g. file_a_content/file_a_name/file_a) onto upload_dataset's standard
    file_content/filename/file_path keys. An existing path skips the upload."""
    if args.get(path_key) and content_key not in args:
        return True, {"path": args[path_key], "filename": os.path.basename(args[path_key])}, label
    up = {}
    if content_key in args:
        up["file_content"] = args[content_key]
    if name_key in args:
        up["filename"] = args[name_key]
    if path_key in args:
        up["file_path"] = args[path_key]
    if "encoding" in args:
        up["encoding"] = args["encoding"]
    ok, res = run_step(dispatch, "upload_dataset", up)
    return ok, res, label


def _upload(dispatch, args):
    """Resolve the single-file input to a server path (default key names)."""
    return _upload_named(dispatch, args, "file_content", "filename", "file_path", "upload")


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


def orchestrate_match_sources(dispatch, args):
    """Link two sources: upload both -> agent_match_sources -> surface matches."""
    steps = []
    ok, res, label = _upload_named(dispatch, args, "file_a_content", "file_a_name", "file_a", "upload_a")
    steps.append({"step": label, "ok": ok, **({"path": res.get("path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("match_sources", steps)
    path_a = res["path"]

    ok, res, label = _upload_named(dispatch, args, "file_b_content", "file_b_name", "file_b", "upload_b")
    steps.append({"step": label, "ok": ok, **({"path": res.get("path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("match_sources", steps)
    path_b = res["path"]

    matches_path = _gen_output_path(path_a, "matches")
    excl = {"exclude_columns": args["exclude_columns"]} if args.get("exclude_columns") else {}
    ok, res = run_step(dispatch, "agent_match_sources",
                       {"file_a": path_a, "file_b": path_b, "output_path": matches_path, **excl})
    results = (res.get("results") or {}) if ok else {}
    m_path = (res.get("matches_path") or res.get("output_path")) if ok else None
    m_pairs = res.get("matched_pairs") if ok else None
    if ok and m_pairs is None:
        m_pairs = results.get("total_matched_records") or results.get("scored_pairs")
    steps.append({"step": "match", "ok": ok,
                  **({"matches_path": m_path, "matched_pairs": m_pairs} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("match_sources", steps)

    outputs = {"matches_path": m_path, "matched_pairs": m_pairs, "match_rate": results.get("match_rate")}
    return _finish("match_sources", steps, outputs=outputs)


def orchestrate_assess_file(dispatch, args):
    """Read-only health check: upload -> analyze_data -> scan. The goldencheck
    `scan` step is optional -- if goldencheck isn't in the build, the composite
    still succeeds (degraded) and reports the profile without a quality grade."""
    steps = []
    degraded = {"scan"}
    ok, res, label = _upload(dispatch, args)
    steps.append({"step": label, "ok": ok, **({"path": res.get("path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("assess_file", steps, degraded_steps=degraded)
    path = res["path"]

    ok, res = run_step(dispatch, "analyze_data", {"file_path": path})
    rows = (res.get("total_records") or res.get("rows") or res.get("row_count")) if ok else None
    steps.append({"step": "analyze", "ok": ok,
                  **({"rows": rows, "profile": res} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("assess_file", steps, degraded_steps=degraded)

    ok, res = run_step(dispatch, "scan", {"file_path": path})
    steps.append({"step": "scan", "ok": ok,
                  **({"health_grade": res.get("health_grade"), "health_score": res.get("health_score"),
                      "total_findings": res.get("total_findings")} if ok else {"error": res.get("error")})})
    return _finish("assess_file", steps, degraded_steps=degraded)


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
    if workflow == "assess_file":
        a = next((s for s in steps if s["step"] == "analyze"), {})
        sc = next((s for s in steps if s["step"] == "scan"), {})
        rows = a.get("rows")
        if sc.get("ok"):
            return (f"{rows} rows profiled; health {sc.get('health_grade')} "
                    f"(score {sc.get('health_score')}, {sc.get('total_findings')} findings).")
        return f"{rows} rows profiled; quality scan unavailable (goldencheck not in this build)."
    if workflow == "match_sources" and outputs:
        pairs = outputs.get("matched_pairs")
        dest = outputs.get("matches_path")
        tail = f" Written to {dest}." if dest else ""
        return f"Matched two sources: {pairs} matched pairs.{tail}"
    return f"{workflow} ok"


_COMPOSITE_SPECS = [
    {"name": "dedupe_file",
     "description": "One call: dedupe a single CSV. Uploads the file, auto-configures matching, runs entity resolution with confidence gating, and writes golden (deduplicated) records to a CSV. Returns a summary + the golden file path.",
     "inputSchema": {"type": "object", "properties": {**_FILE_INPUT_PROPS,
        "exclude_columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to exclude from matching."}}, "required": []},
     "orchestrate": orchestrate_dedupe_file},
    {"name": "assess_file",
     "description": "One call: assess a single file's quality. Uploads the file, profiles it (record count, detected domain, recommended matching strategy), then runs a data-quality scan (health grade + findings). Read-only -- writes nothing. The quality scan is skipped gracefully if goldencheck isn't installed.",
     "inputSchema": {"type": "object", "properties": {**_FILE_INPUT_PROPS}, "required": []},
     "orchestrate": orchestrate_assess_file},
    {"name": "match_sources",
     "description": "One call: link two sources. Uploads both files, then runs cross-source matching with intelligent strategy selection and returns the matched pairs (and a matches file path when the engine writes one).",
     "inputSchema": {"type": "object", "properties": {
        "file_a": {"type": "string", "description": "Server path to source A (already uploaded)."},
        "file_a_content": {"type": "string", "description": "Inline bytes for source A (base64 or text)."},
        "file_a_name": {"type": "string", "description": "Original filename for inline source A."},
        "file_b": {"type": "string", "description": "Server path to source B (already uploaded)."},
        "file_b_content": {"type": "string", "description": "Inline bytes for source B (base64 or text)."},
        "file_b_name": {"type": "string", "description": "Original filename for inline source B."},
        "encoding": {"type": "string", "description": "Inline content encoding: 'base64' (default) or 'text'."},
        "exclude_columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to exclude from matching."}},
        "required": []},
     "orchestrate": orchestrate_match_sources},
]


def build_composites(dispatch):
    tools, table = [], {}
    for spec in _COMPOSITE_SPECS:
        tools.append(Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"]))
        fn = spec["orchestrate"]
        table[spec["name"]] = (lambda f: (lambda name, args: f(dispatch, args or {})))(fn)
    return tools, table
