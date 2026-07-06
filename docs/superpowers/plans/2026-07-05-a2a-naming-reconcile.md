# A2A Naming Reconciliation — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Alias goldenmatch's 3 verified same-op A2A skill pairs to Python's canonical id (non-breaking, TS-side) and move them to `a2a_skills.shared`; document the 2 non-aliasable pairs.

**Architecture:** TS-only. A single `A2A_AGENT_ID_ALIASES` map (tool-id → canonical A2A id) drives (1) a card-id override in `buildCardSkills` so the card advertises the canonical id, and (2) a canonical→tool resolution at the top of `dispatchAnySkill` so the canonical id routes to the existing agent-tool handler. Legacy ids keep dispatching for free. Python is already the reference (unchanged).

**Tech Stack:** TypeScript (a2a server, vitest), Python (pytest guard), YAML manifest.

**Spec:** `docs/superpowers/specs/2026-07-05-a2a-naming-reconcile-design.md`

**The 3 alias pairs (canonical ← legacy tool-id):** `autoconfig`←`auto_configure`, `compare_strategies`←`agent_compare_strategies`, `transform`←`run_transforms`. **Documented-different (untouched):** `quality` (1:2 vs scan_quality+fix_quality), `pprl` (runs) vs `suggest_pprl` (suggests).

**Anchors (verified):** `src/node/a2a/server.ts` — `buildCardSkills` :192, AGENT_SKILLS loop :201, doc comment :186-190, `dispatchAnySkill` :505, `AGENT_TOOL_NAMES.has(skill)` :509. `src/core/agent/skills.ts` — `auto_configure` :279, `agent_compare_strategies` :318, `run_transforms` :491. `AGENT_TOOL_NAMES` from AGENT_SKILLS (`node/mcp/agent-tools.ts:40-42`). `emit_ts_surface.mjs:58` emits a2a from `AGENT_CARD.skills.map(s=>s.id)`. Existing test `tests/unit/a2a-skill-parity.test.ts` (#1457, canonical-not-legacy pattern :21-26). Manifest `parity/goldenmatch.yaml` a2a_skills: python_only has autoconfig/compare_strategies/transform; ts_only has auto_configure/agent_compare_strategies/run_transforms.

**Environment / SOP:**
- Branch `feat/a2a-naming-reconcile` off `origin/main`.
- **TS is CI-only** (box OOMs) — write + read-verify. Python guard + manifest structure are box-safe (`POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe`, PYTHONPATH shadow for the guard). Run **ruff** on any Python file touched (the #1451 lesson).
- benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`); arm auto-merge + STOP.

---

## Task 1: TypeScript — alias map + card override + dispatch resolution (CI-only)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/node/a2a/server.ts`
- Modify: `packages/typescript/goldenmatch/tests/unit/a2a-skill-parity.test.ts`

> Do NOT run vitest/tsc. Write + read-verify; CI verifies.

- [ ] **Step 1: Add the alias map** (near the top of the card section, before `buildCardSkills`):
```ts
// A2A naming reconciliation: 3 agent skills advertise Python's canonical id on
// the A2A card (a2a_skills parity). The underlying agent-tool id (also the MCP
// tool id) is UNCHANGED — the A2A card is a separate surface. tool-id -> canonical.
const A2A_AGENT_ID_ALIASES: Record<string, string> = {
  auto_configure: "autoconfig",
  agent_compare_strategies: "compare_strategies",
  run_transforms: "transform",
};
// canonical -> tool-id, for dispatch resolution.
const A2A_CANONICAL_TO_TOOL: Record<string, string> = Object.fromEntries(
  Object.entries(A2A_AGENT_ID_ALIASES).map(([tool, canon]) => [canon, tool]),
);
```

- [ ] **Step 2: Override the advertised id in `buildCardSkills`** (:201). Change:
```ts
  for (const def of AGENT_SKILLS) push(toAgentSkill({ id: def.id, description: def.description }));
```
to:
```ts
  for (const def of AGENT_SKILLS)
    push(toAgentSkill({ id: A2A_AGENT_ID_ALIASES[def.id] ?? def.id, description: def.description }));
```

- [ ] **Step 3: Resolve canonical→tool at the top of `dispatchAnySkill`** (:505-ish). Add `resolved` and use it for the routing (do NOT reassign the `skill` param — the reviewer flagged `no-param-reassign`):
```ts
export async function dispatchAnySkill(skill: string, input: Record<string, unknown>): Promise<unknown> {
  const resolved = A2A_CANONICAL_TO_TOOL[skill] ?? skill;
  if (AGENT_TOOL_NAMES.has(resolved)) return handleAgentTool(resolved, input);
  if (MEMORY_TOOL_NAMES.has(resolved)) return unwrapTextContent(await handleMemoryTool(resolved, input));
  if (IDENTITY_TOOL_NAMES.has(resolved)) return unwrapTextContent(await handleIdentityTool(resolved, input));
  return dispatchSkill(resolved, input);
}
```
(Read the actual current body first — replace each `skill` use in the routing with `resolved`. For every non-canonical id `resolved === skill`, so behavior is unchanged except the 3 canonical ids now route to their agent tool.)

- [ ] **Step 4: Update the `buildCardSkills` doc comment** (:186-190) — add a parallel line to the existing dedupe/explain_pair note:
> Three agent skills advertise Python's canonical id (`autoconfig`/`compare_strategies`/`transform` for the `auto_configure`/`agent_compare_strategies`/`run_transforms` handlers) via `A2A_AGENT_ID_ALIASES`; the legacy ids still dispatch.

- [ ] **Step 5: Extend `tests/unit/a2a-skill-parity.test.ts`** — mirror the existing canonical-not-legacy block (:21-26). Add:
```ts
  it("advertises the reconciled canonical agent ids, not the legacy ones", () => {
    const ids = new Set(AGENT_CARD.skills.map((s) => s.id));
    for (const canon of ["autoconfig", "compare_strategies", "transform"]) expect(ids.has(canon)).toBe(true);
    for (const legacy of ["auto_configure", "agent_compare_strategies", "run_transforms"]) expect(ids.has(legacy)).toBe(false);
  });

  it("dispatches the canonical id identically to the legacy tool id", async () => {
    // both resolve to the same agent tool -> identical result on a fixture
    for (const [canon, legacy] of [["autoconfig","auto_configure"],["compare_strategies","agent_compare_strategies"],["transform","run_transforms"]]) {
      const input = { rows: [{ id: "1", name: "A" }, { id: "2", name: "A" }] };
      expect(await dispatchAnySkill(canon, input)).toEqual(await dispatchAnySkill(legacy, input));
    }
  });
```
Read the existing test file's imports (`AGENT_CARD`, `dispatchAnySkill`) — they're already imported for the #1457 tests. If a skill's real input keys differ from `{ rows }`, read its handler and match them (the point is identical input to both ids).

- [ ] **Step 6: Verify by reading** — the map + inverse; the card override; `dispatchAnySkill` uses `resolved` for all four routing branches; the doc comment; the test imports resolve. Brace balance intact.

- [ ] **Step 7: Commit**
```bash
git add packages/typescript/goldenmatch/src/node/a2a/server.ts packages/typescript/goldenmatch/tests/unit/a2a-skill-parity.test.ts
git commit -m "feat(a2a-ts): reconcile 3 agent skill ids to Python canonical (card + dispatch alias)"
```

---

## Task 2: Manifest — move 3 canonical ids to a2a_skills.shared (box-safe)

**Files:** `parity/goldenmatch.yaml`

- [ ] **Step 1: Edit the `a2a_skills` partition.**
  - **shared** — add (keep sorted): `autoconfig`, `compare_strategies`, `transform`.
  - **python_only** — remove: `autoconfig`, `compare_strategies`, `transform`.
  - **ts_only** — remove: `auto_configure`, `agent_compare_strategies`, `run_transforms`.
  Result counts: shared 20, python_only 18, ts_only 16.

- [ ] **Step 2: Update the a2a_skills header note** — the 3 reconciled pairs are now shared; refine the divergence note to:
> Remaining a2a_skills divergences are VERIFIED-DIFFERENT ops, not drift: PY `quality` is one scan+fix skill vs TS's `scan_quality`+`fix_quality` (1:2 granularity); PY `pprl` RUNS linkage (`pprl_link`, two files) while TS `suggest_pprl` SUGGESTS params (`profileForAgent`, "recommend") — TS `suggest_pprl` is the A2A analogue of Python's own `suggest_pprl`, not `pprl`.

- [ ] **Step 3: Box-safe structure check.**
Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import yaml; from importlib.util import spec_from_file_location,module_from_spec; import sys; s=spec_from_file_location('g','scripts/check_api_parity.py'); m=module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); man=yaml.safe_load(open('parity/goldenmatch.yaml')); print('a2a:', {k:len(v) for k,v in man['a2a_skills'].items()}, 'structure:', [f.kind for f in m.check_structure(man)])"`
Expected: `a2a: {'shared': 20, 'python_only': 18, 'ts_only': 16} structure: []`. (Full partition vs the real TS emitter is CI-verified.)

- [ ] **Step 4: Commit**
```bash
git add parity/goldenmatch.yaml
git commit -m "chore(parity): move 3 reconciled a2a_skills to shared; document the 2 different-op pairs"
```

---

## Task 3: Python — canonical-id regression guard (box-safe)

**Files:** `packages/python/goldenmatch/tests/test_a2a.py`

- [ ] **Step 1: Append a guard** (Python is unchanged; this locks the canonical ids the TS side aligns to):
```python
def test_a2a_exposes_reconciled_canonical_ids():
    """The 3 A2A skills the TS card aligns to must stay under their canonical ids."""
    from goldenmatch.a2a.server import _SKILLS
    ids = {s["id"] for s in _SKILLS}
    assert {"autoconfig", "compare_strategies", "transform"} <= ids
```

- [ ] **Step 2: Run it + ruff.**
Run: `cd packages/python/goldenmatch && PYTHONPATH=$(pwd) POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_a2a.py::test_a2a_exposes_reconciled_canonical_ids -v` → PASS.
Run: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check packages/python/goldenmatch/tests/test_a2a.py` → All checks passed.

- [ ] **Step 3: Commit**
```bash
git add packages/python/goldenmatch/tests/test_a2a.py
git commit -m "test(a2a): guard the 3 reconciled canonical skill ids on the Python reference"
```

---

## Task 4: PR

- [ ] **Step 1: Docs sweep** — grep docs for `auto_configure`/`agent_compare_strategies`/`run_transforms` presented as an **A2A skill id** (not MCP tool, where they're unchanged). `agent.mdx` is the Python card, already using the canonical ids; expect no changes. Skill counts unchanged (rename, not add/remove).

- [ ] **Step 2: Push + PR + arm auto-merge (STOP).** PR body: 3 verified same-op pairs aliased to Python canonical (card-id override + dispatch map, legacy dispatch preserved); 2 pairs documented as verified-different; manifest a2a_skills 17/21/19→20/18/16; MCP tool ids untouched (A2A card is a separate surface). TS CI-verified; Python guard + manifest box-checked.
```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/a2a-naming-reconcile
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main --title "A2A naming reconciliation (alias 3 verified pairs)" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```

---

## Notes for the implementer

- **TS-only + Python unchanged.** The card override + dispatch map are the whole change; the map is the single source of truth (invert for dispatch).
- **Don't rename the MCP tool.** The override is in `buildCardSkills` only; `AGENT_TOOL_NAMES`/`AGENT_MCP_TOOLS` still use `def.id` — `auto_configure` etc. stay MCP tools.
- **Manifest in the same PR** — the emitter reads the card, so the override changes the emitted a2a set; the gate fails if the manifest doesn't match.
- **Run ruff** on the Python guard (the #1451 lint lesson).
- **Do NOT touch** `quality`/`scan_quality`/`fix_quality`/`pprl`/`suggest_pprl` — verified-different, documented.
