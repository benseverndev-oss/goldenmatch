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

**Provisioning (corrected — these files are gitignored, NOT committed).**
`tests/benchmarks/datasets/` is matched by `packages/python/goldenmatch/.gitignore:17`, so the
CSVs are absent from a fresh checkout (a GH runner, or this worktree). The repo's established
pattern — used by `benchmarks.yml` and `eval-benchmarks.yml` — is to **download** DBLP-ACM at
job time from Leipzig (`https://dbs.uni-leipzig.de/file/DBLP-ACM.zip`), with a mirror override
via the `GOLDENMATCH_DBLP_ACM_URL` repo variable. So this benchmark is **not** zero-network;
that was never required (the benchmark already needs network for `pip install rapidfuzz`). The
new workflow reuses `eval-benchmarks.yml`'s exact `curl`+`unzip`+`cp` "Fetch DBLP-ACM (if
needed)" step. The loader reads the standard path (anchored to `__file__` — `scripts/` is CWD
locally but repo-root in CI, the documented fixture-path gotcha) and raises a clear error if
absent; the workflow's download step guarantees presence on the runner.

> Not used: `packages/python/infermap/benchmark/cases/valentine/magellan/dblp_acm_003/` is a
> committed but *different* (smaller Valentine/Magellan) DBLP-ACM cut with a different GT
> format — not the Leipzig 2616/2294-row corpus this benchmark targets.

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

### 2. Venue matching (the abbreviation field — GT-derived; reports a *real-data precision tradeoff*, not a CV win)

> **Corrected after a spec review ran this construction on the real CSVs.** DBLP-ACM is a
> **5-venue corpus**, so the GT yields exactly **5 clusters / 10 distinct venue strings**.
> That is too few for the entity-grouped CV harness: `evaluate_cv(k=5)` folds on
> `entity_index % k`, so each fold holds one entity and its test set is all-positive /
> zero-negative → degenerate (every method P=1.0, AUC=`nan`, AbbrevAlign merely ties JW).
> **So venue is NOT run through `evaluate_cv`.** Instead it is reported via the in-sample
> `evaluate()` path (which the harness already labels an optimistic ceiling) + ROC-AUC + an
> explicit false-positive list. This is the honest, defensible framing.

- For every matched pair, add an edge `(dblp_venue_string, acm_venue_string)`. Connected
  components over these edges = real venue clusters, purely from the ground truth, **no
  hand-curation**. The real clusters (verified) are exactly five:
  - `{VLDB, Very Large Data Bases}`
  - `{VLDB J., The VLDB Journal — The International Journal on Very Large Data Bases}`
  - `{ACM Trans. Database Syst., ACM Transactions on Database Systems (TODS)}`
  - `{SIGMOD Conference, International Conference on Management of Data}`
  - `{SIGMOD Record, ACM SIGMOD Record}`
- entity_id = venue cluster; variants = the **distinct, normalized** venue strings in the
  cluster (`variant_type` = the source). Normalize with `html.unescape` + whitespace strip/
  collapse (ACM strings carry `&mdash;` and trailing spaces); dedup so the eval measures
  short↔long matching, not exact repeats.
- Tiny (10 strings) → no sampling. Report `evaluate()` (in-sample AUC / best-F1 / P / R)
  only.
- **Real result (verified in-sample):** AbbrevAlign separates pairs better per-pair
  (**AUC 0.930 vs JW 0.875**) but its precision drops (~0.57) because it scores
  cross-cluster pairs like `VLDB` ↔ `The VLDB Journal …` = 1.0 — it **over-merges
  conference vs journal**. That is a concrete real-data instance of the acronym-collision
  precision risk (handoff #2, IBM vs *Indian Bank Mumbai*). The report lists these
  false positives explicitly. This *strengthens* the productionization recommendation:
  AbbrevAlign belongs as a **gated, learned-combiner feature**, not a JaroWinkler
  replacement.

## Implementation

All changes in `packages/python/goldenmatch/scripts/`:

- `bench_abbrevalign.py`:
  - `load_dblp_acm(max_entities: int, seed: int) -> tuple[dict, dict]` — reads the three CSVs
    (latin-1), normalizes text (`html.unescape` + whitespace strip/collapse), returns
    `(title_dataset, venue_dataset)`. Includes a small stdlib connected-components helper
    (reuse one if the file already has a stdlib-only impl; else a local one). Pure stdlib
    `csv` + `html` + the existing imports.
  - A `--dblp-acm` mode in `main()` that writes a **combined** report to
    `examples/forge_runs/abbrevalign_benchmark_dblp_acm.md` and prints it, with two sections:
    - **Title ER** — `evaluate()` + `evaluate_cv()` + the existing `render()`/`render_cv()`
      (CV is sound here: 200 entities / k=5 = 40 per fold, real negatives).
    - **Venue** — `evaluate()` + `render()` **only** (no CV — see the boxed note above), plus
      a small `_venue_false_positives()` helper that lists cross-cluster pairs AbbrevAlign
      scores above its best-F1 threshold (the conf-vs-journal over-merges).
    The `--synthetic` and curated modes are untouched.
  - The empty `abbrev/nickname/typo` slice columns render as `—` already (the harness's NaN
    guard) — **no harness change required**.
- `.github/workflows/bench-abbrevalign.yml` — new, `workflow_dispatch` only, modeled on the
  existing `bench-*.yml`. `runs-on: large-new-64GB` (repo convention for bench/eval lanes).
  Steps: checkout → setup Python → `pip install rapidfuzz==3.14.5` → **Fetch DBLP-ACM (if
  needed)** — the exact `curl https://dbs.uni-leipzig.de/file/DBLP-ACM.zip`+`unzip`+`cp` step
  copied from `eval-benchmarks.yml`, honoring `GOLDENMATCH_DBLP_ACM_URL` (`vars.DBLP_ACM_URL`)
  as a mirror override → run `python bench_abbrevalign.py --dblp-acm` from `scripts/` → write
  the report to `$GITHUB_STEP_SUMMARY` → upload it as an artifact. The workflow stays
  **read-only** (`permissions: contents: read`, matching the `eval-benchmarks.yml` template —
  no commit-back/push); the generated report is committed to the branch *manually* after
  fetching the artifact (`gh run download`). On-demand and isolated; **not** folded into
  `run_benchmarks.py`/`benchmarks.yml` because this is a standalone research artifact.

## Testing / gates

- A small in-file self-test for `load_dblp_acm`'s graph logic, in two parts:
  - **Always runs** (no data needed): hand-built mapping → assert correct connected
    components; assert normalization (`&mdash;`/whitespace) feeds the distinct-string dedup.
  - **Runs only if the real CSVs are present** (else `skip` with a message — they're
    gitignored, absent on a clean checkout): assert the venue clusters from the real CSVs are
    exactly the five listed above (a regression guard on the construction the deliverable
    rests on). On the GH runner the download step makes this branch run.
  Invoked under `python bench_abbrevalign.py --dblp-acm --selftest` (or a `_selftest()`
  alongside the existing prototype self-tests — match the file's existing convention).
- `ruff check` clean on the touched script — this is the real `scripts/` gate (`pyright`
  only runs on `pyrightconfig.json` changes; pytest does not import `scripts/`). Watch the
  rules that bit prior forge work: `F401`, `UP045` (`X | None`), `UP035` (`Callable` from
  `collections.abc`), `E401`.
- Local validation is **loader-only** (build the datasets, eyeball cluster sizes / the five
  venue clusters) — fast. Requires copying the cached CSVs from the main working tree into the
  worktree's gitignored `tests/benchmarks/datasets/DBLP-ACM/` first (they don't materialize in
  a worktree checkout). The full O(N²) `evaluate_cv` is **only** run on the GH runner, per the
  "through GH runner" requirement and the memory-starved box.
- Script stays pure-stdlib + rapidfuzz; no new dependency, no polars/goldenmatch import.

## Deliverable

1. The committed report `examples/forge_runs/abbrevalign_benchmark_dblp_acm.md` (generated on
   the runner, downloaded via `gh run download`, committed manually).
2. The GH-runner run link.
3. An honest findings section framing the real-data contrast: on a real labeled corpus,
   AbbrevAlign **ties** JW on generic publication titles (held-out CV — no harm, generalizes)
   and on the abbreviation-heavy venue field shows **higher per-pair separation (AUC) at a
   precision cost** — it over-merges conference vs journal. Both halves point the same way:
   add `abbrev_align` as a **gated comparator feature for abbreviation-heavy fields** feeding
   the learned scorer, *not* a JaroWinkler replacement. (The precision cost is exactly what
   the learned combiner / handoff-#2 IDF-gating is for.)

## Risks / open questions

- **Venue is small and in-sample only.** 5 clusters / 10 strings can't support held-out CV,
  so the venue number is an in-sample ceiling (AUC + best-F1), explicitly labeled as such —
  not a held-out claim. The value is the *direction* (per-pair separation) and the concrete
  precision-failure list, on real data. The loader self-test pins the 5 clusters so the
  construction can't silently drift.
- **AbbrevAlign's real failure mode is precision, not the CC build.** The connected-components
  construction is correct (5 clean clusters); the over-merge is inside AbbrevAlign's *scoring*
  (it rates `VLDB` ≈ `The VLDB Journal …` = 1.0). The report surfaces these false positives
  explicitly rather than hiding them — that is the point of the venue section.
- **Title tie is "undramatic."** Expected and honest; the credibility comes from it being a
  real corpus, and from the venue section's precision finding. Framed as such in the report.
- **Runtime.** Bounded by `--max-entities`; default 200 sits at the synthetic run's proven
  scale, well under the 60-min lane budget.
