# GoldenMatch product analytics

GoldenMatch can send **anonymous, opt-in** usage events so maintainers can see
real engagement (which features/surfaces get used, retention) — signal that PyPI
download counts can't show. It is built for a privacy-positioned tool that ships
PPRL, so the guarantees are strict.

## It is OFF by default

Nothing is collected unless **both** of these are set:

```bash
export GOLDENMATCH_ANALYTICS=1          # explicit opt-in (off by default)
export POSTHOG_API_KEY=phc_xxxxxxxx     # the destination project key
# optional: export POSTHOG_HOST=https://us.i.posthog.com
```

To turn it off again: unset `GOLDENMATCH_ANALYTICS` (or set it to `0`). To reset
your anonymous id, delete `~/.goldenmatch/analytics_id`.

On a user's machine this is strictly opt-in. On the project's **own** hosted
services (the docs site and the Railway MCP deployment) the flag is set
server-side — those are owned properties, not user machines.

## What is collected

Anonymous, coarse, non-identifying scalars only. The complete allow-list of
event properties that can ever leave the process (`goldenmatch/core/analytics.py`,
`_ALLOWED_PROPS`):

| property | example | meaning |
|---|---|---|
| `surface` | `library` / `cli` / `mcp` | where the call came from |
| `command` | `dedupe` | CLI command name (our static table) |
| `tool` | `find_duplicates` | MCP tool name (our static table) |
| `backend` | `bucket` | execution backend chosen |
| `row_bucket` | `10K-100K` | **scale band — never the exact count** |
| `duration_bucket` | `1-10s` | wall-time band |
| `result_bucket` | `1K-10K` | cluster-count band |
| `scorer_count` / `matchkey_count` | `2` | config shape (integers) |
| `native_available` | `true` | is the rust kernel present |
| `planning_effort` | `normal` | auto-config effort tier |
| `config_source` / `had_reference` / `mode` | scalar | small internal enums |
| `gm_version` / `python_version` / `os` | `1.30.0` | environment |

Plus a random `distinct_id` (a UUID4 stored locally — **no** hostname, username,
MAC, or any hardware fingerprint).

## What is NEVER collected

- No record values, no column names, no file paths, no queries — **no data**.
- The MCP capture sends the **tool name only**, never the tool `arguments`.
- Enforced in code, not just by convention: `capture()` accepts only the
  allow-listed keys above and additionally drops any value that is path-like
  (`/` or `\`) or longer than 64 chars. Callers pass pre-bucketed scalars.

## Engineering guarantees

- **Fail-open.** Analytics never raises and never blocks a run; the HTTP post is
  fire-and-forget on a daemon thread with a 2s timeout, all errors swallowed.
- **Privacy is tested, not promised.** `tests/test_analytics.py` is the contract:
  default-off, allow-list enforcement, path-like/over-long value rejection,
  anonymous-id format, and fail-open are all asserted. A regression fails CI.

> Not to be confused with **controller telemetry** (`web/controller_telemetry.py`),
> which is the auto-config introspection output returned to the *caller* — that
> is a feature surface, not usage analytics, and sends nothing anywhere.
