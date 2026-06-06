# Security hardening arc — 2026-06-05

A sweep of goldenmatch's Dependabot + code-scanning alert queue plus a
targeted Scorecard climb. Working ledger:
`docs/superpowers/plans/2026-06-05-security-alerts-remediation.md`
(gitignored, local-only — the STATUS LEDGER section there is the fine-
grained source of truth; this node carries the durable shape).

## What the sweep covered

- **42 open alerts** (7 Dependabot + 35 code scanning) as of 2026-06-05.
- **Scorecard 6.1** across Token-Permissions (0), Signed-Releases (0),
  and Fuzzing (0) checks.

## Shipped (merged)

- **#760 — TokenPermissions (2 of 3 fixed):** moved two top-level
  `contents: write` and `packages: write` grants down to job level.
- **#761 — Dependabot lockfile regen (infermap TS bench runner):**
  vitest 1.6.1->4.1.8, vite/esbuild/postcss/fast-uri transitives; also
  fixed a broken pre-fold `file:` dep path that had been silently stale.
- **#762 — Dependabot pyo3 bridge pin:** `>=0.24.1,<0.25` per
  GHSA-pph8-gcv7-4qj5.
- **#764 — py/log-injection (9 sites):** new
  `goldenmatch/core/_logging.py` `sanitize_for_log()` applied at the
  9 call sites identified by CodeQL.
- **#768 — py/path-injection (19 sites):** new
  `goldenmatch/core/_paths.py` `safe_path()` — NUL rejection,
  `resolve()`, opt-in containment via `GOLDENMATCH_ALLOWED_ROOT` env or
  `base_dir` arg; applied at all entry boundaries including 9 MCP
  `_tool_*` handlers via `_safe_path_or_error`; the rollback delete
  loop validates the final joined path. Dismissed post-merge with
  justification (CodeQL cannot credit a conditional barrier; local file
  access is the product for a local-first tool; containment is deploy-
  time policy via `GOLDENMATCH_ALLOWED_ROOT`).
- **#770 — Signed-Releases (Scorecard 0->8):** `publish-goldenmatch-pg.yml`
  now cosign-keyless-signs each tarball (`.sigstore` bundle) + attaches
  GitHub build provenance (`.intoto.jsonl`) on release; new
  `sign-release-assets.yml` retro-signed goldenmatch-pg v0.6.0/v0.7.0
  (signature only; no retroactive provenance). Scores 10 automatically
  on the next pg release.
- **#772 — TokenPermissions last grant (Scorecard 0->10):**
  `update-download-badges` `contents: write` and `publish-containers`
  `packages: write` moved to job level; resolves the third dismissed
  finding.
- **#778 — Fuzzing (Python, Scorecard):** hypothesis property suite
  (`tests/test_property_invariants.py`); also fixed hypothesis missing
  from the root `[dependency-groups] dev` (property tests had been
  silently skipping in CI).
- **#783 — Fuzzing (TS, Scorecard 0->10):** fast-check property suite
  (`packages/typescript/goldenmatch/tests/unit/property-invariants.test.ts`);
  this is the surface Scorecard detects (it does not credit hypothesis).
- **3 TokenPermissions dismissed:** `generate-bench-dataset.yml`
  job-level `contents: write` is required for release uploads and has no
  narrower scope; documented in the dismissal.
- **4 PinnedDependencies dismissed:** Dockerfiles already
  digest+version-pinned (#742); pip `--require-hashes` ruled
  disproportionate for internal one-shot images.

## The CodeQL Autofix incident

A CodeQL "Potential fix..." commit landed on the #768 branch mid-session
and rewrote `safe_path` to fail-closed, breaking every file-touching
test. Reverted.

**Lesson (durable):** never apply Autofix to the validation barrier
itself. Check the remote branch log when CI errors do not match local
code; a ghost commit is the likely culprit.

## Property-test bug ledger

Four real bugs found by the new suites before they merged:

1. **Python dice/jaccard single-pair scorers crash on different-length
   bloom hex** (numpy broadcast error). `strict-xfail` pinned; tracked
   as issue #784.
2. **The literal string `"null"` collapses auto-config** (goldencheck
   sentinel drop -> `ConfigValidationError`). Tolerated; `@example`-
   pinned in the hypothesis suite.
3. **TS `jaro("","")` returns 1 vs Python 0.** Canary-pinned divergence
   in the fast-check suite; no fix yet.
4. **TS `stdNameProper` non-idempotent on 3-way-case unicode (U+1F80).**
   `it.fails`-pinned; source fix pending (no issue yet).

## OPEN ACTIONS

1. **Railway `goldenmatch-mcp` service:** set
   `GOLDENMATCH_ALLOWED_ROOT=/data` once the service's data layout is
   confirmed (pairs with the `GOLDENMATCH_MCP_TOKEN` open action from
   the surface-hardening node).
2. **Issue #784** — fix dice/jaccard bloom scorer on different-length
   inputs (numpy broadcast).
3. **TS `stdNameProper` titlecase fix** — no issue filed yet.

## Remaining Scorecard zeros and why they stay

| Check | Score | Why it stays |
|---|---|---|
| Maintained | 0 | Repo-age gate; auto-heals mid-August 2026. |
| CII-Best-Practices | 0 | Needs maintainer questionnaire (manual). |
| Code-Review | 0 | Solo-maintainer; structural. |
| Contributors | 0 | Solo-maintainer; structural. |
| Branch-Protection | 4 | Requires second-person approvals to reach 8; structural. |
| Pinned-Dependencies | 6 | pip hash-pinning ruled disproportionate for internal images. |
| SAST | 9 | pip hash-pinning is the last gap; same ruling. |

---
**Classification:** planning/workstream • **Last updated:** 2026-06-05
