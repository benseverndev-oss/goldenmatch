from __future__ import annotations

import logging
import os
from collections.abc import Callable

from mcp.types import Tool

logger = logging.getLogger(__name__)

Dispatch = dict[str, Callable[[str, dict], dict]]

# GoldenPipe runs check->flow->dedupe in ONE process on an in-memory frame, so a
# composite can chain the stages without writing intermediate CSVs to disk. It is
# a hard dependency of this package (`goldenpipe[mcp]`); the guard only matters in
# a stripped build, where the composite falls back to the tool-dispatch chain.
try:
    import goldenpipe as _gp

    HAS_PIPE = True
except ImportError:  # pragma: no cover - goldenpipe is a declared dependency
    HAS_PIPE = False
    _gp = None


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
    if ok and not res.get("path"):
        # Guard the happy-path assumption: a malformed upload return (no path)
        # degrades to a clean composite failure instead of a KeyError downstream.
        return False, {"error": f"upload_dataset returned no path: {res}"}, label
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
    # Surface auto_configure's result for transparency, but do NOT hand it to
    # agent_deduplicate: the MCP tool's `config` param expects a real Config
    # object (or None), not auto_configure's serialized display dict -- passing
    # the dict dies with `'dict' object has no attribute 'get_matchkeys'`. Omit
    # config so agent_deduplicate auto-configures internally (config=None path).
    config = res.get("config") or res

    golden_path = _gen_output_path(path, "golden")
    ok, res = run_step(dispatch, "agent_deduplicate",
                       {"file_path": path, "output_path": golden_path, **excl})
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


def _golden_to_polars(golden):
    """Normalize a DedupeStage `golden` artifact to a Polars frame for writing.

    Depending on the goldenmatch build, `result.golden` is a `pl.DataFrame`
    (has `.write_csv`) or a `pa.Table` (Arrow-native path). Returns None for an
    unrecognized/absent value so the caller degrades cleanly."""
    if golden is None:
        return None
    if hasattr(golden, "write_csv"):  # polars.DataFrame
        return golden
    try:
        import polars as pl
        import pyarrow as pa

        if isinstance(golden, pa.Table):
            return pl.from_arrow(golden)
    except Exception:  # pragma: no cover - defensive
        logger.exception("clean_and_dedupe: could not normalize golden artifact")
    return None


def _confidence_from_scored_pairs(scored_pairs):
    """Reconstruct the auto_merge/review/reject buckets agent_deduplicate reports.

    GoldenPipe runs plain `dedupe_df` (no review gating), so it surfaces
    `scored_pairs` but not the confidence distribution. Re-derive it with the
    same thresholds AgentSession uses (gate_pairs: >0.95 merge, 0.75-0.95
    review, <0.75 reject). Returns {} if pairs are absent or gating is
    unavailable."""
    if not scored_pairs:
        return {}
    try:
        from goldenmatch.core.review_queue import gate_pairs

        merged, review, rejected = gate_pairs(list(scored_pairs))
        return {"auto_merged": len(merged), "review": len(review), "auto_rejected": len(rejected)}
    except Exception:  # pragma: no cover - defensive
        logger.exception("clean_and_dedupe: could not reconstruct confidence distribution")
        return {}


def _run_pipeline_inprocess(path, exclude_columns, golden_path):
    """Run GoldenPipe's check->flow->dedupe chain in-process on `path` and write
    golden once. No intermediate CSVs. Returns the fields the composite summary
    needs, or {"error": ...} on failure."""
    token = None
    if exclude_columns:
        # Same mechanism agent_deduplicate uses to exclude columns from matching;
        # threads into GoldenPipe's internal auto-config (DedupeStage calls the
        # goldenmatch controller).
        from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS

        token = _RUNTIME_EXCLUDE_COLUMNS.set(list(exclude_columns))
    try:
        result = _gp.run(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        if token is not None:
            from goldenmatch.core.autoconfig import _RUNTIME_EXCLUDE_COLUMNS

            _RUNTIME_EXCLUDE_COLUMNS.reset(token)

    if result.status.value == "failed":
        return {"error": "; ".join(result.errors) or "pipeline failed"}

    golden = _golden_to_polars(result.artifacts.get("golden"))
    stats = result.artifacts.get("match_stats") or {}
    conf = _confidence_from_scored_pairs(result.artifacts.get("scored_pairs"))
    if golden is not None:
        golden.write_csv(golden_path)
    return {
        "golden_path": golden_path if golden is not None else None,
        "golden_records": golden.height if golden is not None else None,
        "total_records": stats.get("total_records"),
        "confidence_distribution": conf,
    }


def orchestrate_clean_and_dedupe(dispatch, args):
    """Normalize then dedupe. In-process (default): upload -> one GoldenPipe
    check->flow->dedupe run, no intermediate CSV. Fallback (no goldenpipe):
    upload -> run_transforms -> agent_deduplicate over a cleaned CSV on disk."""
    steps = []
    ok, res, label = _upload(dispatch, args)
    steps.append({"step": label, "ok": ok, **({"path": res.get("path")} if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("clean_and_dedupe", steps)
    path = res["path"]

    if not HAS_PIPE:
        return _clean_and_dedupe_via_dispatch(dispatch, args, path, steps)

    golden_path = _gen_output_path(path, "golden")
    pr = _run_pipeline_inprocess(path, args.get("exclude_columns"), golden_path)
    if "error" in pr:
        steps.append({"step": "pipeline", "ok": False, "error": pr["error"]})
        return _finish("clean_and_dedupe", steps)
    cd = pr["confidence_distribution"]
    steps.append({"step": "pipeline", "ok": True,
                  "golden_path": pr["golden_path"], "auto_merge": cd.get("auto_merged"),
                  "review": cd.get("review"), "reject": cd.get("auto_rejected")})
    outputs = {"golden_path": pr["golden_path"], "golden_records": pr["golden_records"],
               "total_records": pr["total_records"]}
    return _finish("clean_and_dedupe", steps, outputs=outputs)


def _clean_and_dedupe_via_dispatch(dispatch, args, path, steps):
    """Legacy tool-dispatch chain (run_transforms -> agent_deduplicate over a
    cleaned CSV). Used only when goldenpipe is unavailable. `steps` already
    carries the upload step."""
    excl = {"exclude_columns": args["exclude_columns"]} if args.get("exclude_columns") else {}

    cleaned_path = _gen_output_path(path, "cleaned")
    ok, res = run_step(dispatch, "run_transforms", {"file_path": path, "output_path": cleaned_path})
    steps.append({"step": "clean", "ok": ok,
                  **({"cleaned_path": res.get("output_path"), "transforms_applied": res.get("transforms_applied")}
                     if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("clean_and_dedupe", steps)
    cleaned = res.get("output_path") or cleaned_path

    golden_path = _gen_output_path(cleaned, "golden")
    ok, res = run_step(dispatch, "agent_deduplicate",
                       {"file_path": cleaned, "output_path": golden_path, **excl})
    cd = res.get("confidence_distribution", {}) if ok else {}
    steps.append({"step": "deduplicate", "ok": ok,
                  **({"auto_merge": cd.get("auto_merged"), "review": cd.get("review"),
                      "reject": cd.get("auto_rejected"), "golden_path": res.get("golden_path")}
                     if ok else {"error": res.get("error")})})
    if not ok:
        return _finish("clean_and_dedupe", steps)

    outputs = {"cleaned_path": cleaned, "golden_path": res.get("golden_path"),
               "golden_records": res.get("golden_records"),
               "total_records": (res.get("results") or {}).get("total_records")}
    return _finish("clean_and_dedupe", steps, outputs=outputs)


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
    if workflow == "clean_and_dedupe" and outputs:
        p = next((s for s in steps if s["step"] == "pipeline"), None)
        if p:  # in-process GoldenPipe path
            return (f"{outputs.get('total_records')} records cleaned + deduped in-process -> "
                    f"{outputs.get('golden_records')} golden; {p.get('auto_merge')} merged, "
                    f"{p.get('review')} to review. Written to {outputs.get('golden_path')}.")
        c = next((s for s in steps if s["step"] == "clean"), {})
        d = next((s for s in steps if s["step"] == "deduplicate"), {})
        return (f"{outputs.get('total_records')} records cleaned "
                f"({c.get('transforms_applied')} transforms) -> "
                f"{outputs.get('golden_records')} golden; {d.get('auto_merge')} merged, "
                f"{d.get('review')} to review. Written to {outputs.get('golden_path')}.")
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
    {"name": "clean_and_dedupe",
     "description": "One call: clean then dedupe a single CSV. Uploads the file, runs GoldenFlow transforms (phone/date/unicode/categorical normalization), then runs entity resolution on the cleaned data and writes golden records. Returns a summary + the golden file path. Requires the transform extra for the clean step.",
     "inputSchema": {"type": "object", "properties": {**_FILE_INPUT_PROPS,
        "exclude_columns": {"type": "array", "items": {"type": "string"}, "description": "Columns to exclude from matching."}}, "required": []},
     "orchestrate": orchestrate_clean_and_dedupe},
]


def build_composites(dispatch):
    tools, table = [], {}
    for spec in _COMPOSITE_SPECS:
        tools.append(Tool(name=spec["name"], description=spec["description"], inputSchema=spec["inputSchema"]))
        fn = spec["orchestrate"]
        table[spec["name"]] = (lambda f: (lambda name, args: f(dispatch, args or {})))(fn)
    return tools, table
