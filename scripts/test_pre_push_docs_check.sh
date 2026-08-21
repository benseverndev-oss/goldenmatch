#!/bin/sh
# Ref-parsing rules of pre_push_docs_check.sh.
#
# Exercises the decision the hook makes about WHICH refs to gate, via the
# GM_PREPUSH_PRINT_PLAN seam, so it costs milliseconds instead of running the
# doc generators. The generators are already covered by CI's config_matrix and
# docs_regen jobs; what was never covered is this parsing, which is where the
# tag bug lived.
set -eu

ROOT=$(git rev-parse --show-toplevel)
HOOK="$ROOT/scripts/pre_push_docs_check.sh"
URL="https://github.com/benseverndev-oss/goldenmatch.git"
ZERO=0000000000000000000000000000000000000000

# CI checks out at depth 1, so origin/main may not be a resolvable ref here.
# The one case that needs it is SKIPPED and counted, never silently passed -- a
# skip that reads as a pass is how a check stops firing without anyone noticing.
MAIN=$(git rev-parse origin/main 2>/dev/null || echo "")

fails=0
skips=0

check() {
  name=$1; want=$2; input=$3
  got=$(printf '%s\n' "$input" | GM_PREPUSH_PRINT_PLAN=1 sh "$HOOK" origin "$URL" 2>/dev/null || true)
  if [ "$got" = "$want" ]; then
    echo "ok   - $name"
  else
    echo "FAIL - $name"
    echo "       want: [$want]"
    echo "       got:  [$got]"
    fails=$((fails + 1))
  fi
}

# THE regression. A tag push carries no new commits, and a back-fill tag points
# at an OLDER commit, so gating it diffs backwards against main and blocks a
# push that adds nothing.
check "tag push is not gated" \
  "" \
  "refs/tags/goldencheck-v3.5.0 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/goldencheck-v3.5.0 $ZERO"

check "several tags at once are not gated" \
  "" \
  "refs/tags/a-v1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/a-v1 $ZERO
refs/tags/b-v2 bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb refs/tags/b-v2 $ZERO"

# The cases that must STILL be gated, so the fix above did not quietly turn the
# hook off for everything.
check "existing branch gates from its remote sha" \
  "base=cccccccccccccccccccccccccccccccccccccccc head=dddddddddddddddddddddddddddddddddddddddd" \
  "refs/heads/f dddddddddddddddddddddddddddddddddddddddd refs/heads/f cccccccccccccccccccccccccccccccccccccccc"

if [ -n "$MAIN" ]; then
  check "brand-new branch falls back to origin/main" \
    "base=$MAIN head=dddddddddddddddddddddddddddddddddddddddd" \
    "refs/heads/new dddddddddddddddddddddddddddddddddddddddd refs/heads/new $ZERO"
else
  echo "SKIP - brand-new branch falls back to origin/main (no origin/main ref)"
  skips=$((skips + 1))
fi

check "branch deletion is not gated" \
  "" \
  "(delete) $ZERO refs/heads/gone cccccccccccccccccccccccccccccccccccccccc"

check "empty stdin is not gated" "" ""

# A tag riding along with a branch must not suppress the branch's own gate.
check "tag alongside a branch still gates the branch" \
  "base=cccccccccccccccccccccccccccccccccccccccc head=dddddddddddddddddddddddddddddddddddddddd" \
  "refs/tags/v1 aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa refs/tags/v1 $ZERO
refs/heads/f dddddddddddddddddddddddddddddddddddddddd refs/heads/f cccccccccccccccccccccccccccccccccccccccc"

# A non-goldenmatch remote exits 0 before parsing anything, so every case above
# would pass vacuously against the wrong URL. Pin that the URL gate is the one
# being satisfied, not bypassed.
plan=$(printf '%s\n' "refs/heads/f dddddddddddddddddddddddddddddddddddddddd refs/heads/f cccccccccccccccccccccccccccccccccccccccc" \
  | GM_PREPUSH_PRINT_PLAN=1 sh "$HOOK" origin "https://github.com/someone/other.git" 2>/dev/null || true)
if [ -z "$plan" ]; then
  echo "ok   - an unrelated remote is passed through untouched"
else
  echo "FAIL - an unrelated remote should not be gated, got [$plan]"
  fails=$((fails + 1))
fi

if [ "$fails" -eq 0 ]; then
  echo ""
  echo "all ref-parsing cases passed ($skips skipped)"
  exit 0
fi
echo ""
echo "$fails case(s) failed"
exit 1
