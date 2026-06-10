# Security Alerts Remediation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close all 7 open Dependabot alerts and all 35 open code-scanning alerts on `benseverndev-oss/goldenmatch` via 5 sequential PRs plus one verify-and-dismiss task (fix where real, dismiss-with-justification where the flagged behavior is required by design or already remediated).

**Architecture:** Five independent PRs, each branched off `origin/main`, merged one at a time (cheapest first), plus a no-code task for the Dockerfile-pinning alerts already remediated by PR #742. Two new shared helpers land in `goldenmatch/core/`: `_logging.py` (`sanitize_for_log`) and `_paths.py` (`safe_path`), then get applied at every CodeQL-flagged call site. Config-only PRs (workflows, Dockerfiles, lockfile, Cargo) carry no new tests; the two helper PRs are TDD.

**Tech Stack:** GitHub Actions workflows, Docker, npm lockfile, pyo3/maturin, Python stdlib (`logging`, `pathlib`), pytest, CodeQL + StepSecurity/Scorecard alerts API.

---

## Ground rules (read before any task)

- **Branching/auth SOP:** every task = fresh branch off `origin/main` (NOT off the current `chore/sail-s4-network` checkout, which is dirty). Push as `benzsevern`: run `gh auth switch --user benzsevern` before push, and `gh auth switch --user benzsevern-mjh` immediately after the PR is opened. If `gh pr create` 401s after the switch, prefix with `GH_TOKEN=$(gh auth token --user benzsevern)`.
- **Worktrees:** create each branch in an isolated worktree under `.worktrees/` (gitignored), e.g. `git worktree add .worktrees/sec-pr1 -b chore/sec-workflow-permissions origin/main`.
- **Tests:** NEVER run the full pytest suite locally (OOMs the box — repo rule). Run only the specific test files you create: `python -m pytest packages/python/goldenmatch/tests/unit/test_<name>.py -v` from the package dir. Full-suite verification happens in CI on the PR.
- **Merging:** `main` requires `ci-required` + strict up-to-date head. After approval, `gh pr merge <N> --squash --delete-branch --auto`, then babysit: if a previously merged PR made the branch stale, run `gh pr update-branch <N>` and re-poll. CI poll: `while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done`.
- **Alert verification:** after each PR merges, re-query the relevant alerts (commands given per task) and confirm they auto-close (CodeQL/Scorecard alerts close on the next default-branch scan; Dependabot closes on lockfile/manifest merge). Dismissals are explicit API calls given inline.
- **Alert API base:** `export GH_TOKEN=$(gh auth token --user benzsevern)` then `gh api repos/benseverndev-oss/goldenmatch/...`.
- **Local Python on this Windows box:** any command that imports polars needs `POLARS_SKIP_CPU_CHECK=1` (and `PYTHONIOENCODING=utf-8`) in the env or it hangs on a WMI query.

### Alert inventory (state as of 2026-06-05)

**Dependabot (7):** #14 vitest critical, #5/#4 fast-uri high, #3 postcss med, #2 vite med, #1 esbuild med — all in `packages/python/infermap/benchmark/runners/ts/package-lock.json` (lockfile resolves vitest **1.6.1** while `package.json` already declares `^4.1.0`; lockfile is stale). #6 pyo3 low — `packages/rust/extensions/bridge/Cargo.toml` pins `>=0.23.3, <0.24`; advisory fixed in 0.24.1.

**Code scanning (35):**
- `TokenPermissionsID` (3, high): #373 `bench-df-cluster-edges.yml` (no top-level `permissions:` at all), #372 `generate-bench-dataset.yml` (job-level `contents: write` — REQUIRED for release upload → dismiss), #312 `benchmarks.yml` (job-level `actions: write` — NOT actually needed → remove).
- `PinnedDependenciesID` (4, med): #376 `Dockerfile.bench:17`, #375 `Dockerfile.qis:23`, #374 `Dockerfile.embprov:18`, #320 `dbt-goldensuite/spcs/Dockerfile:30` — **already remediated on main by PR #742 (merged 2026-06-05 06:15 UTC: digest-pinned base images, exact pip versions, qis pinned to commit SHA)**; alerts remain open only because Scorecard hasn't rescanned and/or wants pip hash-pinning. Verify-then-dismiss, no code change (Task 2).
- `py/log-injection` (9, med): #396-#399 `mcp/agent_tools.py`, #402/#403 `core/match_one.py`, #404 `core/rollback.py`, #401 `core/lineage.py`, #400 `core/domain_registry.py`.
- `py/path-injection` (19, high): #377/#388-#392 `mcp/server.py`, #383-#387 `core/rollback.py`, #393-#395 `core/smart_ingest.py`, #381/#382 `core/lineage.py`, #378/#379 `core/domain_registry.py`, #380 `core/ingest.py`.

Alert line numbers refer to `main`; this plan references **functions**, which are stable.

---

### Task 1: PR 1 — workflow token permissions (closes #373, #312; dismisses #372)

**Files:**
- Modify: `.github/workflows/bench-df-cluster-edges.yml` (add top-level permissions block after `name:`)
- Modify: `.github/workflows/benchmarks.yml` (remove job-level `actions: write`)
- No change: `.github/workflows/generate-bench-dataset.yml` (dismiss instead)

- [ ] **Step 1: Create worktree + branch**

```bash
git fetch origin main
git worktree add .worktrees/sec-pr1 -b chore/sec-workflow-permissions origin/main
cd .worktrees/sec-pr1
```

- [ ] **Step 2: Add top-level permissions to bench-df-cluster-edges.yml**

The file currently has NO `permissions:` block. Insert immediately after the `name: bench-df-cluster-edges` line (line 1, before the comment block):

```yaml
name: bench-df-cluster-edges

permissions:
  contents: read
```

Verify nothing in the workflow writes via GITHUB_TOKEN first: `grep -nE "GITHUB_TOKEN|GH_TOKEN|gh api|gh release" .github/workflows/bench-df-cluster-edges.yml` — expect only checkout/upload-artifact usage (neither needs write). If a write usage appears, scope a job-level grant instead and note it in the PR body.

- [ ] **Step 3: Remove `actions: write` from benchmarks.yml**

In the `benchmarks` job (~line 49 on main), the job-level permissions block is:

```yaml
    permissions:
      contents: read
      # Allows the job to comment on the workflow summary + upload artifacts.
      actions: write
```

Replace with:

```yaml
    permissions:
      contents: read
```

Rationale for the PR body: `actions/upload-artifact` and `$GITHUB_STEP_SUMMARY` require no token permission; the comment in the workflow was wrong. Confirmed nothing else in the workflow calls the Actions API (`grep -nE "gh api|actions/cache|workflow" .github/workflows/benchmarks.yml` shows only checkout/upload-artifact and the cron comment).

- [ ] **Step 4: Validate YAML parses**

```bash
python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/bench-df-cluster-edges.yml')); yaml.safe_load(open('.github/workflows/benchmarks.yml')); print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit, push, PR**

```bash
git add .github/workflows/bench-df-cluster-edges.yml .github/workflows/benchmarks.yml
git commit -m "chore(ci): least-privilege GITHUB_TOKEN permissions on bench workflows

Closes code-scanning TokenPermissionsID alerts 373 (no top-level
permissions on bench-df-cluster-edges) and 312 (unneeded job-level
actions:write on benchmarks -- upload-artifact and step summaries
need no token permission)."
gh auth switch --user benzsevern
git push -u origin chore/sec-workflow-permissions
gh pr create --title "chore(ci): least-privilege token permissions on bench workflows" --body "..."
gh auth switch --user benzsevern-mjh
```

Note: workflow-file changes force ALL CI jobs to run (path-filter rule) — expect a longer CI run than a doc PR.

- [ ] **Step 6: Merge (auto-merge + babysit), then dismiss #372**

`generate-bench-dataset.yml` already follows least privilege (top-level `contents: read`, job-level `contents: write` only on the job that uploads release assets). Scorecard scores any job-level `write` as 0 regardless. Dismiss:

```bash
export GH_TOKEN=$(gh auth token --user benzsevern)
gh api -X PATCH repos/benseverndev-oss/goldenmatch/code-scanning/alerts/372 \
  -f state=dismissed -f dismissed_reason="won't fix" \
  -f dismissed_comment="contents:write is required for this job to upload bench-dataset release assets; already scoped to job level with top-level contents:read. Least privilege for this workflow's purpose."
```

- [ ] **Step 7: Verify #373 and #312 close after the next default-branch scan**

```bash
gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts/373 -q .state
gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts/312 -q .state
```

Expected: `dismissed`/`fixed` (Scorecard runs on a schedule — if still `open` hours later, don't block; note it and continue. Check the schedule with `gh api repos/benseverndev-oss/goldenmatch/actions/workflows --paginate -q '.workflows[].path' | grep -iE "scorecard|step"`).

---

### Task 2: Dockerfile pinning alerts — verify-then-dismiss, NO PR (targets #376, #375, #374, #320)

**Files:** none. The remediation already landed on main as **PR #742** (merged 2026-06-05 06:15 UTC): all four Dockerfiles have digest-pinned `FROM` lines, exact pip versions (`polars==1.41.2`, `numpy==2.4.6`, `recordlinkage==0.16`, `cryptography==48.0.0`, etc.), and `Dockerfile.qis` pins a commit SHA instead of the git branch. Do NOT create a branch or PR for this task.

The 4 alerts remain open for one (or both) of two reasons: Scorecard hasn't rescanned main since #742, and/or Scorecard's pinned-dependencies check additionally demands pip `--require-hashes` pinning, which is disproportionate for these internal one-shot bench/Railway images.

- [ ] **Step 1: Confirm main really carries the #742 pinning (guard against revert)**

```bash
git fetch origin main
git show "origin/main:packages/python/goldenmatch/Dockerfile.bench" | grep -E "^FROM|=="
git show "origin/main:Dockerfile.qis" | grep -E "^FROM|git\+"
git show "origin/main:Dockerfile.embprov" | grep -E "^FROM|=="
git show "origin/main:packages/python/goldenmatch/dbt-goldensuite/spcs/Dockerfile" | grep -E "^FROM|=="
```

(On this Windows box the Bash tool mangles `origin/main:path` args unless quoted — keep the quotes, or use the PowerShell tool.)

Expected: every `FROM` carries `@sha256:`, every pip dep is `==`-pinned, qis installs from a 40-char commit SHA. If any of that is missing, STOP — main moved again; re-inventory before acting.

- [ ] **Step 2: Check whether Scorecard has rescanned since #742 and whether the alerts closed**

```bash
export GH_TOKEN=$(gh auth token --user benzsevern)
for n in 376 375 374 320; do gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts/$n -q '[.number,.state,.most_recent_instance.commit_sha[0:8]] | @tsv'; done
```

If `most_recent_instance.commit_sha` predates the #742 merge commit, the scan is stale — find and trigger the scanning workflow (`gh api repos/benseverndev-oss/goldenmatch/actions/workflows --paginate -q '.workflows[] | [.path,.state] | @tsv' | grep -iE "scorecard|step|codeql"`, then `gh workflow run <file> --ref main` if it supports dispatch; otherwise wait for its schedule and continue with Task 3 in the meantime — revisit in the Task 7 sweep).

- [ ] **Step 3: Dismiss whatever survives a post-#742 scan**

Alerts still open against the pinned Dockerfiles are the no-hashes complaint. Dismiss:

```bash
gh api -X PATCH repos/benseverndev-oss/goldenmatch/code-scanning/alerts/<n> \
  -f state=dismissed -f dismissed_reason="won't fix" \
  -f dismissed_comment="Base image digest-pinned and all pip deps exact-version-pinned (PR #742). Full --require-hashes pinning is disproportionate for this internal one-shot bench/Railway image (no production traffic, rebuilt deliberately)."
```

---

### Task 3: PR 3 — infermap TS runner lockfile regen (closes Dependabot #14, #5, #4, #3, #2, #1)

**Files:**
- Modify: `packages/python/infermap/benchmark/runners/ts/package-lock.json` (regenerated)
- Possibly modify: `packages/python/infermap/benchmark/runners/ts/package.json` (only if vitest 4.x needs a config tweak)

**Context:** the lockfile resolves vitest **1.6.1** but `package.json` already declares `^4.1.0` — the lockfile predates the devDep bump. A regen pulls vitest 4.1.x and current vite/esbuild/postcss/fast-uri transitively, clearing all 6 alerts. This is a standalone npm project (NOT part of the pnpm/turbo workspace — it lives under a python package), so plain `npm` is correct here.

- [ ] **Step 1: Create worktree + branch**

```bash
git worktree add .worktrees/sec-pr3 -b chore/sec-infermap-ts-lockfile origin/main
cd .worktrees/sec-pr3/packages/python/infermap/benchmark/runners/ts
```

- [ ] **Step 2: Regenerate the lockfile**

```bash
rm -rf node_modules package-lock.json
npm install
```

Note: `infermap` resolves via `file:../../../../typescript/infermap` — that path must exist in the worktree (it does; it's in-repo).

- [ ] **Step 3: Verify the vulnerable packages moved**

```bash
python -c "
import json
lock = json.load(open('package-lock.json'))
for name in ['vitest','vite','esbuild','postcss','fast-uri']:
    for k,v in lock['packages'].items():
        if k.endswith('node_modules/'+name):
            print(k, '=>', v.get('version'))
"
```

Expected: vitest >= 4.1.0, vite >= 6.x/7.x (no <= 6.4.1), esbuild > 0.24.2 everywhere (watch for a NESTED old `node_modules/vite/node_modules/esbuild` — the current lockfile has 0.21.5 there), postcss >= 8.5.10, fast-uri > 3.1.1.

- [ ] **Step 4: Run the runner's own checks**

```bash
npm run typecheck
npm run build
npm test
```

Expected: all pass. vitest 1.x → 4.x is a major jump: if tests fail on config/API changes (e.g. `vitest.config` workspace options, `test.poolOptions`), fix minimally and include in the PR. If the failures are deep, STOP and report — do not park a broken runner.

- [ ] **Step 5: Commit (lockfile + any config fixes), push, PR (auth dance)**

```bash
git add package-lock.json package.json
git commit -m "chore(deps): regenerate infermap TS bench runner lockfile (vitest 1.6 -> 4.1)

Clears Dependabot alerts 14 (vitest critical), 4/5 (fast-uri),
1 (esbuild), 2 (vite), 3 (postcss) -- all transitive of the stale
lockfile; package.json already declared vitest ^4.1.0."
```

- [ ] **Step 6: After merge, verify Dependabot alerts auto-closed**

```bash
gh api repos/benseverndev-oss/goldenmatch/dependabot/alerts -q '.[] | select(.state=="open") | [.number,.dependency.package.name] | @tsv'
```

Expected: only #6 (pyo3) remains.

---

### Task 4: PR 4 — pyo3 bump in bridge crate (closes Dependabot #6)

**Files:**
- Modify: `packages/rust/extensions/bridge/Cargo.toml` (version range + the stale advisory comment above it)
- Possibly modify: `packages/rust/extensions/bridge/src/*.rs` (~2k lines total: `api.rs`, `convert.rs`, `error.rs`, `lib.rs`) if 0.24 API changes bite

**Context:** advisory = buffer overflow in `PyString::from_object`, fixed in pyo3 0.24.1. Current pin `>=0.23.3, <0.24` with a comment saying the 0.24 bump is a separate task. The 0.23→0.24 migration is small compared to 0.22→0.23 (the `IntoPyObject` rework already happened in 0.23). The `native` crate already allows `<0.25` and `datafusion-udf` is on 0.28 — only `bridge` is stuck.

**Local-box constraint:** `cargo check` is fine locally; do NOT run `cargo build`/`cargo test` locally (OOM risk — repo rule). Functional verification happens in CI.

- [ ] **Step 1: Create worktree + branch**

```bash
git worktree add .worktrees/sec-pr4 -b chore/sec-pyo3-bump origin/main
cd .worktrees/sec-pr4/packages/rust/extensions/bridge
```

- [ ] **Step 2: Bump the pin**

In `Cargo.toml`, replace:

```toml
pyo3 = { version = ">=0.23.3, <0.24", features = ["auto-initialize"] }
```

with:

```toml
pyo3 = { version = ">=0.24.1, <0.25", features = ["auto-initialize"] }
```

Also rewrite the comment block above it (it references the old 0.23 GHSA and says "Bumping to 0.24+ is a separate task") to: pinned `>=0.24.1` for RUSTSEC/GHSA buffer-overflow fix in `PyString::from_object`; `<0.25` to stay on the same API line as the `native` crate.

- [ ] **Step 3: Check compile**

```bash
cargo check 2>&1 | tail -30
```

Expected: clean, or a small set of deprecation/API errors. Known 0.24 changes to watch for: `pyo3::prelude` item moves, `Bound<'_, T>` signature tightening, `PyAnyMethods` trait imports. Fix minimally; do not refactor.

- [ ] **Step 4: Check whether the `native` crate's lockfile floor needs the same nudge**

```bash
grep -rn "name = \"pyo3\"" -A1 ../native/Cargo.lock 2>/dev/null | head
```

The `native` range `>=0.23.3, <0.25` already *permits* 0.24.1+, but if its `Cargo.lock` pins a vulnerable resolve (<0.24.1), run `cargo update -p pyo3` in `../native` and include the lockfile in this PR. (Dependabot only flagged `bridge`, so this is belt-and-braces.)

- [ ] **Step 5: Commit, push, PR (auth dance); CI is the real verifier**

```bash
git add Cargo.toml Cargo.lock src/ 2>/dev/null
git commit -m "chore(deps): bump bridge pyo3 to >=0.24.1 (GHSA PyString::from_object overflow)"
```

PR body must note: verified via `cargo check` locally + full CI; local cargo build/test skipped by repo policy.

- [ ] **Step 6: Watch the rust CI lane specifically; after merge, confirm Dependabot #6 closed**

```bash
gh api repos/benseverndev-oss/goldenmatch/dependabot/alerts/6 -q .state
```

Expected: `fixed`.

---

### Task 5: PR 5 — log-injection sanitizer (closes #396-#404, 9 alerts)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/_logging.py`
- Test: `packages/python/goldenmatch/tests/unit/test_log_sanitize.py`
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/agent_tools.py` (3 `logger.info` sites in `_dispatch`: scan_quality, fix_quality, run_transforms)
- Modify: `packages/python/goldenmatch/goldenmatch/core/match_one.py` (2 sites: `match_one`, `_match_one_brute`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/rollback.py` (1 site in `rollback_run`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/lineage.py` (1 site in `save_lineage`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/domain_registry.py` (1 site in `save_rulebook`)

- [ ] **Step 1: Create worktree + branch**

```bash
git worktree add .worktrees/sec-pr5 -b feat/sec-log-sanitize origin/main
cd .worktrees/sec-pr5/packages/python/goldenmatch
```

- [ ] **Step 2: Write the failing test**

Create `tests/unit/test_log_sanitize.py`:

```python
"""Tests for goldenmatch.core._logging.sanitize_for_log."""

from goldenmatch.core._logging import sanitize_for_log


def test_strips_newlines_and_carriage_returns():
    assert sanitize_for_log("a\nb\rc") == "a b c"


def test_strips_ansi_and_control_chars():
    assert sanitize_for_log("ok\x1b[31mred\x07") == "okred"


def test_truncates_long_values():
    out = sanitize_for_log("x" * 5000)
    assert len(out) <= 1000
    assert out.endswith("...")


def test_non_string_values_coerced():
    assert sanitize_for_log(0.85) == "0.85"
    from pathlib import Path
    assert sanitize_for_log(Path("a/b")) in ("a/b", "a\\b")


def test_plain_string_unchanged():
    assert sanitize_for_log("normal_file.csv") == "normal_file.csv"
```

- [ ] **Step 3: Run it, verify it fails**

```bash
python -m pytest tests/unit/test_log_sanitize.py -v
```

Expected: FAIL — `ModuleNotFoundError: goldenmatch.core._logging`. (Remember `POLARS_SKIP_CPU_CHECK=1` if the package import chain pulls polars.)

- [ ] **Step 4: Implement the helper**

Create `goldenmatch/core/_logging.py`:

```python
"""Log-output sanitization (CodeQL py/log-injection mitigation).

User-supplied values (file paths, run ids, config strings) flow into
log lines. Strip control characters so a crafted value can't forge
log records or smuggle ANSI escapes into terminals tailing the log.
"""

from __future__ import annotations

import re

_CONTROL_CHARS = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_MAX_LEN = 1000


def sanitize_for_log(value: object, max_length: int = _MAX_LEN) -> str:
    """Return a log-safe string: newlines collapsed, ANSI/control chars
    stripped, truncated to *max_length*."""
    s = str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    s = _CONTROL_CHARS.sub("", s)
    if len(s) > max_length:
        s = s[: max_length - 3] + "..."
    return s
```

- [ ] **Step 5: Run the test, verify it passes**

```bash
python -m pytest tests/unit/test_log_sanitize.py -v
```

Expected: 5 passed.

- [ ] **Step 6: Apply at the 9 flagged sites**

Pattern — wrap ONLY the tainted argument, leave the format string and untainted args alone. The sites (function-anchored; find each `logger.info` with grep, line numbers drift):

1. `mcp/agent_tools.py` `_dispatch` / scan_quality: `logger.info("scan_quality: scanning %s (%d records)", sanitize_for_log(file_path), df.height)`
2. `mcp/agent_tools.py` `_dispatch` / fix_quality: sanitize `file_path` and `fix_mode`.
3. `mcp/agent_tools.py` `_dispatch` / run_transforms: sanitize `file_path`.
4. `core/match_one.py` `match_one`: the tainted value is `mk.threshold` (config-sourced float) — coerce instead of sanitize: `float(mk.threshold)` inline in the call. `float()` is a recognized sanitizer and honest about the type.
5. `core/match_one.py` `_match_one_brute`: same `float(mk.threshold)` coercion.
6. `core/rollback.py` `rollback_run`: sanitize `run_id`.
7. `core/lineage.py` `save_lineage`: sanitize `path` (`sanitize_for_log(path)`).
8. `core/domain_registry.py` `save_rulebook`: sanitize `rulebook.name` and `path`.

Import as `from goldenmatch.core._logging import sanitize_for_log` (in `mcp/agent_tools.py` too — absolute imports match the package style).

- [ ] **Step 7: Targeted regression check (imports still work, no syntax errors)**

```bash
python -m py_compile goldenmatch/mcp/agent_tools.py goldenmatch/core/match_one.py goldenmatch/core/rollback.py goldenmatch/core/lineage.py goldenmatch/core/domain_registry.py goldenmatch/core/_logging.py
python -m pytest tests/unit/test_log_sanitize.py -v
```

Then run the existing test files that already cover the touched modules (find them: `grep -rln "match_one\|rollback\|lineage\|domain_registry" tests/unit | head`), individually — NOT the full suite.

- [ ] **Step 8: Commit, push, PR (auth dance)**

```bash
git commit -m "feat(security): sanitize user-supplied values in log output

Adds core/_logging.sanitize_for_log (strip CR/LF + ANSI/control chars,
truncate) and applies it at the 9 CodeQL py/log-injection sites."
```

- [ ] **Step 9: After merge, verify alerts #396-#404 close on the next CodeQL scan**

```bash
for n in 396 397 398 399 400 401 402 403 404; do gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts/$n -q '[.number,.state] | @tsv'; done
```

If `float()`/`sanitize_for_log` isn't recognized by CodeQL for a residual site, dismiss it: `-f dismissed_reason="false positive" -f dismissed_comment="Value passes through sanitize_for_log (core/_logging.py) which strips CR/LF and control chars before logging."`

---

### Task 6: PR 6 — path-injection hardening (targets the 19 high alerts)

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/_paths.py`
- Test: `packages/python/goldenmatch/tests/unit/test_safe_path.py`
- Modify: `packages/python/goldenmatch/goldenmatch/mcp/server.py` (`_tool_pprl_link`, `_tool_export_results`, plus any other flagged `_tool_*` site found by grep)
- Modify: `packages/python/goldenmatch/goldenmatch/core/ingest.py` (`load_file`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/smart_ingest.py` (`detect_encoding`, `smart_load`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/rollback.py` (`rollback_run`, `_load_run_log`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/lineage.py` (`save_lineage`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/domain_registry.py` (`save_rulebook`)
- Modify: `packages/python/goldenmatch/README.md` or the MCP docs section (document `GOLDENMATCH_ALLOWED_ROOT`)

**Design (decided with Ben):** harden with a shared helper, opt-in sandbox root.

```
safe_path(value, *, base_dir=None) -> Path
  1. fspath() the value; reject NUL bytes (ValueError)
  2. Path(value).resolve()  — collapses ../, resolves symlinks
  3. containment root = base_dir or env GOLDENMATCH_ALLOWED_ROOT (unset => no containment, local-first default)
  4. if root set and not resolved.is_relative_to(root.resolve()): raise PathOutsideAllowedRootError
```

**Honesty note:** CodeQL's taint tracking may not recognize a *conditional* containment barrier — expect some of the 19 to stay open after the PR. The endgame for residuals is dismissal with the justification "path normalized + validated via core/_paths.safe_path; containment enforced when GOLDENMATCH_ALLOWED_ROOT is set; arbitrary local file access is the product for this local-first tool." The hardening is real either way: the MCP server deployed on Railway gets `GOLDENMATCH_ALLOWED_ROOT=/data` set as an env var (final step).

- [ ] **Step 1: Create worktree + branch**

```bash
git worktree add .worktrees/sec-pr6 -b feat/sec-safe-path origin/main
cd .worktrees/sec-pr6/packages/python/goldenmatch
```

- [ ] **Step 2: Write the failing tests**

Create `tests/unit/test_safe_path.py`:

```python
"""Tests for goldenmatch.core._paths.safe_path."""

from pathlib import Path

import pytest

from goldenmatch.core._paths import PathOutsideAllowedRootError, safe_path


def test_plain_path_resolves(tmp_path):
    f = tmp_path / "data.csv"
    f.write_text("a,b\n1,2")
    assert safe_path(str(f)) == f.resolve()


def test_rejects_null_byte():
    with pytest.raises(ValueError):
        safe_path("data\x00.csv")


def test_traversal_collapsed(tmp_path):
    p = safe_path(str(tmp_path / "sub" / ".." / "data.csv"))
    assert ".." not in p.parts


def test_containment_blocks_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    with pytest.raises(PathOutsideAllowedRootError):
        safe_path(str(outside), base_dir=root)


def test_containment_allows_inside(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    inside = root / "ok.csv"
    assert safe_path(str(inside), base_dir=root) == inside.resolve()


def test_env_root(tmp_path, monkeypatch):
    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))
    with pytest.raises(PathOutsideAllowedRootError):
        safe_path(str(tmp_path / "outside.csv"))
    assert safe_path(str(root / "in.csv")) == (root / "in.csv").resolve()


def test_no_root_no_containment(tmp_path, monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ALLOWED_ROOT", raising=False)
    assert safe_path(str(tmp_path / "anything")) == (tmp_path / "anything").resolve()
```

- [ ] **Step 3: Run, verify failure**

```bash
python -m pytest tests/unit/test_safe_path.py -v
```

Expected: FAIL — `ModuleNotFoundError: goldenmatch.core._paths`.

- [ ] **Step 4: Implement the helper**

Create `goldenmatch/core/_paths.py`:

```python
"""Path validation (CodeQL py/path-injection mitigation).

GoldenMatch is local-first: reading the user's own files by path is the
product, so containment is OPT-IN. Setting GOLDENMATCH_ALLOWED_ROOT (or
passing base_dir) jails all user-supplied paths under that root --
deploy-time hardening for network-exposed surfaces (the Railway MCP
server sets it to the /data volume).
"""

from __future__ import annotations

import os
from pathlib import Path

_ENV_ROOT = "GOLDENMATCH_ALLOWED_ROOT"


class PathOutsideAllowedRootError(ValueError):
    """Raised when a user-supplied path escapes the configured root."""


def safe_path(value: str | os.PathLike, *, base_dir: str | os.PathLike | None = None) -> Path:
    """Normalize *value* and (when a root is configured) enforce containment.

    Raises ValueError on NUL bytes, PathOutsideAllowedRootError on escape.
    """
    raw = os.fspath(value)
    if "\x00" in raw:
        raise ValueError("path contains NUL byte")
    resolved = Path(raw).resolve()
    root = base_dir if base_dir is not None else os.environ.get(_ENV_ROOT)
    if root:
        root_resolved = Path(root).resolve()
        if not resolved.is_relative_to(root_resolved):
            raise PathOutsideAllowedRootError(
                f"path {str(resolved)!r} is outside allowed root {str(root_resolved)!r}"
            )
    return resolved
```

- [ ] **Step 5: Run tests, verify pass**

```bash
python -m pytest tests/unit/test_safe_path.py -v
```

Expected: 7 passed.

- [ ] **Step 6: Apply at entry points (one commit per file, run that file's existing tests after each)**

Principle: validate at the **boundary where the user value enters**, then pass the returned `Path` downstream — don't sprinkle `safe_path` on derived paths.

1. `core/ingest.py` `load_file`: `path = safe_path(path)` replacing `path = Path(path)` (before the `.exists()` check).
2. `core/smart_ingest.py` `smart_load`: `path = safe_path(path)` replacing `path = Path(path)`. `detect_encoding` receives the already-validated path from `smart_load`; if it's also called with raw user input elsewhere (grep callers), add `path = safe_path(path)` at its top too.
3. `core/rollback.py` `rollback_run`: `output_dir = safe_path(output_dir)` at function top; inside the delete loop, `p = safe_path(filepath, base_dir=None)` then the existing `is_absolute` join logic — but run the JOINED path through `safe_path(p)` again before `p.unlink()` (the run-log JSON is attacker-influenceable, this is the delete primitive — strictest site).
4. `core/lineage.py` `save_lineage`: `output_dir = safe_path(output_dir)` before `mkdir`. Also sanitize `run_name` into the filename: `run_name = Path(run_name).name` (strips any directory components) before building `f"{run_name}_lineage.json"`.
5. `core/domain_registry.py` `save_rulebook`: `path = safe_path(path)` replacing `path = Path(path)`.
6. `mcp/server.py` `_tool_pprl_link`: `file_a = safe_path(args["file_a"])`, `file_b = safe_path(args["file_b"])` — wrap in try/except returning `{"error": str(exc)}` consistent with the function's existing error style.
7. `mcp/server.py` `_tool_export_results`: `path = safe_path(output_path)` with the same try/except style.
8. `mcp/server.py`: grep for the remaining flagged sites (`grep -n "Path(args\|Path(output_path\|Path(file_path" goldenmatch/mcp/server.py`) — alerts #377/#388-#392 span ~6 lines; apply the same pattern to each `_tool_*` that takes a path arg.

After each file: `python -m py_compile <file>` + run that module's existing unit-test file(s) individually.

- [ ] **Step 7: Add a containment integration test for the MCP boundary**

Append to `tests/unit/test_safe_path.py`:

```python
def test_mcp_export_respects_root(tmp_path, monkeypatch):
    root = tmp_path / "jail"
    root.mkdir()
    monkeypatch.setenv("GOLDENMATCH_ALLOWED_ROOT", str(root))
    from goldenmatch.mcp import server as mcp_server
    result = mcp_server._tool_export_results(str(tmp_path / "escape.json"), "json")
    assert "error" in result
```

(Adjust the call signature to match the actual function — verify with grep before writing. If `_tool_export_results` needs global state (`_result`), set the minimal stub the error path requires, or pick whichever flagged `_tool_*` is stateless as the integration probe.)

- [ ] **Step 8: Document the env var**

Add `GOLDENMATCH_ALLOWED_ROOT` to wherever the package documents env vars (grep `GOLDENMATCH_` in `packages/python/goldenmatch/README.md` and the docs tree; follow the existing format): "Opt-in path sandbox. When set, every user-supplied file path (MCP tools, ingest, rollback, lineage, domain registry) must resolve under this directory."

- [ ] **Step 9: Commit, push, PR (auth dance)**

```bash
git commit -m "feat(security): safe_path validation for user-supplied file paths

Adds core/_paths.safe_path (NUL rejection, resolve(), opt-in
GOLDENMATCH_ALLOWED_ROOT containment) and applies it at every CodeQL
py/path-injection site: ingest, smart_ingest, rollback (incl. the
delete loop), lineage, domain_registry, and the MCP tool handlers."
```

- [ ] **Step 10: After merge — set the Railway sandbox root, then triage residual alerts**

Set the env var on the deployed MCP service (Railway project `golden-suite-mcp`, service `goldenmatch-mcp`): `GOLDENMATCH_ALLOWED_ROOT=/data`. **Produce the command / do it via the Railway MCP tooling only with Ben's confirmation** (it changes prod service behavior — existing users passing paths outside `/data` would start erroring; confirm the service's actual data layout first).

Then check the 19 alerts:

```bash
for n in 377 378 379 380 381 382 383 384 385 386 387 388 389 390 391 392 393 394 395; do gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts/$n -q '[.number,.state] | @tsv'; done
```

Dismiss residuals (expected — conditional barrier):

```bash
gh api -X PATCH repos/benseverndev-oss/goldenmatch/code-scanning/alerts/<n> \
  -f state=dismissed -f dismissed_reason="won't fix" \
  -f dismissed_comment="Path is normalized and validated via core/_paths.safe_path (NUL rejection, resolve(), opt-in GOLDENMATCH_ALLOWED_ROOT containment -- set on the deployed MCP service). Arbitrary local-file access by user-supplied path is the product for this local-first tool; containment is deploy-time policy."
```

---

### Task 7: Final sweep

- [ ] **Step 1: Verify both counters are zero**

```bash
export GH_TOKEN=$(gh auth token --user benzsevern)
gh api repos/benseverndev-oss/goldenmatch/dependabot/alerts -q '[.[] | select(.state=="open")] | length'
gh api repos/benseverndev-oss/goldenmatch/code-scanning/alerts --paginate -q '[.[] | select(.state=="open")] | length'
```

Expected: `0` and `0`. CodeQL/Scorecard close on their next default-branch run — if non-zero immediately after the last merge, identify which scan hasn't rerun yet before treating anything as a miss.

- [ ] **Step 2: Clean up worktrees**

```bash
git worktree remove .worktrees/sec-pr1  # ... and pr3..pr6 (no pr2 worktree — Task 2 was no-code)
git worktree prune
```

- [ ] **Step 3: Report**

Summarize per-alert disposition (fixed vs dismissed + justification) back to Ben.
