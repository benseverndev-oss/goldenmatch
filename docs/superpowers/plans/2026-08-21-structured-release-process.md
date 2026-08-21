# Structured Release Process Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the "have I made enough progress?" release judgement with a standing release PR per package whose version is derived from conventional-commit types.

**Architecture:** `release-please` in manifest mode watches merges to `main` and keeps one open release PR per package, carrying the version-file bumps and a generated CHANGELOG. Merging that PR pushes a tag, and the repo's existing 39 publish workflows react to tags exactly as they do today. This adds a decision layer; it replaces no publishing machinery.

**Tech Stack:** `release-please` (googleapis) via `googleapis/release-please-action`, GitHub Actions, JSON config.

**Spec:** `docs/superpowers/specs/2026-08-21-structured-release-process-design.md`

## Prerequisite, already done

The spec names a prerequisite: consolidate the duplicated publish workflows so
this manifest configures thin callers rather than divergent copies. That landed
before this plan -- #2705 (nine native workflows onto a shared template, -949
lines) merged, and #2706 (goldenfuzz/goldenphonetic, -202 lines) followed. No
task here repeats it.

## Global Constraints

- **ADR 0019 stands.** A release is cut by pushing a tag; `publish-goldenmatch.yml` owns the release lifecycle and creates the release as a **draft** so signed assets attach before immutability seals it. Never `gh release create` by hand.
- **goldenmatch uses bare `v*` tags**; every other package is prefixed (`<component>-v*`). Verified against release-please's config schema: `packages` entries are typed `additionalProperties: {"$ref": "#/definitions/ReleaserConfigOptions"}`, and `include-component-in-tag` is a property of that definition, so it is settable **per package**.
- **Bump rule:** any `feat` → MINOR; only `fix`/`perf` → PATCH; only `docs`/`test`/`chore`/`ci`/`build`/`style`/`refactor`/`bench`/`diag` → no release; `!` or `BREAKING CHANGE:` → MAJOR.
- **`bench` and `diag` are no-bump** — they are this repo's own commit types for instrumentation and diagnostics, not consumer-facing change. `release` is excluded outright (it is the bot's own commit).
- **Zero of the last 200 commits carry a breaking marker**, so MAJOR never fires on its own. `Release-As:` is the only escape hatch.
- **golden-suite pins floors as `>=MAJOR.MINOR`** (e.g. `>=3.14`, not `>=3.14.0`), and every pin carries an inline comment explaining why that floor exists. Those comments are load-bearing documentation and must survive any automated bump.
- Do not run the full pytest suite locally — it OOMs under xdist. Run the targeted commands each task names.

---

### Task 1: Prove the derivation against real history before configuring anything

**Files:**
- Create: `scripts/derive_next_version.py`
- Test: `scripts/test_derive_next_version.py`

**Interfaces:**
- Produces: `derive_bump(commit_subjects: list[str]) -> str | None` returning `"major"`, `"minor"`, `"patch"`, or `None` for no release.

The spec asserts the repo's commit history can drive versioning. That claim is measurable and nothing should be built on it unmeasured. This task makes the rule executable and runs it over real history, so wave 1 starts from evidence.

- [ ] **Step 1: Write the failing test**

```python
"""The bump rule from the release-process spec, pinned to this repo's vocabulary."""
from __future__ import annotations

from derive_next_version import derive_bump


def test_feat_gives_minor():
    assert derive_bump(["feat(identity): add a thing", "fix(x): y"]) == "minor"


def test_only_fixes_give_patch():
    assert derive_bump(["fix(a): one", "perf(b): two"]) == "patch"


def test_docs_and_chores_give_no_release():
    assert derive_bump(["docs: readme", "chore(ci): bump", "test: add"]) is None


def test_repo_specific_types_are_no_bump():
    """bench/diag are this repo's own types: instrumentation, not product change."""
    assert derive_bump(["bench(spark): size the lane", "diag(fs): show refits"]) is None


def test_bang_gives_major():
    assert derive_bump(["feat(api)!: drop the polars dependency"]) == "major"


def test_breaking_change_footer_gives_major():
    assert derive_bump(["feat(api): x\n\nBREAKING CHANGE: drops polars"]) == "major"


def test_release_commits_are_ignored():
    """The bot's own commit must not itself trigger a release."""
    assert derive_bump(["release: goldenmatch 3.14.0"]) is None


def test_unknown_types_are_ignored_not_crashed():
    assert derive_bump(["wip whatever", "merge branch 'x'"]) is None
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /d/show_case/<worktree>
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_derive_next_version.py -q
```

Expected: `ModuleNotFoundError: No module named 'derive_next_version'`

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""Derive a semver bump from conventional-commit subjects.

The rule lives in docs/superpowers/specs/2026-08-21-structured-release-process-design.md.
This module exists so the rule is executable and testable BEFORE it is encoded
in release-please config, where it would only be observable by watching PRs
appear.
"""
from __future__ import annotations

import re

_HEADER = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?: ", re.IGNORECASE
)

MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf"}
# This repo's own vocabulary beyond the conventional set. bench/diag are
# instrumentation and diagnostics; release is the bot's own commit.
NO_BUMP_TYPES = {
    "docs", "test", "chore", "ci", "build", "style", "refactor",
    "bench", "diag", "release",
}


def derive_bump(commit_subjects: list[str]) -> str | None:
    """Return 'major' | 'minor' | 'patch' | None for a list of commit messages."""
    bump: str | None = None
    for message in commit_subjects:
        header = message.splitlines()[0] if message else ""
        match = _HEADER.match(header)
        if "BREAKING CHANGE:" in message:
            return "major"
        if not match:
            continue
        if match.group("bang"):
            return "major"
        ctype = match.group("type").lower()
        if ctype in MINOR_TYPES:
            bump = "minor"
        elif ctype in PATCH_TYPES and bump != "minor":
            bump = "patch"
    return bump
```

- [ ] **Step 4: Run it and watch it pass**

Expected: 8 passed.

- [ ] **Step 5: Run the rule over real history and record what it says**

```bash
git log origin/main --format='%s' -200 > /tmp/subjects.txt
/d/show_case/goldenmatch/.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'scripts')
from derive_next_version import derive_bump
subs=[l.rstrip() for l in open('/tmp/subjects.txt',encoding='utf-8') if l.strip()]
print('200-commit window ->', derive_bump(subs))
"
git log origin/main --format='%s' \$(git describe --tags --abbrev=0 --match 'v*')..origin/main > /tmp/since.txt
/d/show_case/goldenmatch/.venv/Scripts/python.exe -c "
import sys; sys.path.insert(0,'scripts')
from derive_next_version import derive_bump
subs=[l.rstrip() for l in open('/tmp/since.txt',encoding='utf-8') if l.strip()]
print('since last v* tag ->', derive_bump(subs), f'({len(subs)} commits)')
"
```

Record both answers in the commit message. The second is the bump v3.15.0 would receive — the spec predicts MINOR (two `feat` commits landed since v3.14.0). If it says anything else, **stop and report**: either the rule or the spec's reading of history is wrong, and that must be settled before configuring release-please.

- [ ] **Step 6: Commit**

```bash
git add scripts/derive_next_version.py scripts/test_derive_next_version.py
git commit -m "feat(release): make the version-derivation rule executable and testable"
```

---

### Task 2: Wave 1 config — goldenmatch and golden-suite

**Files:**
- Create: `release-please-config.json`
- Create: `.release-please-manifest.json`
- Create: `.github/workflows/release-please.yml`

**Interfaces:**
- Consumes: nothing from Task 1 at runtime (that module documents and tests the rule; release-please implements it internally).
- Produces: the two config files every later wave appends packages to.

- [ ] **Step 1: Read the current versions from source, not from memory**

```bash
rg -n '^version' packages/python/goldenmatch/pyproject.toml packages/python/golden-suite/pyproject.toml
```

At time of writing these are `3.14.0` and `0.5.1`. **Use whatever the command prints**, not these values — main moves.

- [ ] **Step 2: Write the manifest**

`.release-please-manifest.json`:

```json
{
  "packages/python/goldenmatch": "3.14.0",
  "packages/python/golden-suite": "0.5.1"
}
```

- [ ] **Step 3: Write the config**

`release-please-config.json`:

```json
{
  "$schema": "https://raw.githubusercontent.com/googleapis/release-please/main/schemas/config.json",
  "separate-pull-requests": true,
  "changelog-sections": [
    { "type": "feat", "section": "Features" },
    { "type": "fix", "section": "Bug Fixes" },
    { "type": "perf", "section": "Performance" },
    { "type": "docs", "section": "Documentation", "hidden": true },
    { "type": "test", "section": "Tests", "hidden": true },
    { "type": "chore", "section": "Chores", "hidden": true },
    { "type": "ci", "section": "CI", "hidden": true },
    { "type": "build", "section": "Build", "hidden": true },
    { "type": "refactor", "section": "Refactoring", "hidden": true },
    { "type": "style", "section": "Style", "hidden": true },
    { "type": "bench", "section": "Benchmarks", "hidden": true },
    { "type": "diag", "section": "Diagnostics", "hidden": true }
  ],
  "packages": {
    "packages/python/goldenmatch": {
      "release-type": "python",
      "package-name": "goldenmatch",
      "include-component-in-tag": false,
      "changelog-path": "CHANGELOG.md",
      "extra-files": [
        "goldenmatch/__init__.py",
        { "type": "json", "path": "server.json", "jsonpath": "$.version" },
        { "type": "json", "path": "server.json", "jsonpath": "$.packages[0].version" }
      ]
    },
    "packages/python/golden-suite": {
      "release-type": "python",
      "package-name": "golden-suite",
      "component": "golden-suite",
      "include-component-in-tag": true,
      "changelog-path": "CHANGELOG.md",
      "extra-files": ["golden_suite/__init__.py"]
    }
  }
}
```

Two details that are load-bearing:

- `include-component-in-tag: false` on goldenmatch produces the bare `v3.15.0` tag that `publish-goldenmatch.yml` triggers on. Setting it true would produce `goldenmatch-v3.15.0`, which **no workflow listens for** — the release would appear and nothing would publish.
- `server.json` carries the version in **two** places (`$.version` and `$.packages[0].version`). Both entries are required; bumping one leaves the MCP registry sync publishing a mismatched version.

- [ ] **Step 4: Write the workflow**

`.github/workflows/release-please.yml`:

```yaml
name: release-please

# Maintains one standing release PR per package. Merging that PR pushes the
# package's tag, which the existing publish-*.yml workflows react to -- this
# workflow publishes nothing itself.
on:
  push:
    branches: [main]
  workflow_dispatch:

permissions:
  contents: write
  pull-requests: write

jobs:
  release-please:
    runs-on: ubuntu-latest
    steps:
      - uses: googleapis/release-please-action@a02a34c4d625f9be7cb89156071d8567266a2445  # v4.1.3
        with:
          config-file: release-please-config.json
          manifest-file: .release-please-manifest.json
```

- [ ] **Step 5: Validate the config against the schema before pushing**

```bash
/d/show_case/goldenmatch/.venv/Scripts/python.exe -c "
import json,urllib.request
cfg=json.load(open('release-please-config.json',encoding='utf-8'))
man=json.load(open('.release-please-manifest.json',encoding='utf-8'))
missing=[p for p in cfg['packages'] if p not in man]
assert not missing, f'packages in config but not manifest: {missing}'
extra=[p for p in man if p not in cfg['packages']]
assert not extra, f'packages in manifest but not config: {extra}'
print('config/manifest package sets agree:', sorted(man))
"
/d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest scripts/test_workflow_yaml.py -q
```

Expected: the assertion prints both package paths, and the workflow YAML gate passes.

- [ ] **Step 6: Commit**

```bash
git add release-please-config.json .release-please-manifest.json .github/workflows/release-please.yml
git commit -m "feat(release): wave 1 -- standing release PRs for goldenmatch and golden-suite"
```

---

### Task 3: Verify wave 1 on a branch before it can touch main

**Files:**
- Modify: none (verification only)

release-please only acts on `push: main`, so merging wave 1 is itself the first live test. That is the wrong order. This task proves the config off-main first.

- [ ] **Step 1: Dry-run the config with release-please's own CLI**

```bash
npx --yes release-please@17 release-pr \
  --repo-url=benseverndev-oss/goldenmatch \
  --config-file=release-please-config.json \
  --manifest-file=.release-please-manifest.json \
  --target-branch=main \
  --dry-run \
  --token="$(gh auth token)"
```

- [ ] **Step 2: Read what it says it would do**

Confirm, for goldenmatch:
- the proposed version matches Task 1 Step 5's `since last v* tag` answer
- the proposed **tag is bare** (`v3.15.0`), not `goldenmatch-v3.15.0`
- the changelog body contains the `feat`/`fix` entries and none of the hidden types

If the tag is prefixed, `include-component-in-tag` is not doing what the schema implies. **Stop and report** — that is the assumption the spec flagged, and a wrong answer here changes wave 1's shape.

- [ ] **Step 3: Record the dry-run output in the PR body**

Paste the proposed version, tag and changelog section. This is the evidence that the config does what the spec claims, and it is the only such evidence available before merge.

- [ ] **Step 4: Commit nothing; open the wave-1 PR**

```bash
gh pr create --base main --title "feat(release): wave 1 -- standing release PRs for goldenmatch and golden-suite" --body "<dry-run output + what to watch for on merge>"
```

---

### Task 4: The golden-suite lockstep

**Files:**
- Create: `scripts/bump_suite_floor.py`
- Test: `scripts/test_bump_suite_floor.py`
- Modify: `.github/workflows/release-please.yml`

**Interfaces:**
- Produces: `bump_floor(text: str, package: str, new_version: str) -> str` returning the pyproject text with only that package's floor raised.

The rule — a member release drags golden-suite's floor — is tribal knowledge today and has no release-please equivalent.

- [ ] **Step 1: Write the failing test**

```python
"""golden-suite floor bumps must not damage the reasons written beside them."""
from __future__ import annotations

from bump_suite_floor import bump_floor

LINE = (
    '    "goldenmatch[polars]>=3.14",        # entity resolution: dedupe, match, '
    "golden records (>=3.14: distributed Fellegi-Sunter 4.13x faster at 250M)\n"
)


def test_raises_the_floor_to_major_minor_only():
    """The convention is >=3.15, never >=3.15.0 -- match what is already there."""
    out = bump_floor(LINE, "goldenmatch", "3.15.0")
    assert '"goldenmatch[polars]>=3.15"' in out


def test_preserves_the_extras_marker():
    out = bump_floor(LINE, "goldenmatch", "3.15.0")
    assert "[polars]" in out


def test_preserves_the_trailing_comment_verbatim():
    """Those comments explain WHY a floor exists. Losing them loses the reason."""
    out = bump_floor(LINE, "goldenmatch", "3.15.0")
    assert "distributed Fellegi-Sunter 4.13x faster at 250M" in out


def test_leaves_other_packages_untouched():
    text = LINE + '    "goldencheck[polars]>=3.5",   # data validation\n'
    out = bump_floor(text, "goldenmatch", "3.15.0")
    assert '"goldencheck[polars]>=3.5"' in out


def test_never_lowers_a_floor():
    """A re-run or an out-of-order event must not walk the floor backwards."""
    out = bump_floor(LINE, "goldenmatch", "3.13.0")
    assert '"goldenmatch[polars]>=3.14"' in out
```

- [ ] **Step 2: Run it and watch it fail**

Expected: `ModuleNotFoundError: No module named 'bump_suite_floor'`

- [ ] **Step 3: Implement**

```python
#!/usr/bin/env python3
"""Raise a golden-suite dependency floor without touching anything else on the line.

golden-suite pins members as `>=MAJOR.MINOR` and writes the REASON for each floor
in a trailing comment. A naive bump destroys that reason, which is the most
valuable part of the line. This rewrites only the version.
"""
from __future__ import annotations

import re


def bump_floor(text: str, package: str, new_version: str) -> str:
    major, minor, *_ = new_version.split(".")
    target = (int(major), int(minor))
    pattern = re.compile(
        rf'("{re.escape(package)}(?:\[[^\]]*\])?>=)(\d+)\.(\d+)(?:\.\d+)?"'
    )

    def replace(m: re.Match[str]) -> str:
        current = (int(m.group(2)), int(m.group(3)))
        if current >= target:
            return m.group(0)  # never walk a floor backwards
        return f'{m.group(1)}{major}.{minor}"'

    return pattern.sub(replace, text)
```

- [ ] **Step 4: Run it and watch it pass**

Expected: 5 passed.

- [ ] **Step 5: Write the CLI that the workflow actually calls**

`bump_floor` is the pure function; the workflow needs something that maps
release-please's `paths_released` output onto golden-suite's pyproject. Create
`scripts/apply_suite_floors.py`:

```python
#!/usr/bin/env python3
"""Raise golden-suite's floors for every member released in this run.

release-please emits `paths_released` (a JSON array of package paths) and
`releases_created`. This maps those paths to package names, reads each one's
just-released version from the manifest, and rewrites only the floor.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

from bump_suite_floor import bump_floor

SUITE = pathlib.Path("packages/python/golden-suite/pyproject.toml")
MANIFEST = pathlib.Path(".release-please-manifest.json")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--released", required=True,
                    help="release-please paths_released output (JSON array)")
    args = ap.parse_args(argv)

    try:
        paths = json.loads(args.released or "[]")
    except json.JSONDecodeError:
        print(f"::error::could not parse paths_released: {args.released!r}",
              file=sys.stderr)
        return 1
    if not paths:
        print("no packages released; nothing to do")
        return 0

    versions = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = original = SUITE.read_text(encoding="utf-8")
    for path in paths:
        # golden-suite does not pin itself.
        if path.endswith("/golden-suite"):
            continue
        version = versions.get(path)
        if not version:
            print(f"::warning::{path} released but absent from the manifest")
            continue
        package = path.rsplit("/", 1)[-1]
        text = bump_floor(text, package, version)

    if text == original:
        print("no floor changed")
        return 0
    SUITE.write_text(text, encoding="utf-8")
    print(f"raised floors in {SUITE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

Note it writes the file but does not commit. The next release-please run picks
the change up through golden-suite's own standing PR, which is the whole point:
the floor bump is reviewable rather than silently pushed to main.

- [ ] **Step 6: Wire it into the workflow**

Append to `.github/workflows/release-please.yml`, after the release-please step:

```yaml
      # A member release drags golden-suite's floor. release-please has no
      # native equivalent, and leaving it manual is how it gets forgotten
      # out-of-hours. Only the version is rewritten; the reason beside it stays.
      - uses: actions/checkout@df4cb1c069e1874edd31b4311f1884172cec0e10  # v6
        if: steps.release.outputs.releases_created == 'true'
      - name: Raise golden-suite floors for any member released in this run
        if: steps.release.outputs.releases_created == 'true'
        env:
          RELEASES: ${{ steps.release.outputs.paths_released }}
        run: python scripts/apply_suite_floors.py --released "$RELEASES"
```

Give the release-please step `id: release` so those outputs resolve.

- [ ] **Step 7: Commit**

```bash
git add scripts/bump_suite_floor.py scripts/test_bump_suite_floor.py scripts/apply_suite_floors.py .github/workflows/release-please.yml
git commit -m "feat(release): encode the golden-suite lockstep rather than remembering it"
```

---

### Task 5: Document the MAJOR escape hatch

**Files:**
- Modify: `CONTRIBUTING.md`

The single most likely silent failure in this design: **zero of the last 200 commits carry a breaking marker**, yet 3.0.0 genuinely was breaking. Derived versioning will therefore never cut a MAJOR on its own, and nothing will complain.

- [ ] **Step 1: Add the section**

```markdown
## Marking a breaking change

Version numbers are derived from commit types, so a breaking change is only a
MAJOR if you say so. Two ways:

    feat(api)!: drop the Polars dependency

or a footer:

    BREAKING CHANGE: goldenmatch no longer installs Polars by default

**This does not happen by itself.** Across the 200 commits before this process
was adopted, none carried either marker -- and 3.0.0 (the Polars eviction) was
genuinely breaking. If you ship a break without marking it, it goes out as a
MINOR and nobody finds out until a consumer does.

To force a specific version regardless of derivation, put `Release-As: 4.0.0`
in a commit footer.
```

- [ ] **Step 2: Commit**

```bash
git add CONTRIBUTING.md
git commit -m "docs(contributing): how to mark a breaking change, and why it will not happen by itself"
```

---

### Task 6: Wave 2 — the remaining Python packages

**Files:**
- Modify: `release-please-config.json`
- Modify: `.release-please-manifest.json`

Packages: `goldencheck`, `goldencheck-types`, `goldenflow`, `goldenpipe`, `infermap`, `goldenanalysis`, `goldengraph`, `goldenmatch-kg`, `goldenfuzz`, `goldenphonetic`, `goldensuite-mcp`.

- [ ] **Step 1: Derive each package's current version from source**

```bash
for d in goldencheck goldencheck-types goldenflow goldenpipe infermap goldenanalysis goldengraph goldenmatch-kg goldenfuzz goldenphonetic goldensuite-mcp; do
  v=$(rg -m1 -o '^version = "([^"]+)"' -r '$1' "packages/python/$d/pyproject.toml" 2>/dev/null)
  echo "packages/python/$d: $v"
done
```

Do not copy versions from this plan — it will be stale. Use what the command prints.

- [ ] **Step 2: Confirm which of them carry a server.json**

```bash
fd -t f 'server.json' packages/python --max-depth 3
```

At time of writing: `goldenanalysis`, `goldencheck`, `goldenflow`, `goldenmatch`, `goldenpipe`, `infermap`. Each needs **both** `extra-files` json entries (`$.version` and `$.packages[0].version`), exactly as goldenmatch does in Task 2.

- [ ] **Step 3: Add each package to both files**

Every entry takes `include-component-in-tag: true` and a `component` matching its existing tag prefix. Confirm each prefix against real tags rather than assuming it matches the directory name:

```bash
git ls-remote --tags origin | sed 's#.*refs/tags/##' | sed 's/\^{}//' | rg -o '^[a-z-]+(?=-v[0-9])' | sort -u
```

- [ ] **Step 4: Re-run the config/manifest agreement check from Task 2 Step 5**

Expected: both sets agree and list all 13 packages.

- [ ] **Step 5: Dry-run as in Task 3 Step 1 and check every proposed tag**

Each must match a prefix that an existing `publish-*.yml` listens for. A tag nothing listens for produces a release that publishes nothing — silently.

- [ ] **Step 6: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "feat(release): wave 2 -- standing release PRs for the remaining Python packages"
```

---

### Task 7: Wave 3 — JS and native packages

**Files:**
- Modify: `release-please-config.json`
- Modify: `.release-please-manifest.json`

- [ ] **Step 1: Enumerate the JS and native tag prefixes from real tags**

```bash
git ls-remote --tags origin | sed 's#.*refs/tags/##' | sed 's/\^{}//' \
  | rg -o '^[a-z-]+(?=-v[0-9])' | sort -u | rg 'js$|native$|wasm|duckdb|pg$|hnsw|embed|spark'
```

- [ ] **Step 2: Add JS packages with `release-type: node`**

Their version carrier is `package.json`; no `extra-files` needed.

- [ ] **Step 3: Add native packages with `release-type: rust`**

Their version carrier is `Cargo.toml`. Note the crate directories are **not** uniformly named — `native/`, `native-flow/`, `analysis-native/`, `hnsw-py/`, `goldenphonetic-py/`. Read each from the workflow that publishes it:

```bash
rg -n 'manifest-path:' .github/workflows/publish-*.yml
```

- [ ] **Step 4: Re-run the agreement check and a dry run**

- [ ] **Step 5: Commit**

```bash
git add release-please-config.json .release-please-manifest.json
git commit -m "feat(release): wave 3 -- standing release PRs for the JS and native packages"
```

---

### Task 8: Retire the manual SOP

**Files:**
- Modify: `context-network/operations/release-and-registries.md`
- Create: `context-network/decisions/00XX-derived-release-versioning.md`

- [ ] **Step 1: Write the ADR**

Record: the trigger was a judgement call; the bump is now derived; ADR 0019's tag-push mechanics are unchanged and this sits above them; the MAJOR marker is convention, not enforcement.

Use the next free number — check `ls context-network/decisions/`.

- [ ] **Step 2: Update the operations doc**

The "cut a release by pushing the tag" SOP becomes "merge the standing release PR; it pushes the tag". Keep the manual path documented as the fallback for when release-please is unavailable — it is still what ADR 0019 describes and it still works.

- [ ] **Step 3: Commit**

```bash
git add context-network/
git commit -m "docs(release): record derived versioning as a decision and retire the manual trigger"
```

---

## Verification

1. A merge to main produces or updates a standing release PR per package with pending changes.
2. goldenmatch's proposed tag is bare `v*`. This is the single highest-risk config value: a prefixed tag would create a release that no workflow publishes.
3. Merging a release PR pushes the tag and the existing publish workflow runs.
4. `scripts/test_derive_next_version.py` and `scripts/test_bump_suite_floor.py` pass.
5. golden-suite's floors rise without losing their trailing comments.

## Known limitations

- **Generated notes read worse than hand-written ones.** #2703's notes explained *why* the bump was MINOR and carried measured figures; derived notes are a categorised commit list. The PR body is editable before merging.
- **MAJOR is convention, not enforcement.** See Task 5.
- **Nothing here validates that a release is safe** — only that it is correctly numbered and described. CI gates merges; that is a different problem and deliberately out of scope.
- **The 13% of commits outside the type map** do not appear in changelogs until their types are added to `changelog-sections`.
