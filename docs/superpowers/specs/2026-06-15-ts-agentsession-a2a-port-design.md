# TypeScript AgentSession + A2A Port Design

**Status:** Draft for review
**Date:** 2026-06-15
**Scope:** Full parity (AgentSession + 16 agent MCP tools + A2A skill expansion 10 → full coverage)

## Problem

The TypeScript `goldenmatch` package is at core-ER parity with Python but is missing
the **agent surface**: the `AgentSession` orchestrator, the 16 agent-level MCP tools,
and full A2A skill coverage. Per the 2026-06-15 versioning-policy analysis this is the
last *undeclared* parity gap (distributed engine and web UI are declared Python-only;
the agent surface is not). It blocks any honest "TS at parity / stable" claim.

Current TS state (verified):
- **No `AgentSession`** — no `analyze` / `autoconfigure` / `deduplicate` / `matchSources`
  / `compareStrategies` orchestrator, no `selectStrategy` decision logic.
- **No agent-level MCP tools** — the TS MCP server has 30 tools (19 base + 5 memory +
  6 identity), none agent-level.
- **A2A exists but partial** — `src/node/a2a/server.ts` (~487 LOC) has a Node HTTP server
  + ~10 skills, no bearer auth, not backed by a unified session/registry. Python's A2A
  advertises 28 skills (agent + identity + memory + cross-run) with fail-closed bearer auth.

Python reference: `packages/python/goldenmatch/goldenmatch/core/agent.py` (AgentSession,
668 LOC), `mcp/agent_tools.py` (16 tools, 828 LOC), `a2a/server.py` + `a2a/skills.py`
(467 + 442 LOC).

## Goal

Port the agent surface to TS at behavior parity, **honoring the edge-safe constraint**
(`src/core/**` imports no `node:*`), **without importing Python's registry duplication**,
and flip the versioning-policy parity matrix to "AgentSession/A2A: ported."

## Non-Goals

- Not porting Polars (TS operates on `Row[]`); not porting distributed/Ray/GPU/web-UI.
- Not refactoring the Python side (its non-DRY `AGENT_TOOLS` vs `_SKILLS` lists stay as-is).
- Not adding streaming/push to A2A (Python's card honestly advertises `streaming:false`).

## Architecture (Approach A: edge-safe core session + shared registry)

### Module layout

```
src/core/agent/
  session.ts     AgentSession -- edge-safe, operates on Row[]
  strategy.ts    profileForAgent / selectStrategy / buildAlternatives / decisionToConfig (pure)
  skills.ts      AGENT_SKILLS registry + dispatchSkill() (the shared dispatcher)
  types.ts       AgentProfile, StrategyDecision, skill I/O types
src/node/agent/
  session-file.ts   analyzeFile / deduplicateFile / matchSourcesFile / ... (load CSV, call core)
  loader.ts         node CSV loader, injected as the I/O seam
```

`src/core/agent/**` MUST NOT import `node:*` (enforced by build separation, verified per
the package's existing edge-safety rule). It reuses existing TS core: `dedupe`, `match`,
`AutoConfigController`, `ReviewQueue` (memory backend), domain registry, `serializeTelemetry`.

### Section 1 — Shared skill registry + the I/O seam

`AGENT_SKILLS` is the single source of truth for agent-level skills:

```ts
interface SkillDef {
  id: string;
  description: string;
  inputSchema: JSONSchema;   // reused verbatim by the MCP Tool + the A2A card
  handler: (args: Record<string, unknown>, ctx: SkillContext) => Promise<Record<string, unknown>>;
}

interface SkillContext {
  session: AgentSession;                       // fresh per call (statelessness, below)
  loadTable(source: string): Promise<Row[]>;   // the I/O seam -- node injects a CSV loader
  memoryStore?: MemoryStore;                   // optional, for agent_approve_reject
  identityStore?: IdentityStore;               // optional, for identity skills routed via A2A
}

export const AGENT_SKILLS: SkillDef[];
export function dispatchSkill(id: string, args: Record<string, unknown>, ctx: SkillContext): Promise<Record<string, unknown>>;
```

**The edge-safe seam is dependency injection, not a node import.** A handler does:

```ts
const rows = (args.rows as Row[] | undefined) ?? await ctx.loadTable(args.file_path as string);
```

Core never touches `node:fs`; the node surfaces construct a `ctx` whose `loadTable` reads
CSV from disk. Justified improvement over Python: TS agent skills accept **rows-or-path**,
so they also run edge-side (Python is file-path only). When neither `rows` nor a working
`loadTable` is available, the handler throws a clear error (caught by the dispatcher).

### Section 2 — AgentSession (core, on rows)

```ts
class AgentSession {
  data: Row[] | null = null;
  config: GoldenMatchConfig | null = null;
  result: DedupeResult | MatchResult | null = null;
  reviewQueue = new ReviewQueue({ backend: "memory" });
  reasoning: Record<string, unknown> = {};
  lastTelemetry: Telemetry | null = null;

  analyze(rows: Row[]): AnalyzeResult;                       // sync: profile + strategy + alternatives
  autoconfigure(rows: Row[]): AutoconfigResult;              // run controller, capture telemetry shape
  deduplicate(rows: Row[], config?: GoldenMatchConfig): Promise<DeduplicateResult>;  // pipeline + gating + review queue
  matchSources(rowsA: Row[], rowsB: Row[], config?: GoldenMatchConfig): Promise<MatchResult>;
  compareStrategies(rows: Row[], groundTruth?: GroundTruth): Promise<CompareResult>;
}
```

Decision logic lives in `strategy.ts` (`profileForAgent`, `selectStrategy`,
`buildAlternatives`, `decisionToConfig`) -- pure functions, the highest-value parity
target (deterministic branching: PPRL if sensitive, exact_only / exact_then_fuzzy /
fuzzy / domain_extraction / fallback). Sync/async split mirrors the existing TS
convention (`autoConfigureRows` sync; `dedupe` returns a Promise).

`deduplicate` reproduces Python's confidence gating + review-queue accumulation and the
`{results, reasoning, confidence_distribution, storage, last_telemetry}` return shape.
`last_telemetry` is `{available:false, source:"deduplicate"}` in the stateless dispatch
path (matches Python -- the controller ContextVar isn't readable cross-request).

### Section 3 — Killing the two-list drift

Both surfaces derive their advertised lists from the **same registries** (base + memory +
identity already exist in TS; agent is the new one):

- **MCP** (`src/node/mcp/server.ts`): import `AGENT_SKILLS`, render each as an MCP `Tool`,
  add to `TOOLS`, route agent tool names through `dispatchSkill` with a node `ctx`
  (real `loadTable`). Tool count goes 30 -> ~46. Routed through the **json-wrapping
  base-tool path**, not `_AGENT_TOOL_NAMES`-style pre-wrapping (the TS server's
  `tool_count` is already derived dynamically from `TOOLS.length`, so no hardcoded count
  to update).
- **A2A** (`src/node/a2a/server.ts`): build the agent card's `skills[]` from the **union**
  of all registries (base + memory + identity + agent) and route `/tasks/send {skill}`
  through a unified dispatcher that delegates to the right group (agent -> `dispatchSkill`;
  identity/memory -> existing dispatch). This is what expands A2A from ~10 to full coverage,
  and the two surfaces cannot drift because they read the same source.

### Section 4 — A2A server parity

The TS A2A server already has the Node HTTP transport + task lifecycle. To reach parity:
- **Agent card** (`GET /.well-known/agent.json`): match Python's `build_agent_card` shape
  (`name`, `description`, `url`, `version`, `provider`, `capabilities{streaming:false,
  pushNotifications:false}`, `skills`, `authentication{schemes:["bearer"]}`), with `skills`
  built from the unified registry union.
- **Task lifecycle**: `submitted -> working -> completed/failed`, in-memory task registry
  (`Map<taskId, Task>`), `POST /tasks/send`, `GET /tasks/{id}`, `POST /tasks/{id}/cancel`,
  `GET /health`. Confirm/extend the existing implementation against Python's routes.
- **Fail-closed bearer auth** (new in TS): middleware checks `Authorization: Bearer <token>`
  when `GOLDENMATCH_AGENT_TOKEN` is set; public paths `/health` + `/.well-known/agent.json`;
  binding to a non-loopback host without a token raises at startup (mirrors Python +
  the existing MCP/REST token discipline noted in the root CLAUDE.md).

### Section 5 — Statelessness + error handling

Each MCP tool call and each A2A task instantiates a **fresh** `AgentSession` (stateless
per request, exactly like Python). Within one session object, state persists
(`analyze` -> `deduplicate` reuse `this.data`); across requests it does not. Handlers
throw on error; the dispatcher catches and returns `{error: message}` (MCP `TextContent`
JSON) / sets task `state:"failed"` + `error` (A2A). Optional-dependency skills
(goldencheck/goldenflow analogues, if present in TS) fail-open with a clear "not
installed" message, matching Python.

### Section 6 — Node file-loading layer

`src/node/agent/session-file.ts` provides the file-path entry points
(`analyzeFile(path)`, `deduplicateFile(path, config?)`, ...) that read CSV via
`src/node/agent/loader.ts` and delegate to the core `AgentSession`. This mirrors the
existing `dedupe`/`dedupeFile` pair and is the only place CSV-from-disk lives.

### Section 7 — Parity testing

New Python emitter `scripts/emit_agent_fixtures.py` writes goldens under
`packages/typescript/goldenmatch/tests/parity/fixtures/agent-*.json` over a small set of
representative datasets (sensitive, strong-id, fuzzy, mixed), capturing:
- `analyze`: profile + the `select_strategy` decision + `build_alternatives`.
- `autoconfigure`: committed config + telemetry shape.
- `deduplicate`: `reasoning` + `confidence_distribution` + result metrics.
- `compareStrategies`: per-strategy metrics.
- **Agent card**: skill ids + input schemas.

TS `tests/parity/agent-*.test.ts` assert match -- **structural** for strategy decisions
and the card (deterministic), **4-decimal** for numeric metrics. Determinism clamps
(pinned ids/timestamps) follow the existing emitter conventions. The `select_strategy`
decision table is the keystone parity contract.

## Phasing (waves -> npm releases)

Each wave is independently testable and shippable:
1. **Core**: `AgentSession` + `strategy.ts` + `AGENT_SKILLS` + `dispatchSkill` +
   analyze/select_strategy fixtures. (the heart)
2. **MCP**: wire the 16 agent tools (30 -> ~46) + dispatch parity.
3. **A2A**: unified card/dispatcher + bearer auth + full skill coverage + card/task-lifecycle parity.
4. **Node + docs**: file-loading wrappers, end-to-end dedupe/match/compare skills, declare
   the surface in `packages/typescript/goldenmatch/CLAUDE.md`, flip the versioning-policy
   parity matrix to "AgentSession/A2A: ported."

## Risks / Open Questions

- **Strategy-decision parity drift.** `selectStrategy` is deterministic branching; any
  divergence from Python's thresholds shows up as a fixture mismatch. Mitigation: the
  keystone fixture set exercises every branch.
- **A2A skill union scope.** The 28 Python A2A skills span agent + identity + memory +
  cross-run. Some cross-run skills (e.g. `analyze_blocking`, `compare_clusters`,
  `schema_match`) may not have TS equivalents yet; the plan must audit each and either
  port the missing handler or omit it from the card with a note (no silent gap).
- **Telemetry shape.** `last_telemetry.available:false` in stateless dispatch must match
  Python exactly so the fixture passes.

## Rollout

Land waves 1-4 as separate PRs/releases off this branch; the parity matrix flip (and any
`1.0.0` decision from the versioning policy) happens only after wave 4 is green.
