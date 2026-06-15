# TS AgentSession Port — Wave 2 (MCP wiring) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development or executing-plans. Checkbox (`- [ ]`) steps.

**Goal:** Port the remaining backend-having agent skills into `AGENT_SKILLS` and wire all of them into the TS MCP server (30 → 44 tools), declaring the three no-TS-backend tools Python-only.

**Architecture:** Wave 1 shipped 6 AgentSession-backed skills + `dispatchSkill`. Wave 2 adds 8 more skill handlers in `src/core/agent/skills.ts` (edge-safe; optional-dep ones via `await import("pkg" as string)`), then `src/node/mcp/server.ts` renders the 14 `SkillDef`s as MCP `Tool`s and routes their names through `dispatchSkill` with a node `loadTable` (the existing `readFile`).

**Tech stack:** TS strict (no local typecheck — CI is the gate). Spec: `docs/superpowers/specs/2026-06-15-ts-agentsession-a2a-port-design.md`.

---

## Scope decision (made from a Wave-2 API audit)

The 17 Python agent tools split by TS backend availability:
- **6 already in `AGENT_SKILLS`** (Wave 1): `analyze_data`, `auto_configure`, `agent_deduplicate`, `agent_match_sources`, `agent_compare_strategies`, `suggest_pprl`.
- **8 to ADD (have a TS backend):**
  - `agent_explain_pair` → `explainPair` (`src/core/explain.ts:161`).
  - `agent_explain_cluster` → **declarative** (stateless): return `{ note: "Cluster explanation requires a loaded dataset; not available in stateless dispatch." }` (mirrors Python).
  - `controller_telemetry` → **declarative**: `{ available: false, note: "Telemetry is only available inside a live controller run." }`.
  - `agent_review_queue` → load rows, `dedupe`, `gatePairs(result.scoredPairs)`, return the `needsReview` items.
  - `agent_approve_reject` → record an approve/reject (in-memory `ReviewQueue`; memory-store write is a Wave-3+ follow-up — return `{ recorded: true, decision }`).
  - `scan_quality` / `fix_quality` → optional `await import("goldencheck" as string)`; fail-open `{ error: "goldencheck not installed" }`.
  - `run_transforms` → optional `await import("goldenflow" as string)`; fail-open `{ error: "goldenflow not installed" }`.
- **3 DECLARED Python-only (NO TS backend):** `sensitivity`, `incremental`, `certify_recall`. No TS `runSensitivity`/`runIncremental`/`certifyRecall` exists; porting them is a separate effort. Declare them Python-only in the TS CLAUDE.md (Wave 4), exactly like the distributed engine / web UI. **Not registered, not advertised** — no silent gap.

Final MCP tool count: 30 + 14 = **44** (the TS server derives `tool_count` from `TOOLS.length`, so no hardcoded count to update; but check `tests/unit/mcp-server.test.ts` for any count assertion and update it).

---

## File Structure

| File | Change |
|------|--------|
| `src/core/agent/skills.ts` | Add the 8 new `SkillDef`s to `AGENT_SKILLS` (+ their handlers). Edge-safe (optional-dep via dynamic import). |
| `src/node/mcp/agent-tools.ts` (new) | Render `AGENT_SKILLS` → MCP `Tool[]` (`AGENT_MCP_TOOLS`) + `AGENT_TOOL_NAMES` + `handleAgentTool(name, args)` that builds a node `SkillContext` (`loadTable = (p) => readFile(p)`) and calls `dispatchSkill`. |
| `src/node/mcp/server.ts` | Add `...AGENT_MCP_TOOLS` to `TOOLS`; add an `AGENT_TOOL_NAMES`-routing branch in `handleTool` (before the default) that returns `await handleAgentTool(name, args)`. |
| `tests/unit/agent-skills.test.ts` | Extend: each new skill dispatches; optional-dep ones fail-open. |
| `tests/unit/mcp-agent-tools.test.ts` (new) | `AGENT_MCP_TOOLS` has 14 entries with name/description/inputSchema; `handleAgentTool("analyze_data", {rows})` returns a strategy. |

---

## Tasks

### Task 1: Add the 8 skill handlers to `AGENT_SKILLS`
**Files:** `src/core/agent/skills.ts`, `tests/unit/agent-skills.test.ts`

- [ ] Add each `SkillDef` (id/description/inputSchema/handler). Handlers:
  - `agent_explain_pair`: `import { explainPair } from "../explain.js"`; args `{ record_a, record_b }` → `explainPair(...)` (read its signature first; adapt). Return its result.
  - `agent_explain_cluster`, `controller_telemetry`: declarative objects (above).
  - `agent_review_queue`: `const rows = args.rows ?? await ctx.loadTable(args.file_path)`; `const r = await dedupe(rows)`; `return { pending: gatePairs(r.scoredPairs).needsReview }`.
  - `agent_approve_reject`: validate `{ decision }` ∈ {approve,reject}; `return { recorded: true, decision }`.
  - `scan_quality`/`fix_quality`: `try { const gc = await import("goldencheck" as string); ... } catch { return { error: "goldencheck not installed" }; }`.
  - `run_transforms`: same with `goldenflow`.
- [ ] inputSchemas: mirror Python `mcp/agent_tools.py` (each tool's properties + required). Accept `rows` OR `file_path` for the data-bearing ones (the rows-or-path seam).
- [ ] Tests: dispatch each new id; assert optional-dep tools return `{error: ...}` when the peer is absent (it won't be installed in CI).
- [ ] Edge-safety: `grep -rn "node:\|Buffer\|process\.\|require(" src/core/agent/` → nothing (dynamic `import("pkg" as string)` is allowed; do NOT static-import node).
- [ ] Commit.

### Task 2: `src/node/mcp/agent-tools.ts` (render + dispatch)
**Files:** create `src/node/mcp/agent-tools.ts`, `tests/unit/mcp-agent-tools.test.ts`

- [ ] `AGENT_MCP_TOOLS: readonly Tool[] = AGENT_SKILLS.map(s => ({ name: s.id, description: s.description, inputSchema: s.inputSchema }))`.
- [ ] `AGENT_TOOL_NAMES = new Set(AGENT_SKILLS.map(s => s.id))`.
- [ ] `handleAgentTool(name, args)`: `const ctx = { session: new AgentSession(), loadTable: async (p: string) => readFile(p) }; return dispatchSkill(name, args, ctx);` (import `readFile` from `../connectors/file.js`, `AgentSession`/`dispatchSkill`/`AGENT_SKILLS` from `../../core/agent/index.js`). NOTE: `readFile` is sync returning `Row[]`; wrap in `async`/`Promise.resolve`.
- [ ] Test: `AGENT_MCP_TOOLS.length === 14`; `handleAgentTool("analyze_data", { rows: [...] })` returns `{ strategy: ... }`.
- [ ] Commit.

### Task 3: Wire into `server.ts`
**Files:** `src/node/mcp/server.ts`, `tests/unit/mcp-server.test.ts` (update count if asserted)

- [ ] Import `AGENT_MCP_TOOLS, AGENT_TOOL_NAMES, handleAgentTool` from `./agent-tools.js`.
- [ ] `TOOLS = [...EXISTING_TOOLS, ...MEMORY_TOOLS, ...IDENTITY_TOOLS, ...AGENT_MCP_TOOLS]` (line ~363) → 44.
- [ ] In `handleTool` (before the default `Unknown tool`): `if (AGENT_TOOL_NAMES.has(name)) return await handleAgentTool(name, args);`. (The existing result-wrapping `{content:[{type:"text",text:JSON.stringify(result)}]}` applies — `handleAgentTool` returns a plain object, same as the other handlers.)
- [ ] Confirm `mcp-server.test.ts` tool-count assertion (if any) → 44. Update the A2A/MCP description string if it hardcodes a tool count (grep `tools` in server.ts).
- [ ] Commit.

### Task 4: Verify + CI
- [ ] Edge-safety sweep on `src/core/agent/`.
- [ ] Push; CI is the typecheck+test gate (box OOMs vitest). Fix any strict-TS nit (expect `exactOptionalPropertyTypes`/`noUncheckedIndexedAccess`).
- [ ] Final review of the diff.

## Notes
- **No local TS toolchain** — write carefully against the exact contracts; CI gates. Read `explainPair`'s real signature before coding `agent_explain_pair`.
- **YAGNI:** no A2A changes here (Wave 3); no node file-loaders (Wave 4). Just core skills + MCP.
- The 3 Python-only tools get their CLAUDE.md declaration in Wave 4 (kept together with the parity-matrix flip).
