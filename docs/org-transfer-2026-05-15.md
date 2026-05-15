# Repo transfer: `benzsevern/*` → `benseverndev-oss/*`

**Date:** 2026-05-15
**Scope:** 18 repositories transferred from the personal account `benzsevern` to a new GitHub organization `benseverndev-oss` under the `benseverndev` enterprise.

## What moved

| # | Old URL | New URL | Visibility | Status |
|---|---|---|---|---|
| 1 | `benzsevern/goldenmatch` | `benseverndev-oss/goldenmatch` | Public | Active |
| 2 | `benzsevern/goldencheck` | `benseverndev-oss/goldencheck` | Public | Archived |
| 3 | `benzsevern/goldenflow` | `benseverndev-oss/goldenflow` | Public | Archived |
| 4 | `benzsevern/goldenpipe` | `benseverndev-oss/goldenpipe` | Public | Archived |
| 5 | `benzsevern/infermap` | `benseverndev-oss/infermap` | Public | Archived |
| 6 | `benzsevern/goldenmatch-extensions` | `benseverndev-oss/goldenmatch-extensions` | Public | Archived |
| 7 | `benzsevern/goldencheck-action` | `benseverndev-oss/goldencheck-action` | Public | Archived |
| 8 | `benzsevern/dbt-goldencheck` | `benseverndev-oss/dbt-goldencheck` | Public | Archived |
| 9 | `benzsevern/goldencheck-types` | `benseverndev-oss/goldencheck-types` | Public | Archived |
| 10 | `benzsevern/dqbench` | `benseverndev-oss/dqbench` | Public | Active |
| 11 | `benzsevern/devpilot` | `benseverndev-oss/devpilot` | Public | Active |
| 12 | `benzsevern/knowledge-base` | `benseverndev-oss/knowledge-base` | Public | Active |
| 13 | `benzsevern/goldenmatch-shell-company-network` | `benseverndev-oss/goldenmatch-shell-company-network` | Public | Active |
| 14 | `benzsevern/goldenmatch-wallet-attribution` | `benseverndev-oss/goldenmatch-wallet-attribution` | Public | Active |
| 15 | `benzsevern/goldenmatch-vuln-attribution` | `benseverndev-oss/goldenmatch-vuln-attribution` | Public | Active |
| 16 | `benzsevern/goldenmatch-sanctions-reconciliation` | `benseverndev-oss/goldenmatch-sanctions-reconciliation` | Public | Active |
| 17 | `benzsevern/golden-showcase` | `benseverndev-oss/golden-showcase` | **Private** | Active |
| 18 | `benzsevern/goldentoken` | `benseverndev-oss/goldentoken` | **Private** | Active |

## Why we moved

Performance benchmarking for the `gm.dedupe_df()` zero-config path needed
larger GitHub-hosted runners (`ubuntu-latest-large`, 64-core / 256 GB RAM)
to get measurements that aren't dominated by laptop variability or
Polars OOM-then-hang on the local machine.

GitHub's enterprise larger-runner model is **organization-scoped only**.
Even with a runner group set to "All organizations" and "Allow public
repositories" enabled, a personal-account repo (`benzsevern/<repo>`)
cannot consume those runners — the access grant has no path to reach
repos that don't sit under an org under the enterprise.

Transferring into an org under the `benseverndev` enterprise was the
only way to keep both:

- The runner-group grant working (org repos qualify).
- The repos as open-source artifacts under our control.

`benseverndev-oss` is the org created for this. The scope expanded
beyond `goldenmatch` to consolidate the full Golden Suite + companion
repos in one place, even though only the active repos directly benefit
from the runners today — archives moved for org tidiness, and the two
private repos moved to keep all Ben-Severn projects under one
enterprise-managed roof.

## What auto-redirects (GitHub-guaranteed)

GitHub permanent redirects keep these working indefinitely for all 18
transferred repos (the only known break point is if a new repo is
created at any of the old `benzsevern/<repo>` slots — that would
displace the redirect):

- **HTTPS clones of any old URL.** `git clone https://github.com/benzsevern/<repo>` still works (git follows the redirect).
- **GitHub REST + GraphQL API calls** against the old `owner/repo` paths. The response payload reflects the new owner.
- **PR / issue / commit / blob deep links.** `https://github.com/benzsevern/<repo>/pull/123` redirects to the equivalent under `benseverndev-oss`.
- **Raw content URLs** (`raw.githubusercontent.com/benzsevern/<repo>/...`) redirect.
- **PyPI release-source-link metadata** on already-published versions resolves to the new URL once GitHub's release storage is queried.

## What does NOT auto-redirect (action items)

| Surface | Status | Action |
|---|---|---|
| `ghcr.io/benzsevern/<image>:<tag>` container pulls | Old images stay where they were last pushed; future builds publish under `ghcr.io/benseverndev-oss/<image>` | Update any `docker pull` commands and Compose files. Existing tags still pullable until GitHub garbage-collects abandoned namespaces. |
| Smithery hosted-MCP listings (`smithery.ai/servers/benzsevern/<pkg>`) | Listing identity is tied to the old owner | Re-register or relink each listing under `smithery.ai/servers/benseverndev-oss/<pkg>` |
| MCP Registry namespace (`io.github.benzsevern/<pkg>`) | Registered identifier — does not follow GitHub redirects | Server.json `name` fields in this cleanup are bumped to `io.github.benseverndev-oss/<pkg>`. The next `publish-mcp.yml` run (on any release) will sync the registry. Old listings at `io.github.benzsevern/<pkg>` stay as orphans until manually removed. |
| GitHub Pages sites (`benzsevern.github.io/<repo>/...`) | The user-pages site at `benzsevern.github.io` stays at the personal account; project Pages for transferred repos rebuild under `benseverndev-oss.github.io/<repo>/` on next workflow run | Pages will redeploy under the new org domain. Old URLs become 404 once Pages rebuilds. Bookmarks against the old URL break. |
| Wiki repos (`benzsevern/<repo>.wiki.git`) | Transferred with each main repo automatically | Update any clone scripts to use `benseverndev-oss/<repo>.wiki.git` |
| Repo-level secrets (`PYPI_TOKEN`, `TEAMS_PR_WEBHOOK_BENZSEVERN_MJH`, `CLAUDE_CODE_OAUTH_*`, etc.) | Carried over with each transfer | No action needed |
| Org-level / enterprise secrets | Were never on the personal account; need to be added to `benseverndev-oss` separately | Add fresh as needed; workflows that consume them fail loudly until provisioned |
| Local clones | Continue to push/pull via the redirect | `git remote set-url origin https://github.com/benseverndev-oss/<repo>.git` recommended but not required |
| `package.json`, `pyproject.toml`, `server.json` `repository`/`homepage`/`urls` | Updated in this cleanup PR for this monorepo only | Other transferred repos have their own metadata to update — sibling repo PRs are out of scope here |
| Funding (`FUNDING.yml: github: benzsevern`) | Funding still flows to the personal account `benzsevern` (the user owns both the personal account and the org) | No change — leave as `github: benzsevern` |

## What changed in this monorepo cleanup

Search-and-replace `benzsevern/<repo>` → `benseverndev-oss/<repo>` for the
18 transferred repos, plus the related surfaces (Pages URLs, Smithery
URLs, ghcr.io namespaces, MCP registry IDs). Two preservation rules:

1. **`benzsevern-mjh`** is the user's work account, distinct from the
   personal `benzsevern` — preserved verbatim.
2. **`benzsevern@<email-domain>`** patterns are author/maintainer email
   addresses — preserved verbatim.
3. **`benzsevern.github.io` (bare, with no project suffix)** is the
   user's personal Pages site — preserved. Only project-Pages URLs of
   the transferred repos were rewritten.

In addition:

- `.github/workflows/publish-containers.yml` comment updated; the workflow
  itself uses `${{ github.repository_owner }}` dynamically, so future
  container pushes auto-route to the new org.
- `.github/workflows/pages.yml` comment updated; Pages will redeploy under
  the new URL on next run.
- `docs/sql-postgres.md`, `dbt/goldencheck/README.md`, `goldensuite-mcp/README.md`, `rust/extensions/README.md`, `examples/typescript/README.md`, and `README.md` had `ghcr.io/benzsevern/<image>` references updated to `ghcr.io/benseverndev-oss/<image>` for monorepo-published images.
- `CLAUDE.md` GitHub auth note widened: the `benzsevern` auth dance now
  covers both `benzsevern/*` AND `benseverndev-oss/*` repos (the org is
  owned by the personal account; same dance applies).
- `server.json` `name` fields for all five suite packages bumped to
  `io.github.benseverndev-oss/<pkg>`. The next release-triggered run of
  `publish-mcp.yml` syncs the registry; until then the listings still
  exist at the old identifier in the official MCP registry.

## Verification

```bash
# Should return 0 — no bare benzsevern/<repo> refs left for any transferred repo
grep -rEn "benzsevern/(goldenmatch|goldencheck|goldenflow|goldenpipe|infermap|dqbench|goldenmatch-extensions|goldencheck-action|dbt-goldencheck|goldencheck-types|devpilot|knowledge-base|goldenmatch-shell-company-network|goldenmatch-wallet-attribution|goldenmatch-vuln-attribution|goldenmatch-sanctions-reconciliation|golden-showcase|goldentoken)\\b" \
  --include="*.md" --include="*.toml" --include="*.json" --include="*.py" \
  --include="*.ts" --include="*.tsx" --include="*.yml" --include="*.yaml" \
  --include="*.txt" --include="*.sh" --include="*.ipynb" --include="Cargo.toml" \
  --exclude-dir=".claude" --exclude-dir="node_modules" --exclude-dir=".venv" \
  --exclude-dir="dist" --exclude-dir="build" --exclude-dir="_archive" | wc -l

# Should return >0 (preserved) — these are intentional
grep -rln "github: benzsevern" --include="*.yml" 2>&1 | wc -l   # FUNDING.yml
grep -rln "user benzsevern" --include="*.md" 2>&1 | wc -l       # gh auth notes
grep -rln "benzsevern-mjh" 2>&1 | wc -l                          # work account
```

## For downstream consumers

If you depend on any transferred repo via any of these patterns:

- **`pip install <package>`** — no change; PyPI package names are unchanged.
- **`npm install <package>`** — no change; npm package names are unchanged.
- **`docker pull ghcr.io/benzsevern/<image>`** — switch to `docker pull ghcr.io/benseverndev-oss/<image>` for new pulls. Existing tags pulled remain on local disk; CI scripts pulling fresh should update.
- **CI workflows in your repos that reference `benzsevern/<repo>` by URL** — update at convenience; redirects keep them working in the interim.
- **MCP clients consuming the hosted server at `goldenmatch-mcp-production.up.railway.app/mcp/`** — no change; the Railway deployment URL is independent of the GitHub URL.
- **Documentation bookmarks to `benzsevern.github.io/<repo>/...`** — switch to `benseverndev-oss.github.io/<repo>/...`. Old URLs become 404 once Pages rebuilds at the new domain.
- **MCP clients listing servers from the registry at `registry.modelcontextprotocol.io`** — old listings under `io.github.benzsevern/<pkg>` still resolve until they're manually deleted; new listings under `io.github.benseverndev-oss/<pkg>` appear after the next package release triggers `publish-mcp.yml`.

## Reverting (if ever needed)

A reverse transfer back to `benzsevern/<repo>` is possible at any time
via the same `POST /repos/{owner}/{repo}/transfer` REST API. GitHub
redirects continue to work in both directions, so a return move would
not break downstream consumers — but it would re-break enterprise
larger-runner access, so it would only make sense if the runner story
were resolved another way.
