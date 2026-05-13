# Security Policy

## Supported Versions

| Package          | Versions          |
| ---------------- | ----------------- |
| `goldenmatch`    | latest minor only |
| `goldencheck`    | latest minor only |
| `goldenflow`     | latest minor only |
| `goldenpipe`     | latest minor only |
| `infermap`       | latest minor only |
| `goldenmatch-js` | latest minor only |
| `goldenflow-js`  | latest minor only |
| `goldencheck-js` | latest minor only |
| `infermap-js`    | latest minor only |

The monorepo at `benzsevern/goldenmatch` hosts the Golden Suite (six Python
packages plus the TypeScript ports under `packages/typescript/`). Each
package ships its own version stream on PyPI / npm.

Security fixes ship in the next patch release of the affected package
only. Older minor versions do not receive security backports — pin and
upgrade.

## Reporting a Vulnerability

**Please do NOT open a public GitHub issue for security reports.**

Use one of these private channels instead:

1. **GitHub Private Security Advisory** (preferred): open one at
   <https://github.com/benzsevern/goldenmatch/security/advisories/new>.
   You can reference any of the Golden Suite packages from that one
   monorepo advisory.
2. **Email**: `ben@bensevern.dev` with `[security]` in the subject.

Please include:

- Which package + version is affected (e.g. `goldenmatch==1.14.0`).
- A minimal reproducer or description of the attack surface.
- Whether the issue is exploitable remotely or requires local access /
  a specific data shape.
- Your suggested severity (CVSS optional but appreciated).

### Response targets (best-effort, single-maintainer project)

| Stage                          | Target  |
| ------------------------------ | ------- |
| Acknowledge receipt            | 3 days  |
| Triage + severity confirmation | 7 days  |
| Fix landed on `main`           | 14 days for high/critical; best-effort otherwise |
| PyPI / npm release with fix    | within 7 days of fix landing on `main` |

These are targets, not SLAs. This is a personal open-source project
without a paid support contract; please don't treat the timeline as a
commitment.

## Coordinated Disclosure

If you intend to publish the vulnerability after a fix ships, please
coordinate the publication date with the maintainer so users have time
to upgrade. Credit will be given in the release notes unless you ask
to remain anonymous.

## Scope

In scope:

- Code execution, sandbox escape, or privilege escalation in any
  Golden Suite package or the published CLI tools.
- Data leakage via crash, log, or error output that should not have
  surfaced upstream data.
- Authentication / authorization bypass in the MCP, A2A, or REST
  server surfaces (`goldenmatch agent-serve`, `goldenmatch serve`,
  `goldenmatch mcp-serve`).
- PPRL protocol weaknesses (`goldenmatch.pprl`) — e.g. bloom-filter
  parameter choices that leak input identity beyond the documented
  security level.

Out of scope (not a vulnerability):

- Resource exhaustion from passing pathologically large or adversarial
  input to a CLI invocation under your own user account. Use a
  resource-cgroup wrapper.
- Issues that require an attacker to already have shell access on the
  host running the tool.
- Vulnerabilities in third-party dependencies — please report those to
  the upstream project. Dependabot keeps our pinned versions current.
- Issues in remote MCP server deployments (`*.up.railway.app`) — these
  are demo environments without uptime guarantees; report the
  underlying code issue, not the deployment.
