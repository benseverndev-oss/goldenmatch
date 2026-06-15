# Algorithm Forge — handoff

Pick-up notes for continuing this work with a local Claude. Everything below lives on
branch **`claude/visual-perf-languages-t6zehw`**, draft **PR #992** (CI green).

## TL;DR — what this is

A closed-loop "algorithm forge" that drives Claude through **survey → structurize →
propose → validate** over the history of string-similarity algorithms (entity
resolution's workhorse), to invent better ones. It produced 25 scored candidate
algorithms; the top 15 were implemented as runnable prototypes; the #1 pick
(**AbbrevAlign**) was iterated three times and benchmarked against goldenmatch's real
production comparators.

**Headline result (held-out, entity-grouped CV):**
- Curated set (45 positives): `AbbrevAlign` v3 **F1 0.953** vs JaroWinkler 0.889.
- Synthetic set (393 positives): learned combiner `JW+AbbrevAlign+NickGraph` **F1 0.921**
  vs JaroWinkler **0.697**; AbbrevAlign alone 0.899. The gains generalize, and the
  learned fusion wins once it has data.

## File inventory

| Path | What |
|---|---|
| `scripts/algorithm_forge.py` | The LLM loop. Opus 4.8 structured outputs + adaptive thinking, prompt-caching, USD/iteration/verdict budget. Writes a JSON log + Markdown report. `--mock` runs offline. |
| `scripts/forge_prototypes.py` | All 15 top algorithms as pure-stdlib reference impls + in-file self-tests. AbbrevAlign, SelfThresh, BlockSimDual, ChannelMix, CalibFS, TokenRoleAlign, PrefixTrieSoftTFIDF, FieldTypeAware, ActiveMarginSim, AnytimeLev, NickGraph, BayesLenNorm, StackEnsemble, RecurAlign, SegmentSwapAware. |
| `scripts/bench_abbrevalign.py` | Benchmarks AbbrevAlign vs goldenmatch's actual rapidfuzz comparators + Monge-Elkan/Soft-TFIDF. Curated + `--synthetic` datasets, entity-grouped CV, learned combiner. |
| `examples/forge_runs/run_25.md` | The 25-`yes` forge run report (4-axis scores + leaderboard). |
| `examples/forge_runs/abbrevalign_benchmark.md` | Curated benchmark report (auto-generated). |
| `examples/forge_runs/abbrevalign_benchmark_synthetic.md` | Synthetic-scale benchmark report (auto-generated). |
| `examples/forge_runs/HANDOFF.md` | This file. |

## How to run (from `packages/python/goldenmatch/scripts/`)

```bash
# Prototypes: demo scoreboard + self-tests (pure stdlib, no deps)
python forge_prototypes.py

# Benchmark: needs rapidfuzz (goldenmatch pins rapidfuzz==3.14.5)
pip install rapidfuzz
python bench_abbrevalign.py             # curated (45 positives) -> abbrevalign_benchmark.md
python bench_abbrevalign.py --synthetic # scaled (~393 positives) -> ..._synthetic.md  (~2 min)

# The forge loop, offline (no API key, deterministic mock data):
python algorithm_forge.py --mock --max-iterations 3 --out /tmp/forge_demo

# The forge loop for real (drives Claude; costs tokens):
pip install -U anthropic
export ANTHROPIC_API_KEY=sk-ant-...
python algorithm_forge.py --budget-usd 5 --max-iterations 6 --target-verdict yes \
  --target-count 1 --out forge_run
```

> **Note:** in the remote session this was built in, there was no `ANTHROPIC_API_KEY`
> and the installed `anthropic` SDK was too old, so `algorithm_forge.py` was only ever
> run in `--mock` mode. Locally, install a current `anthropic` and set a key to run the
> real survey→propose→validate loop. The forge's API code targets the current SDK surface
> (`messages.create` with `output_config.format`, `thinking={"type":"adaptive"}`).

## How the algorithms evolved (the iteration trail)

- **v1** — AbbrevAlign = Soft-TFIDF where a token may align to a *span* of tokens
  (acronyms: IBM ↔ International Business Machines). Strict generalization of Soft-TFIDF.
- **v2** — folded nickname/alias equivalence (Bob↔Robert) into the secondary token
  similarity. Fixed measured nickname-recall weakness. Curated CV F1 0.767→0.902.
- **v3** — acronym matching now **skips stopwords** (FBI ← Federal Bureau *of*
  Investigation; ATT ← American Telephone *and* Telegraph) while staying tight (every
  *content* token in the run must supply a letter, so GE ≠ General Motors). F1 0.902→0.953.
- **scale** — added a synthetic generator (`--synthetic`) with realistic noise the
  algorithms don't model + organic hard negatives. Confirmed the gains generalize and the
  learned combiner wins at data volume.

All changes are in `forge_prototypes.py::abbrev_align` and its helpers
(`_abbrev_token_sim`, `_acronym_match`, `_ACRONYM_STOP`). Self-tests guard each property.

## Open threads / next steps (in priority order)

1. ~~**Real labeled ER dataset.**~~ **DONE** (PR #1004 + this report
   `abbrevalign_benchmark_dblp_acm.md`). Wired the real Leipzig **DBLP-ACM** corpus into the
   exact CV harness via `bench_abbrevalign.py --dblp-acm` (loader builds title-ER entities
   from the perfect-mapping + GT-derived venue clusters); runs on the `bench-abbrevalign`
   GH-runner lane. **Result:** on real labeled data AbbrevAlign holds up - held-out CV
   F1 0.861 vs JaroWinkler 0.826 on titles (no harm, modest edge; ties the MongeElkan/
   SoftTFIDF hybrids it generalizes), and on the abbreviation-heavy venue field it separates
   better (AUC 0.925 vs 0.840) **but at a precision cost** (0.571 - it over-merges conference
   vs journal, `VLDB` ~ `The VLDB Journal`). Confirms the productionization framing below:
   ship it as a **gated comparator feature** for abbreviation-heavy fields, not a JW
   replacement. (Venue can't support held-out CV - only 5 venues - so it's reported
   in-sample + AUC.)
2. **Acronym-collision precision** (IBM vs *Indian Bank Mumbai* — both valid acronym
   expansions, fundamentally ambiguous for a pure string metric). Try IDF-gating the
   acronym score (confidence ∝ IDF mass of the matched span) and/or letting the learned
   combiner weigh it down. Measure precision delta on the hard-negative slice.
3. **Productionize into goldenmatch's scorer.** `goldenmatch/core/scorer.py` already
   composes rapidfuzz comparators (`jaro_winkler`, `jaccard`, `levenshtein`,
   `token_sort_ratio`, `partial_ratio`) into a Fellegi-Sunter score. The validated
   recommendation: add `abbrev_align` + `nick_graph_sim` as two more comparator features
   feeding that learned scorer — *not* as a JaroWinkler replacement. Port the two functions
   to a real module (vectorized / rapidfuzz-backed), add tests, gate behind a config flag.
4. **Vectorize.** The prototypes are correctness-first pure Python. For real use, the inner
   token sims should use rapidfuzz and the per-pair loops should be Polars/numpy expressions
   (mirror how `scorer.py` does it).

## Environment / CI gotchas (save yourself the rediscovery)

- **ruff is the gate for `scripts/`.** `pyright` only runs when `pyrightconfig.json`
  changes, and pytest does **not** import `scripts/` (not `test_*` files). So the
  `python_goldenmatch_coverage` lane = `ruff check packages/python/goldenmatch` + the
  pytest suite. Keep new scripts ruff-clean.
- Ruff rules that bit this work: `F401` (unused import), `UP045` (use `X | None` not
  `Optional[...]`), `UP035` (import `Callable` from `collections.abc`), `E401` (one import
  per line). Run `ruff check <file>` locally before pushing.
- **CI cancellation artifacts:** pushing a new commit while the prior commit's run is
  in-flight cancels it, which flips the `ci-required` gate to `failure` even though **0
  jobs actually failed**. If you see a `ci-required` failure, check
  `get_job_logs(failed_only=true)` — if it says 0 failed jobs, it's a cancellation, not a
  bug. Avoid by batching pushes.
- `git` commits in this repo: the session used `git -c commit.gpgsign=false`. Auth for
  `benseverndev-oss/*` is the personal `benzsevern` account (see root `CLAUDE.md`).

## Quick correctness checks (should all pass)

```bash
python forge_prototypes.py        # ends with "all prototype self-tests passed"
ruff check forge_prototypes.py bench_abbrevalign.py algorithm_forge.py
python bench_abbrevalign.py        # AbbrevAlign tops the curated held-out CV table
```
