# Cross-language API parity: manifest + drift gate

**Date:** 2026-07-04
**Status:** Design (approved for spec review)
**Scope of this slice:** the mechanism + goldenmatch as the reference package, over **MCP tools + CLI commands**. A2A skills and the other five packages are explicit follow-ups (§9).

> **Code verification note:** all code claims below are verified against the `origin/main` checkout at `D:\show_case\gg-local-llm`. Do **not** verify against `D:\show_case\goldenmatch` — that is a separate worktree pinned to an older feature branch and reads stale code (e.g. a different `TOOLS` definition, an older version string).

---

## 1. Goal

The Golden Suite ships every package in Python **and** TypeScript, but the two runtimes have drifted: goldenmatch exposes ~69 MCP tools in Python and ~45 in TS; CLI command sets differ. Some gaps are intentional (edge-safe scoping); others are accidental and nobody notices until a docs audit finds them.

This slice makes the cross-language **operation surface** a governed, single-source-of-truth artifact: a declarative **parity manifest** per package that partitions each surface into `shared` / `python_only` / `ts_only`, plus a CI **drift gate** that fails when the real code diverges from the manifest. The gate does not force TS to implement everything Python does — it makes every gap a *deliberate, reviewed* decision and catches *undeclared* drift the moment it lands.

This extends the suite's existing byte-parity-gate discipline (goldenpipe-core, goldenflow-core replay golden vectors across surfaces) from the compute kernels up to the public operation surface.

---

## 2. Scope

**In scope — two "operation" surfaces**, which are discretely enumerable *string sets*:
- **MCP tools** — the tool names each package's MCP server advertises.
- **CLI commands** — the top-level command names, plus sub-app *group* names, each package's CLI exposes.

A load-bearing property, **verified true for these two surfaces**: MCP tool names and CLI command strings are the same token in Python and TS — no `snake_case`↔`camelCase` conversion (e.g. MCP `find_duplicates`, `suggest_config`; CLI `dedupe`, `match`, `mcp-serve` are spelled identically on both sides). So the comparison is a plain set diff with **no name normalization**.

**A2A skills are deferred (see §9), not in this slice.** The no-normalization property does *not* hold for A2A: Python A2A skills carry an `id` (`analyze_data`, `deduplicate`, `configure`, …; `a2a/server.py:18`), while the TS agent card's skills carry only a `name` with a *divergent token set* and **no `id`** (`dedupe`, `match`, `score`, `explain_pair`, …; `node/a2a/server.ts:85`). The same operation is `deduplicate` (PY) vs `dedupe` (TS). A plain set diff would manufacture phantom drift and hide the real correspondence. Gating A2A first requires reconciling the TS agent card to carry A2A-conformant `id`s matching Python — itself a real parity fix this effort surfaces (§9).

**Out of scope (explicit non-goals):**
- **A2A skills** (deferred, §9).
- Library API **function/class names** and **signatures** (fuzzier; `snake_case`↔`camelCase`; different type systems). Possibly a later slice, names-only.
- **REST endpoints** (different frameworks — raw `http.server` vs FastAPI — so extraction is per-package custom).
- **CLI subcommands within sub-app groups** (e.g. `identity split`, `pprl link`). The gate compares the group token (`identity`) as a single name; drift *inside* a group is not caught in this slice. Composite `group.subcommand` tokens are a documented extension (§9). This is called out because "`identity` is shared" reads stronger than it is.
- **Closing** the gaps (porting missing TS tools). This slice governs and surfaces gaps; closing any specific one is separate work the manifest makes visible.
- Packages other than goldenmatch (§9).

---

## 3. Extraction model — runtime-introspection emitters

Each package emits a **surface descriptor** — a JSON document of the actual, currently-registered names — by importing its own registries (no server boot, no network). One emitter per language, both printing the identical shape:

```json
{ "package": "goldenmatch",
  "mcp_tools":    ["find_duplicates", "match_record", ...],
  "cli_commands": ["dedupe", "match", "autoconfig", "identity", ...] }
```

Descriptors are **generated fresh at gate time**, never committed (they are derived, like the `.wasm` artifacts). Each list is sorted for stable output.

Both languages already expose a **single combined tools list**, so no assembly or new `describeSurface()` export is needed:

### 3.1 Python emitter — `scripts/emit_python_surface.py <pkg>`

For goldenmatch (verified symbols):
- **MCP tools:** `from goldenmatch.mcp.server import TOOLS` → `[t.name for t in TOOLS]`. `TOOLS` is the module-level combined list `AGENT_TOOLS + MEMORY_TOOLS + IDENTITY_TOOLS + ROUTING_TOOLS + _BASE_TOOLS` (`mcp/server.py:585`); each element is an mcp-SDK `Tool` with a `.name` attribute.
- **CLI commands:** import the Typer app `goldenmatch.cli.main.app` and read `app.registered_commands` (leaf `TyperCommand`s — take each command's resolved name) and `app.registered_groups` (`TyperGroup`s from `add_typer(...)` — the sub-app names `pprl`, `memory`, `identity`, `config`; `cli/main.py:103,125-127,216`). The union of both name sets is the CLI surface. (These are the real Typer introspection attributes.)

**Import dependency:** importing `goldenmatch.mcp.server` pulls the mcp SDK (`from mcp.server import Server`, `mcp/server.py:23`), which ships only with the `[mcp]` extra; the `Tool` type the `*_TOOLS` lists are built from comes from the same SDK. So the Python emitter runs in an env with **`goldenmatch[mcp]`** installed. goldenmatch also imports polars, so run under `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0`. (The Typer `app` import alone is dependency-clean; only the MCP surface needs the extra.)

The emitter carries a small **per-package registry map** (which module/symbol holds each surface's list, and which extras its import needs), because packages differ. goldenmatch defines the reference entry; the §9 follow-up adds the other five.

### 3.2 TypeScript emitter — `scripts/emit_ts_surface.mjs <pkg>`

For goldenmatch (verified symbols):
- **MCP tools:** `import { TOOLS } from ".../node/mcp/server.js"` → `TOOLS.map(t => t.name)`. `TOOLS` is a module-level `export const TOOLS: readonly Tool[] = [...EXISTING_TOOLS, ...MEMORY_TOOLS, ...IDENTITY_TOOLS, ...AGENT_MCP_TOOLS]` (`node/mcp/server.ts:369`) — a clean combined export.
- **CLI commands:** import the commander `program` from `src/cli.ts` → `program.commands.map(c => c.name())` (top-level commands + sub-command groups like `memory`, `identity`; `src/cli.ts:122`).

**Import-without-boot is confirmed for goldenmatch (the design's headline risk):** `TOOLS` is a module-level export and the MCP `server.connect(...)` / commander `program.parse(...)` calls live inside functions, so importing the module has no server-boot side effect. Where a future package traps its list inside a construction closure, the fix is a tiny module-level `export const TOOLS`/`describeSurface()` in that package.

**CI-only:** the box OOM-kills TS builds, so the TS emitter runs only in CI (where the `typescript` lane already builds goldenmatch-js via turbo; the emitter imports the built `dist`, or loads `src` via `tsx`).

---

## 4. Manifest — `parity/goldenmatch.yaml`

The reviewed source of truth. One file per package, at a neutral repo-root `parity/` dir (it spans a python + a typescript package). Each surface partitions its names into three sets (**illustrative shape — the real lists are produced by `--init` in §5, not hand-authored**):

```yaml
package: goldenmatch
mcp_tools:
  shared:      [find_duplicates, match_record, get_stats]   # MUST exist in both
  python_only: [certify_recall]                             # intentional; comment the reason
  ts_only:     []
cli_commands:
  shared:      [dedupe, match, autoconfig, identity]
  python_only: [watch]
  ts_only:     []
```

Rules: a name appears in **exactly one** of the three lists per surface; `shared`/`python_only`/`ts_only` are disjoint; lists are sorted. Comments record *why* a gap is intentional. A surface a package doesn't ship at all is omitted entirely, not listed empty.

---

## 5. Gate — `scripts/check_api_parity.py <pkg>`

Reads the manifest + both emitted descriptors and asserts the manifest **exactly partitions the union** of the two languages' real surfaces. For each surface, let `PY` = python descriptor set, `TS` = ts descriptor set:

| Condition | Verdict | Message |
|-----------|---------|---------|
| name ∈ `PY ∩ TS` but ∉ `shared` | FAIL | "`X` exists in both — add to `shared`" |
| name ∈ `PY − TS`, ∉ `python_only`, ∉ `shared` | FAIL | "`X` is Python-only and undeclared — add to `python_only` or port it to TS" |
| name ∈ `TS − PY`, ∉ `ts_only`, ∉ `shared` | FAIL | "`X` is TS-only and undeclared — add to `ts_only` or add it to Python" |
| name ∈ `shared` but ∉ `PY` or ∉ `TS` | FAIL | "`X` is declared shared but missing from <lang> — stale manifest or a regression" |
| name ∈ `python_only` but ∈ `TS` | FAIL | "`X` is marked python_only but now exists in TS — move it to `shared`" |
| name ∈ `ts_only` but ∈ `PY` | FAIL | "symmetric of above" |
| name ∈ manifest but ∉ `PY ∪ TS` | FAIL | "`X` is in the manifest but no longer exists — remove it" |

A clean run means: every real name is declared, every declared name is real, and each is in the correct partition. Any change to a package's surface (add/remove/rename a tool) forces a deliberate manifest edit a reviewer sees. The gate prints a per-surface diff and exits non-zero on any failure. (The two `PY−TS`/`TS−PY` rows carry the "∉ `shared`" guard symmetrically, so a name wrongly placed in `shared` is reported once by row 4, not double-counted.)

**`--init` mode:** `check_api_parity.py <pkg> --init` runs both emitters and writes a bootstrap manifest with `shared = PY ∩ TS`, `python_only = PY − TS`, `ts_only = TS − PY`. The **review of the generated `python_only`/`ts_only` lists is the payoff** — every current drift becomes an explicit "intentional gap or a bug to file?" decision. The committed manifest is the human-reviewed result of that pass, not the raw `--init` output.

---

## 6. CI wiring

A path-filtered `api_parity` job (`dorny/paths-filter` on `packages/python/goldenmatch/**`, `packages/typescript/goldenmatch/**`, and `parity/goldenmatch.yaml`). Steps: set up Python (uv), **install the surface-bearing extra** (`goldenmatch[mcp]`) + Node (pnpm build of goldenmatch-js), run `emit_python_surface.py goldenmatch` and `emit_ts_surface.mjs goldenmatch`, then `check_api_parity.py goldenmatch`. Fails the PR on undeclared drift. Editing the manifest re-runs the job (so the manifest stays under test).

Local: the Python emitter (in the `[mcp]` venv) + a manifest self-consistency check (disjointness, sortedness, every `python_only` name absent from the ts partition, etc.) run on the box; the full cross-language gate is CI (TS side).

---

## 7. Error handling

- The gate never crashes on a missing surface: a package that omits a surface key simply skips it in the diff.
- **Environment vs surface failures are distinguished.** An emitter that cannot import a *surface-bearing extra* (e.g. the `mcp` SDK is absent) exits with a distinct "environment not provisioned — install `<pkg>[mcp]`" error, not a surface-regression failure, so a missing dep can't be mistaken for real drift. An import error *within* the package's own code (a real breakage) surfaces as non-zero with the traceback.
- Malformed manifest (a name in two partitions, unsorted, unknown surface key) → the gate fails with a specific structural error before doing any diff.

---

## 8. Testing

- **Gate unit tests** (`tests/` or `scripts/test_api_parity.py`): synthetic manifest + synthetic PY/TS descriptors exercising every row of the §5 table (a clean pass, and one fixture per failure mode) plus the malformed-manifest structural errors and the environment-vs-surface distinction. Pure data, no package imports — fast and box-safe.
- **Emitter smoke test:** `emit_python_surface.py goldenmatch` produces JSON with both keys, each a non-empty sorted list of strings, and the MCP count equals the **measured** `len(goldenmatch.mcp.server.TOOLS)` (assert against the value read from code at test time, never a hardcoded number). The TS emitter smoke runs in CI.
- **The proof:** on goldenmatch, `--init` produces a manifest, a human review confirms the `python_only`/`ts_only` lists, and the committed manifest passes the gate green in CI — end-to-end evidence the emitters + gate work on the richest real surface. A deliberately-injected undeclared-drift diff must turn the gate red.

---

## 9. Rollout & follow-ups

1. **This slice:** the emitters (with goldenmatch's registry map), the manifest format, the gate + tests, the CI job, and `parity/goldenmatch.yaml` reviewed + green — over **MCP tools + CLI commands**.
2. **A2A skills (follow-up, has a prerequisite):** the TS agent card must first be reconciled to carry A2A-conformant `id`s matching Python's skill ids (today it uses `name` with divergent tokens — a real parity bug this effort surfaced). Once reconciled, add an `a2a_skills` surface to the emitters + manifest.
3. **CLI subcommands (optional extension):** emit composite `group.subcommand` tokens so drift *inside* sub-apps (`identity split`, `pprl link`) is caught, not just the group name.
4. **Other five packages (mechanical):** add each package's entries to the emitter registry map, `--init` + review each manifest, extend the CI filter/matrix. Each package's review is itself a small audit that may spin off "port this tool" / "delete this stale command" tickets.

---

## 10. Risks

- **Registry importability (the main risk) — retired for goldenmatch.** Confirmed: Python `mcp.server.TOOLS` and the Typer `app`, and TS `node/mcp/server.ts`'s `export const TOOLS` and the commander `program`, are all reachable by import with no server-boot side effect. Going goldenmatch-first proves the pattern; a future package that traps its list in a construction closure gets a small module-level export.
- **Optional-extra imports.** The MCP surface can only be read in an env with `goldenmatch[mcp]` installed (§3.1, §6, §7). Not a design flaw, but the CI job and the local recipe must provision it, and the gate must not confuse a missing extra with real drift.
- **Truthfulness vs the advertised surface.** The emitter must enumerate the *same* set the running server advertises (MCP `tools/list` = the `TOOLS` list). The combined `TOOLS` export on both sides is exactly what each server serves, so this holds; the smoke test pins the count against the measured value to catch a divergent assembly.
- **Subcommand blindness (§2 non-goal).** Declaring a sub-app group `shared` does not verify its subcommands match. Documented, with the composite-token extension as the fix (§9).
- **TS is CI-only.** No local full-gate signal; mitigated by the box-safe Python emitter + manifest self-check, with CI as the binding gate (consistent with the rest of the suite's TS posture).

---

## 11. Graduation

- `scripts/emit_python_surface.py`, `scripts/emit_ts_surface.mjs`, `scripts/check_api_parity.py` (+ `--init`) implemented; gate unit tests green on the box.
- `parity/goldenmatch.yaml` bootstrapped over MCP tools + CLI commands, human-reviewed (every `python_only`/`ts_only` entry justified), and passing the gate green in CI.
- `api_parity` CI job wired, path-filtered, provisioning `goldenmatch[mcp]`, and demonstrated to FAIL on an injected undeclared-drift diff and PASS once declared.
- A2A skills, CLI subcommands, and the other five packages are captured as tracked follow-ups (§9), not attempted here.

Outcome: the cross-language MCP + CLI surface is governed by a reviewed manifest, and accidental Python↔TS drift becomes a red CI check instead of something a docs audit stumbles on months later — with A2A's real naming divergence surfaced as the next thing to fix.
