#!/usr/bin/env bash
# THROWAWAY spike: fetch the design datasets the FS link-cut matrix reads. This is the design
# subset of .github/workflows/fs-cut-rule-matrix.yml's fetch step. Run it from the repo root.
# Data is gitignored under packages/python/goldenmatch/tests/benchmarks/datasets and never committed.
set -uo pipefail
BASE=packages/python/goldenmatch/tests/benchmarks/datasets
TMP="${TMPDIR:-/tmp}/fs-loss-fetch"
fetch_zip() {
  local name="$1" url="$2"; shift 2
  local dest="$BASE/$name"
  mkdir -p "$dest" "$(dirname "$TMP/$name")"
  if curl -fsSL --retry 3 -o "$TMP/$name.zip" "$url"; then
    mkdir -p "$TMP/$name"
    unzip -o -q "$TMP/$name.zip" -d "$TMP/$name" || echo "WARN: $name: unzip failed"
    for f in "$@"; do
      found=$(find "$TMP/$name" -name "$f" | head -1)
      if [ -n "$found" ]; then cp "$found" "$dest/$f"
      else echo "WARN: $name: $f not in archive"; fi
    done
  else
    echo "WARN: $name download failed"
  fi
}
L=https://dbs.uni-leipzig.de/file
fetch_zip DBLP-ACM "$L/DBLP-ACM.zip" DBLP2.csv ACM.csv DBLP-ACM_perfectMapping.csv
fetch_zip DBLP-Scholar "$L/DBLP-Scholar.zip" DBLP1.csv Scholar.csv DBLP-Scholar_perfectMapping.csv
fetch_zip Amazon-Google "$L/Amazon-GoogleProducts.zip" Amazon.csv GoogleProducts.csv Amzon_GoogleProducts_perfectMapping.csv
# Leipzig clustering trio: CC-BY, Database Group Leipzig; Saeedi, Peukert & Rahm, ADBIS 2017.
LC="$BASE/Leipzig-Clustering"
mkdir -p "$LC/MusicBrainz"
curl -fsSL --retry 3 -o "$LC/MusicBrainz/musicbrainz-20-A01.csv" \
  "https://dbs.uni-leipzig.de/files/datasets/saeedi/musicbrainz-20-A01.csv.dapo" \
  || echo "WARN: MusicBrainz download failed"
fetch_zip Leipzig-Clustering/GeoSettlements \
  "https://dbs.uni-leipzig.de/files/datasets/geographicalSettelments.zip" \
  settlements.json "combinedSettlements(PerfectMatch).json"
fetch_zip Leipzig-Clustering/Affiliations \
  "https://dbs.uni-leipzig.de/files/datasets/affiliationstrings.zip" \
  affiliationstrings_ids.csv affiliationstrings_mapping.csv
echo "fetch done"
