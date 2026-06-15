<!--
Keep PRs scoped. CI is path-filtered, so only the areas you touch will run.
Release notes / changelog text must be ASCII (no em-dashes) - the Release API
rejects them.
-->

## What and why

<!-- One or two sentences: what changed and the reason. Link the issue if any. -->

## Changes

-

## Testing

<!-- Commands you ran (e.g. `uv run pytest packages/python/<pkg>`,
`cargo test`, `pnpm turbo run test`) and the result. -->

## Checklist

- [ ] Tests added/updated and passing locally for the changed package
- [ ] `pre-commit run --all-files` is clean (ruff, whitespace, no secrets)
- [ ] Package `CHANGELOG.md` updated if behavior changed
- [ ] No secrets, tokens, or `.env` files committed
- [ ] Docs / `CLAUDE.md` updated if a workflow or gotcha changed
