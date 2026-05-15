# Repo transfer: `benzsevern/goldenmatch` → `benseverndev-oss/goldenmatch`

**Date:** 2026-05-15
**Old URL:** https://github.com/benzsevern/goldenmatch
**New URL:** https://github.com/benseverndev-oss/goldenmatch
**Affected repo:** This one (the GoldenMatch monorepo). `benzsevern/goldenmatch-extensions` was **not** transferred.

## Why we moved

Performance benchmarking for the `gm.dedupe_df()` zero-config path needed
larger GitHub-hosted runners (`ubuntu-latest-large`, 64-core / 256 GB RAM)
to get measurements that aren't dominated by laptop variability or
Polars OOM-then-hang on the local machine.

GitHub's enterprise larger-runner model is **organization-scoped only**.
Even with a runner group set to "All organizations" and "Allow public
repositories" enabled, a personal-account repo (`benzsevern/goldenmatch`)
cannot consume those runners — the access grant has no path to reach
repos that don't sit under an org under the enterprise.

Transferring into an org under the `benseverndev` enterprise was the
only way to keep both:

- The runner-group grant working (org repos qualify).
- The repo as an open-source artifact under our control.

`benseverndev-oss` is the org created for this; future Golden Suite OSS
repos that need the same runners should also live there.

## What auto-redirects (GitHub-guaranteed)

GitHub permanent redirects keep these working for the foreseeable future
(years, with the only known break point being if the new repo is itself
renamed or deleted):

- **HTTPS clones of the old URL.** `git clone https://github.com/benzsevern/goldenmatch` still works.
- **GitHub REST + GraphQL API calls** against the old `owner/repo` path. The response payload reflects the new owner; clients that follow redirects work transparently.
- **PR / issue / commit / blob deep links.** `https://github.com/benzsevern/goldenmatch/pull/237` redirects to the equivalent under `benseverndev-oss`.
- **Raw content URLs** (`raw.githubusercontent.com/benzsevern/...`) redirect.
- **PyPI release-source-link metadata** on already-published versions points at the new URL once GitHub's release storage is queried (no code change needed for already-shipped packages).

## What does NOT auto-redirect (action items)

| Surface | Status | Action |
|---|---|---|
| `ghcr.io/benzsevern/<image>:<tag>` container pulls | Old images stay where they were last pushed; future builds publish under `ghcr.io/benseverndev-oss/<image>` | Update any `docker pull` commands and Compose files. Existing tags still pullable until garbage-collected. |
| Smithery hosted-MCP listings (`smithery.ai/servers/benzsevern/goldenmatch`) | Listing identity is tied to the old owner | Re-register or relink the listing under `benseverndev-oss/goldenmatch` on Smithery |
| MCP Registry namespace (`io.github.benzsevern/goldenmatch`, `io.github.benzsevern/goldencheck`, etc.) | Registered identifier — does not follow GitHub redirects | Re-publish under `io.github.benseverndev-oss/<pkg>` via `.github/workflows/publish-mcp.yml`. Note: only `goldenmatch` was transferred; goldencheck/goldenflow/goldenpipe/infermap keep their existing namespace |
| GitHub Pages site (`benzsevern.github.io/goldenmatch/...`) | The user-pages site itself stays at the personal account; the project Pages move with the repo | Pages will rebuild under `benseverndev-oss.github.io/goldenmatch/` on the next `pages` workflow run. Bookmarks against the old URL break |
| Wiki repo (`benzsevern/goldenmatch.wiki.git`) | Transferred with the main repo automatically | Update any clone scripts to use `benseverndev-oss/goldenmatch.wiki.git` |
| Repo-level secrets (`PYPI_TOKEN`, `TEAMS_PR_WEBHOOK_BENZSEVERN_MJH`, `CLAUDE_CODE_OAUTH_*`) | Carried over with the transfer | No action needed |
| Org-level / enterprise secrets | Were never on the personal account; needed to be added to `benseverndev-oss` separately | Add fresh as needed; workflows that consume them fail loudly until provisioned |
| Local clones | Continue to push/pull via the redirect | `git remote set-url origin https://github.com/benseverndev-oss/goldenmatch.git` recommended but not required |
| `package.json`, `pyproject.toml`, `server.json` `repository`/`homepage`/`urls` | Updated in this cleanup PR | Published artifacts already on PyPI/npm keep their old metadata; next release reflects the new URL |
| `goldenmatch-extensions` (sibling repo) | **Not transferred.** Still at `benzsevern/goldenmatch-extensions` | Stay as-is; if it needs larger runners in the future, transfer it separately |

## What changed in this cleanup

Search-and-replace `benzsevern/goldenmatch` → `benseverndev-oss/goldenmatch`
across the live tree, with two carve-outs:

1. **`benzsevern/goldenmatch-extensions`** is preserved — that repo stays
   on the personal account.
2. **`io.github.benzsevern/<pkg>`** MCP registry identifiers are
   preserved — those need a separate re-publication workflow (the GitHub
   OIDC trust relationship is keyed on the original repo).

In addition:

- `.github/workflows/publish-containers.yml` comment updated; the workflow
  itself already used `${{ github.repository_owner }}` so future container
  pushes auto-route to the new org.
- `.github/workflows/pages.yml` comment updated; Pages will redeploy under
  the new URL on next run.
- `docs/sql-postgres.md`, `dbt/goldencheck/README.md`, `goldensuite-mcp/README.md`, `rust/extensions/README.md`, `examples/typescript/README.md`, and `README.md` had `ghcr.io/benzsevern/<image>` references updated to `ghcr.io/benseverndev-oss/<image>` for monorepo-published images.
- `CLAUDE.md` GitHub auth note widened: the `benzsevern` auth dance now covers both `benzsevern/*` AND `benseverndev-oss/*` repos (the org is owned by the personal account).

## Verification

```bash
# Should return 21 (matches for goldenmatch-extensions, which is correct)
grep -rln "benzsevern/goldenmatch-extensions" \
  --include="*.md" --include="*.toml" --include="*.json" --include="*.py" \
  --include="*.ts" --include="*.tsx" --include="*.yml" --include="*.yaml" \
  --include="*.txt" --include="*.sh" --include="*.ipynb" --include="Cargo.toml" \
  --exclude-dir=".claude" --exclude-dir="node_modules" --exclude-dir=".venv" \
  --exclude-dir="dist" --exclude-dir="build" --exclude-dir="_archive" | wc -l

# Should return 0 (no bare benzsevern/goldenmatch refs left)
grep -rln "benzsevern/goldenmatch[^-]" \
  --include="*.md" --include="*.toml" --include="*.json" --include="*.py" \
  --include="*.ts" --include="*.tsx" --include="*.yml" --include="*.yaml" \
  --include="*.txt" --include="*.sh" --include="*.ipynb" --include="Cargo.toml" \
  --exclude-dir=".claude" --exclude-dir="node_modules" --exclude-dir=".venv" \
  --exclude-dir="dist" --exclude-dir="build" --exclude-dir="_archive" | wc -l
```

## For downstream consumers

If you depend on this repo via any of these patterns, here's what to do:

- **`pip install goldenmatch`** — no change; PyPI package name is unchanged.
- **`npm install goldenmatch`** — no change; npm package name is unchanged.
- **`docker pull ghcr.io/benzsevern/goldenmatch-mcp`** — switch to `docker pull ghcr.io/benseverndev-oss/goldenmatch-mcp` for new pulls. Existing tags pulled remain on local disk; CI scripts pulling fresh should update.
- **CI workflows in your repos that reference `benzsevern/goldenmatch` by URL** — update at convenience; redirects keep them working in the interim.
- **MCP clients consuming the hosted server at `goldenmatch-mcp-production.up.railway.app/mcp/`** — no change; the Railway deployment URL is independent of the GitHub URL.
- **Documentation bookmarks to `benzsevern.github.io/goldenmatch/...`** — switch to `benseverndev-oss.github.io/goldenmatch/...`. Old URL becomes a 404 once Pages rebuilds at the new domain.

## Reverting (if ever needed)

A reverse transfer back to `benzsevern/goldenmatch` is possible at any
time via the same `POST /repos/{owner}/{repo}/transfer` REST API. GitHub
redirects continue to work in both directions, so a return move would
not break downstream consumers — but it would re-break enterprise
larger-runner access, so it would only make sense if the runner story
were resolved another way.
