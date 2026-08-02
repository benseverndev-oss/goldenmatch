# ER-Matcher data pipeline

Builds the training/eval corpus for the OSS ER-matcher (Qwen2.5-1.5B LoRA SFT,
Apache-2.0) by
blending multiple **license-clean** entity-matching sources into the unified
JSONL row contract the trainer already consumes. This is Phase 1a of the
production-grade data effort (see
`docs/superpowers/specs/2026-07-27-er-matcher-multi-source-data-pipeline-design.md`
and the plan alongside it).

Everything here is **CPU / box-safe**: no `torch`/`transformers`, no network in
the unit tests. The GPU trainer (`train.py`, `modal_train.py`) is unchanged and
still reads `data/er_matcher/{train,val,test}.jsonl`.

## Row contract

Each source emits the trainer's existing row, extended with one `dataset`
provenance field (the trainer ignores unknown keys):

```
{a: {...fields}, b: {...fields}, label: "match"|"no_match",
 domain, source, dataset, eid_a, eid_b}
```

Heterogeneous schemas are intentional — mixing product `{title,brand,price}`,
person `{name,address,dob}`, and citation `{authors,venue,year}` improves
robustness. The shared serializer (`goldenmatch.core.er_matcher.prompt.build_chat`)
renders arbitrary fields.

## Sources

| Source | Loader | License | Mechanism | Domain | In training corpus? |
|---|---|---|---|---|---|
| `febrl` | `FebrlSource` | MPL-1.1 (ANUOS) | **bundle** | person (synthetic) | yes |
| `abt_buy`, `amazon_google` | `LeipzigSource` | **CC-BY** (attribution required) | **bundle** | product | yes |
| `dblp_acm`, `dblp_scholar` | `LeipzigSource` | **CC-BY** (attribution required) | **bundle** | citation | yes |
| `magellan_*` | `MagellanSource` | cite-only | **fetch** | product/citation | **no — eval-only** |
| `ncvr` | `NcvrSource` (stub) | public-record (real PII) | **fetch** | person | **no — eval-only, never bundled** |

**License obligations:**
- **Leipzig (CC-BY):** redistribution is permitted *with attribution* — credit the
  Leipzig DB Group + the VLDB2010 paper (carried in `sources.yaml` and the
  manifest). Pull Abt-Buy / Amazon-Google from Leipzig, **not** Magellan (same
  rows, Magellan has no license).
- **FEBRL (MPL-1.1):** synthetic person data, redistributable.
- **Magellan/DeepMatcher (cite-only):** no redistribution grant — **fetch at
  build, never commit**; eval-parity only.
- **NCVR (real voter PII):** legally public but no redistribution grant — **never
  bundled** into a published model; fetch-only, eval-only, documented. The
  license-clean *person* training data is synthetic (FEBRL + the Phase 1b rich
  generator), by design.

The `manifest.json` records every source's license + attribution (for the model
card), including the eval-only ones that contribute no training rows.

## Running it

```bash
# from the repo root
python scripts/er_matcher/build_corpus.py \
    --sources scripts/er_matcher/sources.yaml \
    --out-dir data/er_matcher \
    --seed 20260727
# -> data/er_matcher/{train,val,test}.jsonl + manifest.json
```

`build_corpus` constructs every configured source (so a broken `sources.yaml`
fails loudly), but only **bundle/generate, non-`eval_only`** sources contribute
rows. `--cap N` limits each source to N rows per split (the "cap oversized
sources" lever; ratio-blending by `weight` is deferred to a later sub-project).
The cap preserves class balance: it interleaves each source's `match` and
`no_match` rows round-robin before truncating, so a capped split still
contains both labels (in roughly the source's original ratio) instead of
degenerating into all-match rows.

### Data acquisition (not committed)

`sources.yaml` points each bundled source's `kwargs.root` at
`data/er_matcher/raw/<source>/`. Those raw datasets are **not committed** —
download the license-clean bundled sources (FEBRL, the Leipzig CC-BY sets) into
those paths before running `build_corpus` against real data. Fetch-only sources
(Magellan, NCVR) require `GOLDENMATCH_ALLOW_FETCH=1` and are consumed by the eval
harness (a later sub-project), not the training build.

## Interpretability (Layer 1)

- `interp/decision_geometry.py` — probes the geometry of the model's
  "same-entity" decision at the decision site (last-token final-layer hidden
  state). Establishes that the decision is a **low-dimensional linear structure**
  (0.959 linear-probe accuracy, one held-out direction at 0.955 AUC vs hard
  negatives, ~4–8 effective dims). Correlational/final-layer; the causal
  residual-stream + SAE follow-on is the GPU/Modal path. See
  `docs/design/2026-08-02-15b-decision-geometry-layer1.md`. Pure helpers are
  unit-tested model-free in `test_decision_geometry.py`.

## Module map

- `sources/base.py` — `Row`, the `PairSource` protocol, the loader registry.
- `sources/splits.py` — deterministic entity-level `split_of` (shared with `gen_pairs.py`).
- `sources/negatives.py` — deterministic blocker + bounded hard/easy negative synthesis (optional cross-partition constraint).
- `sources/csv_tables.py` — shared `read_id_table` (used by leipzig + magellan).
- `sources/{leipzig,febrl,magellan,ncvr}.py` — the loaders.
- `sources_config.py` — `sources.yaml` schema, strict validation, the `build_source` factory.
- `build_corpus.py` — the blend driver → JSONL + manifest.

## Testing

```bash
# box-safe: no GPU, no network (fetch is guarded by GOLDENMATCH_ALLOW_FETCH)
pytest scripts/er_matcher/
```
Loaders/pipeline are deterministic (same seed → byte-identical JSONL) and unit-tested against tiny fixtures under `tests/fixtures/`.

## Deferred / follow-ups

- **Phase 1b:** the rich synthetic generator (census-scale vocab, CRM/org schemas,
  dsgen-calibrated corruption) replacing `gen_pairs.py`'s internals, registered as
  a `synthetic` source.
- **MusicBrainz** (Leipzig, CC-BY): multi-source clustering shape — needs a loader
  distinct from the two-table `LeipzigSource`.
- `sources_config`: guard `kwargs` against keys that collide with the builder's
  explicit args (`name`/`domain`/`seed`) so a bad entry raises a clear `ValueError`
  instead of a `TypeError`.
- Sub-project 2 (run re-architecture for scale) and sub-project 3 (cross-benchmark
  eval harness — where Magellan/NCVR get consumed).
