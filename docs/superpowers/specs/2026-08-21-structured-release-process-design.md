# Structured release process

Date: 2026-08-21
Status: design approved, plan pending

## Problem

Releases are cut when the maintainer decides enough progress has accumulated.
The mechanics are well-specified -- ADR 0019 settled them, and 40+ publish
workflows implement them -- but the *decision* is not. Every release requires
two judgement calls: is this enough, and what number does it get.

The cost is the judgement itself, not the frequency. Measured over the five
weeks 2026-07-13 to 2026-08-20, goldenmatch cut 13 releases (one every 2-4
days), and 100+ releases went out across all package prefixes in 90 days. The
process is not producing rare releases; it is producing frequent ones, each
preceded by a decision nobody wants to make.

Two symptoms visible in the data:

- **The version number carries almost no signal.** Of those 13 releases, 11 were
  MINOR and 2 were PATCH. A consumer seeing 3.11 -> 3.12 learns nothing about
  whether it matters to them.
- **3.5 and 3.9 do not exist.** Gaps of that shape are abandoned cuts.

## Scope

All ~40 publish targets: Python, npm and native/Rust. Approximately 25 distinct
tag prefixes are already in use -- 91 bare `v*` (goldenmatch), 24
`goldenmatch-js`, 23 `golden-suite`, 15 each `goldenmatch-native` and
`goldencheck`, then a long tail down to 2.

These are not independent subsystems; they are one shape repeated, which a
single manifest can express declaratively. Rollout is nonetheless staged (see
Rollout) so a configuration error costs one version stream rather than forty.

## What does not change

ADR 0019 stands in full. A release is still cut by pushing a tag; the publish
workflow still owns the release lifecycle, creating the release as a draft so
signed assets can attach before immutability seals it. Every existing publish
workflow keeps working untouched, because each triggers on a tag and tags keep
arriving.

This design adds a decision layer above that machinery. It replaces nothing.

## The rule

Version derives from conventional-commit types since the package's last tag:

| Commits since last tag | Bump |
|---|---|
| any `feat` | MINOR |
| only `fix` / `perf` | PATCH |
| only `docs` / `test` / `chore` / `ci` / `build` / `style` / `refactor` / `bench` / `diag` | no release |
| any `!` suffix or `BREAKING CHANGE:` footer | MAJOR |

This is viable because the repo is already structured: **174 of the last 200
commits on main (87%) match the conventional format**, with the type
distribution `fix` 70, `feat` 41, `docs` 18, `perf` 16, `chore` 12, `ci` 9,
`build` 4, `test` 4.

The 13% that do not match are not sloppiness -- they are a deliberate wider
vocabulary: `bench(spark):`, `diag(fs):`, `diag(scoring):`, and `release:`.
These map explicitly. `bench` and `diag` are no-bump: they are instrumentation
and diagnostics, not consumer-facing change. `release` is excluded outright, as
it is the bot's own commit.

**Expected behaviour change, and the point of the exercise.** With 70 fixes to
41 feats, deriving bumps produces considerably more PATCH releases than the
current process, which ships almost everything as MINOR. Version numbers begin
to distinguish "a bug you may have hit was fixed" from "there is something new
here".

## The standing PR

`release-please` in manifest mode. On every merge to main it recomputes, per
package, what the next version would be, and maintains one open pull request
per package titled `chore(main): release <package> <version>`. That PR carries
the version-file bumps and a generated CHANGELOG section. Merging it tags the
release, which triggers the existing publish workflow.

Leaving the PR open costs nothing; it accumulates. Cutting a release becomes
merging a PR that already shows exactly what would ship.

Configuration lives in two files at the repo root:

- `release-please-config.json` -- per-package release type, component name, tag
  format, changelog path, and extra version-carrying files.
- `.release-please-manifest.json` -- the current version of each package, seeded
  from the existing tags.

Per-package specifics the config must express:

- **goldenmatch uses bare `v*` tags**; every other package is prefixed. This is
  `include-component-in-tag: false` for goldenmatch and `true` elsewhere.
  **VERIFIED 2026-08-21** against release-please's own config schema
  (`schemas/config.json`, read via the GitHub API): `packages` is typed
  `additionalProperties: {"$ref": "#/definitions/ReleaserConfigOptions"}`, the
  same definition applied at the root, and `include-component-in-tag` is a
  property of `ReleaserConfigOptions`. It is therefore settable **per package**,
  so goldenmatch keeps its bare `v*` tags alongside prefixed siblings in one
  manifest.

  Recorded because the first answer was the opposite: `manifest-releaser.md`
  states the option is root-level and "applies uniformly across all packages".
  Had that been believed, goldenmatch would have had to change its tag format
  (breaking `publish-goldenmatch.yml`'s `v*` trigger and orphaning 91 tags) or
  sit outside the scheme. The schema is authoritative; the prose is not.
- **Three release types**: `python` (pyproject.toml), `node` (package.json),
  `rust` (Cargo.toml).
- **Non-standard version carriers** need `extra-files`: `__init__.py` for the
  Python packages, and `server.json` for the MCP-registry packages.

## The golden-suite lockstep

The existing rule -- a member release drags golden-suite's floor -- has no
native equivalent in release-please. It becomes an explicit workflow step that
runs after a member's release PR merges: bump the member's floor in
`golden-suite`'s dependency pins, which then flows into golden-suite's own
standing PR through the normal path.

Encoding it is deliberate. It is currently tribal knowledge, and it is exactly
the class of rule that gets forgotten during an out-of-hours release.

## The MAJOR escape hatch

**Zero of the last 200 commits carry a `!` marker or a `BREAKING CHANGE:`
footer**, yet 3.0.0 was genuinely breaking -- it evicted Polars from the
dependency tree. A purely derived scheme would therefore never cut a major
again, and would fail silently: a breaking change would ship as a minor and
nobody would notice until a consumer broke.

This is the highest-risk part of the design. Two guards:

1. `Release-As: 4.0.0` in a commit footer forces a specific version regardless
   of derivation.
2. `CONTRIBUTING.md` gains a short section on marking breaking changes, so the
   convention is written down rather than assumed.

Neither guard is automatic. The residual risk is accepted and stated here so it
is a known limitation rather than a surprise.

## Prerequisite: consolidate the duplicate publish workflows

Decided 2026-08-21, after measuring the machinery this design sits on.

The 39 publish workflows total 4,087 lines in four tiers: 18 thin callers
(31-38 lines) that already delegate to `_publish-pypi.yml` / `_publish-js.yml`;
9 native workflows (142-156 lines) that are ~90% identical to each other; a
~82%-identical pair at 175 lines (`goldenfuzz`, `goldenphonetic`); and 4
genuinely bespoke ones (`mcp`, `spark-jar`, `containers`, `pg`) that stay as
they are. `publish-goldenmatch.yml` is also 175 lines but genuinely distinct --
it carries the ADR-0019 draft-release and cosign flow.

The consolidation this repo already started is stalled halfway: the reusable
pattern exists and 18 packages use it; 11 near-duplicates never migrated.
Collapsing them removes roughly 1,100 of 4,087 lines (~27% of the release
machinery).

Between two native workflows, exactly three things vary: the workflow `name`,
the `MANIFEST` path to the crate's `Cargo.toml`, and a tag prefix repeated in
FOUR separate `if:` conditions. Across nine files that is 36 hand-maintained
copies of a tag prefix -- the bug surface that makes this machinery feel
unmaintainable.

**This lands before the release manifest reaches those packages**, so each is
touched once rather than twice, and wave 3 configures thin callers rather than
divergent workflows. It is also lower risk in isolation: it changes no version
streams, and a mistake surfaces as a failed publish rather than a wrong version.

## Rollout

Three waves, one config file, staged enablement, after the prerequisite above:

1. **goldenmatch + golden-suite.** Proves version derivation against real
   commits, the bare-tag component, and the lockstep step.
2. **Remaining Python packages** -- goldencheck, goldenflow, goldenpipe,
   infermap, goldenanalysis, goldengraph, goldenmatch-kg, goldenfuzz,
   goldenphonetic, goldensuite-mcp.
3. **JS and native packages.**

Each wave is a separate PR adding packages to the manifest. A misconfiguration
in wave 1 affects one version stream.

## Known limitations

- **Generated release notes read worse than hand-written ones.** The current
  notes are unusually good: #2703 explained why the bump was MINOR rather than
  PATCH and carried measured before/after figures. Derived notes are a
  categorised commit list. `changelog-sections` improves the grouping, and the
  PR body is editable before merging, but narrative is lost by default. This is
  a real trade, accepted knowingly.
- **The 13% of commits outside the type map do not appear in changelogs**
  until their types are added to the configuration.
- **The MAJOR guard is convention, not enforcement** (see above).
- **Nothing here validates that a release is *safe*** -- only that it is
  correctly numbered and described. Readiness gating was considered and
  excluded: it is a different problem, and CI already gates merges.
