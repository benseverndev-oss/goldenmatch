# 0010 — publish-containers: mirror BuildKit/binfmt into ghcr (off anonymous Docker Hub)

**Status:** accepted (2026-06-10, PR #846)
**Evidence:** 30-day audit — 11 fails across 6 different packages, all transient registry timeouts, zero code bugs; verified `main` run `27284102426` (8/8 jobs green, no retry twin fired).

## Context
`publish-containers` (7-image matrix, every push to `main`) went red ~1 run in 18 over 30 days. Every failure was a transient network/registry timeout, never a code bug. Dominant mode: `docker/setup-buildx-action` bootstraps BuildKit by pulling `moby/buildkit:buildx-stable-1` from Docker Hub **anonymously** (the workflow logged into ghcr but never Docker Hub). On GitHub's shared runner egress IPs Docker Hub throttles anonymous pulls into timeouts (`context deadline exceeded`); 7 legs pulling buildkit in parallel each push meant a random leg raced into a throttled window. Secondary modes: transient ghcr 502s and GHA-cache (`actions-cache`) blob copy errors at `Build and push`.

## Decision
1. **Mirror the helper images into ghcr.** A prerequisite `mirror` job copies `moby/buildkit:buildx-stable-1` + `tonistiigi/binfmt:latest` into `ghcr.io/<owner>/{buildkit,binfmt}` once per run (retried); the 7 legs pull them from ghcr via `setup-buildx` `driver-opts:` / `setup-qemu` `image:`. ghcr login moved **ahead** of the buildx bootstrap so the private mirror is pullable. Net: Docker Hub off the hot path; 7 unguarded parallel pulls become 1 retried read.
2. **Retry-once backstop** (`continue-on-error` + `outcome == 'failure'` guard) on QEMU / Buildx / Build-and-push, for the residual ghcr/cache blips.
3. **`publish` runs even if `mirror` fails** (`needs.mirror.result == 'success' || 'failure'`): a stale-but-present ghcr copy still unblocks all images; only a cancelled mirror skips it.

### Rejected alternatives
- **Docker Hub login (`docker/login-action` for `docker.io`).** Raises anonymous limits but needs a Docker Hub username + token as new repo secrets, and the failure was a *timeout* not a hard 429. Preferred removing Docker Hub from the hot path over negotiating its limits — zero new secrets.
- **A third-party retry action (`Wandalen/wretry`, `nick-fields/retry`).** The repo SHA-pins everything and uses native `--retry` only; a new third-party action on the publish critical path is a supply-chain cost. Used native retry-once (a duplicated guarded step) instead.
- **A separate scheduled mirror workflow.** Creates an ordering trap (a brand-new workflow can't be `workflow_dispatch`-ed off a feature branch until it lands on the default branch, so the mirror couldn't be bootstrapped pre-merge). The in-workflow prereq job is self-bootstrapping and cuts Docker Hub pulls 7 to 1.

## Consequences
- Operational detail lives in root `CLAUDE.md` (`## publish-containers flakes`) — point, don't duplicate.
- A red `publish-containers` is cosmetic (content-addressed images, prior `:latest` stays valid, next push self-heals); don't chase it as a build break.
- One new small dependency, the `mirror` job, mitigated by 3x retry + the `if:` that lets `publish` proceed on a stale ghcr copy.

---
**Classification:** decision/accepted • **Last updated:** 2026-06-10
