# goldenmatch A2A naming reconciliation — design

**Status:** approved (brainstorm 2026-07-05), pending spec review
**Context:** the A2A parity gate (#1461) surfaced goldenmatch's A2A skill-id naming
divergences and froze them in `parity/goldenmatch.yaml`'s `a2a_skills` header as a
follow-up. This closes that follow-up for the *verified same-operation* pairs (like
#1457 did for `dedupe`↔`deduplicate`), and documents the rest as intentional.
Related: `project_api_parity_gate`.

## 1. Problem

Five candidate divergent pairs (Python `_SKILLS` id / TS `AGENT_CARD` skill id).
Investigated each against both sides' handlers + descriptions:

| Pair | Python | TypeScript | Verdict |
| --- | --- | --- | --- |
| `autoconfig` / `auto_configure` | Run AutoConfigController → config + telemetry | Run the iterative auto-config controller → committed config + telemetry | **same op → alias** |
| `compare_strategies` / `agent_compare_strategies` | `session.compare_strategies` | Run multiple candidate strategies + compare metrics | **same op → alias** |
| `transform` / `run_transforms` | Normalize formats via GoldenFlow (`run_transform`) | Run GoldenFlow transforms, normalize phone/date/… | **same op → alias** |
| `quality` / `scan_quality` + `fix_quality` | one skill: scan **and** fix (mode) | two skills (scan; scan+apply) | **1:2 granularity — document** |
| `pprl` / `suggest_pprl` | **runs** `pprl_link` on two files | **suggests** params (`profileForAgent`, "recommend") | **different ops — document** |

The last two are genuine coverage/semantic differences, not renames: aliasing a 1:2
split or a run-vs-suggest pair would be wrong. (`suggest_pprl` is actually the A2A
analogue of Python's own `suggest_pprl` MCP tool, not `pprl`.)

## 2. Goal

Make the 3 verified same-op pairs answerable by the canonical (Python) id on both
A2A cards, non-breakingly, and move those 3 ids to `a2a_skills.shared`. Document the
2 non-aliasable pairs as verified-different. Python is unchanged (already the
spec-shaped reference advertising the canonical ids).

## 3. Design (all TypeScript, in `src/node/a2a/server.ts`)

The 3 TS skills (`auto_configure`, `agent_compare_strategies`, `run_transforms`)
are `AGENT_SKILLS` (from `src/core/agent/skills.ts`), whose ids double as the
MCP agent-tool names and are dispatched via `AGENT_TOOL_NAMES` → `handleAgentTool`.
Reconciliation decouples the **A2A card id** (advertised) from the underlying
**agent-tool id** (dispatched) for these 3 — the A2A card is a separate surface
from MCP tools, so the MCP tool ids are untouched.

### 3.1 One source-of-truth alias map

```ts
// A2A card advertises Python's canonical id; the underlying agent-tool id
// (also the MCP tool id) is unchanged. tool-id -> canonical A2A id.
const A2A_AGENT_ID_ALIASES: Record<string, string> = {
  auto_configure: "autoconfig",
  agent_compare_strategies: "compare_strategies",
  run_transforms: "transform",
};
```

### 3.2 Advertise the canonical id (`buildCardSkills`, :201)

In the `AGENT_SKILLS` mapping loop, override the advertised id:
```ts
for (const def of AGENT_SKILLS)
  push(toAgentSkill({ id: A2A_AGENT_ID_ALIASES[def.id] ?? def.id, description: def.description }));
```
So the card advertises `autoconfig`/`compare_strategies`/`transform` for those 3;
all other agent skills keep `def.id`. De-dup-by-id still holds (no collision — the
3 canonical ids exist nowhere else in the card union; verified).

### 3.3 Dispatch the canonical id (`dispatchAnySkill`, :505)

The card now advertises `autoconfig`, but `AGENT_TOOL_NAMES` contains
`auto_configure`. Resolve canonical → tool-id at the top of `dispatchAnySkill`,
before the `AGENT_TOOL_NAMES.has(skill)` check (:509):
```ts
const A2A_CANONICAL_TO_TOOL = Object.fromEntries(
  Object.entries(A2A_AGENT_ID_ALIASES).map(([tool, canon]) => [canon, tool]),
);
// inside dispatchAnySkill, first line:
skill = A2A_CANONICAL_TO_TOOL[skill] ?? skill;
```
`autoconfig` → `auto_configure` → `handleAgentTool`. The legacy ids
(`auto_configure`, …) still dispatch for free — they *are* the agent-tool names, so
they pass the `AGENT_TOOL_NAMES` check unchanged. Non-breaking.

### 3.4 Manifest (`parity/goldenmatch.yaml` a2a_skills, same PR)

The emitted TS `a2a_skills` set gains `autoconfig`/`compare_strategies`/`transform`
and loses `auto_configure`/`agent_compare_strategies`/`run_transforms` (no longer
advertised — dispatch-only). Python's set is unchanged. So:
- **shared**: add `autoconfig`, `compare_strategies`, `transform` (17 → 20)
- **python_only**: remove those 3 (21 → 18)
- **ts_only**: remove `auto_configure`, `agent_compare_strategies`, `run_transforms` (19 → 16)

Update the a2a_skills header: the 3 reconciled pairs are now shared; refine the
remaining note to "VERIFIED-DIFFERENT (not drift): PY `quality` is one scan+fix
skill vs TS's `scan_quality`+`fix_quality` split (1:2); PY `pprl` RUNS linkage
(`pprl_link`, two files) while TS `suggest_pprl` SUGGESTS params — different ops."

## 4. Testing

- **TypeScript (CI-only — box OOMs):** extend/add an a2a test:
  - `AGENT_CARD.skills` advertises `autoconfig`, `compare_strategies`, `transform`
    and does **not** advertise `auto_configure`/`agent_compare_strategies`/
    `run_transforms` as separate skills.
  - `dispatchAnySkill("autoconfig", …)` routes to the same handler as
    `dispatchAnySkill("auto_configure", …)` (both resolve to the agent tool) — assert
    identical results on a fixture; same for the other 2 pairs.
- **Python (box-safe):** a light regression guard that `_SKILLS` still exposes
  `autoconfig`, `compare_strategies`, `transform` (the canonical ids the TS side
  now aligns to). Python code is otherwise untouched.
- **Gate:** the `api_parity` goldenmatch shard verifies the manifest matches the
  real emitted surfaces (the authoritative check for the partition move).

## 5. Rollout / docs

- Single PR, branch `feat/a2a-naming-reconcile` off `origin/main`. TS code + TS test
  + Python guard + manifest update. benzsevern gh; merge-queue → `gh pr merge
  --auto --squash` (no `--delete-branch`); arm auto-merge, stop.
- rollout-docs-sweep: `agent.mdx` already advertises the Python ids (it's the
  Python card); confirm no doc lists `auto_configure`/`run_transforms` as the A2A
  skill id. Skill counts unchanged (rename, not add/remove).

## 6. Risks

- **Low, TS-only, additive** — 3 card-id renames + a 3-entry dispatch map, legacy
  dispatch preserved. The one caveat (§3): the A2A card id is now decoupled from the
  agent-tool id for these 3; that is intentional and correct (A2A card ≠ MCP tools),
  and the MCP tool ids are untouched.
- **De-dup / collision**: the 3 canonical ids don't collide with any existing card
  id (verified). A collision would surface as a dropped skill — the §4 card test
  (asserting all 3 are advertised) guards it.
- **Semantic mis-call**: the 2 documented pairs were verified different from the
  handlers, not assumed; the header records the evidence so a future reader doesn't
  "reconcile" them by mistake.
