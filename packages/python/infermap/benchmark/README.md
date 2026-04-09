# infermap benchmark

A 200-case accuracy benchmark for the `infermap` schema mapping engine. Two parallel runners (Python + TypeScript) consume a shared corpus, emit identical-schema `report.json` files, and feed a Python aggregator that posts a sticky PR comment on every qualifying pull request.

See the [design spec](../docs/design/2026-04-08-infermap-accuracy-benchmark-design.md) for the full rationale and the [implementation plan](../docs/design/2026-04-08-infermap-accuracy-benchmark-implementation.md) for the phase-by-phase build log.

## Quick start

From the repo root:

```bash
# Install the Python bench runner (if not already)
pip install -e benchmark/runners/python

# Build and install the TS bench runner (if not already)
cd packages/infermap-js && npm install && npm run build && cd -
cd benchmark/runners/ts && npm install --install-links && npm run build && cd -

# Run both benchmarks + show delta vs local baseline
make bench

# Or individually
make bench-python
make bench-ts

# Run the 5-case self-test smoke corpus
make bench-self-test

# Run unit + parity tests for both runners
make bench-test

# Overwrite the local baseline with current results
make bench-baseline

# Preview the PR comment that would be posted
make bench-comment
```

## Corpus structure

```
benchmark/
├── manifest.json                        # committed case index (currently small — real corpus arrives in Phase 11)
├── report.schema.json                   # JSON Schema both runners validate against
├── synthetic.config.json                # canonical schemas + transforms for synthetic generation
├── baselines/
│   ├── main.json                        # committed scorecard from latest main
│   └── main.metadata.json               # git sha, versions, timestamp sidecar
├── cases/
│   ├── valentine/                       # research benchmark cases (Phase 11, TBD)
│   ├── real_world/                      # hand-labeled cases (Phase 12, TBD)
│   └── synthetic/
│       └── generated.json               # 80 cases written by Python's canonical generator
├── self-test/                           # 5-case smoke corpus
│   ├── manifest.json
│   ├── cases/{perfect,zero,mixed,all_unmapped,adversarial}/
│   └── expected_self_test.json          # committed scorecard the CLI `--assert-against` checks
├── tests/
│   └── parity/
│       ├── metric_inputs.json           # shared metric parity inputs
│       └── metric_expected.json         # hand-computed expected outputs (anti-drift floor)
├── runners/
│   ├── python/                          # `infermap-bench` — installable Python package
│   └── ts/                              # `@infermap/bench` — private npm workspace package
├── aggregate.py                         # merges both reports → sticky PR comment markdown
├── build_baseline.py                    # writes baselines/main.json on push-to-main
└── check_regression.py                  # CI gate — exit 1 if F1 regressed > 2%
```

## Adding a real-world case

**Before you start, confirm the source is on the license allowlist.** See §Licensing below — redistributing Kaggle data or other non-allowlisted content is a supply-chain / legal problem that's hard to unwind from git history.

Then follow this checklist for each case:

1. **Identify the source.** Download the raw CSV from an allowlisted source.
2. **Sample to ≤100 rows per side** — use `head -n 101` or `pandas.sample(n=100, random_state=42)`. The 100-row cap keeps the repo light and the benchmark fast.
3. **Identify or author a canonical target schema.** Often this is one of the 8 canonical schemas from `synthetic.config.json` (customer, order, patient, product, employee, transaction, event, address). Reuse where possible.
4. **Create the case directory:** `benchmark/cases/real_world/<slug>/`
5. **Write the four required files:**
   - `source.csv` — the raw (sampled) source data
   - `target.csv` — the target schema with 2-3 rows of placeholder data
   - `expected.json` — the ground-truth mapping. **Every source column AND every target column must appear in exactly one of `mappings[].source` / `unmapped_source` (and similarly for target).** This is the coverage invariant. No "undecided" middle ground.
   - `case.json` — full provenance: `name`, `url`, `license`, `attribution`, plus `tags`, `expected_difficulty`, and optional `notes`.
6. **Verify the coverage invariant** by loading the case through the Python runner:
   ```bash
   python -c "
   from infermap_bench.cases import load_case
   from infermap_bench.manifest import load_manifest
   refs = load_manifest('benchmark/manifest.json')
   for r in refs:
       if r.id == 'real_world/<slug>':
           load_case('benchmark', r)
           print('ok')
   "
   ```
7. **Rebuild the manifest** to include the new case:
   ```bash
   python -m infermap_bench rebuild-manifest
   ```
8. **Commit the case directory + updated manifest** as a single commit:
   ```bash
   git add benchmark/cases/real_world/<slug>/ benchmark/manifest.json
   git commit -m "case(bench): add real_world/<slug> (<license>)"
   ```

Target distribution for the v1 real-world corpus: ~15 easy, ~15 medium, ~10 hard for 40 cases total.

## Licensing — allowlist for committed data

To prevent supply-chain and legal issues from committing restrictively-licensed data, real-world CSVs must come from one of these sources:

**Allowed:**
- **CC0 / public domain** — government open data (data.gov, EU Open Data Portal, UK ONS, etc.)
- **CC-BY** — with attribution recorded in `case.json.source.attribution`
- **MIT / BSD / Apache 2.0** datasets from GitHub with explicit redistribution permission in the LICENSE file
- **The project's own synthetic or anonymized archives** with owner permission

**Explicitly disallowed without project-specific review:**
- Kaggle datasets (Kaggle ToS typically forbids redistribution)
- Proprietary commercial data
- Anything with "non-commercial" restrictions (CC-NC, etc.)
- Anything containing personally identifiable information (PII)
- Anything containing health information subject to HIPAA or similar regulation

If a case would be valuable but the source is borderline, open an issue for discussion before committing.

## Aggregation conventions (spec §9)

The four metrics are aggregated differently because they have different semantics:

| Metric | Per-case | Slice aggregation | Why |
|---|---|---|---|
| F1 | yes (for triage) | **micro** — sum TP/FP/FN across cases, then compute | Matches Valentine benchmark + schema-matching literature. Handles varying case sizes correctly. |
| top-1 | yes | **macro** — mean of per-case values | Per-field metric; equal weight per case is better for spotting per-difficulty regressions. |
| MRR | yes | **macro** | Same reasoning as top-1. |
| ECE | n/a | **population** — flatten all predictions in slice | ECE is inherently a population-level calibration statistic. |

A worked example on 2 cases:
- Case A: 5 expected mappings, all correct. `tp=5, fp=0, fn=0`. Per-case F1 = 1.0. Per-case top-1 = 1.0 (assume one source field, correctly mapped).
- Case B: 20 expected mappings, 10 correct. `tp=10, fp=5, fn=10`. Per-case F1 = 10/15 ≈ 0.667. Per-case top-1 = 0.5 (assume half right).

**Slice scorecard:**
- Micro F1: sum counts → `tp=15, fp=5, fn=10` → P=15/20, R=15/25 → F1 = 2*0.75*0.6/(0.75+0.6) = 0.667
- Macro top-1: (1.0 + 0.5) / 2 = 0.75

Notice how micro F1 weights case B more heavily (it has more mappings), while macro top-1 weights both cases equally. This is intentional — the headline F1 should reflect the total mapping volume, but per-case metrics should treat every case as one data point.

When comparing against external systems, specify "micro F1 on <slice>" explicitly — some older schema-matching papers use macro F1 and the numbers will differ.

## Schema evolution (spec §6.6)

Every contract file (`manifest.json`, `case.json`, `expected.json`, `report.json`, `synthetic.config.json`, `baselines/main.json`) carries a top-level `version` integer. Evolution rules:

**Additive within a major version.** Adding a new optional field is a minor change; existing readers ignore unknown fields. Removing a field, renaming it, changing its type, or changing its semantics bumps the version integer.

**Forward migration.** When bumping a version:

1. Runner code lands first (new version support, still reads old version).
2. Generator / writer code lands second (emits new version).
3. In-flight data files get migrated by `python -m infermap_bench migrate --from N --to M`. The subcommand is stubbed at v1; real migration steps land when the first bump happens.
4. `baselines/main.json` is regenerated from scratch on the first push-to-main after the bump — historical baselines are intentionally abandoned because replaying them against new metrics is ambiguous. The regeneration is recorded in `main.metadata.json`'s `changelog` field.

**Backward compatibility window.** Runners support reading the previous major version for one release cycle after a bump. Beyond that they refuse to load and point at the `migrate` subcommand.

**Baseline history is lost on version bumps.** This is an acceptable trade-off: version bumps are expected to be rare (~once every 6–12 months), and `git log -p benchmark/baselines/main.json` still shows the historical scorecard values even after a regeneration.

## Known gotchas

### Windows + `file:` dependencies

The TS bench runner depends on `infermap` via a `file:` dep pointing at `packages/infermap-js`. On Windows with npm@10, plain `npm install` fails with `EISDIR: illegal operation on a directory, symlink`. Use `npm install --install-links` instead, which copies the dep instead of symlinking. CI on Linux doesn't hit this bug but we use `--install-links` in the workflow anyway for consistency.

### Stale `file:` deps after API changes

Because `file:` deps snapshot at install time, **whenever the main TS package's API changes, the bench runner needs `rm -rf node_modules/infermap && npm install --install-links infermap`**. The Phase 1 `returnScoreMatrix` addition tripped this during implementation. If you see type errors about missing fields on `MapResult` inside the bench runner, refresh the `file:` copy.

### Bot push to `main` requires branch-protection allowance

The `baseline-update` CI job pushes `benchmark/baselines/main.json` after every merge to `main`. For this to work:

- `github-actions[bot]` must be in the "Restrict who can push to matching branches" allowlist (or that restriction must be disabled for main)
- If "Require signed commits" is enabled, you must either configure a signing key for the bot, disable signed commits on main, or accept that the `baseline-update` job will fail silently and fall back to manual baseline updates

The job uses the default `GITHUB_TOKEN`, not a PAT or GitHub App token, **by design**: the default token does not trigger downstream workflows, which is what prevents an infinite `push → benchmark → baseline-update → push` loop. Never replace the token without understanding this constraint.

### `make help` needs GNU Make on Windows

The root `Makefile` has bench targets (`make bench`, `make bench-python`, etc.). On bare Windows (Git Bash without WSL), `make` is not installed — use `choco install make` or invoke the underlying commands directly. CI on Linux has make pre-installed.

### Node 20 action deprecation

CI workflows set `FORCE_JAVASCRIPT_ACTIONS_TO_NODE24=true` at the job level to opt into Node 24 for `actions/checkout@v4` and `actions/setup-node@v4` early, silencing the Node 20 deprecation warning before its hard deadline in September 2026.

## Running the benchmark against a specific slice

Use the `--only` filter:

```bash
# Only Valentine cases (when they land in Phase 11)
python -m infermap_bench run --only category:valentine

# Only hard cases
python -m infermap_bench run --only difficulty:hard

# Only cases tagged alias_dominant
python -m infermap_bench run --only tag:alias_dominant

# Only cases whose id starts with "synthetic/customer/"
python -m infermap_bench run --only synthetic/customer/
```

The TS runner supports the same filters via `node dist/cli.cjs run --only <filter>`.

## Troubleshooting

**Self-test fails with scorecard mismatch.** The `expected_self_test.json` captures the baseline at a specific infermap version. If scorer weights or logic change, the values will drift. Re-capture via `python -m infermap_bench run --self-test --output /tmp/out.json` and inspect the delta before updating the committed expected file.

**Runner suite times out in CI.** Both runners should finish the full 200-case benchmark in under 2 minutes on GitHub's free tier. If either exceeds 5 minutes, something is wrong — look for a scorer that's gone quadratic on sample size.

**Sticky comment doesn't update.** `marocchino/sticky-pull-request-comment@v2` keys on the `header:` input. If you rename the header, old comments become orphans and new ones post as duplicates. The current header is `infermap-benchmark` — don't change it without also cleaning up orphans manually.

**`regression-ack` check is blocking an unrelated PR.** The passthrough is supposed to let PRs with no `benchmark.yml` run through. If the check is blocking anyway, something is wrong with the `gh api ... --jq 'map(select(.head_sha == "..."))'` filter in `.github/workflows/regression-ack.yml`. Confirm the PR's head SHA doesn't accidentally match an old benchmark run via `gh api repos/<owner>/<repo>/actions/workflows/benchmark.yml/runs --jq '.workflow_runs[0].head_sha'`.

## See also

- `../docs/design/2026-04-08-infermap-accuracy-benchmark-design.md` — design spec
- `../docs/design/2026-04-08-infermap-accuracy-benchmark-implementation.md` — phase-by-phase plan
- `../packages/infermap-js/README.md` — main TS package docs
- `../infermap/__init__.py` — main Python package entrypoint
