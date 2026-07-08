# Document Ingest A2A Skills Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add two A2A skills (`documents_suggest_schema`, `documents_ingest`) to the goldenmatch agent server, delegating to the existing MCP `handle_document_tool`.

**Architecture:** Two `_SKILLS` agent-card entries in `a2a/server.py` + one `dispatch_skill` branch in `a2a/skills.py` that routes both ids to `handle_document_tool` (returns a dict, the A2A contract). Plus the two CI-enforced bookkeeping updates: the parity manifest and the hard-coded skill count. Pure Python; path-based.

**Tech Stack:** Python, aiohttp (a2a extra), pytest.

**Spec:** `docs/superpowers/specs/2026-07-07-documents-a2a-design.md`

---

## Conventions for every task

- **Worktree:** `D:/show_case/gm-docs-a2a` (branch `feat/documents-a2a`). Do NOT push, do NOT touch `main`. Never `git stash` (it is shared across all worktrees).
- **Test env** (from `packages/python/goldenmatch`):
  ```bash
  cd D:/show_case/gm-docs-a2a/packages/python/goldenmatch
  PY="D:/show_case/goldenmatch/.venv/Scripts/python.exe"
  export PYTHONPATH="D:/show_case/gm-docs-a2a/packages/python/goldenmatch;D:/show_case/gm-docs-a2a/packages/python/goldenflow"
  export GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8
  ```
  Prereq: `"$PY" -c "import aiohttp, mcp, PIL, fitz" 2>/dev/null || "$PY" -m pip install aiohttp "mcp>=1.0" pymupdf Pillow` (the shared venv should already have these).
- **Lint before each commit:** `"$PY" -m ruff check goldenmatch/a2a tests/test_a2a.py`.
- **Commit trailers:** copy from `git log -1 --format=%B`. `git -c commit.gpgsign=false commit`.

---

## File structure (locked)

| path | change |
|---|---|
| `goldenmatch/a2a/server.py` | add 2 entries to `_SKILLS` (closes ~line 325) |
| `goldenmatch/a2a/skills.py` | add 1 branch to `dispatch_skill` (before the terminal `raise`, ~line 466) |
| `tests/test_a2a.py` | bump `test_agent_card_has_38_skills` -> 40; add card-lists + 2 dispatch tests |
| `parity/goldenmatch.yaml` (repo root) | add both ids to `a2a_skills.python_only` (sorted, after `configure`) |
| `README.md` (pkg), `llms.txt` (pkg), `docs/llms.txt` (repo) | bump "38 skills" -> "40 skills" |

---

## Task 1: Skill-card entries + count bump

**Files:** Modify `goldenmatch/a2a/server.py`, `tests/test_a2a.py`, `packages/python/goldenmatch/README.md`, `packages/python/goldenmatch/llms.txt`, `docs/llms.txt`

- [ ] **Step 1: Write/adjust the failing tests** in `tests/test_a2a.py`:
  - Rename `test_agent_card_has_38_skills` -> `test_agent_card_has_40_skills`, change the final assert to `== 40`, and append to its docstring: `"; document ingest added documents_suggest_schema / documents_ingest (38->40)."`.
  - Add a new test:
    ```python
    def test_agent_card_lists_document_skills():
        from goldenmatch.a2a.server import build_agent_card
        ids = {s["id"] for s in build_agent_card("http://localhost:8080")["skills"]}
        assert {"documents_suggest_schema", "documents_ingest"} <= ids
    ```
- [ ] **Step 2: Run -> fail.** `"$PY" -m pytest tests/test_a2a.py::test_agent_card_has_40_skills tests/test_a2a.py::test_agent_card_lists_document_skills -q` (count is 38, ids absent).
- [ ] **Step 3: Implement.** In `goldenmatch/a2a/server.py`, add these two entries to `_SKILLS` (before the closing `]` at ~line 325):
  ```python
      {
          "id": "documents_suggest_schema",
          "name": "Suggest Document Schema",
          "description": "Propose an extraction schema from a sample document (PDF/image).",
          "inputModes": ["application/json"],
          "outputModes": ["application/json"],
      },
      {
          "id": "documents_ingest",
          "name": "Ingest Documents",
          "description": "Extract records from documents against a schema; records ready for dedupe.",
          "inputModes": ["application/json"],
          "outputModes": ["application/json"],
      },
  ```
  Then bump the doc surfaces (grep the WHOLE repo first: `grep -rn "38 skills" packages/ docs/ docs-site/`):
  `packages/python/goldenmatch/README.md`, `packages/python/goldenmatch/llms.txt`, `docs/llms.txt`,
  AND `docs-site/goldenmatch/agent.mdx` (frontmatter description) — change "38 skills" -> "40 skills".
- [ ] **Step 4: Run -> pass** (both tests green).
- [ ] **Step 5: Commit** `feat(a2a): advertise documents_suggest_schema + documents_ingest skills (38->40)`.

---

## Task 2: Dispatch branch + dispatch tests

**Files:** Modify `goldenmatch/a2a/skills.py`, `tests/test_a2a.py`

- [ ] **Step 1: Failing tests** in `tests/test_a2a.py` (near the other `dispatch_skill` tests). Use a real temp PNG so `load_pages` succeeds; a `FakeExtractor` returns canned rows so no live VLM runs:
  ```python
  def test_dispatch_documents_ingest(tmp_path, monkeypatch):
      import io
      from PIL import Image
      import goldenmatch.mcp.document_tools as dt
      from goldenmatch.documents.extractor import FakeExtractor
      from goldenmatch.documents.types import ExtractedRow, ExtractResult, Field, TargetSchema
      from goldenmatch.a2a.skills import dispatch_skill

      p = tmp_path / "a.png"
      Image.new("RGB", (20, 20), "white").save(p)
      schema = TargetSchema([Field("full_name"), Field("email")])
      row = ExtractedRow.from_partial({"full_name": "Ada", "email": "a@x.io"}, {},
                                      schema, source_file="", source_page=0)
      monkeypatch.setattr(dt, "resolve_extractor", lambda b, m: FakeExtractor([ExtractResult(rows=[row])]))
      out = dispatch_skill("documents_ingest", {
          "paths": [str(p)],
          "schema": {"fields": [{"name": "full_name"}, {"name": "email"}]},
      })
      assert out["report"]["n_rows"] == 1
      assert out["records"][0]["full_name"] == "Ada"


  def test_dispatch_documents_suggest_schema(tmp_path, monkeypatch):
      import goldenmatch.mcp.document_tools as dt
      from goldenmatch.documents.types import Field, TargetSchema
      from goldenmatch.a2a.skills import dispatch_skill
      from PIL import Image

      p = tmp_path / "s.png"; Image.new("RGB", (10, 10), "white").save(p)
      monkeypatch.setattr(dt, "suggest_schema_from_file",
                          lambda path, **k: TargetSchema([Field("full_name"), Field("email", kind="email")]))
      out = dispatch_skill("documents_suggest_schema", {"sample_path": str(p)})
      assert out["schema"]["fields"][0]["name"] == "full_name"
  ```
  (Monkeypatch `resolve_extractor`/`suggest_schema_from_file` on `document_tools` because that is the module `handle_document_tool` calls them from.)
- [ ] **Step 2: Run -> fail** (dispatch raises `Unknown skill`).
- [ ] **Step 3: Implement.** In `goldenmatch/a2a/skills.py`, add just before the terminal `raise ValueError(f"Unknown skill: {skill_id}")` (~line 466):
  ```python
      if skill_id in ("documents_ingest", "documents_suggest_schema"):
          from goldenmatch.mcp.document_tools import handle_document_tool
          return handle_document_tool(skill_id, params)
  ```
- [ ] **Step 4: Run -> pass.** Also re-run the whole a2a file: `"$PY" -m pytest tests/test_a2a.py -q` (all green, no count regressions).
- [ ] **Step 5: Commit** `feat(a2a): dispatch document skills to the MCP handler`.

---

## Task 3: Parity manifest declaration

**Files:** Modify `parity/goldenmatch.yaml` (repo root)

- [ ] **Step 1:** In `parity/goldenmatch.yaml`, under `a2a_skills:` -> `python_only:`, insert (sorted — after `configure`, before `identity_audit`):
  ```yaml
    - documents_ingest
    - documents_suggest_schema
  ```
- [ ] **Step 2: Validate** the YAML + placement:
  ```bash
  "$PY" -c "import yaml; d=yaml.safe_load(open('parity/goldenmatch.yaml'))['a2a_skills']['python_only']; assert 'documents_ingest' in d and 'documents_suggest_schema' in d; print('ok, sorted:', d==sorted(d))"
  ```
  (The full `scripts/check_api_parity.py goldenmatch` gate needs the built TS surface and can't run locally — CI verifies it. The manifest edit is the fix; these two ids were the `undeclared_py_only` failure mode for MCP in #1515, now pre-empted for a2a.)
- [ ] **Step 3: Commit** `chore(parity): declare document a2a skills as python_only`.

---

## Task 4: Full local verification

- [ ] **Step 1:** `"$PY" -m pytest tests/test_a2a.py -q` — all green (count is 40, both dispatch tests pass, no regressions).
- [ ] **Step 2:** `"$PY" -m ruff check goldenmatch/a2a tests/test_a2a.py` — clean.
- [ ] **Step 3:** `grep -rn "38 skills" packages/ docs/ docs-site/ 2>/dev/null` returns NOTHING (all four bumped).
- [ ] **Step 4:** any lint fixups -> commit `chore(a2a): lint`.

---

## Done-when

- `documents_suggest_schema` + `documents_ingest` advertised in the agent card + dispatched to
  `handle_document_tool`; `dispatch_skill` returns the records/report and schema dicts.
- Skill count bumped 38 -> 40 in the test AND all three doc surfaces; no stray "38 skills" left.
- Both ids declared in `a2a_skills.python_only` in `parity/goldenmatch.yaml`.
- `tests/test_a2a.py` green, ruff clean.
- Deferred: TS A2A side (TS surface), uploads, new AgentSession methods.
