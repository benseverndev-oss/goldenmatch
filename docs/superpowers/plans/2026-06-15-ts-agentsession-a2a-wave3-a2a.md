# TS AgentSession Port — Wave 3 (A2A) Implementation Plan

> **For agentic workers:** REQUIRED: superpowers:subagent-driven-development. Checkbox steps.

**Goal:** Bring the existing TS A2A server (`src/node/a2a/server.ts`, ~10 skills, no auth) to parity with Python: advertise the union of all tool registries on the agent card, route `/tasks` dispatch to the right registry, and add fail-closed bearer auth.

**Architecture:** Build `AGENT_CARD.skills` from the union (existing A2A base skills + the 14 `AGENT_SKILLS` + `MEMORY_TOOLS` + `IDENTITY_TOOLS`). The task dispatcher routes by skill id: agent→core `dispatchSkill` (node ctx), memory→`handleMemoryTool`, identity→`handleIdentityTool`, else the existing base `dispatchSkill`. Add bearer-auth middleware keyed on `GOLDENMATCH_AGENT_TOKEN`.

**Spec:** `docs/superpowers/specs/2026-06-15-ts-agentsession-a2a-port-design.md`. Python ref: `goldenmatch/a2a/server.py` (build_agent_card + auth middleware + routes).

## Box constraints
- No TS toolchain (CI is the only typecheck+test gate). No pnpm/vitest/tsc/tsup. Write+commit only; use exact contracts read from source.
- Node surface (`src/node/a2a/**`) — node imports are fine here (it's not edge core).

---

## Tasks

### Task 1: Fail-closed bearer auth
**Files:** `src/node/a2a/server.ts`, `tests/unit/a2a-*.test.ts`

- [ ] Read Python `a2a/server.py` auth middleware: `GOLDENMATCH_AGENT_TOKEN` env; if set, require `Authorization: Bearer <token>` on all routes EXCEPT `/health` and `/.well-known/agent.json`; 401 otherwise. Binding to a non-loopback host (host !== "127.0.0.1"/"localhost"/"::1") with NO token set → throw at `startA2aServer` start (fail-closed), mirroring the MCP/REST token discipline (root CLAUDE.md).
- [ ] Implement: at request entry (before route matching, ~line 393), check the token for non-public paths; respond 401 `{error:"Unauthorized"}` if mismatch. Add the startup guard in `startA2aServer`.
- [ ] Tests (extract the auth check into a testable pure fn, e.g. `isAuthorized(pathname, header, token)`): public paths pass without token; with token set, a non-public path requires the matching Bearer; the non-loopback-without-token guard throws.
- [ ] Commit.

### Task 2: Agent card = union of registries
**Files:** `src/node/a2a/server.ts`, `tests/unit/a2a-card.test.ts`

- [ ] Read the current `AGENT_CARD` (~line 44) skill shape (`AgentSkill`). Read `AGENT_SKILLS` (`../../core/agent/index.js`), `MEMORY_TOOLS` (`../mcp/memory-tools.js`), `IDENTITY_TOOLS` (`../mcp/identity-tools.js`) — each entry has `id`/`name` + description.
- [ ] Build `AGENT_CARD.skills` from the union: existing base skills + agent (map `AGENT_SKILLS` id→skill) + memory + identity. De-dup by id if any collide. Match Python's card shape (name/description/url/version/provider/capabilities{streaming:false,pushNotifications:false}/skills/authentication{schemes:["bearer"]}). Add the `authentication` field (currently absent).
- [ ] Test: card `.skills` contains the agent ids (e.g. `analyze_data`), a memory id, an identity id, and a base id; `.authentication.schemes` includes "bearer"; `.capabilities.streaming === false`.
- [ ] Commit.

### Task 3: Unified task dispatch by registry
**Files:** `src/node/a2a/server.ts`, `tests/unit/a2a-dispatch.test.ts`

- [ ] In the POST `/tasks` (and alias `/tasks/send`) handler, route `skill` by id:
  - `AGENT_TOOL_NAMES.has(skill)` → `handleAgentTool(skill, input)` (from `../mcp/agent-tools.js` — reuses the node ctx/loadTable wiring from Wave 2).
  - memory id → `handleMemoryTool(skill, input)`; identity id → `await handleIdentityTool(skill, input)` (these return `TextContent[]` — unwrap `.text` → JSON.parse, or store the text as the result).
  - else → existing base `dispatchSkill(skill, input)`.
- [ ] Add Python-parity routes: `POST /tasks/send` (same handler as POST `/tasks`), `POST /tasks/{id}/cancel` (mark task cancelled). Keep existing `POST /tasks` + `GET /tasks/{id}`.
- [ ] Test (extract a pure `dispatchAnySkill(skill, input)` that does the routing): an agent skill (`analyze_data` with `{rows}`) returns a strategy; an unknown skill throws/errors; a base skill still works.
- [ ] Commit.

### Task 4: Verify + CI
- [ ] Push; CI is the gate. Fix strict-TS nits (`exactOptionalPropertyTypes`/`noUncheckedIndexedAccess`); ensure `.js` import suffixes.
- [ ] Final review.

## Notes
- **YAGNI:** no node file-loaders (Wave 4), no CLAUDE.md/parity-matrix changes (Wave 4). Just A2A.
- Memory/identity handlers return `TextContent[]` (`[{type:"text",text:JSON.stringify(...)}]`); agent `dispatchSkill`/`handleAgentTool` return plain objects. Normalize both to a plain result for the task `result` field.
- Keep the existing 10 base A2A skills working (don't regress their dispatch).
