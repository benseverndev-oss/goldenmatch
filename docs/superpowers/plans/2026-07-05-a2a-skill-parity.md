# goldenmatch A2A Skill Parity — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the TypeScript A2A agent card A2A-spec-conformant (`{id, name}`) and align two verified same-operation skill ids to Python's canonical names, non-breakingly.

**Architecture:** All changes are TS-side — Python A2A is already the reference (spec-shaped, canonical ids). Add an `id` field to the TS `AgentSkill`, make `name` a human label, de-dup the card by `id`. Advertise Python's ids (`deduplicate`, `explain`) for the two aligned skills while keeping the legacy ids (`dedupe`, `explain_pair`) as dispatch-only fall-through aliases.

**Tech Stack:** TypeScript (local `AgentSkill`/`AgentCard` types, vitest), Python (pytest regression guard), Mintlify docs.

**Spec:** `docs/superpowers/specs/2026-07-05-a2a-skill-parity-design.md`

**Environment / SOP:**
- Branch `feat/a2a-skill-parity` (worktree `D:\show_case\gg-local-llm`), based on `origin/main`.
- **TS is CI-only — the box OOMs vitest/tsc.** Write TS code + tests and verify by careful *reading*; do NOT run pnpm/vitest/tsc. CI verifies.
- Python is box-safe: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <target> -v` (the worktree has no venv; borrow the sibling interpreter, and prepend the worktree package root to `PYTHONPATH` so imports resolve to gg-local-llm, not the stale sibling — verify `s.__file__` before trusting results).
- benzsevern gh (`unset GH_TOKEN; gh auth switch --user benzsevern`). Merge-queue repo → `gh pr merge --auto --squash` (no `--delete-branch`). Arm auto-merge + STOP.
- Verify symbols against this worktree, NEVER the stale `D:\show_case\goldenmatch`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `packages/typescript/goldenmatch/src/node/a2a/server.ts` | TS A2A server: `AgentSkill`/card + dispatch | Add `id`; humanize; de-dup by id; align 2 ids + dispatch aliases; doc note |
| `packages/typescript/goldenmatch/tests/unit/a2a-skill-parity.test.ts` | New parity tests | **Create** (CI-only) |
| `packages/typescript/goldenmatch/tests/unit/a2a-card.test.ts` | Existing card test | Re-key assertions `name`→`id`; `dedupe`→`deduplicate` |
| `packages/python/goldenmatch/tests/test_a2a.py` | Python A2A tests | Add reference-shape regression guard |
| `docs-site/goldenmatch/agent.mdx` | A2A docs page | Note shared canonical ids + legacy aliases |

**Anchors (verified, this worktree):** `AgentSkill` interface server.ts:59; `BASE_SKILLS` :85-146 (10 skills, `name` is the machine id); `toAgentSkill` :148-159; `buildCardSkills` :161-182 (de-dup by `skill.name` :171-172; doc comment :161-166); `AGENT_CARD.skills = buildCardSkills()` :198; `dispatchSkill` switch :263 (`case "dedupe"` :264, `case "explain_pair"` :371, default throws :446); `dispatchAnySkill` :480. `SkillDef` = `{id, description}` (no `name`). Python `_SKILLS` reference in `a2a/server.py` (canonical ids `deduplicate` :34, `explain` :48).

---

## Task 1: TypeScript — conformance + id alignment + dispatch aliases (CI-only)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/node/a2a/server.ts`
- Create: `packages/typescript/goldenmatch/tests/unit/a2a-skill-parity.test.ts`
- Modify: `packages/typescript/goldenmatch/tests/unit/a2a-card.test.ts`

> **Do NOT run vitest/tsc.** Write, then verify by reading. CI is the gate.

- [ ] **Step 1: Add `id` to the `AgentSkill` interface** (server.ts:59)

```ts
export interface AgentSkill {
  readonly id: string;
  readonly name: string;
  readonly description: string;
  readonly inputModes: readonly string[];
  readonly outputModes: readonly string[];
}
```

- [ ] **Step 2: Give every `BASE_SKILLS` entry `{ id, name }`** (server.ts:85-146). `id` = the machine id (the two aligned skills adopt Python's canonical id); `name` = curated human label. Full replacement list:

| current `name` | new `id` | new `name` |
| --- | --- | --- |
| dedupe | `deduplicate` | `Deduplicate` |
| match | `match` | `Match` |
| score | `score` | `Score` |
| profile | `profile` | `Profile` |
| suggest_config | `suggest_config` | `Suggest Config` |
| explain_pair | `explain` | `Explain` |
| evaluate | `evaluate` | `Evaluate` |
| list_scorers | `list_scorers` | `List Scorers` |
| list_transforms | `list_transforms` | `List Transforms` |
| list_strategies | `list_strategies` | `List Strategies` |

Each entry becomes e.g.:
```ts
  {
    id: "deduplicate",
    name: "Deduplicate",
    description: "Deduplicate a list of records and return golden records plus clusters.",
    inputModes: ["data/json"],
    outputModes: ["data/json"],
  },
```
Keep every `description`/`inputModes`/`outputModes` verbatim. (This folds Component 2's card-id change into the same edit: only `dedupe`→`deduplicate` and `explain_pair`→`explain` change ids; the other 8 keep their machine id.)

- [ ] **Step 3: Add a `humanize` helper + rework `toAgentSkill`** (server.ts:148). The derived registries (AGENT_SKILLS/memory/identity) carry a machine id but no curated label, so derive one:

```ts
/** Title-case a machine id into a human label: `agent_deduplicate` -> "Agent Deduplicate". */
function humanize(id: string): string {
  return id.split("_").map((w) => (w ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
}

/** Map a registry entry ({id, description}) to the spec-shaped `AgentSkill`. */
function toAgentSkill(entry: {
  readonly id: string;
  readonly description: string;
}): AgentSkill {
  return {
    id: entry.id,
    name: humanize(entry.id),
    description: entry.description,
    inputModes: ["application/json"],
    outputModes: ["application/json"],
  };
}
```

- [ ] **Step 4: De-dup `buildCardSkills` by `id`; update its callers + doc comment** (server.ts:161-182)

```ts
/**
 * Build the card's skill list from the union of every registry: the 10 base
 * A2A skills + the 15 `AGENT_SKILLS` + the memory tools + the identity tools.
 * De-duped by skill `id`; first occurrence wins, so a base skill shadows a
 * same-id registry entry.
 *
 * A2A parity note: the card is A2A-spec-shaped ({id, name}). The core skills
 * share canonical ids with the Python server (deduplicate, match, explain,
 * evaluate, ...); the legacy ids `dedupe`/`explain_pair` still dispatch (see
 * dispatchSkill). The remaining catalog differences (agent_* skills, Python's
 * finer granularity, TS-only score/profile, Python-only identity_audit/etc.,
 * and genuinely different ops like pprl vs suggest_pprl) are INTENTIONAL, not
 * drift — A2A is not gated for parity (MCP tools + CLI are).
 */
function buildCardSkills(): readonly AgentSkill[] {
  const out: AgentSkill[] = [];
  const seen = new Set<string>();
  const push = (skill: AgentSkill): void => {
    if (seen.has(skill.id)) return;
    seen.add(skill.id);
    out.push(skill);
  };
  for (const skill of BASE_SKILLS) push(skill);
  for (const def of AGENT_SKILLS) {
    push(toAgentSkill({ id: def.id, description: def.description }));
  }
  for (const tool of MEMORY_TOOLS) push(toAgentSkill({ id: tool.name, description: tool.description }));
  for (const tool of IDENTITY_TOOLS) push(toAgentSkill({ id: tool.name, description: tool.description }));
  return out;
}
```
(This also satisfies Component 3's in-code documentation.)

- [ ] **Step 5: Add legacy dispatch aliases** (server.ts:264 and :371). Stack the legacy id as a fall-through `case` above the existing canonical body — the card now advertises the canonical id, the legacy id still dispatches:

```ts
    case "deduplicate":   // A2A canonical id
    case "dedupe": {       // legacy alias (still dispatches)
      ...existing dedupe body, unchanged...
    }
```
```ts
    case "explain":        // A2A canonical id
    case "explain_pair": { // legacy alias (still dispatches)
      ...existing explain_pair body, unchanged...
    }
```
Do NOT change the case bodies. `deduplicate`/`explain` are absent from `AGENT_TOOL_NAMES`/`MEMORY_TOOL_NAMES`/`IDENTITY_TOOL_NAMES`, so `dispatchAnySkill` falls through to `dispatchSkill` for them.

- [ ] **Step 6: Write `tests/unit/a2a-skill-parity.test.ts`**

```ts
import { describe, it, expect } from "vitest";
import { AGENT_CARD, dispatchAnySkill } from "../../src/node/a2a/server.js";

describe("A2A skill parity", () => {
  const byId = new Map(AGENT_CARD.skills.map((s) => [s.id, s]));

  it("every card skill has a non-empty id and human name", () => {
    for (const s of AGENT_CARD.skills) {
      expect(typeof s.id).toBe("string");
      expect(s.id.length).toBeGreaterThan(0);
      expect(typeof s.name).toBe("string");
      expect(s.name.length).toBeGreaterThan(0);
    }
  });

  it("all skill ids are unique", () => {
    const ids = AGENT_CARD.skills.map((s) => s.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("advertises canonical ids, not the legacy aliases", () => {
    expect(byId.has("deduplicate")).toBe(true);
    expect(byId.has("explain")).toBe(true);
    expect(byId.has("dedupe")).toBe(false);
    expect(byId.has("explain_pair")).toBe(false);
  });

  it("dispatches the legacy id identically to the canonical id", async () => {
    const rows = [
      { id: "1", name: "Alice", email: "a@x.com" },
      { id: "2", name: "Alice", email: "a@x.com" },
    ];
    expect(await dispatchAnySkill("deduplicate", { rows })).toEqual(
      await dispatchAnySkill("dedupe", { rows }),
    );
    const pair = { row_a: { name: "Jon" }, row_b: { name: "John" } };
    expect(await dispatchAnySkill("explain", pair)).toEqual(
      await dispatchAnySkill("explain_pair", pair),
    );
  });

  it("humanizes derived ids into labels", () => {
    // agent_deduplicate is an AGENT_SKILLS entry -> label "Agent Deduplicate"
    expect(byId.get("agent_deduplicate")?.name).toBe("Agent Deduplicate");
    expect(byId.get("identity_resolve")?.name).toBe("Identity Resolve");
  });
});
```
(If the exact input keys for `explain_pair`/`dedupe` differ from `rows`/`row_a`/`row_b`, read the case bodies at :264/:371 and match them — the point is identical inputs to both ids.)

- [ ] **Step 7: Update `tests/unit/a2a-card.test.ts`** — re-key the machine-id assertions from `s.name` to `s.id` and rename the aligned id:

```ts
  const ids = new Set(AGENT_CARD.skills.map((s) => s.id));

  it("includes a base A2A skill", () => {
    expect(ids.has("deduplicate")).toBe(true);   // was names.has("dedupe")
  });
  it("includes an agent skill (analyze_data)", () => {
    expect(ids.has("analyze_data")).toBe(true);
  });
  it("includes a memory tool id", () => {
    expect(ids.has("list_corrections")).toBe(true);
  });
  it("includes an identity tool id", () => {
    expect(ids.has("identity_resolve")).toBe(true);
  });
  ...
  it("de-dups skills by id (no duplicate ids)", () => {
    expect(ids.size).toBe(AGENT_CARD.skills.length);
  });
```
Leave the auth/streaming and "AgentSkill shape" assertions as-is (the shape test may add `expect(typeof skill.id).toBe("string")`).

- [ ] **Step 8: Verify by reading (do not run):** `id` on the interface; all 10 BASE_SKILLS have `{id, name}`; `deduplicate`/`explain` are the two aligned ids; `toAgentSkill`/`humanize` correct; `buildCardSkills` de-dups by `id` and both callers pass `{id, description}`; the two switch cases stack legacy-above-canonical with unchanged bodies; brace balance intact; both test files consistent.

- [ ] **Step 9: Commit**

```bash
git add packages/typescript/goldenmatch/src/node/a2a/server.ts \
        packages/typescript/goldenmatch/tests/unit/a2a-skill-parity.test.ts \
        packages/typescript/goldenmatch/tests/unit/a2a-card.test.ts
git commit -m "feat(a2a-ts): A2A-spec-conformant AgentSkill (id + human name) + canonical id alignment"
```

---

## Task 2: Python — reference-shape regression guard (box-safe)

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_a2a.py`

This guards the shape the TS side aligns to. Python is already correct, so the test goes green immediately (a characterization guard, not TDD-red).

- [ ] **Step 1: Add the test** (append to `tests/test_a2a.py`)

```python
def test_a2a_skills_are_spec_shaped_and_expose_canonical_ids():
    """Guards the A2A reference shape the TS server aligns to: every skill has
    id + name, and the canonical ids the TS card adopts are present."""
    from goldenmatch.a2a.server import _SKILLS
    for skill in _SKILLS:
        assert skill["id"] and isinstance(skill["id"], str)
        assert skill["name"] and isinstance(skill["name"], str)
    ids = {s["id"] for s in _SKILLS}
    assert {"deduplicate", "explain", "match"} <= ids
```

- [ ] **Step 2: Run it**

Run: `cd packages/python/goldenmatch && PYTHONPATH=$(pwd) POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_a2a.py::test_a2a_skills_are_spec_shaped_and_expose_canonical_ids -v`
Expected: PASS. (Confirm `_SKILLS` resolves to the worktree module first.)

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_a2a.py
git commit -m "test(a2a): guard the Python A2A reference shape (id+name, canonical ids)"
```

---

## Task 3: Docs + PR

**Files:**
- Modify: `docs-site/goldenmatch/agent.mdx`

- [ ] **Step 1: Add a short A2A-parity note to `agent.mdx`.** Find where the page describes the A2A skills/agent card and add a concise paragraph:

> The TypeScript and Python A2A servers share canonical skill ids for the core
> operations (`deduplicate`, `match`, `explain`, `evaluate`, `analyze_data`, the
> `identity_*` set, …), and every skill is A2A-spec-shaped (`id` + human-readable
> `name`). For back-compat the TypeScript server also dispatches the legacy ids
> `dedupe` and `explain_pair`. The two catalogs otherwise differ by design (each
> server exposes skills the other does not); A2A is not parity-gated.

Keep any A2A skill-count claim honest — the TS card count is unchanged (the two
aligned ids collapse into single canonical entries; no net add/remove).

- [ ] **Step 2: Removal/rename grep across doc surfaces** (rollout-docs-sweep): search `docs-site`, `llms.txt`, `llms-full.txt`, READMEs for the strings `dedupe` / `explain_pair` in an **A2A skill** context (not MCP/CLI, where those names are legitimate and unchanged). Update any A2A listing that presented `dedupe`/`explain_pair` as the advertised A2A skill id. (Expected: few or none — the A2A skill list isn't widely enumerated in docs.)

- [ ] **Step 3: Run the box-safe Python guard once more** (Task 2 command) — PASS.

- [ ] **Step 4: Push + PR + arm auto-merge (then STOP)**

```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/a2a-skill-parity
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main \
  --title "goldenmatch A2A skill parity (TS agent-card conformance + id alignment)" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```
PR body: TS-side conformance (`id` + human `name`), the two canonical id alignments with legacy dispatch aliases, Python unchanged (already the reference), docs note; TS verified by CI (box OOMs local vitest). Do NOT poll CI — arm auto-merge and stop.

---

## Notes for the implementer

- **TS is CI-only.** Write + read-verify; never run pnpm/vitest/tsc on the box.
- **Dispatch keys off `id`.** Making `name` human is safe because `dispatchAnySkill`/`dispatchSkill` take a `skill: string` = the id; they never read the card's `name`.
- **Only two ids change** (`dedupe`→`deduplicate`, `explain_pair`→`explain`); the legacy ids stay as fall-through dispatch cases. No handler body changes.
- **De-dup flips to `id`** — behavior-preserving today (every entry has `name === id`), and no registry has a literal `deduplicate`/`explain` id to collide with (agent skills are `agent_*`-prefixed).
- **Python is untouched** except the guard test — it is already the reference shape.
