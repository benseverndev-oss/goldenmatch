# goldenmatch A2A skill parity — design

**Status:** approved (brainstorm 2026-07-05), pending spec review
**Related:** the MCP naming-alias work (#1451) closed the MCP surface's naming
divergence; this closes the A2A surface's — the follow-up that work deferred
("needs the TS agent-card `id` field added first"). Related memory:
`project_api_parity_gate`.

## 1. Problem

goldenmatch's two A2A agent cards diverge on two axes:

1. **Schema shape (the deferred prerequisite).** The A2A `AgentSkill` spec — and
   the Python server — model a skill as `{id, name, description, inputModes,
   outputModes}` where `id` is the machine identifier and `name` is a
   human-readable label. The **TypeScript** `AgentSkill`
   (`packages/typescript/goldenmatch/src/node/a2a/server.ts:59`) is
   `{name, description, inputModes, outputModes}` — no `id`, and `name` doubles
   as the machine id (per its own comment at :83). TS is non-conformant.
2. **Naming.** Two skills that are the *same operation* carry different ids per
   server: `dedupe`(TS)/`deduplicate`(PY), and `explain_pair`(TS)/`explain`(PY).
   (`match`, `evaluate`, `analyze_data`, `controller_telemetry`, `review_config`,
   the `identity_*` set, and the memory skills already agree.)

**Python A2A is already the reference:** it is already spec-shaped and already
uses the canonical ids. So the entire reconciliation is TS-side.

### What is NOT in scope (deliberately)

The two catalogs are *structurally* different — different granularity and naming
philosophy — and most differences are genuine, not drift. These are **documented
as intentional, not force-aliased**:
- Python's finer granularity: `quality`(PY) ↔ `scan_quality`+`fix_quality`(TS);
  `review`(PY) ↔ `agent_review_queue`+`agent_approve_reject`(TS).
- Genuinely different operations that merely look similar:
  `pprl`(PY, *runs* linkage) vs `suggest_pprl`(TS, *suggests* params);
  `configure`/`autoconfig`(PY) vs `suggest_config`/`auto_configure`(TS).
- TS-only base skills: `score`, `profile`, `list_scorers`/`list_transforms`/
  `list_strategies`; the `agent_*`-prefixed AGENT_SKILLS.
- Python-only skills: `identity_audit`/`_seal`/`_verify`, `sensitivity`,
  `analyze_blocking`, `compare_clusters`, `schema_match`, `incremental`, etc.

Also out of scope (per the scope decision): **extending the parity gate to an
`a2a_skills` surface.** The gate stays MCP-tools + CLI only; A2A stays documented,
not CI-enforced.

## 2. Goal

Make the TS A2A agent card A2A-spec-conformant (`{id, name}`) and align the two
verified same-operation skill ids to Python's canonical names — non-breakingly.
After this change:
- Every TS card skill has an `id` (machine) and a human-readable `name`.
- The TS card advertises `deduplicate` and `explain` (Python's ids) for those two
  operations; the legacy ids `dedupe`/`explain_pair` still **dispatch** (they are
  just no longer *advertised* as separate skills).
- Python is unchanged (it is already the reference).

## 3. Design (all TypeScript, in `src/node/a2a/server.ts`)

### 3.1 Component 1 — `AgentSkill` spec conformance

- Add `readonly id: string;` to the `AgentSkill` interface (:59), keeping
  `name`, `description`, `inputModes`, `outputModes`. `name` becomes the
  human-readable label.
- **`BASE_SKILLS`** (:85): each entry gets `{ id: <machine>, name: <Human Label> }`.
  `id` keeps the exact current machine string; `name` gets a curated label
  (e.g. `id: "match", name: "Match"`). Hand-written for these 10.
- **`toAgentSkill`** (:148) — used for the ~26 derived skills (AGENT_SKILLS via
  `def.id`, memory/identity tools via `tool.name`): change its input to carry the
  machine id and emit `{ id, name, ... }`. Since those registries carry no curated
  human label, derive one with a `humanize(id)` helper (title-case + de-underscore,
  e.g. `agent_deduplicate` → `"Agent Deduplicate"`). Keep `description` verbatim.
- **`buildCardSkills`** (:167): the de-dup `seen` set switches from `skill.name`
  to **`skill.id`** (:171-172). (Ids are unique across the four registries; a
  base skill still shadows a same-id registry entry — same first-wins behavior,
  keyed on id.)
- **Dispatch is unchanged.** `dispatchAnySkill`/`dispatchSkill` (:481/:259) route
  on the request's `skill` string, which equals the `id`. No handler edits from
  Component 1.

**Non-breaking caveat (documented):** a client that read the *id-less* card's
`name` as a dispatch key will now read a human label instead of the machine id.
Acceptable at v0.1.0 — the card never exposed an `id`, so such a client was
dispatching off a field the spec designates human-readable. The `id` equals the
old `name` string, so any client that hard-coded the skill string, or that reads
the new `id`, is unaffected.

### 3.2 Component 2 — id alignment (single canonical = Python's)

Only the two **verified same-operation** pairs are aliased (confirmed against the
handlers/descriptions: TS `dedupe` and PY `deduplicate` both deduplicate; TS
`explain_pair` "Explain why two records match" and PY `explain` "Explain why two
records matched or did not match" are the same op).

- In `BASE_SKILLS`, the `dedupe` entry becomes `id: "deduplicate", name:
  "Deduplicate"`, and the `explain_pair` entry becomes `id: "explain", name:
  "Explain"`. So the **card advertises the canonical ids** (one entry each).
- In `dispatchSkill` (:259), preserve the legacy ids as **dispatch-only aliases**
  via fall-through cases (canonical label stacked above the existing body):
  ```ts
  case "deduplicate":   // canonical (A2A card id)
  case "dedupe": { ...existing dedupe body... }

  case "explain":       // canonical (A2A card id)
  case "explain_pair": { ...existing explain_pair body... }
  ```
  No handler body changes — the canonical id falls through to the existing logic,
  and the legacy id keeps working.
- **Python unchanged.** It already advertises `deduplicate`/`explain` and needs no
  legacy alias: an agent using the canonical ids (now on *both* cards) works on
  both servers, and a legacy TS client using `dedupe`/`explain_pair` only ever hit
  the TS server (where the dispatch aliases keep it working) — it never worked
  against Python with those ids, so there is no regression to cover.

### 3.3 Component 3 — document the intentional differences

The remaining catalog differences (§1 "not in scope") are genuine, not drift.
Record that so a future reader doesn't mistake them for bugs:
- A concise comment block near `buildCardSkills` in `src/node/a2a/server.ts`
  summarizing: the card is A2A-spec-shaped (`{id, name}`); the two aligned ids;
  and that the granularity/`agent_*`/TS-only/PY-only differences are intentional
  (A2A is not gated for parity — MCP tools + CLI are).
- A short note on the docs-site A2A page (`docs-site/goldenmatch/agent.mdx`):
  the TS and Python A2A servers share canonical ids for the core skills
  (`deduplicate`, `match`, `explain`, `evaluate`, …); the legacy TS ids
  `dedupe`/`explain_pair` still dispatch.

## 4. Testing

**TypeScript (CI-only — the box OOMs vitest/tsc):** new
`tests/unit/a2a-skill-parity.test.ts`:
- Every skill in `AGENT_CARD.skills` has a non-empty `id` and a non-empty `name`.
- All `id`s are unique (de-dup by id works).
- The card advertises `deduplicate` and `explain`, and does **not** advertise
  `dedupe` or `explain_pair` as separate skills.
- `dispatchAnySkill("deduplicate", input)` and `dispatchAnySkill("dedupe", input)`
  return the same result on a small fixture; same for `explain`/`explain_pair`.
- `humanize` produces the expected labels for a couple of derived ids.

**Python (box-safe):** a regression test asserting `_SKILLS` entries all carry
`id` + `name`, and that the canonical ids `deduplicate` and `explain` are present
(guards the reference shape the TS side aligns to).

## 5. Rollout / docs

- Single PR, branch `feat/a2a-skill-parity` off `origin/main`. TS code + TS tests
  + the Python regression test + the docs-site note. benzsevern gh; merge-queue
  repo → `gh pr merge --auto --squash` (no `--delete-branch`); arm auto-merge, stop.
- rollout-docs-sweep: the A2A page and any llms.txt A2A-skill listing/count stay
  honest (the TS card gains `id`/human `name`; no net skill-count change — the two
  aliases collapse into the canonical entries, so the TS card count is unchanged).

## 6. Risks

- **Low, TS-only, additive.** New `id` field + a cosmetic advertised-id change on
  two skills, with legacy dispatch preserved. The §3.1 caveat (a client dispatching
  off the old card's `name`) is the only behavioral edge, v0.1.0-acceptable.
- **`explain`/`explain_pair` sameness** was verified from the handler descriptions;
  the implementation re-confirms against the actual dispatch bodies before aliasing.
  If they are found to differ, ship only `dedupe`/`deduplicate` and document
  `explain`/`explain_pair` as a coverage difference instead.
- **`humanize` label quality** for derived skills is cosmetic; a wrong-looking
  title-case label is not load-bearing (dispatch keys off `id`, not `name`).
