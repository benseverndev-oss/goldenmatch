# A2A skills in the API-parity gate — design

**Status:** approved-in-scope (2026-07-05), pending spec review
**Context:** the API-parity gate covers MCP tools + CLI commands across 6 packages
(#1446, #1449). A2A skills were explicitly deferred (§9) "pending TS agent-card
reconciliation FIRST" — that reconciliation shipped for goldenmatch (#1457) and
the other three A2A packages were already `{id, name}`-conformant. This closes the
deferral: A2A becomes a gated surface, turning "the A2A catalogs differ by design"
prose into an enforced, drift-catching partition. Related: `project_api_parity_gate`.

## 1. Problem

Nothing catches A2A skill drift between the Python and TypeScript agent cards. A
skill added/renamed/removed on one server but not the other is invisible until a
docs audit or a broken cross-server agent stumbles on it — exactly the class the
MCP gate already prevents for tools, and that #1457 just fixed *by hand* for
goldenmatch's `dedupe`/`deduplicate`.

A2A exists in **4** packages (Python + TS): goldenmatch, goldencheck, goldenflow,
goldenpipe. (infermap and goldenanalysis have no A2A server.)

## 2. Goal

Add `a2a_skills` as a third gated surface (alongside `mcp_tools`, `cli_commands`),
emitted for the 4 A2A packages and partitioned shared/python_only/ts_only in each
package's `parity/<pkg>.yaml`. The gate already skips surfaces absent from a
package, so infermap/goldenanalysis need no change.

## 3. Design

### 3.1 The gate — one line

`scripts/check_api_parity.py:10` — add `"a2a_skills"` to the `SURFACES` tuple.
That is the *only* gate change: `init_manifest` already `continue`s on a surface
empty for a package, and `run_checks` already `continue`s on a surface not in the
manifest (verified at check_api_parity.py:74-88). So a package with no A2A simply
carries no `a2a_skills` key, on both the emitter and manifest sides, and is skipped.

### 3.2 Python emitter — `a2a_skills` producer (per-package accessor variance)

`scripts/emit_python_surface.py` gains an `a2a_skills` surface for the 4 A2A
packages. The skill-catalog accessor varies (same kind of variance the MCP
producer already handles for `TOOLS`):
- **goldenmatch** exposes a flat `_SKILLS` list: `[s["id"] for s in a2a.server._SKILLS]`.
- **goldencheck / goldenflow / goldenpipe** nest skills in a card dict:
  `[s["id"] for s in a2a.server.AGENT_CARD["skills"]]`.

A single producer handles both: import `<pkg>.a2a.server`; use `_SKILLS` if the
module defines it, else `AGENT_CARD["skills"]`; return `sorted(s["id"] ...)`. This
is **box-safe** — verified: importing all four a2a modules runs clean with no
server bind (the `.listen`/serve call lives inside each package's `run_*server`
entry, not at import). Computed Python skill counts: goldenmatch 38 (`_SKILLS`),
goldencheck 9, goldenflow 6, goldenpipe 4 (`AGENT_CARD["skills"]`). Packages without
an `a2a` module get no `a2a_skills` key. A per-package REGISTRY flag marks which
packages have A2A.

### 3.3 TypeScript emitter — `a2a_skills` producer

`scripts/emit_ts_surface.mjs` gains an `a2a_skills` surface: import the built
`dist/node/a2a/server.js`, read `AGENT_CARD.skills.map(s => s.id)`. Import-safe:
`server.listen(...)` lives inside `runA2aServer()`, not at module top level.
CI-only (box OOMs TS builds), as with `mcp_tools`.

**Prerequisite (one line):** goldencheck's TS `AGENT_CARD` is currently `const
AGENT_CARD = {...}` (unexported, server.ts:30) — add `export` so the emitter can
import it. The other three already `export const AGENT_CARD`. Non-breaking. Their
skill entries all carry `id` (verified).

### 3.4 Manifests — bootstrap 4 `a2a_skills` partitions

For each of the 4 A2A packages, `--init` produces the `a2a_skills` partition
(`shared = PY∩TS`, `python_only = PY−TS`, `ts_only = TS−PY`), which is human-
reviewed and committed into the existing `parity/<pkg>.yaml`. Expect large
python_only sets (e.g. goldenmatch's 38 Python skills vs its TS card union) —
intentional coverage differences, exactly as the MCP manifests carry. Note the
un-reconciled packages already show likely divergences to triage — goldenflow/
goldenpipe use hyphenated Python ids (`run-pipeline`, `transform-data`,
`map-schemas`, `diff-results`) whose TS counterparts may differ. The bootstrap
**may surface genuine naming divergences** in goldencheck/goldenflow/goldenpipe A2A
(only goldenmatch's was reconciled in #1457): each is triaged — a real
same-op-different-id pair is either fixed (a follow-up) or recorded in the manifest
header as a known divergence; an intentional coverage gap is just partitioned. The
gate does not force them to be equal; it makes each a reviewed decision.

### 3.5 CI

The `api_parity` job is already a 6-package matrix that builds each package
(`pnpm turbo run build --filter=<pkg>` builds `dist/node/a2a`) and runs both
emitters + the gate. Adding the surface needs no job change beyond the paths
filter: add `packages/*/*/a2a/**` and `packages/typescript/*/src/node/a2a/**` to
the `api_parity` filter (ci.yml ~:415) so A2A edits trigger the gate.

## 4. Testing

- **Gate unit tests** (`scripts/test_api_parity.py`, box-safe):
  - **BLOCKER fix (must be in this PR):** `test_structure_flags_unknown_surface`
    (~:73-76) currently uses `"a2a_skills"` as its *unknown-surface* negative
    fixture. The moment `"a2a_skills"` joins `SURFACES` it becomes *known*,
    `check_structure` stops flagging it, and that assertion fails. Repoint the
    fixture to a genuinely-unregistered name (e.g. `"grpc_methods"`). One line.
  - Extend the structure/partition tests to include an `a2a_skills` surface (it
    flows through the same `SURFACES` loop — a smoke assertion that a 3-surface
    manifest partitions correctly and that a package lacking `a2a_skills` is
    skipped, not failed).
- **Python emitter smoke** (box-safe): the existing per-package emitter smoke test
  gains an assertion that the 4 A2A packages emit a non-empty sorted `a2a_skills`
  and the 2 non-A2A packages emit none. Assert goldenmatch's `a2a_skills` equals
  the measured `[s["id"] for s in _SKILLS]` (never a hardcoded count).
- **TS emitter** is CI-only; the `api_parity` matrix is the authoritative check
  that the emitted TS `a2a_skills` matches each manifest.

## 5. Rollout / docs

- Single PR, branch `feat/a2a-parity-gate` off `origin/main` (has #1457). Gate +
  both emitters + goldencheck `export` + 4 bootstrapped manifests + filter + tests.
- benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`);
  arm auto-merge, stop.
- rollout-docs-sweep: the parity-gate docs / any "surfaces covered" note updates
  from "MCP tools + CLI" to "+ A2A skills".

## 6. Risks

- **Bootstrap surfaces real divergences** in the un-reconciled 3 packages. That's
  the point, but it adds triage to the manifest review. Mitigation: a genuine
  same-op naming divergence is recorded in the manifest header as a known follow-up
  (like the MCP header did) rather than blocking this PR on fixing it — the gate's
  job is to *surface and freeze* the partition, not to force reconciliation.
- **TS a2a import side effects.** If any package's `dist/node/a2a/server.js` ran
  the server at import, the emitter would hang. Verified all four bind
  `server.listen` inside `run*A2aServer()`. The emitter smoke (CI) fails loud if an
  import blocks.
- **Accessor drift.** The Python producer's `_SKILLS`-else-`AGENT_CARD["skills"]`
  is the current variance; a fifth idiom would need a registry entry. Fail-loud if
  a package flagged as having A2A yields zero skills.
