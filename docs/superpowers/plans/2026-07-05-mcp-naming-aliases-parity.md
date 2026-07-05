# goldenmatch MCP Naming-Alias Parity — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make goldenmatch's five divergently-named MCP operations answerable by both names on both servers (Python + TypeScript), non-breakingly, and move the nine alias names to `shared` in the parity manifest.

**Architecture:** Aliases are pure name indirection to existing handlers — no operation logic is duplicated. Python funnels aliases through the shared `_BASE_TOOLS` component (both advertise paths sum it) and a shared `_resolve_alias()` called by both dispatchers (`call_tool` + the aggregator's `dispatch`). The suite aggregator filters goldenmatch's aliases out so the suite's `profile` stays goldencheck's. TS adds derived alias tool-defs + fall-through `switch` cases.

**Tech Stack:** Python (mcp SDK `Tool`, pytest), TypeScript (mcp SDK `Tool`, vitest), YAML manifest, `check_api_parity.py` gate.

**Spec:** `docs/superpowers/specs/2026-07-05-mcp-naming-aliases-parity-design.md`

**Environment / SOP:**
- Branch `feat/parity-p0-mcp-aliases` (worktree `D:\show_case\gg-local-llm`), based on `origin/main`.
- Python tests are box-safe: run with `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0` and the main `.venv` (has `goldenmatch[mcp]`). Prefer `.venv/Scripts/python -m pytest` over `uv run`.
- **TS is CI-only — the box OOMs TS builds/vitest.** Write the TS test + code; do NOT run vitest locally. The `api_parity` + TS CI lanes verify it.
- benzsevern gh account (`unset GH_TOKEN; gh auth switch --user benzsevern`). Merge-queue repo → `gh pr merge --auto --squash` WITHOUT `--delete-branch`.
- Verify all symbols against this worktree, NEVER the stale `D:\show_case\goldenmatch`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `packages/python/goldenmatch/goldenmatch/mcp/server.py` | Python MCP server: tool defs, advertise paths, both dispatchers | Add alias map + resolver + `ALIAS_TOOLS` into `_BASE_TOOLS`; normalize in `dispatch` + `call_tool` |
| `packages/python/goldenmatch/tests/test_mcp_aliases.py` | Python alias unit + integration tests | **Create** |
| `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` | Suite aggregator | Filter gm aliases in `_adapt_goldenmatch` |
| `packages/python/goldensuite-mcp/tests/test_aggregator_smoke.py` | Aggregator smoke tests | Add `profile`-owner + alias-absence test |
| `packages/typescript/goldenmatch/src/node/mcp/server.ts` | TS MCP server: tool defs + `handleTool` switch | Add `ALIAS_TOOLS` + fall-through cases |
| `packages/typescript/goldenmatch/tests/unit/mcp-aliases.test.ts` | TS alias tests | **Create** (CI-only) |
| `parity/goldenmatch.yaml` | Parity manifest | Move 9 names to `mcp_tools.shared`; update header |

**Anchor lines (this worktree, verified):** `_BASE_TOOLS` = server.py:94-582; `TOOLS` sum = :585; `dispatch` = :588; inline `list_tools` rebuild = :620; `call_tool` = :862; `_AGENT_TOOL_NAMES` = :54; `AGENT_TOOLS` import = :35. Aggregator `_adapt_goldenmatch` = goldensuite-mcp server.py:47-50. TS `EXISTING_TOOLS` = server.ts:88; `TOOLS` spread = :369-374; `switch (name)` = :509 (`dedupe`:510, `match`:543, `explain_pair`:593, `explain_cluster`:615, `profile`:667).

---

## Task 1: Python — alias map, resolver, and advertised alias tools

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/server.py` (insert after `_BASE_TOOLS` closes at :582, before the `TOOLS =` sum at :585)
- Test: `packages/python/goldenmatch/tests/test_mcp_aliases.py` (Create)

- [ ] **Step 1: Write the failing test** (`tests/test_mcp_aliases.py`)

```python
"""Alias parity for the goldenmatch MCP server. Box-safe: needs goldenmatch[mcp].
Run: POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 python -m pytest tests/test_mcp_aliases.py -v"""
import pytest
from goldenmatch.mcp import server as gm

EXPECTED_ALIASES = {
    "dedupe": "find_duplicates",
    "match": "match_record",
    "explain_pair": "explain_match",
    "profile": "profile_data",
    "explain_cluster": "agent_explain_cluster",
}


def test_alias_map_is_exactly_the_five_pairs():
    assert gm._MCP_TOOL_ALIASES == EXPECTED_ALIASES


def test_resolve_alias_maps_each_alias_to_canonical():
    for alias, canonical in EXPECTED_ALIASES.items():
        assert gm._resolve_alias(alias) == canonical
    # non-alias names pass through untouched
    assert gm._resolve_alias("find_duplicates") == "find_duplicates"
    assert gm._resolve_alias("nonexistent") == "nonexistent"


def test_aliases_are_advertised_in_the_base_component():
    # Load-bearing: aliases MUST live in _BASE_TOOLS so BOTH advertise paths
    # (the TOOLS var at :585 AND the inline list_tools rebuild at :620) see them.
    base_names = {t.name for t in gm._BASE_TOOLS}
    assert set(EXPECTED_ALIASES) <= base_names


def test_aliases_appear_in_TOOLS_union():
    names = {t.name for t in gm.TOOLS}
    assert set(EXPECTED_ALIASES) <= names


def test_alias_schema_matches_canonical():
    by_name = {t.name: t for t in gm.TOOLS}
    for alias, canonical in EXPECTED_ALIASES.items():
        assert by_name[alias].inputSchema == by_name[canonical].inputSchema
        assert canonical in (by_name[alias].description or "")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_mcp_aliases.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute '_MCP_TOOL_ALIASES'`.

- [ ] **Step 3: Implement the alias map, resolver, and alias-tool builder**

Insert immediately after the `_BASE_TOOLS = [ ... ]` literal closes (server.py:582), before the `# TOOLS is the union ...` comment at :584:

```python
# --- Cross-language naming aliases (Python<->TS MCP parity) -----------------
# Each alias forwards to an EXISTING handler; no operation logic is duplicated.
# See docs/superpowers/specs/2026-07-05-mcp-naming-aliases-parity-design.md.
_MCP_TOOL_ALIASES = {
    "dedupe": "find_duplicates",
    "match": "match_record",
    "explain_pair": "explain_match",
    "profile": "profile_data",
    "explain_cluster": "agent_explain_cluster",
}


def _resolve_alias(name: str) -> str:
    """Map an alias tool name to its canonical name (identity for non-aliases)."""
    return _MCP_TOOL_ALIASES.get(name, name)


def _build_alias_tools() -> list[Tool]:
    """Derive alias Tool objects from their canonical tools so schemas can't drift.
    Canonicals live in AGENT_TOOLS (agent_explain_cluster) + _BASE_TOOLS (the rest)."""
    canon = {t.name: t for t in AGENT_TOOLS + _BASE_TOOLS}
    tools = []
    for alias, target in _MCP_TOOL_ALIASES.items():
        c = canon[target]
        tools.append(Tool(
            name=alias,
            description=f"Alias for `{target}`. {c.description}",
            inputSchema=c.inputSchema,
        ))
    return tools


# Append aliases to the shared _BASE_TOOLS component so BOTH advertise paths
# (the TOOLS var below AND the inline list_tools rebuild) pick them up.
_BASE_TOOLS += _build_alias_tools()
```

(The existing `TOOLS = AGENT_TOOLS + ... + _BASE_TOOLS` at :585 now includes the aliases automatically.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_mcp_aliases.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_mcp_aliases.py
git commit -m "feat(mcp): advertise goldenmatch Python MCP naming aliases"
```

---

## Task 2: Python — normalize aliases in both dispatchers

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/server.py` (`dispatch` head :589; `call_tool` head :862)
- Test: `packages/python/goldenmatch/tests/test_mcp_aliases.py` (extend)

- [ ] **Step 1: Write the failing test** (append to `test_mcp_aliases.py`)

```python
import csv
from pathlib import Path


def _tiny_csv(tmp_path: Path) -> str:
    p = tmp_path / "people.csv"
    with p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "name", "email"])
        w.writerow(["1", "Alice Smith", "alice@example.com"])
        w.writerow(["2", "Alice Smith", "alice@example.com"])
        w.writerow(["3", "Bob Jones", "bob@example.com"])
    return str(p)


def test_dispatch_routes_alias_to_canonical_handler(tmp_path, monkeypatch):
    # The aggregator entrypoint (module-level dispatch) must resolve aliases —
    # this is the path goldensuite-mcp uses. profile/profile_data is the cheapest pair.
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    path = _tiny_csv(tmp_path)
    from goldenmatch.mcp import server as gm
    via_alias = gm.dispatch("profile", {"path": path})
    via_canonical = gm.dispatch("profile_data", {"path": path})
    assert via_alias == via_canonical


def test_dispatch_explain_cluster_resolves_into_agent_path():
    # explain_cluster -> agent_explain_cluster must resolve BEFORE the
    # _AGENT_TOOL_NAMES check so it reaches the agent handler.
    from goldenmatch.mcp import server as gm
    assert gm._resolve_alias("explain_cluster") == "agent_explain_cluster"
    assert "agent_explain_cluster" in gm._AGENT_TOOL_NAMES
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_mcp_aliases.py::test_dispatch_routes_alias_to_canonical_handler -v`
Expected: FAIL — `dispatch("profile", ...)` falls through to `_handle_tool("profile", ...)` with no matching branch (KeyError / unknown-tool error), so it won't equal the `profile_data` result.

- [ ] **Step 3: Implement — add `_resolve_alias` at the top of both dispatchers**

In `dispatch` (server.py:588), make the first line of the body:

```python
def dispatch(name: str, args: dict) -> dict:
    """Unified dispatcher used by goldensuite-mcp aggregator. ..."""
    name = _resolve_alias(name)   # <-- add as first statement
    if name in _AGENT_TOOL_NAMES:
        ...
```

In the `call_tool` handler (server.py:862), add resolution as the first statement of the body (before the analytics `capture`, so analytics record the canonical name):

```python
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        name = _resolve_alias(name)   # <-- add first
        # Anonymous, opt-in usage event ...
        try:
            from goldenmatch.core.analytics import capture
            ...
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_mcp_aliases.py -v`
Expected: PASS (all tests).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/mcp/server.py packages/python/goldenmatch/tests/test_mcp_aliases.py
git commit -m "feat(mcp): route goldenmatch MCP aliases through both dispatchers"
```

---

## Task 3: Suite aggregator — exclude goldenmatch aliases

**Files:**
- Modify: `packages/python/goldensuite-mcp/goldensuite_mcp/server.py` (`_adapt_goldenmatch` :47-50)
- Test: `packages/python/goldensuite-mcp/tests/test_aggregator_smoke.py` (extend)

- [ ] **Step 1: Write the failing test** (append to `test_aggregator_smoke.py`)

```python
def test_suite_profile_stays_goldencheck_not_goldenmatch_alias():
    """goldenmatch's new `profile` alias must NOT shadow goldencheck's `profile`
    file-profiler in the aggregated surface."""
    from goldensuite_mcp.server import _aggregate
    tools, name_to_dispatch = _aggregate()
    names = {t.name for t in tools}
    assert "profile" in names
    # profile must dispatch to goldencheck, not goldenmatch
    from goldencheck.mcp import server as gc
    assert name_to_dispatch["profile"] is gc.dispatch


def test_goldenmatch_aliases_absent_from_aggregated_surface():
    from goldensuite_mcp.server import _aggregate
    from goldenmatch.mcp import server as gm
    tools, _ = _aggregate()
    names = {t.name for t in tools}
    assert not (set(gm._MCP_TOOL_ALIASES) & names), \
        "goldenmatch aliases must be filtered from the suite surface"
```

(If goldencheck's dispatcher isn't `gc.dispatch`, adjust the identity check to the callable `_adapt_goldencheck()` returns — verify against `goldensuite_mcp/server.py`'s goldencheck adapter.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd packages/python/goldensuite-mcp && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_aggregator_smoke.py -k "profile or aliases" -v`
Expected: FAIL — goldenmatch is first-wins, so `profile` maps to `gm.dispatch` and the alias names appear in the surface.

- [ ] **Step 3: Implement — filter aliases in `_adapt_goldenmatch`**

Replace `_adapt_goldenmatch` (server.py:47-50):

```python
def _adapt_goldenmatch() -> tuple[list[Tool], Callable[[str, dict], dict]]:
    from goldenmatch.mcp import server as gm

    # Exclude goldenmatch's internal Python<->TS naming aliases from the
    # aggregated surface: the suite has one surface per operation, and the
    # `profile` alias would otherwise shadow goldencheck's `profile` tool.
    # gm.dispatch still resolves aliases (harmless — they're just never listed here).
    aliases = set(gm._MCP_TOOL_ALIASES)
    tools = [t for t in gm.TOOLS if t.name not in aliases]
    return _normalize_tools(tools), gm.dispatch
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd packages/python/goldensuite-mcp && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_aggregator_smoke.py -v`
Expected: PASS (existing smoke tests + the 2 new ones).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldensuite-mcp/goldensuite_mcp/server.py packages/python/goldensuite-mcp/tests/test_aggregator_smoke.py
git commit -m "fix(suite-mcp): exclude goldenmatch aliases so suite profile stays goldencheck"
```

---

## Task 4: TypeScript — alias tool-defs + fall-through dispatch (CI-only)

**Files:**
- Modify: `packages/typescript/goldenmatch/src/node/mcp/server.ts` (add `ALIAS_TOOLS` after `EXISTING_TOOLS` :356-ish; add to `TOOLS` spread :369-374; add fall-through cases in `switch` :509)
- Test: `packages/typescript/goldenmatch/tests/unit/mcp-aliases.test.ts` (Create)

> **Do NOT run vitest locally — the box OOMs.** Write test + code; CI verifies. The four TS aliases map: `find_duplicates`→`dedupe`, `match_record`→`match`, `explain_match`→`explain_pair`, `profile_data`→`profile`. (No `agent_explain_cluster` alias — already shared.)

- [ ] **Step 1: Write the test** (`tests/unit/mcp-aliases.test.ts`)

```ts
import { describe, it, expect } from "vitest";
import { TOOLS, handleTool } from "../../src/node/mcp/server.js";

const ALIAS_TO_CANONICAL: Record<string, string> = {
  find_duplicates: "dedupe",
  match_record: "match",
  explain_match: "explain_pair",
  profile_data: "profile",
};

describe("MCP naming aliases", () => {
  it("advertises all four alias names", () => {
    const names = new Set(TOOLS.map((t) => t.name));
    for (const alias of Object.keys(ALIAS_TO_CANONICAL)) {
      expect(names.has(alias)).toBe(true);
    }
  });

  it("each alias schema equals its canonical schema", () => {
    const byName = new Map(TOOLS.map((t) => [t.name, t]));
    for (const [alias, canonical] of Object.entries(ALIAS_TO_CANONICAL)) {
      expect(byName.get(alias)!.inputSchema).toEqual(byName.get(canonical)!.inputSchema);
      expect(byName.get(alias)!.description).toContain(canonical);
    }
  });

  it("profile_data dispatches identically to profile", async () => {
    const viaAlias = await handleTool("profile_data", { path: "nonexistent_xyz.csv" });
    const viaCanonical = await handleTool("profile", { path: "nonexistent_xyz.csv" });
    expect(viaAlias).toEqual(viaCanonical);
  });
});
```

- [ ] **Step 2: Implement — derive `ALIAS_TOOLS` and add to `TOOLS`**

After the `EXISTING_TOOLS` array closes (server.ts ~:356), add:

```ts
// Cross-language naming aliases (Python<->TS MCP parity). Each forwards to an
// existing handler via a fall-through switch case below; schemas are derived
// from the canonical tool so they can't drift.
const _TS_TOOL_ALIASES: Record<string, string> = {
  find_duplicates: "dedupe",
  match_record: "match",
  explain_match: "explain_pair",
  profile_data: "profile",
};

const ALIAS_TOOLS: Tool[] = Object.entries(_TS_TOOL_ALIASES).map(([alias, canonical]) => {
  const c = EXISTING_TOOLS.find((t) => t.name === canonical);
  if (!c) throw new Error(`alias canonical not found: ${canonical}`);
  return { ...c, name: alias, description: `Alias for \`${canonical}\`. ${c.description}` };
});
```

Add `...ALIAS_TOOLS` to the `TOOLS` spread (server.ts:369-374):

```ts
export const TOOLS: readonly Tool[] = [
  ...EXISTING_TOOLS,
  ...ALIAS_TOOLS,
  ...MEMORY_TOOLS,
  ...IDENTITY_TOOLS,
  ...AGENT_MCP_TOOLS,
];
```

- [ ] **Step 3: Implement — fall-through switch cases**

In `handleTool`'s `switch (name)` (server.ts:509), stack each alias label directly above its canonical `case` so it falls through (no body duplicated):

```ts
      case "find_duplicates":   // alias
      case "dedupe": {
        ...existing dedupe body...
      }
```

Do the same for: `case "match_record":` above `case "match":` (:543); `case "explain_match":` above `case "explain_pair":` (:593); `case "profile_data":` above `case "profile":` (:667).

- [ ] **Step 4: Update the manifest expectation in the existing TS test if needed**

Check `tests/unit/mcp-server.test.ts:21` ("every tool name is unique") — aliases have distinct names, so it still passes. No change expected; confirm by reading, don't run.

- [ ] **Step 5: Commit**

```bash
git add packages/typescript/goldenmatch/src/node/mcp/server.ts packages/typescript/goldenmatch/tests/unit/mcp-aliases.test.ts
git commit -m "feat(mcp-ts): add goldenmatch TS MCP naming aliases (find_duplicates/match_record/explain_match/profile_data)"
```

---

## Task 5: Manifest — move nine names to `shared`

**Files:**
- Modify: `parity/goldenmatch.yaml`

- [ ] **Step 1: Edit the manifest**

In `mcp_tools`:
- **Add to `shared`** (keep alphabetical): `dedupe`, `explain_cluster`, `explain_match`, `explain_pair`, `find_duplicates`, `match`, `match_record`, `profile`, `profile_data`.
- **Remove from `python_only`**: `explain_match`, `find_duplicates`, `match_record`, `profile_data`.
- **Remove from `ts_only`**: `dedupe`, `explain_cluster`, `explain_pair`, `match`, `profile`.

Every partition must stay **sorted** and **disjoint** (the gate's `check_structure` enforces both).

- [ ] **Step 2: Update the header** (lines 8-13)

Replace category-1 text so it reads as resolved:

```yaml
#   1. NAMING ALIASES (resolved 2026-07-05 via non-breaking aliases — both
#      servers now answer to both names, so these are shared: dedupe/find_duplicates,
#      match/match_record, explain_pair/explain_match, profile/profile_data, and
#      explain_cluster (alias of the shared agent_explain_cluster)). CLI info/score/
#      tui(TS) vs interactive(PY) are NOT aliases (distinct ops) — left as coverage gaps.
```

- [ ] **Step 3: Verify structure locally with the gate's structural check**

Run: `POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 .venv/Scripts/python -c "import yaml,pathlib; from importlib.util import spec_from_file_location,module_from_spec; s=spec_from_file_location('g','scripts/check_api_parity.py'); m=module_from_spec(s); s.loader.exec_module(m); man=yaml.safe_load(open('parity/goldenmatch.yaml')); fails=m.check_structure(man); print('STRUCTURE FAILS:', [f.kind for f in fails])"`
Expected: `STRUCTURE FAILS: []` (sorted + disjoint + known surfaces).

> The full partition check needs the TS descriptor (CI-only). CI's `api_parity` goldenmatch shard is the authoritative green/red. Structure check above is the box-safe pre-flight.

- [ ] **Step 4: Commit**

```bash
git add parity/goldenmatch.yaml
git commit -m "chore(parity): move 9 goldenmatch MCP aliases to shared"
```

---

## Task 6: P1 docs — annotate the remaining gaps as intentional

**Files:**
- Modify: `parity/goldenmatch.yaml` (header, category 2)

- [ ] **Step 1: Extend the category-2 header note**

Add explicit intent for the stateful-query tools and the Python-only subsystems:

```yaml
#   2. PYTHON-RICHER COVERAGE (intentional — Python is the fuller, STATEFUL server):
#      get_stats / get_cluster / list_clusters are Python-only BECAUSE the TS MCP is
#      stateless — its dedupe() returns stats + clusters inline (DedupeResult), so there
#      is no server-held dataset to query. Domains (create_domain/list_domains/test_domain)
#      and PPRL (pprl_link/pprl_auto_config) are Python-only subsystems. Plus the
#      Python-only CLI commands (autoconfig, anomalies, lineage, sensitivity, serve-ui, ...).
```

- [ ] **Step 2: Note the goldencheck deferral** (comment near the header end)

```yaml
# NOTE: goldencheck's sole py-only MCP tool `install_domain` is likewise intentional
# (TS core exposes a read-only domain registry, no install path). That annotation
# lands in parity/goldencheck.yaml once PR #1449 (the 6-package rollout) merges.
```

- [ ] **Step 3: Commit**

```bash
git add parity/goldenmatch.yaml
git commit -m "docs(parity): document intentional Python-only MCP gaps (stateful query, domains, pprl)"
```

---

## Task 7: Docs sweep + PR

**Files:** doc surfaces listing MCP tool names (per `.claude/doc-surfaces.md` if present).

- [ ] **Step 1: Grep doc surfaces for the canonical tool names**

Run: `grep -rniE "find_duplicates|match_record|explain_match|profile_data|agent_explain_cluster" docs-site docs README*.md 2>/dev/null | grep -viE "spec|plan|parity" | head`
For each doc that lists the canonical tool, add a one-line note that the alias name also works (e.g. "`find_duplicates` (alias: `dedupe`)"). Keep tool-count claims honest: Python MCP +5, TS MCP +4. Use the rollout-docs-sweep skill's inventory if `.claude/doc-surfaces.md` exists.

- [ ] **Step 2: Run the box-safe Python suite one more time**

Run: `cd packages/python/goldenmatch && POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 ../../../.venv/Scripts/python -m pytest tests/test_mcp_aliases.py -v` and the aggregator test.
Expected: all PASS.

- [ ] **Step 3: Push + open PR + arm auto-merge (then STOP)**

```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/parity-p0-mcp-aliases
gh pr create --repo benseverndev-oss/goldenmatch --title "goldenmatch MCP naming-alias parity (P0 + P1 docs)" --body "..."
gh pr merge --auto --squash    # NO --delete-branch (merge-queue repo)
```

PR body: summarize P0 (Python 5 + TS 4 aliases, 9 names → shared), the aggregator `profile` fix, P1 header docs, and that goldencheck's note follows #1449. Do NOT poll CI — arm auto-merge and stop.

---

## Notes for the implementer

- **DRY:** alias schemas are *derived* from canonicals on both sides — never hand-copy a schema.
- **The two-path trap (Python):** aliases go in `_BASE_TOOLS` (a component both advertise paths sum), and `_resolve_alias` is called in BOTH `dispatch` and `call_tool`. Touching only one path advertises-but-can't-serve or serves-but-isn't-listed.
- **`explain_cluster`** must resolve before the `_AGENT_TOOL_NAMES` check (it's an agent tool); Task 2's test guards this.
- **TS is CI-only.** Write it, commit it, let CI verify. Do not run vitest/build on the box.
- **Manifest + code in the same PR** — the `api_parity` gate fails otherwise (that coupling is the point).
