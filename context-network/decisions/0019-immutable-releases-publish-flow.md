# 0019 — Immutable-releases publish flow (publish-goldenmatch.yml)

**Status:** accepted • **Shipped:** PR #1063 (2026-06-18)

## Context
The repo has GitHub's **immutable-releases** setting ON: once a release is
published, its assets are sealed and no more can be uploaded. The prior
`publish-goldenmatch.yml` triggered on `release: published` (a human created
the release) and then attached the cosign-signed wheel/sdist + `.sigstore`
bundles to that already-published release. Under immutable releases the attach
step fails: `Cannot upload asset ... to an immutable release`. The v2.1.0 run
hit exactly this — PyPI publish + cosign sign + provenance attest all succeeded,
and the attach was the only red step, so 2.1.0 shipped to PyPI but its GitHub
Release carries no signed bundles. Left unfixed it would fail every release.

## Decision
Make the workflow OWN the release lifecycle so assets are uploaded while the
release is still a draft (the only window an immutable release accepts them):

- Trigger on **push of a bare `v*` tag** (prefixed tags like `goldenmatch-js-v*`
  don't match `v*`); `workflow_dispatch` takes a required `tag` input for
  retro-publish.
- Pipeline: build + stage web → PyPI publish (`skip-existing`) → cosign sign +
  build-provenance attest (now unconditional, not gated on the release event) →
  extract notes from the `## [<version>]` CHANGELOG section → create a **draft**
  release with the assets attached → `gh release edit --draft=false --latest`.

New SOP: **cut a release by pushing the tag only** — `git push origin vX.Y.Z`.
Do NOT `gh release create` by hand; a human-published release is immutable
immediately and the workflow can't attach to it.

## Consequence
Future releases attach the signed wheel/sdist/sigstore bundles correctly, and
release notes are single-sourced from the package CHANGELOG. v2.1.0's release
stays bundle-less (already sealed; not retro-fixable). The `attestations: true`
input remains ignored because `PYPI_TOKEN` (password) disables trusted
publishing — pre-existing and benign. Alternative considered and rejected:
turning OFF the immutable-releases repo setting to keep the old attach-after-
publish flow — keeping immutability and fixing the flow is the better trade.
Mirrored in the root `CLAUDE.md` "Post-fold GitHub Actions" note.

---
**Classification:** decision/accepted • **Last updated:** 2026-06-18
