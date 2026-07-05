# Cross-language API parity: manifest + drift gate

**Date:** 2026-07-04
**Status:** Design (approved for spec review)
**Scope of this slice:** the mechanism + goldenmatch as the reference package. Rolling the manifest to the other five packages is an explicit follow-up.

---

## 1. Goal

The Golden Suite ships every package in Python **and** TypeScript, but the two runtimes have drifted: goldenmatch exposes 69 MCP tools in Python and 45 in TS, CLI command sets differ, A2A skill lists differ. Some gaps are intentional (edge-safe scoping); others are accidental and nobody notices until a docs audit finds them.

This slice makes the cross-language **operation surface** a governed, single-source-of-truth artifact: a declarative **parity manifest** per package that partitions each surface into `shared` / `python_only` / `ts_only`, plus a CI **drift gate** that fails when the real code diverges from the manifest. The gate does not force TS to implement everything Python does — it makes every gap a *deliberate, reviewed* decision and catches *undeclared* drift the moment it lands.

This extends the suite's existing byte-parity-gate discipline (goldenpipe-core, goldenflow-core replay golden vectors across surfaces) from the compute kernels up to the public operation surface.

---

## 2. Scope

**In scope — the three "operation" surfaces**, which are discretely enumerable *string sets*:
- **MCP tools** — the tool names each package's MCP server advertises.
- **CLI commands** — the top-level command (and sub-app) names each package's CLI exposes.
- **A2A skills** — the skill ids each package's A2A agent card advertises.

A load-bearing property: these three are **protocol / UX strings that are identical across languages by design** (an MCP tool name, an A2A skill id, and a CLI command string are the same token in Python and TS — no `snake_case`↔`camelCase` conversion). So the comparison is a plain set diff with **no name normalization**.

**Out of scope (explicit non-goals):**
- Library API **function/class names** and **signatures** (fuzzier; `snake_case`↔`camelCase`; different type systems). Possibly a later slice, names-only.
- **REST endpoints** (the packages use different frameworks — raw `http.server` vs FastAPI — so extraction is per-package custom).
- **Closing** the gaps (porting missing TS tools). This slice governs and surfaces gaps; closing any specific one is separate work the manifest makes visible.
- Packages other than goldenmatch (follow-up; see §9).

---

## 3. Extraction model — runtime-introspection emitters

Each package emits a **surface descriptor** — a JSON document of the actual, currently-registered names — by importing its own registries (no server boot, no network). One emitter per language, both printing the identical shape:

```json
{ "package": "goldenmatch",
  "mcp_tools":    ["find_duplicates", "match_record", ...],
  "cli_commands": ["dedupe", "match", "autoconfig", "pprl", ...],
  "a2a_skills":   ["analyze_data", "deduplicate", ...] }
```

Descriptors are **generated fresh at gate time**, never committed (they are derived, like the `.wasm` artifacts). Sort each list for stable output.

### 3.1 Python emitter — `scripts/emit_python_surface.py <pkg>`

Imports the package's registries and enumerates names. For goldenmatch the known entry points (verified in code):
- **MCP tools:** `from goldenmatch.mcp.server import TOOLS` → `[t.name for t in TOOLS]` (the single combined `AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + ROUTING_TOOLS + _BASE_TOOLS` list; `mcp/server.py:585`).
- **CLI commands:** introspect the Typer app `goldenmatch.cli.main.app` — `app.registered_commands` (leaf command names) + `app.registered_groups` (sub-app names: `pprl`, `memory`, `identity`, `config`). Read the resolved command/group names, not the source.
- **A2A skills:** `from goldenmatch.a2a.server import _SKILLS` (or the exact list the agent card advertises via `"skills": _SKILLS`, `a2a/server.py:346`) → each skill's `id`.

Box-safe: goldenmatch imports polars, so the emitter runs under `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0`.

The emitter carries a small **per-package registry map** (which module/symbol holds each surface's list), because packages differ (goldenanalysis/infermap have no A2A; some name their Typer app differently). goldenmatch defines the reference entry; the follow-up adds the other five's entries.

### 3.2 TypeScript emitter — `scripts/emit_ts_surface.mjs <pkg>`

A Node ESM script that imports the built (or `tsx`-loaded) TS package registries:
- **MCP tools:** the combined tools array in `src/node/mcp/server.ts` (`MEMORY_TOOLS` + `IDENTITY_TOOLS` + `AGENT_MCP_TOOLS` + base) → each `.name`.
- **CLI commands:** the commander `program` in `src/cli.ts` → `program.commands.map(c => c.name())`.
- **A2A skills:** the skills union in `src/node/a2a/server.ts` (`BASE_SKILLS` + `AGENT_SKILLS` + memory + identity) → each `.id`.

**CI-only:** the box OOM-kills TS builds, so the TS emitter runs only in CI (where the `typescript` lane already builds via turbo). The emitter needs the package's registry lists to be importable **without booting the server** — the one implementation risk this slice de-risks on goldenmatch (§8). Where a combined list isn't already exported, the emitter assembles the same union the server does, or the package exports a small `describeSurface()`.

---

## 4. Manifest — `parity/goldenmatch.yaml`

The reviewed source of truth. One file per package, at a neutral repo-root `parity/` dir (it spans a python + a typescript package). Each surface partitions its names into three sets:

```yaml
package: goldenmatch
mcp_tools:
  shared:      [find_duplicates, match_record, get_stats, ...]   # MUST exist in both
  python_only: [certify_recall, retrieve_similar, ...]           # intentional; comment the reason
  ts_only:     []
cli_commands:
  shared:      [dedupe, match, autoconfig, ...]
  python_only: [sail, snowflake, watch, ...]
  ts_only:     []
a2a_skills:
  shared:      [...]
  python_only: [...]
  ts_only:     []
```

Rules: a name appears in **exactly one** of the three lists per surface; `shared`/`python_only`/`ts_only` are disjoint; lists are sorted. Comments record *why* a gap is intentional. A surface a package doesn't ship at all (e.g. A2A for infermap, later) is omitted entirely, not listed empty.

---

## 5. Gate — `scripts/check_api_parity.py <pkg>`

Reads the manifest + both emitted descriptors and asserts the manifest **exactly partitions the union** of the two languages' real surfaces. For each surface, let `PY` = python descriptor set, `TS` = ts descriptor set:

| Condition | Verdict | Message |
|-----------|---------|---------|
| name ∈ `PY ∩ TS` but ∉ `shared` | FAIL | "`X` exists in both — add to `mcp_tools.shared`" |
| name ∈ `PY − TS`, ∉ `python_only` (and ∉ shared) | FAIL | "`X` is Python-only and undeclared — add to `python_only` or port it to TS" |
| name ∈ `TS − PY`, ∉ `ts_only` | FAIL | "`X` is TS-only and undeclared — add to `ts_only` or add it to Python" |
| name ∈ `shared` but ∉ `PY` or ∉ `TS` | FAIL | "`X` is declared shared but missing from <lang> — stale manifest or a regression" |
| name ∈ `python_only` but ∈ `TS` | FAIL | "`X` is marked python_only but now exists in TS — move it to `shared`" |
| name ∈ `ts_only` but ∈ `PY` | FAIL | "symmetric of above" |
| name ∈ manifest but ∉ `PY ∪ TS` | FAIL | "`X` is in the manifest but no longer exists — remove it" |

A clean run means: every real name is declared, every declared name is real, and each is in the correct partition. Any change to a package's surface (add/remove/rename a tool) forces a deliberate manifest edit a reviewer sees. The gate prints a per-surface diff and exits non-zero on any failure.

**`--init` mode:** `check_api_parity.py <pkg> --init` runs both emitters and writes a bootstrap manifest with `shared = PY ∩ TS`, `python_only = PY − TS`, `ts_only = TS − PY`. The **review of the generated `python_only`/`ts_only` lists is the payoff** — every current drift becomes an explicit "intentional gap or a bug to file?" decision. The committed manifest is the human-reviewed result of that pass, not the raw `--init` output.

---

## 6. CI wiring

A path-filtered `api_parity` job (`dorny/paths-filter` on `packages/python/goldenmatch/**`, `packages/typescript/goldenmatch/**`, and `parity/goldenmatch.yaml`). Steps: set up Python (uv) + Node (pnpm build of goldenmatch-js), run `emit_python_surface.py goldenmatch` and `emit_ts_surface.mjs goldenmatch`, then `check_api_parity.py goldenmatch`. Fails the PR on undeclared drift. Editing the manifest re-runs the job (so the manifest stays under test).

Local: the Python emitter + a manifest self-consistency check (disjointness, sortedness, python_only-not-in-python-descriptor sanity) run on the box; the full cross-language gate is CI (TS side).

---

## 7. Error handling

- The gate never crashes on a missing surface: a package that doesn't ship A2A simply has no `a2a_skills` key and the gate skips it.
- An emitter that fails to import a registry exits non-zero with the import error (a real breakage the gate should surface, not swallow).
- Malformed manifest (a name in two partitions, unsorted, unknown surface key) → the gate fails with a specific structural error before doing any diff.

---

## 8. Testing

- **Gate unit tests** (`scripts/test_api_parity.py` or `tests/`): synthetic manifest + synthetic PY/TS descriptors exercising every row of the §5 table (a clean pass, and one fixture per failure mode) plus the malformed-manifest structural errors. Pure data, no package imports — fast and box-safe.
- **Emitter smoke tests:** `emit_python_surface.py goldenmatch` produces JSON with the three keys, each a non-empty sorted list of strings, and the MCP count matches the known `len(TOOLS)`. (The TS emitter smoke runs in CI.)
- **The proof:** on goldenmatch, `--init` produces a manifest, a human review confirms the `python_only`/`ts_only` lists, and the committed manifest passes the gate green in CI — end-to-end evidence the emitters + gate work on the richest real surface.

---

## 9. Rollout

1. **This slice:** the emitters (with goldenmatch's registry map), the manifest format, the gate + tests, the CI job, and `parity/goldenmatch.yaml` reviewed + green.
2. **Follow-up (mechanical):** add the other five packages' entries to the emitter registry map, `--init` + review each manifest, extend the CI filter/matrix. Each package's `python_only`/`ts_only` review is itself a small audit that may spin off "port this tool" or "delete this stale command" tickets.

---

## 10. Risks

- **Registry importability (the main risk).** The emitters assume each surface's names are reachable by importing a module, without booting a server or causing heavy side effects. Verified true for goldenmatch Python (`TOOLS`, the Typer `app`, `_SKILLS`) and plausible for TS (the imported `*_TOOLS` arrays, commander `program`, `*_SKILLS`). Proving it on goldenmatch is exactly why goldenmatch goes first; if a TS registry only exists inside a server-construction closure, the fix is a tiny `describeSurface()` export in that package.
- **Truthfulness vs the advertised surface.** The emitter must enumerate the *same* set the running server would advertise (MCP `tools/list`, the A2A agent card, `cli --help`). Where a single combined symbol exists it is authoritative; where the server assembles the union at request time, the emitter assembles the identical union. A smoke test pins the count against a known value to catch a divergent assembly.
- **TS is CI-only.** No local full-gate signal; mitigated by the box-safe Python emitter + manifest self-check, with CI as the binding gate (consistent with the rest of the suite's TS posture).

---

## 11. Graduation

- `scripts/emit_python_surface.py`, `scripts/emit_ts_surface.mjs`, `scripts/check_api_parity.py` (+ `--init`) implemented; gate unit tests green on the box.
- `parity/goldenmatch.yaml` bootstrapped, human-reviewed (every `python_only`/`ts_only` entry justified), and passing the gate green in CI.
- `api_parity` CI job wired, path-filtered, and demonstrated to FAIL on an injected undeclared-drift diff and PASS once declared.
- The follow-up (other five packages) is captured as a tracked next step, not attempted here.

Outcome: the cross-language operation surface is governed by a reviewed manifest, and accidental Python↔TS drift becomes a red CI check instead of something a docs audit stumbles on months later.
