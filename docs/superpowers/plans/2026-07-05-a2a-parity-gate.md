# A2A Skills in the API-Parity Gate — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `a2a_skills` as a third gated surface in the API-parity gate for the 4 A2A packages (goldenmatch/goldencheck/goldenflow/goldenpipe).

**Architecture:** One-line `SURFACES` change (the gate already skips per-package-absent surfaces) + an `a2a_skills` producer in each emitter handling the per-package accessor variance + bootstrapped `a2a_skills` partitions in the 4 manifests. Python side box-safe; TS side + full partition verified in CI.

**Tech Stack:** Python (gate + Python emitter, pytest), Node ESM (TS emitter), YAML manifests, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-05-a2a-parity-gate-design.md`

**Computed Python skill sets (spec review, box-verified):** goldenmatch 38 (`_SKILLS`), goldencheck 9, goldenflow 6, goldenpipe 4 (`AGENT_CARD["skills"]`). goldenflow/goldenpipe use hyphenated ids (`run-pipeline`, `transform-data`, `map-schemas`, `diff-results`) — likely bootstrap divergences to triage.

**Environment / SOP:**
- Branch `feat/a2a-parity-gate` (worktree `D:\show_case\gg-local-llm`), off `origin/main` (has #1457).
- Python emitter + gate tests box-safe: `PYTHONPATH=<pkg-root> POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe ...` (borrow sibling interpreter; verify `__file__` resolves to gg-local-llm).
- **TS emitter is CI-only** (box OOMs). Write + read-verify.
- benzsevern gh; merge-queue → `gh pr merge --auto --squash` (no `--delete-branch`); arm auto-merge + STOP.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `scripts/check_api_parity.py` | The gate | Add `"a2a_skills"` to `SURFACES` (line 10) |
| `scripts/test_api_parity.py` | Gate unit tests | Repoint unknown-surface fixture; add a2a_skills smoke |
| `scripts/emit_python_surface.py` | Python surface emitter | Add `a2a_skills` producer for the 4 A2A packages |
| `scripts/emit_ts_surface.mjs` | TS surface emitter (CI-only) | Add `a2a` producer for the 4 A2A packages |
| `packages/typescript/goldencheck/src/node/a2a/server.ts` | goldencheck TS A2A | `export` the `const AGENT_CARD` (:30) |
| `parity/{goldenmatch,goldencheck,goldenflow,goldenpipe}.yaml` | Manifests | Bootstrap `a2a_skills` partition |
| `.github/workflows/ci.yml` | CI | Add a2a paths to the `api_parity` filter |

**Anchors:** `SURFACES` check_api_parity.py:10; unknown-surface fixture test_api_parity.py:73-76; Python emitter `REGISTRY` (per-package `{surface: (fn, extra)}`); TS emitter `REGISTRY` (per-package literal, `mcp:null` pattern for absent surfaces); goldencheck TS `const AGENT_CARD` server.ts:30; api_parity CI filter ci.yml:415-424.

---

## Task 1: Gate surface + Python emitter + gate tests (box-safe, TDD)

**Files:** `scripts/check_api_parity.py`, `scripts/test_api_parity.py`, `scripts/emit_python_surface.py`

- [ ] **Step 1: Repoint the unknown-surface fixture + add a2a_skills tests** (`test_api_parity.py`)

Change `test_structure_flags_unknown_surface` (:73-76) to use a name that stays unregistered:
```python
def test_structure_flags_unknown_surface():
    m = {"grpc_methods": {"shared": [], "python_only": [], "ts_only": []}}
    fails = gate.check_structure(m)
    assert any(f.kind == "unknown_surface" for f in fails)
```
Add an a2a_skills smoke:
```python
def test_a2a_skills_surface_partitions_and_absent_is_skipped():
    py = {"package": "gm", "mcp_tools": ["a"], "cli_commands": ["c"], "a2a_skills": ["s1", "s2"]}
    ts = {"package": "gm", "mcp_tools": ["a"], "cli_commands": ["c"], "a2a_skills": ["s1"]}
    m = gate.init_manifest(py, ts)
    assert m["a2a_skills"] == {"shared": ["s1"], "python_only": ["s2"], "ts_only": []}
    assert gate.run_checks(m, py, ts) == []
    # a package with no a2a_skills key is skipped, not failed
    py2 = {"package": "im", "mcp_tools": ["a"], "cli_commands": ["c"]}
    ts2 = {"package": "im", "mcp_tools": ["a"], "cli_commands": ["c"]}
    m2 = gate.init_manifest(py2, ts2)
    assert "a2a_skills" not in m2
    assert gate.run_checks(m2, py2, ts2) == []
```

- [ ] **Step 2: Run — confirm the new a2a test FAILS, the repointed fixture PASSES**

Run: `POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_api_parity.py -q`
Expected: `test_a2a_skills_surface_partitions_and_absent_is_skipped` fails (a2a_skills not in SURFACES yet → `init_manifest` skips it → `m["a2a_skills"]` KeyError); the repointed unknown-surface test passes.

- [ ] **Step 3: Add `"a2a_skills"` to `SURFACES`** (check_api_parity.py:10)
```python
SURFACES = ("mcp_tools", "cli_commands", "a2a_skills")
```

- [ ] **Step 4: Run gate tests — confirm ALL pass**

Run: same pytest command. Expected: all pass.

- [ ] **Step 5: Add the `a2a_skills` producer to `emit_python_surface.py`**

Add a producer factory + register it for the 4 A2A packages only:
```python
def _a2a(package: str):
    def fn() -> list[str]:
        mod = importlib.import_module(f"{package}.a2a.server")
        skills = getattr(mod, "_SKILLS", None)
        if skills is None:
            skills = mod.AGENT_CARD["skills"]   # goldencheck/goldenflow/goldenpipe
        return [s["id"] for s in skills]
    return fn

_A2A_PACKAGES = ("goldenmatch", "goldencheck", "goldenflow", "goldenpipe")
```
In the REGISTRY build, add `"a2a_skills": (_a2a(pkg), "a2a")` to the surface dict **only** for packages in `_A2A_PACKAGES`. (infermap/goldenanalysis keep just mcp_tools + cli_commands.)

- [ ] **Step 6: Extend the Python emitter smoke test** (`test_api_parity.py`, the existing per-package emitter smoke): assert the 4 A2A packages emit a non-empty sorted `a2a_skills`, and that infermap/goldenanalysis emit no `a2a_skills` key. For goldenmatch, assert `a2a_skills == sorted(s["id"] for s in goldenmatch.a2a.server._SKILLS)` (measured, never hardcoded).

- [ ] **Step 7: Run the real Python emitter for all 4 — box-safe sanity**

Run for each: `PYTHONPATH=packages/python/<pkg> POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe scripts/emit_python_surface.py <pkg>` and confirm `a2a_skills` is present + sorted for the 4, absent for infermap/goldenanalysis. Record the 4 id lists (for Task 3).

- [ ] **Step 8: Commit**
```bash
git add scripts/check_api_parity.py scripts/test_api_parity.py scripts/emit_python_surface.py
git commit -m "feat(parity): add a2a_skills surface to the gate + Python emitter"
```

---

## Task 2: goldencheck `export` + TS emitter producer (CI-only code)

**Files:** `packages/typescript/goldencheck/src/node/a2a/server.ts`, `scripts/emit_ts_surface.mjs`

> Do NOT run pnpm/tsc/node. Write + read-verify; CI verifies.

- [ ] **Step 1: Export goldencheck's AGENT_CARD** (server.ts:30): `const AGENT_CARD = {` → `export const AGENT_CARD = {`. (Non-breaking; the other 3 already export it.)

- [ ] **Step 2: Add the `a2a` producer to `emit_ts_surface.mjs`.** Mirror the existing `mcp` producer. For each of the 4 A2A packages, add an `a2a` field to its REGISTRY entry pointing at `dist/node/a2a/server.js`; the emitter imports `AGENT_CARD` and returns `AGENT_CARD.skills.map(s => s.id).sort()`. For infermap/goldenanalysis, set `a2a: null` (the existing absent-surface pattern) so no `a2a_skills` key is emitted. Guard: if the module has no `AGENT_CARD`, emit nothing for that surface (fail-loud only if a flagged package yields zero).

- [ ] **Step 3: Read-verify** — the export is present; the emitter reads `AGENT_CARD.skills[].id` from the a2a dist path; the 4 A2A packages have an `a2a` entry, the other 2 are `null`; import is side-effect-free (`.listen` is inside `run*A2aServer`, not top-level).

- [ ] **Step 4: Commit**
```bash
git add packages/typescript/goldencheck/src/node/a2a/server.ts scripts/emit_ts_surface.mjs
git commit -m "feat(parity): export goldencheck A2A card + a2a_skills TS emitter producer"
```

---

## Task 3: Bootstrap the 4 `a2a_skills` manifest partitions

**Files:** `parity/{goldenmatch,goldencheck,goldenflow,goldenpipe}.yaml`

The Python ids are box-computable (Task 1 Step 7). The TS ids need the built descriptor (CI-only) — but they are **statically extractable from source** for the bootstrap: read each package's `src/node/a2a/server.ts` `AGENT_CARD.skills` ids (goldencheck/goldenflow/goldenpipe are inline arrays; goldenmatch's is `buildCardSkills()` = the union of `BASE_SKILLS` ids + `AGENT_SKILLS` ids + `MEMORY_TOOLS`/`IDENTITY_TOOLS` names — resolve each array's ids from source). Compute the partition offline; **CI is the verifier** — if the static extraction is off, the gate reds and prints the correct `--init`, which is captured and fixed (documented bootstrap-capture mechanic).

- [ ] **Step 1: Compute per-package `a2a_skills` partitions.** For each of the 4: `shared = PY∩TS`, `python_only = PY−TS`, `ts_only = TS−PY`, each sorted. PY from the Python emitter; TS from static source extraction.

- [ ] **Step 2: Triage divergences.** A `ts_only`/`python_only` pair that is the *same operation under a different id* (e.g. a hyphen/underscore or synonym divergence in goldenflow/goldenpipe) is a genuine naming divergence — record it in that manifest's header comment as a known follow-up (mirror the MCP manifest header style). An intentional coverage gap is just partitioned. Do NOT fix divergences in this PR (the gate's job is to freeze the partition; reconciliation is a follow-up).

- [ ] **Step 3: Write the `a2a_skills` block into each of the 4 `parity/<pkg>.yaml`** (disjoint + sorted; the gate's `check_structure` enforces both).

- [ ] **Step 4: Box-safe structure check** each manifest:
`POLARS_SKIP_CPU_CHECK=1 D:/show_case/goldenmatch/.venv/Scripts/python.exe -c "import yaml; from importlib.util import spec_from_file_location,module_from_spec; s=spec_from_file_location('g','scripts/check_api_parity.py'); m=module_from_spec(s); s.loader.exec_module(m); man=yaml.safe_load(open('parity/<pkg>.yaml')); print([f.kind for f in m.check_structure(man)])"` → `[]`.

- [ ] **Step 5: Commit**
```bash
git add parity/goldenmatch.yaml parity/goldencheck.yaml parity/goldenflow.yaml parity/goldenpipe.yaml
git commit -m "chore(parity): bootstrap a2a_skills partitions for the 4 A2A packages"
```

---

## Task 4: CI filter + PR

**Files:** `.github/workflows/ci.yml`

- [ ] **Step 1: Add a2a paths to the `api_parity` filter** (ci.yml:415-424):
```yaml
              - 'packages/python/*/*/a2a/**'
              - 'packages/typescript/*/src/node/a2a/**'
```

- [ ] **Step 2: Validate ci.yml parses** (`yaml.safe_load`).

- [ ] **Step 3: Push + PR + arm auto-merge (STOP).** PR body: a2a_skills is now gated for the 4 A2A packages; one-line gate change; per-package accessor variance; the bootstrapped partitions + any recorded naming divergences; closes the deferred gate §9 A2A item. **Watch the first CI run's `api_parity` shards** for the 4 A2A packages — if a shard reds on the a2a partition, the gate's stderr prints the correct `--init`; capture it from the shard log (`gh api .../jobs/<id>/logs`), reconcile the manifest, push the fix. (Do NOT poll in a tight loop — capture concluded shards; matrix builds ~5-8 min each.)

```bash
unset GH_TOKEN; gh auth switch --user benzsevern
git push -u origin feat/a2a-parity-gate
GH_TOKEN=$(gh auth token --user benzsevern) gh pr create --repo benseverndev-oss/goldenmatch --base main --title "A2A skills in the API-parity gate" --body-file <body>
GH_TOKEN=$(gh auth token --user benzsevern) gh pr merge --auto --squash --repo benseverndev-oss/goldenmatch  # NO --delete-branch
```

---

## Notes for the implementer

- **The gate change is one line** — the surface machinery already handles per-package-absent surfaces (verified). Don't over-engineer.
- **Fixture repoint is a blocker** — `a2a_skills` was the unknown-surface negative fixture; it must move or the existing test breaks.
- **TS is CI-only** — the manifest TS side is static-extracted for the bootstrap; CI's real emitter is the truth. A first-CI-run reconcile is expected, not a failure.
- **Don't reconcile divergences here** — surface + freeze the partition; record naming divergences in headers as follow-ups (like the MCP gate did).
