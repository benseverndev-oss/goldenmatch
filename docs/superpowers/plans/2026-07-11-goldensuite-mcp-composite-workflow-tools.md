# goldensuite-mcp Composite Workflow Tools Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four one-call "composite" workflow tools (`dedupe_file`, `match_sources`, `assess_file`, `clean_and_dedupe`) to the `goldensuite-mcp` aggregator, each orchestrating the underlying MCP dispatchers so an agent does a common multi-step path in a single call.

**Architecture:** Two phases. **Phase 1** (goldenmatch): add an optional `output_path` to `agent_deduplicate` / `agent_match_sources` so the stateless ER tools can write their golden/linked records to a CSV (today they return summaries only, and the base `export_results` tool is dead in the aggregator because it reads a module-global `_result` that is never populated there). **Phase 2** (goldensuite-mcp): a new `composites.py` module whose composite tools call the aggregated `name_to_dispatch` table, thread each step's output to the next, and return a merged `{summary, steps[], config, outputs}` shape; registered in `_aggregate` and added to `CURATED_TOOLS`.

**Tech Stack:** Python 3.11+, `mcp` (`mcp.types.Tool`), pytest, polars (already a goldenmatch dep). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-10-goldensuite-mcp-composite-workflow-tools-design.md`

**Prerequisites:** #1639 (curated listing) + #1640 (`suite_find_tools`) merged to `main`. Phase 2 rebases onto a `main` that also has Phase 1.

---

## Test environment (Windows worktree)

All pytest commands run through the main repo venv with the worktree packages on `PYTHONPATH` (worktree-skew playbook). Set once per shell:

```bash
cd /d/show_case/gm-composites
PP=$(for d in packages/python/*/; do echo -n "D:/show_case/gm-composites/${d%/};"; done)
RUN="GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 PYTHONPATH=$PP ../../show_case/goldenmatch/.venv/Scripts/python.exe -m pytest"
```

Then e.g. `eval "$RUN packages/python/goldensuite-mcp/tests/test_composites.py -q"`.

Commit with `git -c commit.gpgsign=false commit` and the standard Co-Authored-By / Claude-Session trailer.

---

## File Structure

**Phase 1 — goldenmatch (its own PR/branch `feat/goldenmatch-agent-output-path` off `main`):**
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` — `agent_deduplicate` + `agent_match_sources` handlers (write golden/linked CSV when `output_path` given) and their two `Tool` definitions (add `output_path` to `inputSchema`).
- Test: `packages/python/goldenmatch/tests/test_agent_output_path.py` (new).

**Phase 2 — goldensuite-mcp (`feat/goldensuite-mcp-composites`, rebased onto Phase-1 main):**
- Create: `packages/python/goldensuite-mcp/goldensuite_mcp/composites.py` — composite specs, `run_step`, `build_composites`.
- Modify: `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` — call `build_composites` in `_aggregate`; add composite names to `CURATED_TOOLS`.
- Test: `packages/python/goldensuite-mcp/tests/test_composites.py` (new).
- Modify: `packages/python/goldensuite-mcp/README.md`, `CHANGELOG.md`, `pyproject.toml`, `goldensuite_mcp/__init__.py` (version bump, lockstep).

---

# Phase 1 — goldenmatch golden-out

### Task 1.1: Spike — pin how golden records come out of the stateless dedupe path

**Files:**
- Test: `packages/python/goldenmatch/tests/test_agent_output_path.py`

- [ ] **Step 1: Write a probe test that asserts the golden frame is reachable**

```python
# packages/python/goldenmatch/tests/test_agent_output_path.py
from pathlib import Path
import polars as pl
from goldenmatch.core.agent import AgentSession

def _fixture_csv(tmp_path: Path) -> str:
    df = pl.DataFrame({
        "name": ["John Smith", "Jon Smith", "Mary Jones", "Karen White"],
        "email": ["j@x.com", "j@x.com", "m@y.com", "k@z.com"],
    })
    p = tmp_path / "in.csv"
    df.write_csv(p)
    return str(p)

def test_dedupe_result_exposes_golden(tmp_path):
    raw = AgentSession().deduplicate(_fixture_csv(tmp_path))
    result = raw["results"]
    # The golden frame must be reachable and writable. If this fails with
    # golden is None, the pipeline ran with output_golden=False -> see step 3.
    assert getattr(result, "golden", None) is not None
    assert result.golden.height >= 1
```

- [ ] **Step 2: Run it to see whether golden is populated by default**

Run: `eval "$RUN packages/python/goldenmatch/tests/test_agent_output_path.py::test_dedupe_result_exposes_golden -q"`
Expected: either PASS (golden populated by default — good) or FAIL with `golden is None`.

- [ ] **Step 3: If golden is None, find how to enable it**

If FAIL: inspect `dedupe_df` in `goldenmatch/_api.py` and `run_pipeline`/`output_golden` in `goldenmatch/core/pipeline.py`. Determine the call that populates golden (likely `dedupe_df(df, output_golden=True)` or a config flag). Record the exact call in a comment at the top of the test file — Task 1.2 uses it. If golden is populated by default, no change needed; note that.

- [ ] **Step 4: Commit the pinned probe**

```bash
git add packages/python/goldenmatch/tests/test_agent_output_path.py
git -c commit.gpgsign=false commit -m "test(goldenmatch): pin stateless golden-frame extraction for agent output_path"
```

### Task 1.2: `agent_deduplicate` writes golden CSV when `output_path` is given

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` — `agent_deduplicate` handler (~line 644-665) + its `Tool` definition (~line 150).
- Test: `packages/python/goldenmatch/tests/test_agent_output_path.py`

- [ ] **Step 1: Write the failing test**

```python
def test_agent_deduplicate_writes_golden(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch
    from goldenmatch.core.agent import AgentSession
    out = tmp_path / "golden.csv"
    res = _dispatch(
        "agent_deduplicate",
        {"file_path": _fixture_csv(tmp_path), "output_path": str(out)},
        AgentSession,
    )
    assert res["golden_path"] == str(out)
    assert res["golden_records"] >= 1
    assert out.exists()
    got = pl.read_csv(out)
    assert got.height == res["golden_records"]
    # internal columns are stripped
    assert not any(c.startswith("__") for c in got.columns)

def test_agent_deduplicate_no_output_path_unchanged(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch
    from goldenmatch.core.agent import AgentSession
    res = _dispatch("agent_deduplicate", {"file_path": _fixture_csv(tmp_path)}, AgentSession)
    assert "golden_path" not in res
    assert "results" in res  # existing summary contract intact
```

- [ ] **Step 2: Run to verify it fails**

Run: `eval "$RUN packages/python/goldenmatch/tests/test_agent_output_path.py -q"`
Expected: `test_agent_deduplicate_writes_golden` FAILS (`KeyError: 'golden_path'`); the `_unchanged` test PASSES.

- [ ] **Step 3: Implement — write golden in the handler**

In `agent_tools.py`, the `agent_deduplicate` branch: after `raw = session.deduplicate(...)`, before building the summary return, add (using the extraction pinned in Task 1.1; `raw["results"].golden` shown here):

```python
        golden_extra: dict = {}
        output_path = args.get("output_path")
        if output_path:
            safe = _ingest.safe_path(output_path)  # allowed-root guard; see _ingest
            gres = raw.get("results")
            golden = getattr(gres, "golden", None)
            if golden is None:
                golden_extra = {"golden_path": None, "golden_error": "no golden frame produced"}
            else:
                cols = [c for c in golden.columns if not c.startswith("__")]
                golden.select(cols).write_csv(str(safe))
                golden_extra = {"golden_path": str(safe), "golden_records": golden.height}
```

Then merge `golden_extra` into the returned dict (`return {..., **golden_extra}`). Confirm `_ingest.safe_path` (or the existing `_safe_path_or_error`) is the right guard — reuse whatever the file-writing tools already use; do not invent a new one.

- [ ] **Step 4: Run to verify pass**

Run: `eval "$RUN packages/python/goldenmatch/tests/test_agent_output_path.py -q"`
Expected: all PASS.

- [ ] **Step 5: Add `output_path` to the `agent_deduplicate` Tool inputSchema**

In the `agent_deduplicate` `Tool(...)` definition (~line 150), add to `inputSchema.properties`:

```python
                "output_path": {
                    "type": "string",
                    "description": "Optional. If given, write the golden (deduplicated) records to this CSV path and return golden_path + golden_records. Omit to get the summary only.",
                },
```

- [ ] **Step 6: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py packages/python/goldenmatch/tests/test_agent_output_path.py
git -c commit.gpgsign=false commit -m "feat(goldenmatch): agent_deduplicate optional output_path writes golden CSV"
```

### Task 1.3: `agent_match_sources` writes linked-pairs CSV when `output_path` is given

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` — `agent_match_sources` handler (~line 667-686) + its `Tool` (~line 169).
- Test: `packages/python/goldenmatch/tests/test_agent_output_path.py`

- [ ] **Step 1: Write the failing test**

```python
def _two_fixtures(tmp_path):
    a = tmp_path / "a.csv"; b = tmp_path / "b.csv"
    pl.DataFrame({"name": ["John Smith", "Mary Jones"], "id": [1, 2]}).write_csv(a)
    pl.DataFrame({"name": ["Jon Smith", "Karen White"], "id": [9, 8]}).write_csv(b)
    return str(a), str(b)

def test_agent_match_sources_writes_matches(tmp_path):
    from goldenmatch.mcp.agent_tools import _dispatch
    from goldenmatch.core.agent import AgentSession
    a, b = _two_fixtures(tmp_path)
    out = tmp_path / "matches.csv"
    res = _dispatch("agent_match_sources",
                    {"file_a": a, "file_b": b, "output_path": str(out)}, AgentSession)
    assert res["matches_path"] == str(out)
    assert out.exists()
```

Inspect what `session.match_sources(...)["results"]` exposes for the linked frame (Task 1.1-style probe if needed — it may be `.golden`, `.matches`, or `.linked`). Use the actual attribute; assert `matched_pairs` count if available.

- [ ] **Step 2: Run to verify it fails** — `KeyError: 'matches_path'`.

- [ ] **Step 3: Implement** the same `output_path` pattern in the `agent_match_sources` branch, writing the linked/matched frame (attribute confirmed above), returning `{"matches_path": ..., "matched_pairs": N}`.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Add `output_path` to the `agent_match_sources` Tool inputSchema** (same property text, "matched pairs" wording).

- [ ] **Step 6: Commit.**

### Task 1.4: Phase 1 PR

- [ ] Push `feat/goldenmatch-agent-output-path`, open PR to `main`, arm auto-merge (`gh pr merge <n> --auto --squash`). Phase 2 waits for this to land.

---

# Phase 2 — composites (goldensuite-mcp)

> Rebase `feat/goldensuite-mcp-composites` onto a `main` containing Phase 1 + #1639/#1640 before starting Task 2.2's dedupe end-to-end. `assess_file` (Task 2.4) needs no Phase 1.

### Task 2.1: `composites.py` scaffold + `run_step` helper

**Files:**
- Create: `packages/python/goldensuite-mcp/goldensuite_mcp/composites.py`
- Test: `packages/python/goldensuite-mcp/tests/test_composites.py`

- [ ] **Step 1: Write the failing test for `run_step`**

```python
# tests/test_composites.py
from goldensuite_mcp.composites import run_step

def _table(**tools):
    # tools: name -> callable(name, args) -> dict
    return dict(tools)

def test_run_step_success():
    t = _table(foo=lambda n, a: {"value": a["x"] + 1})
    ok, res = run_step(t, "foo", {"x": 1})
    assert ok is True and res == {"value": 2}

def test_run_step_error_dict_is_failure():
    t = _table(foo=lambda n, a: {"error": "boom"})
    ok, res = run_step(t, "foo", {})
    assert ok is False and "boom" in res["error"]

def test_run_step_raise_is_failure():
    def boom(n, a): raise ValueError("kaboom")
    ok, res = run_step(_table(foo=boom), "foo", {})
    assert ok is False and "kaboom" in res["error"]

def test_run_step_missing_tool_is_failure():
    ok, res = run_step({}, "nope", {})
    assert ok is False and "nope" in res["error"]
```

- [ ] **Step 2: Run to verify it fails** — `ModuleNotFoundError: goldensuite_mcp.composites`.

- [ ] **Step 3: Implement `run_step`**

```python
# goldensuite_mcp/composites.py
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
```

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): composites run_step helper`).

### Task 2.2: `dedupe_file` composite (reference implementation)

**Files:**
- Modify: `goldensuite_mcp/composites.py`
- Test: `tests/test_composites.py`

- [ ] **Step 1: Write failing unit test with a fake dispatch table**

```python
def _fake_dedupe_table(rec):
    # rec: list to record calls
    def upload(n, a): rec.append(("upload", a)); return {"path": "/up/in.csv", "bytes": 10, "filename": "in.csv"}
    def autoconf(n, a): rec.append(("autoconf", a)); return {"config": {"matchkeys": ["exact(email)"]}}
    def dedup(n, a):
        rec.append(("dedup", a))
        return {"confidence_distribution": {"auto_merged": 2, "review": 1, "auto_rejected": 0},
                "golden_path": a["output_path"], "golden_records": 3, "results": {"total_records": 4}}
    return {"upload_dataset": upload, "auto_configure": autoconf, "agent_deduplicate": dedup}

def test_dedupe_file_shape_and_threading():
    from goldensuite_mcp.composites import build_composites
    rec = []
    tools, dispatch = build_composites(_fake_dedupe_table(rec))
    out = dispatch["dedupe_file"]("dedupe_file", {"file_content": "...", "filename": "in.csv"})
    assert out["workflow"] == "dedupe_file"
    assert out["ok"] is True
    steps = [s["step"] for s in out["steps"]]
    assert steps == ["upload", "auto_configure", "deduplicate"]
    # threading: uploaded path reached later steps
    assert dict(rec)["autoconf"]["file_path"] == "/up/in.csv"
    assert dict(rec)["dedup"]["file_path"] == "/up/in.csv"
    # golden path generated + surfaced in outputs
    assert out["outputs"]["golden_path"].endswith(".golden.csv")
    assert isinstance(out["summary"], str) and out["summary"]

def test_dedupe_file_short_circuits_on_step_failure():
    from goldensuite_mcp.composites import build_composites
    tbl = _fake_dedupe_table([])
    tbl["auto_configure"] = lambda n, a: {"error": "autoconf boom"}
    _, dispatch = build_composites(tbl)
    out = dispatch["dedupe_file"]("dedupe_file", {"file_content": "...", "filename": "in.csv"})
    assert out["ok"] is False
    assert [s["step"] for s in out["steps"]] == ["upload", "auto_configure"]
    assert "boom" in out["summary"].lower() or "auto_configure" in out["summary"]
```

- [ ] **Step 2: Run to verify it fails** — `ImportError: build_composites`.

- [ ] **Step 3: Implement `build_composites` + the `dedupe_file` orchestrator**

Add to `composites.py`: an `_gen_output_path(src_path, suffix)` helper that derives a sibling path under the same dir (e.g. `<stem>.golden.csv`) — this keeps the output under the uploads/allowed-root dir that `upload_dataset` already returned. Implement `orchestrate_dedupe_file(dispatch, args)` following the spec chain (upload → auto_configure → agent_deduplicate with generated `output_path`), building `steps` via `run_step`, short-circuiting on `ok is False`, and returning the merged dict. Register it in `build_composites` with its `Tool` (inputSchema mirrors `upload_dataset`: `file_content`, `filename`, `file_path`, `encoding`, plus `exclude_columns`). Full code:

```python
import os
from mcp.types import Tool

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
    steps.append({"step": label, "ok": ok, **({"path": res.get("path"), "rows": res.get("rows")} if ok else {"error": res.get("error")})})
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

def _finish(workflow, steps, config=None, outputs=None):
    ok = all(s["ok"] for s in steps)
    out = {"workflow": workflow, "ok": ok, "summary": _summarize(workflow, steps, outputs), "steps": steps}
    if config is not None:
        out["config"] = config
    if outputs is not None:
        out["outputs"] = outputs
    return out

def _summarize(workflow, steps, outputs):
    if not all(s["ok"] for s in steps):
        bad = next(s for s in steps if not s["ok"])
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
```

- [ ] **Step 4: Run to verify pass** — `eval "$RUN packages/python/goldensuite-mcp/tests/test_composites.py -q"`.

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): dedupe_file composite`).

### Task 2.3: Register composites in `_aggregate` + curate

**Files:**
- Modify: `goldensuite_mcp/server.py` (`_aggregate`, `CURATED_TOOLS`)
- Test: `tests/test_composites.py`

- [ ] **Step 1: Write the failing integration test**

```python
def test_dedupe_file_registered_and_curated():
    from goldensuite_mcp.server import _aggregate, _apply_tool_filter
    import os
    os.environ.pop("GOLDENSUITE_MCP_TOOLS", None)
    tools, dispatch = _aggregate()
    names = {t.name for t in tools}
    assert "dedupe_file" in names
    assert "dedupe_file" in dispatch
    listed = {t.name for t in _apply_tool_filter(tools)}
    assert "dedupe_file" in listed  # curated by default
    # discoverable via suite_find_tools
    out = dispatch["suite_find_tools"]("suite_find_tools", {})
    assert "dedupe_file" in {r["name"] for r in out["tools"]}
```

- [ ] **Step 2: Run to verify it fails** — `dedupe_file` not in names.

- [ ] **Step 3: Wire `build_composites` into `_aggregate`**

In `server.py::_aggregate`, after the sub-package loop and **before** the `suite_find_tools` block (so composites are in the catalog snapshot), add:

```python
    from goldensuite_mcp.composites import build_composites
    composite_tools, composite_dispatch = build_composites(name_to_dispatch)
    for tool in composite_tools:
        if tool.name in seen:
            logger.warning("composite %r shadowed by earlier %s", tool.name, seen[tool.name])
            continue
        seen[tool.name] = "goldensuite"
        all_tools.append(tool)
        name_to_dispatch[tool.name] = composite_dispatch[tool.name]
```

Note: `build_composites` closes over `name_to_dispatch`. Because Python closures capture by reference and the composite dispatchers are only *called* later (at request time), the table they see already contains every sub-package tool. Confirm the `suite_find_tools` snapshot line runs after this block.

Add the composite names to `CURATED_TOOLS`:

```python
    # composite workflow tools -- one-call happy paths
    "dedupe_file", "match_sources", "assess_file", "clean_and_dedupe",
```

- [ ] **Step 4: Run to verify pass.** Also run the whole suite-mcp test dir to confirm no regression: `eval "$RUN packages/python/goldensuite-mcp/tests/ -q"`.

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): register composites in _aggregate + curate`).

### Task 2.4: `assess_file` composite (read-only, no Phase 1 dependency)

**Files:** `composites.py`, `tests/test_composites.py`

- [ ] **Step 1: Failing unit test** with a fake table exposing `upload_dataset`, `analyze_data`, `scan`; assert steps `["upload","analyze","scan"]`, `ok True`, `summary` mentions rows + a quality signal. Add a **degraded** test: table missing `scan` → analyze step `ok True`, scan step `ok False`, composite `ok True` (degraded), profile still present.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `orchestrate_assess_file`** — upload → `analyze_data(file_path=path)` → `scan(path)` (check the goldencheck `scan` arg name — likely `path`; confirm from `goldencheck.mcp.server` TOOLS). No `output_path`, no `config`/`outputs` export. Degraded rule: a failed `scan` does **not** flip the composite to `ok:false` (override `_finish` for this workflow, or pass a `degraded_steps={"scan"}` set so `_finish` ignores it when computing `ok`). Add its spec entry (inputSchema = `_FILE_INPUT_PROPS` only). Extend `_summarize` for `assess_file`.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): assess_file composite`).

### Task 2.5: `match_sources` composite

**Files:** `composites.py`, `tests/test_composites.py`

- [ ] **Step 1: Failing unit test** — fake table with `upload_dataset` (called twice) + `agent_match_sources`; two file inputs (`file_a_content`/`file_a_name` + `file_b_content`/`file_b_name`, or `file_a`/`file_b`). Assert both uploads happened, `agent_match_sources` got both paths + a generated `output_path`, and `outputs.matches_path` is surfaced.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `orchestrate_match_sources`** — resolve two inputs via `_upload` (generalize `_upload` to take a field prefix, or add `_upload_named`), then `agent_match_sources(file_a=pa, file_b=pb, output_path=<gen matches>)`, thread and surface `matches_path`/`matched_pairs`. Spec entry inputSchema mirrors `agent_match_sources`. Extend `_summarize`.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): match_sources composite`).

### Task 2.6: `clean_and_dedupe` composite

**Files:** `composites.py`, `tests/test_composites.py`

- [ ] **Step 1: Failing unit test** — fake table with `upload_dataset`, `run_transforms`, `agent_deduplicate`. Assert steps `["upload","clean","deduplicate"]`; `run_transforms` got a generated `output_path` (cleaned CSV) and its returned `output_path` is threaded as `agent_deduplicate`'s `file_path`; final `outputs.golden_path` surfaced. Add a soft-dep test: `run_transforms` returns `{"error": "goldenflow is not installed…"}` → composite `ok:false`, short-circuits at `clean`.

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement `orchestrate_clean_and_dedupe`** — upload → `run_transforms(file_path=path, output_path=<gen cleaned>)` → capture `res["output_path"]` as `cleaned` → `agent_deduplicate(file_path=cleaned, output_path=<gen golden>)`. Spec entry inputSchema = `_FILE_INPUT_PROPS` + `exclude_columns`. Extend `_summarize`.

- [ ] **Step 4: Run to verify pass.**

- [ ] **Step 5: Commit** (`feat(goldensuite-mcp): clean_and_dedupe composite`).

### Task 2.7: End-to-end parity + docs + version

**Files:** `tests/test_composites.py`, `README.md`, `CHANGELOG.md`, `pyproject.toml`, `goldensuite_mcp/__init__.py`

- [ ] **Step 1: Write one real end-to-end test per composite through the aggregator**

For `assess_file` (no Phase 1 needed) and — once Phase 1 is on main — `dedupe_file`: build the real aggregator (`_aggregate()`), call the composite dispatch on a tiny fixture CSV written to a temp path passed as `file_path`, assert `ok True` and (for dedupe) that `outputs.golden_path` exists on disk. **Parity:** call the same tools by hand through the aggregated dispatch in sequence and assert the composite's `outputs` (summary counts + golden file row count) match.

- [ ] **Step 2: Run to verify** — `eval "$RUN packages/python/goldensuite-mcp/tests/ -q"`. If Phase 1 isn't merged yet, mark the dedupe-family end-to-end tests with `pytest.mark.skipif` on absence of the `output_path` capability (probe `agent_deduplicate` schema) so the suite stays green until Phase 1 lands.

- [ ] **Step 3: Lint** — `eval "${RUN%pytest} -m ruff check packages/python/goldensuite-mcp/"` (or run ruff directly). Fix any findings.

- [ ] **Step 4: Docs** — add a "Composite workflows" section to `README.md` (the four tools, one-call examples, note the merged return shape + that they orchestrate the granular tools). Add a `0.5.0` entry to `CHANGELOG.md`.

- [ ] **Step 5: Version bump (lockstep)** — `pyproject.toml` and `goldensuite_mcp/__init__.py` `__version__` both to `0.5.0` (the version_consistency gate checks both — do not skip `__init__`).

- [ ] **Step 6: Commit** (`feat(goldensuite-mcp): composite end-to-end tests + docs + 0.5.0`).

### Task 2.8: Phase 2 PR

- [ ] Push `feat/goldensuite-mcp-composites`, open PR to `main`, arm auto-merge. Confirm `version_consistency` + `docs_consistency` gates are green (both files bumped).

---

## Notes for the implementer

- **Curated + discoverable for free:** because composites are added to `all_tools`/`name_to_dispatch` in `_aggregate` before the `suite_find_tools` snapshot and their names are in `CURATED_TOOLS`, they list by default and appear in discovery with no extra wiring — Task 2.3's test guards this.
- **`safe_path` / allowed-root:** every generated `output_path` derives from the path `upload_dataset` returned, which is already under the allowed-root uploads dir — so the Phase 1 write guard passes. Never build an output path from raw caller input.
- **Don't call Python APIs from composites.** Only the dispatch table. That is the parity guarantee (spec, "Why the dispatcher seam").
- **Confirm leaf field names** (`confidence_distribution` keys, `scan`'s arg name, `run_transforms`' returned `output_path` key, the match result's linked-frame attribute) against the real tools as you implement each task — the spec fixes the shape, not every leaf.
