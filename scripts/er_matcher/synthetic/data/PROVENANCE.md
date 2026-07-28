# Synthetic vocab data — provenance

- `census_surnames.csv` — US Census Bureau, *Frequently Occurring Surnames
  from the 2010 Census* (`name,rank,count`, ranks 1-10000). Public domain
  (17 U.S.C. § 105). Copied verbatim from
  `packages/python/goldenmatch/goldenmatch/refdata/data/census_surnames_2010_top10k.csv`
  (see that directory's own `PROVENANCE.md` for the original source archive
  and pull date) so this package stays importable without a goldenmatch
  dependency.
- `first_names.txt` — representative list of common US given names spanning
  genders and eras, written from general knowledge (SSA given-name data is
  US-gov public domain; this list is a hand-curated stand-in, not a scrape).
- `cities.csv` (`city,state,zip_prefix`) — representative US city/state/
  zip-prefix facts, written from general knowledge. Public domain facts
  (city names, state abbreviations, ZIP-prefix ranges are not copyrightable).
