# CLEAR-KG — dataset card

## Summary

CLEAR-KG evaluates knowledge-graph construction from document troves along four
tracks. Its data is **generated on demand** (deterministic, seeded) except one
real-data track that fetches Wikipedia at run time. **Nothing corpus-sized is
committed** — every generator/fetcher is committed and reproducible instead.

| track | data source | ground truth | committed? |
|---|---|---|---|
| A · extraction | `extract_data.py` (synthetic KG → alias/homograph docs) | gold triples, by construction | generator only |
| B · corpus-level ER | `generate.py` (synthetic homograph corpus) | gold entity per mention, by construction | generator only |
| B · real-data | `real_data.py` (English **Wikipedia** action API) | article titles = entity ids; outbound links = co-mention neighborhoods | fetch-on-demand, cached to gitignored `data/` |
| C · faithfulness | `grounding_data.py` (supported / distractor / hallucinated triples) | per-triple verdict + provenance, by construction | generator only |
| D · CLEAR composite | `pipeline_data.py` (unified corpus) | aligned entity/triple/provenance truth | generator only |

## Provenance & licensing

- **Synthetic tracks (A, B-synth, C, D):** entity/org/place name pools are common
  first/last names and invented org names; no real individuals are targeted. Output
  is deterministic given `seed`. Public-domain-equivalent (author-generated).
- **Real-data track (B-real):** plain-text extracts + outbound links from English
  Wikipedia via `https://en.wikipedia.org/w/api.php` (`prop=extracts|links`).
  Wikipedia text is CC BY-SA 4.0; this track **fetches at run time and never
  commits the text** (gitignored `data/wiki/`), so the repo redistributes no
  Wikipedia content — only the list of curated ambiguous surfaces and article
  titles in `real_data.py::HOMOGRAPH_GROUPS`.

## Intended use & metrics

Measure a doc→KG system on: triple-F1 (A), homograph **split-rate** + pairwise/B³
(B), grounded-&-correct + distractor false-support + confidence AUROC/ECE (C), and
the **CLEAR** composite (D). See `RESULTS.md` for the numbers and `SPEC.md` §3 for
exact metric definitions.

## Limitations & honest scoping

- **Phase 0 is templated prose**, not LLM-generated — it proves the *mechanism*
  and the *metric*, not realistic extraction accuracy. LLM-generated prose + a
  real NLI grounding backstop are later phases (SPEC §8).
- **The synthetic tracks use a consistent, distinctive co-mention signature** so
  the ER/grounding mechanism is cleanly visible; real corpora are noisier (the
  real-data track and the WhoIsWho SND result both show the neighborhood signal is
  genuinely sparse). Do not read the synthetic 1.000s as field accuracy.
- **The real-data track's neighbor signature is per-article** (stable), which
  validates the split-rate axis, not a sparse-recall regime.
- **Graded-own-homework risk** is real and named head-on (SPEC §8): mitigations are
  real content (Wikipedia / Wikidata via the er-kg-bench companion), the committed
  regenerable generators, and offline tests pinning every claim.

## Companion

The real *packaged frameworks* are scored on real Wikidata/RxNorm data in
[`../er-kg-bench`](../er-kg-bench), which now also reports the homograph split-rate.
