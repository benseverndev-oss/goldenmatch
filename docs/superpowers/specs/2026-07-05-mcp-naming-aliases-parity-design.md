# goldenmatch MCP naming-alias parity — design

**Status:** approved (brainstorm 2026-07-05), pending spec review
**Depends on:** the API parity gate (PR #1446, on main) and its 6-package rollout
(PR #1449, armed) — this closes the P0 gap that gate *surfaced*.
**Related memory:** `project_api_parity_gate`.

## 1. Problem

The cross-language API parity gate (`scripts/check_api_parity.py` +
`parity/goldenmatch.yaml`) surfaced that goldenmatch's Python and TypeScript MCP
servers expose the **same operation under different names**. An agent that learned
the tool surface against one server cannot call the other by the names it knows:

| Operation            | Python MCP tool     | TypeScript MCP tool |
| -------------------- | ------------------- | ------------------- |
| deduplicate a file   | `find_duplicates`   | `dedupe`            |
| match against ref    | `match_record`      | `match`             |
| explain a pair score | `explain_match`     | `explain_pair`      |
| profile a dataset    | `profile_data`      | `profile`           |
| explain a cluster    | `agent_explain_cluster` (shared) | `explain_cluster` |

The first four are clean 1:1 renames (each name lives in exactly one server's
`python_only`/`ts_only` partition). The fifth is asymmetric: `agent_explain_cluster`
is already **shared** (both servers expose it), while TS additionally exposes a
bare `explain_cluster` doing the same job — so only the bare name diverges.

The gate records these today as declared `python_only` / `ts_only` entries with a
header note calling them a "real parity bug, FOLLOW-UP to reconcile the aliases."
This spec is that follow-up.

### Out of scope (deliberately)

- **CLI naming.** The header also flags `info`/`score`/`tui` (TS) vs `interactive`
  (PY). These are **not** a clean 1:1 rename — `info`/`score`/`tui` are genuinely
  TS-specific convenience commands and `interactive` is a genuinely Python-only
  TUI. There is no shared operation hiding under different names, so there is
  nothing to alias. Left as declared, intentional coverage gaps.
- **A2A skill naming.** A2A has the *same* divergence (`deduplicate` id in Python
  vs `dedupe` name with no `id` in the TS agent card), but reconciling it needs
  the TS agent-card `id` field added first — a larger, separate change. Deferred
  (parity gate §9 already lists A2A as deferred; it is not yet gated).
- **The P1 "port the missing tools" idea is dropped.** Recon established the TS
  MCP surface is *stateless by design* (`dedupe(rows)` returns a `DedupeResult`
  with `clusters` + `stats` inline — see `src/core/types.ts`), so Python's
  stateful `get_stats` / `get_cluster` / `list_clusters` query tools have no
  TS analogue to build, and TS lacks the domains/PPRL subsystems entirely. Those
  gaps are real and intentional, not bugs. We **document** them (§4), not build them.

## 2. Goal

Make every one of the five operations answerable by **both** names on **both**
servers, non-breakingly (no existing name changes or is removed). After this
change the nine alias names move from `python_only`/`ts_only` to `shared` in
`parity/goldenmatch.yaml`, and the gate enforces that they stay there.

Concretely:

- **Python MCP gains 5 alias tools:** `dedupe`, `match`, `explain_pair`,
  `profile`, `explain_cluster`.
- **TypeScript MCP gains 4 alias tools:** `find_duplicates`, `match_record`,
  `explain_match`, `profile_data`.
  (No TS `agent_explain_cluster` alias is needed — it is already shared.)

Nine names move to `shared`:
`dedupe`, `explain_cluster`, `explain_pair`, `find_duplicates`, `explain_match`,
`match`, `match_record`, `profile`, `profile_data`.

## 3. Design

### 3.1 Aliases are pure name indirection — never a second implementation

Each alias resolves to the **existing** handler. No operation logic is copied.
An alias tool advertises the **same input schema** as its canonical tool and a
description of the form `Alias for \`<canonical>\`. <canonical one-liner>`, so a
client sees an identical calling contract under either name.

### 3.2 Python (`packages/python/goldenmatch/goldenmatch/mcp/server.py`)

**Critical:** goldenmatch's Python MCP has *two* advertise paths and *two*
dispatch paths, not one. The fix must funnel aliases through the shared
**component** structures both paths reference, not the top-level composed objects.

- **Advertise path A** — the `@server.list_tools` handler (server.py:618-620)
  does **not** return the composed `TOOLS` var; it *rebuilds the sum inline*:
  `return AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + ROUTING_TOOLS + _BASE_TOOLS`.
- **Advertise path B** — the exported `TOOLS` var (server.py:585, same sum) is
  what the parity emitter (`emit_python_surface.py`) and smoke test import.
- **Dispatch path A** — the `@server.call_tool` handler (server.py:862), used by
  the standalone server; agent tools via `if name in _AGENT_TOOL_NAMES` (:871),
  then base tools.
- **Dispatch path B** — the module-level `dispatch(name, args)` (server.py:588),
  used by the `goldensuite-mcp` aggregator (`goldensuite_mcp/server.py:50` does
  `return _normalize_tools(list(gm.TOOLS)), gm.dispatch`). Same name-routing,
  separate function.

Appending aliases only to the `TOOLS` var (path B) or normalizing only in
`call_tool` (dispatch A) would advertise aliases the live/aggregated servers
can't serve. Three edits:

1. **One alias map + one resolver helper (shared by both dispatch paths).**

   ```python
   _MCP_TOOL_ALIASES = {
       "dedupe": "find_duplicates",
       "match": "match_record",
       "explain_pair": "explain_match",
       "profile": "profile_data",
       "explain_cluster": "agent_explain_cluster",
   }

   def _resolve_alias(name: str) -> str:
       return _MCP_TOOL_ALIASES.get(name, name)
   ```

2. **Normalize at the top of BOTH dispatch functions.** First line of
   `dispatch` (server.py:588) *and* of the `call_tool` handler (server.py:862):
   `name = _resolve_alias(name)`. Placing it before the `_AGENT_TOOL_NAMES`
   check in each means `explain_cluster` routes into the agent path correctly.

3. **Advertise via a component list, not the composed var.** Build an
   `ALIAS_TOOLS` list of 5 `Tool` objects (each derived from its canonical
   tool's `inputSchema` with the alias `name` + "Alias for `<canonical>`. …"
   description — a helper reads `_MCP_TOOL_ALIASES` + the canonical `Tool`
   objects so schemas never diverge) and **append it to `_BASE_TOOLS`**.
   Because both advertise paths sum `_BASE_TOOLS`, this single append flows into
   the live `list_tools` (path A), the exported `TOOLS` var (path B), and the
   aggregator (which reads `gm.TOOLS`). `_BASE_TOOLS` is chosen because it is
   summed by every path; the alias for `explain_cluster` still *dispatches* to
   the agent handler via the resolver — the advertising list is orthogonal to
   the routing.

`len(TOOLS)` grows by exactly 5; the parity smoke test (which asserts the emitter
count equals the measured `len(TOOLS)`) keeps that honest.

### 3.3 TypeScript (`packages/typescript/goldenmatch/src/node/mcp/server.ts`)

`TOOLS` (server.ts:369) = `[...EXISTING_TOOLS, ...MEMORY_TOOLS, ...IDENTITY_TOOLS,
...AGENT_MCP_TOOLS]`. The four target ops live in `EXISTING_TOOLS` and are
dispatched by the `switch (name)` in `handleTool` (~server.ts:509). Two edits:

1. **Advertise the aliases.** Append 4 alias `Tool` defs to `EXISTING_TOOLS`
   (or a dedicated `ALIAS_TOOLS` array folded into the `TOOLS` spread), each
   carrying the canonical tool's `inputSchema` with the alias `name` +
   "Alias for …" description. Derive them from a
   `{ find_duplicates: "dedupe", match_record: "match", explain_match:
   "explain_pair", profile_data: "profile" }` map applied over the canonical
   defs (mirror of the Python helper).

2. **Dispatch the aliases.** Stack each alias as a fall-through `case` above its
   canonical case, e.g.:
   ```ts
   case "find_duplicates":
   case "dedupe": { /* existing dedupe body */ }
   ```
   No body is duplicated — the alias label falls through to the canonical block.

### 3.4 Manifest (`parity/goldenmatch.yaml`)

Move the nine names into `mcp_tools.shared` (keeping every partition sorted and
disjoint — the gate's structural check enforces both). Remove them from
`python_only` (`find_duplicates`, `match_record`, `explain_match`, `profile_data`)
and `ts_only` (`dedupe`, `match`, `explain_pair`, `profile`, `explain_cluster`).
Update the header: category 1 ("naming divergence … FOLLOW-UP") becomes "resolved
via non-breaking aliases (both servers answer to both names)."

This manifest edit lands in the **same PR** as the code (the gate fails otherwise —
that coupling is the whole point of the gate).

## 4. P1 documentation — intentional gaps, no code

Refine the manifest header(s) so the remaining Python-only / TS-only entries read
as deliberate decisions rather than unexamined drift:

- **`parity/goldenmatch.yaml`:** extend category 2 to name the stateful query tools
  explicitly — `get_stats` / `get_cluster` / `list_clusters` are Python-only
  **because** the TS MCP is stateless (`dedupe` returns `stats` + `clusters`
  inline via `DedupeResult`), so there is no server-held dataset to query. Note
  domains (`create_domain`/`list_domains`/`test_domain`) and PPRL
  (`pprl_link`/`pprl_auto_config`) are Python-only because those subsystems are
  Python-only.
- **`parity/goldencheck.yaml`:** annotate `install_domain` (its sole py-only MCP
  tool) as intentional — the TS core exposes a read-only domain registry
  (`listAvailableDomains`/`getDomainTypes`, no install path).
  **Sequencing:** `parity/goldencheck.yaml` is created by PR #1449 (not yet on
  main). If #1449 has merged when this work lands, fold the goldencheck header
  note into this PR; otherwise it is a one-line follow-up after #1449 merges.
  This PR does not block on #1449.

## 5. Testing

- **Python (box-safe, runs locally with `goldenmatch[mcp]`):**
  - The live `list_tools` handler (not just the `TOOLS` var) returns all five
    alias names, and each alias's `inputSchema` equals its canonical tool's
    schema. Assert against the handler from `create_server`, so a regression to
    advertise-path-A is caught.
  - **Dispatch path A:** `dispatch("dedupe", args)` returns byte-identical output
    to `dispatch("find_duplicates", args)` on a small fixture; same for the other
    four pairs, including `dispatch("explain_cluster", …)` ==
    `dispatch("agent_explain_cluster", …)` (the agent-route case). This is the
    aggregator path — the one Issue 2 flagged as previously unrouted.
  - The `call_tool` handler resolves the same aliases (a lighter test, since both
    now share `_resolve_alias`).
  - The existing parity smoke test (`scripts/test_api_parity.py`) still passes
    with the emitter count matching the grown `len(TOOLS)`.
- **TypeScript (CI-only — the box OOMs TS builds):**
  - `TOOLS` contains the four alias names; a unit test asserts calling
    `find_duplicates` and `dedupe` through `handleTool` yields identical results
    on a fixture (same for the other three pairs).
- **Gate (CI):** `check_api_parity.py goldenmatch` is green with the nine names in
  `shared`. As a red/green teeth check, moving one alias out of `shared` in the
  manifest must fail the gate (already covered by the gate's own unit tests; no
  new gate test needed).

## 6. Rollout / docs

- Single PR: Python aliases + TS aliases + `parity/goldenmatch.yaml` update +
  P1 header notes. Branch `feat/parity-p0-mcp-aliases` off `origin/main`.
  benzsevern gh; merge-queue repo → `gh pr merge --auto --squash` (no
  `--delete-branch`); arm auto-merge and stop.
- Docs sweep (rollout-docs-sweep): the MCP tool reference / tuning docs list tool
  names — add the alias names where the canonical tools are documented, noting
  they are aliases. Keep tool-count claims honest (Python MCP +5, TS MCP +4).

## 7. Risks

- **Low.** Purely additive — no existing tool name changes or is removed, so no
  client breaks. The only behavioral surface is five/four *new* names that
  forward to audited handlers.
- **Schema drift between alias and canonical** is prevented by deriving alias
  schemas from the canonical `Tool` objects (not hand-copying), on both sides.
- **`explain_cluster` routing:** the Python alias must be normalized *before* the
  `_AGENT_TOOL_NAMES` check so it reaches the agent handler; the test in §5
  covers this exact path.
- **Two-path drift (the main implementation trap):** Python advertises via two
  paths (composed `TOOLS` var + inline `list_tools` rebuild) and dispatches via
  two (`call_tool` + module-level `dispatch` for the suite aggregator). Aliases
  that touch only one path advertise-but-can't-serve or serve-but-aren't-listed.
  §3.2 funnels through the shared `_BASE_TOOLS` component + a shared
  `_resolve_alias` called by both dispatchers so they cannot drift; §5 tests the
  aggregator `dispatch` path explicitly.
