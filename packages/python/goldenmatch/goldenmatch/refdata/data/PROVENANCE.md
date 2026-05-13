# Reference data — provenance

Every file in this directory is bundled with `goldenmatch` and ships under a license
compatible with the package's MIT distribution. This file is the canonical record
of where each dataset came from, when it was pulled, and the license that
applies. Add a new section when you add a new file.

## `census_surnames_2010_top10k.csv`

- **Source.** U.S. Census Bureau, *Frequently Occurring Surnames from the 2010
  Census*. Source archive:
  `https://www2.census.gov/topics/genealogy/2010surnames/names.zip`
  (file `Names_2010Census.csv` inside the zip).
- **Pulled.** 2026-05-13.
- **License.** Public domain. Census Bureau data products are works of the
  U.S. federal government and are not subject to copyright protection in the
  United States (17 U.S.C. § 105). The Bureau's open data policy permits
  unrestricted redistribution.
- **Transformation.** Filtered to ranks 1–10000 inclusive; the `ALL OTHER
  NAMES` row (rank 0) is dropped. Demographic columns (`pctwhite`, `pctblack`,
  etc.) are dropped to keep the bundle small and avoid implying demographic
  inference. Final columns: `name`, `rank`, `count`.
- **Coverage.** Top 10,000 surnames cover roughly 90% of the U.S. population
  by count. Names ranked >10,000 fall back to a "rare" weight at lookup time.
- **Size.** ~176 KB. Bundled directly in the wheel.
- **Regenerate.** `python -m goldenmatch.refdata.scripts.fetch_census_surnames`
  downloads the source archive and re-emits this file. Pin the source-archive
  hash on regeneration to detect upstream changes.
