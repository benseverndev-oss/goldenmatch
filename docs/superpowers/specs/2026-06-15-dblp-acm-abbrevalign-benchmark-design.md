# Real-dataset benchmark for AbbrevAlign (DBLP-ACM)

**Date:** 2026-06-15
**Branch / PR:** `claude/visual-perf-languages-t6zehw` / #992 (algorithm_forge)
**Status:** design approved, pre-implementation

## Problem

The AbbrevAlign result in PR #992 is the forge's #1 pick: it beats JaroWinkler on
entity-grouped CV (curated F1 0.953 vs 0.889; synthetic learned combiner 0.921 vs 0.697).
But **every benchmark so far is curated or synthetic** — the transformation distribution is
*known*, which structurally flatters structure-aware methods like AbbrevAlign. The handoff
(`examples/forge_runs/HANDOFF.md`, next-steps #1) names this the single result that would
make the conclusion production-credible: run the **exact** CV harness on a real labeled ER
corpus.

## Goal

Wire the real, committed **DBLP-ACM** corpus into the existing `bench_abbrevalign.py` CV
harness and produce an honest credibility report, with the heavy run executed **on a GitHub
runner** (the dev box is memory-starved; the run is O(N²) in pure Python).

Non-goals: productionizing AbbrevAlign into `core/scorer.py` (handoff #3), acronym-collision
precision work (handoff #2), vectorization (handoff #4). Those are separate follow-ups.

## Data source

`packages/python/goldenmatch/tests/benchmarks/datasets/DBLP-ACM/`:
- `DBLP2.csv` (2616 rows), `ACM.csv` (2294 rows) — columns `id,title,authors,venue,year`,
  latin-1 encoded.
- `DBLP-ACM_perfectMapping.csv` (2224 pairs) — columns `idDBLP,idACM`, the cross-source
  ground-truth same-paper pairs.

Already committed (not gitignored), so the benchmark is **zero-network** — preserving the
handoff's "runs under any network policy" property. The loader anchors paths to `__file__`
(`scripts/` is the CWD locally but repo-root in CI — the repo's documented fixture-path
gotcha). If the files are absent it raises a clear error; there is **no** download fallback.

## Two GT-derived evaluations

Both are shaped as the harness's existing contract: `dict[entity_id, list[(text, variant_type)]]`,
so `evaluate()` and `evaluate_cv()` consume them unchanged.

### 1. Title ER (standard dedup — the "does it hurt?" check)

- Connected components over the perfect-mapping pairs → each matched paper is one entity with
  two variants: `(dblp_title, "dblp")` and `(acm_title, "acm")`.
- Plus a seed-sampled set of **unmatched singletons** (records in neither side of the mapping)
  as organic hard negatives — one variant each.
- **Text = title only.** Titles are the dominant ER signal in DBLP-ACM; concatenating
  authors/venue would muddy attribution of any delta to the comparator. (Concat deliberately
  rejected.)
- **Sampled for tractability.** The harness builds all O(N²) pairs in pure Python; ~4900
  records would be ~12M pairs (hours / OOM). `--max-entities` (default **200**) caps matched
  entities; an equal number of singleton negatives is sampled. ≈600 records ≈180K pairs ≈ the
  existing synthetic run's scale (~minutes). Seed-controlled (`--seed`, default 7) for repro.
- **Expected:** AbbrevAlign ≈ JaroWinkler. For true matches the DBLP and ACM titles are
  near-identical, so JW already saturates and an acronym specialist has nothing to bite on.
  A tie here is the honest "no-harm / generalizes" result.

### 2. Venue matching (the abbreviation task — AbbrevAlign's home turf, GT-derived)

- For every matched pair, add an edge `(dblp_venue_string, acm_venue_string)`. Connected
  components over these edges = real venue clusters — e.g. `VLDB` / `Very Large Data Bases` /
  `VLDB Journal` collapse into one cluster purely from the ground truth, **no hand-curation**.
- entity_id = venue cluster; variants = the **distinct** venue strings in the cluster
  (`variant_type` = the source the string came from). Dedup to distinct strings so the eval
  measures short↔long matching, not trivial exact-string repeats.
- Tiny (a few dozen distinct venue strings) → no sampling, fast.
- **Expected:** AbbrevAlign wins (`"VLDB"` ↔ `"Very Large Data Bases"`,
  `"SIGMOD Record"` ↔ `"International Conference on Management of Data"`).

## Implementation

All changes in `packages/python/goldenmatch/scripts/`:

- `bench_abbrevalign.py`:
  - `load_dblp_acm(max_entities: int, seed: int) -> tuple[dict, dict]` — reads the three CSVs,
    returns `(title_dataset, venue_dataset)`. Includes the UnionFind/connected-components
    helper (or reuses one if a stdlib-only impl already exists in the file; otherwise a small
    local one). Pure stdlib `csv` + the existing imports.
  - A `--dblp-acm` mode in `main()`: builds both datasets, runs the existing `evaluate()` +
    `evaluate_cv()` on each, writes a **combined** report to
    `examples/forge_runs/abbrevalign_benchmark_dblp_acm.md` and prints it. The `--synthetic`
    and curated modes are untouched.
  - The empty `abbrev/nickname/typo` slice columns render as `—` already (the harness's NaN
    guard) — **no harness change required**.
- `.github/workflows/bench-abbrevalign.yml` — new, `workflow_dispatch` only, modeled on the
  existing `bench-*.yml`. `runs-on: large-new-64GB` (repo convention for bench/eval lanes).
  Steps: checkout → setup Python → `pip install rapidfuzz==3.14.5` → run
  `python bench_abbrevalign.py --dblp-acm` from `scripts/` → write the report to
  `$GITHUB_STEP_SUMMARY` → upload it as an artifact. On-demand and isolated; **not** folded
  into `run_benchmarks.py`/`benchmarks.yml` because this is a standalone research artifact.

## Testing / gates

- A small in-file self-test for `load_dblp_acm`'s graph logic: hand-built mapping →
  assert correct connected components, and assert venue clusters dedup to distinct strings.
  Runs under `python bench_abbrevalign.py --dblp-acm --selftest` (or a `_selftest()` invoked
  alongside the existing prototype self-tests — match the file's existing convention).
- `ruff check` clean on the touched script — this is the real `scripts/` gate (`pyright`
  only runs on `pyrightconfig.json` changes; pytest does not import `scripts/`). Watch the
  rules that bit prior forge work: `F401`, `UP045` (`X | None`), `UP035` (`Callable` from
  `collections.abc`), `E401`.
- Local validation is **loader-only** (build the datasets from the real CSVs, eyeball cluster
  sizes / a few venue clusters) — fast. The full O(N²) `evaluate_cv` is **only** run on the
  GH runner, per the "through GH runner" requirement and the memory-starved box.
- Script stays pure-stdlib + rapidfuzz; no new dependency, no polars/goldenmatch import.

## Deliverable

1. The committed report `examples/forge_runs/abbrevalign_benchmark_dblp_acm.md` (generated by
   the runner, committed back).
2. The GH-runner run link.
3. An honest findings section framing the contrast: AbbrevAlign **ties** JW on generic
   publication titles but **wins** on the abbreviation-heavy venue field. This directly
   supports the handoff's productionization recommendation — add `abbrev_align` as a
   comparator *feature for abbreviation-heavy fields*, not as a JaroWinkler replacement.

## Risks / open questions

- **Venue CC over-merge.** If a venue string is genuinely ambiguous across two real venues,
  connected components could merge two clusters. DBLP-ACM venues are a small, clean set
  (SIGMOD / VLDB / ICDE / TODS / SIGMOD Record / VLDB Journal / …), so this is low-risk; the
  loader self-test asserts on a controlled mapping and the report prints cluster contents so
  any merge is visible/auditable.
- **Title tie is "undramatic."** It is the expected and honest result; the value is the
  venue contrast plus the credibility of a real corpus. Framed as such in the report.
- **Runtime.** Bounded by `--max-entities`; default 200 sits at the synthetic run's proven
  scale, well under the 60-min lane budget.
