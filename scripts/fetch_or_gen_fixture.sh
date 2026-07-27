#!/usr/bin/env bash
# Fetch a synthetic bench fixture from the `bench-fixtures` prerelease, or
# generate it and upload it there for reuse.
#
# The generator (scale_audit_5m_generate.py) is deterministic (fixed seed=42),
# so (n_records, dupe_rate) fully keys a fixture. Generating 5M takes ~20 min on
# the runner; this turns that into a ~1 min download on every subsequent run
# (any ref -- release assets are not branch-scoped, unlike actions/cache).
#
# Usage: fetch_or_gen_fixture.sh <n_records> <dupe_rate> <out_csv>
# Requires: GH_TOKEN with contents:write; a built .venv.
set -euo pipefail

N="${1:?n_records}"; DUPE="${2:?dupe_rate}"; OUT="${3:?out_csv}"
REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY unset}"
TAG="bench-fixtures"
ASSET="synthetic_${N}_d${DUPE}.csv"
GT_ASSET="synthetic_${N}_d${DUPE}.ground_truth.csv"
OUTDIR="$(dirname "$OUT")"
GT_OUT="${OUT%.csv}.ground_truth.csv"
mkdir -p "$OUTDIR"

if gh release download "$TAG" --repo "$REPO" --pattern "$ASSET" --dir "$OUTDIR" --clobber 2>/dev/null; then
  mv "$OUTDIR/$ASSET" "$OUT"
  gh release download "$TAG" --repo "$REPO" --pattern "$GT_ASSET" --dir "$OUTDIR" --clobber 2>/dev/null \
    && mv "$OUTDIR/$GT_ASSET" "$GT_OUT" || true
  echo "fixture cache HIT: $ASSET ($(du -h "$OUT" | cut -f1))"
  exit 0
fi

echo "fixture cache MISS: generating $ASSET (~20 min at 5M)..."
.venv/bin/python scripts/scale_audit_5m_generate.py \
  --n-records "$N" --dupe-rate "$DUPE" --output "$OUT"

# Upload for reuse. Non-fatal: a failed upload just means the next run regenerates.
gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1 || \
  gh release create "$TAG" --repo "$REPO" --prerelease \
    --title "bench fixtures (cached)" \
    --notes "Deterministic synthetic bench fixtures, keyed by (n_records, dupe_rate). Auto-populated by bench workflows; safe to delete (regenerated on demand)." 2>/dev/null || true
cp "$OUT" "/tmp/$ASSET"
UPLOADS="/tmp/$ASSET"
[ -f "$GT_OUT" ] && cp "$GT_OUT" "/tmp/$GT_ASSET" && UPLOADS="$UPLOADS /tmp/$GT_ASSET"
# shellcheck disable=SC2086
gh release upload "$TAG" --repo "$REPO" $UPLOADS --clobber 2>/dev/null \
  && echo "fixture generated + cached: $ASSET" \
  || echo "(cache upload failed -- non-fatal; next run regenerates)"
