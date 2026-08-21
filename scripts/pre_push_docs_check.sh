#!/bin/sh
# Derived-doc gate, run before a push instead of after a failed CI run.
#
# Two CI gates fail for reasons a generator can fix locally in ~30s, but only
# tell you about it after a push, a full CI cycle, and a blocked merge queue:
#
#   config_matrix / docs_regen  -- committed generated docs drifted from source
#   docs_staleness (flag rule)  -- a GOLDENMATCH_* flag was added without a
#                                  tuning.mdx entry (NOT auto-fixable: a human
#                                  has to write the entry)
#
# This checks both and BLOCKS. It never edits your working tree: a hook that
# rewrites files mid-push collides with whatever state you were in.
#
# Install (chain it after the oss-push-guard stage, which also reads stdin --
# so buffer stdin once and feed both):
#
#   # in .git/hooks/pre-push, after the guard's own loop:
#   refs=$(cat)                                  # ONCE, at the top of the hook
#   printf '%s\n' "$refs" | <guard logic>
#   printf '%s\n' "$refs" | scripts/pre_push_docs_check.sh "$1" "$2" || exit 1
#
# Escape hatch: SKIP_DOCS_CHECK=1 git push ...
#
# git passes: $1 = remote name, $2 = remote URL. Ref updates arrive on stdin as
#   <local_ref> <local_sha> <remote_ref> <remote_sha>
set -eu

remote_url="${2:-}"
case "$remote_url" in
  *benseverndev-oss/goldenmatch*) : ;;   # the repo whose CI gates these -> check
  *) exit 0 ;;                            # any other remote -> pass through
esac

if [ "${SKIP_DOCS_CHECK:-0}" = "1" ]; then
  echo "pre-push: SKIP_DOCS_CHECK=1 -- skipping the derived-doc gate." >&2
  exit 0
fi

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

zero=0000000000000000000000000000000000000000

# Pick the widest range being pushed, so a multi-ref push is still covered.
# A brand-new branch has no remote_sha; origin/main is then the honest base.
base=""
head=""
while read -r _local_ref local_sha remote_ref remote_sha; do
  [ "$local_sha" = "$zero" ] && continue          # branch deletion -- nothing to check
  # Tags introduce no commits. Whatever a tag points at was already gated when
  # the branch carrying it was pushed, so re-running the generators here only
  # costs 30s. Worse, a tag is frequently cut at an OLDER commit -- the release
  # back-fill tags each package at its version-bump commit -- and the flag rule
  # below would then diff origin/main..<older sha> BACKWARDS and block the push
  # over flags that main added after the tagged commit. Skip the ref entirely
  # rather than trying to pick a base for it.
  case "$remote_ref" in
    refs/tags/*) continue ;;
  esac
  head="$local_sha"
  if [ "$remote_sha" = "$zero" ]; then
    base=$(git rev-parse origin/main 2>/dev/null || echo "")
  else
    base="$remote_sha"
  fi
done

# Nothing being pushed (all deletions, all tags, or empty stdin) -> nothing to
# gate.
[ -n "$head" ] || exit 0

# Test seam: report the range this push resolved to and stop, so the ref-parsing
# rules above can be exercised without paying for the generators. Not read by
# the hook.
if [ "${GM_PREPUSH_PRINT_PLAN:-0}" = "1" ]; then
  echo "base=$base head=$head"
  exit 0
fi

echo "pre-push: checking derived docs (~30s; SKIP_DOCS_CHECK=1 to bypass)..." >&2

# regen_docs.py regenerates into a scratch copy and diffs; it does not touch the
# working tree. uv run is required rather than stylistic -- the generators import
# the config classes to introspect them, so every workspace package must be
# importable. An unsynced workspace is a setup problem, not a clean tree: say so
# rather than passing silently.
if ! out=$(uv run python scripts/regen_docs.py --check 2>&1); then
  case "$out" in
    *ModuleNotFoundError*)
      printf '%s\n' "$out" >&2
      echo "" >&2
      echo "pre-push: the workspace is not synced, so the doc check could not run." >&2
      echo "          Run: uv sync --all-packages" >&2
      echo "          (then re-push; or SKIP_DOCS_CHECK=1 to bypass this once)" >&2
      exit 1
      ;;
  esac
  printf '%s\n' "$out" >&2
  echo "" >&2
  echo "pre-push: committed generated docs are stale." >&2
  echo "          Run: just docs      (regenerates), then review the diff and commit." >&2
  exit 1
fi

# The flag rule. Base/head matter here: it asks what THIS push adds or removes,
# not what the tree looks like. Nothing it finds can be auto-fixed.
if [ -n "$base" ]; then
  if ! out=$(uv run python scripts/check_docs_staleness.py --base "$base" --head "$head" 2>&1); then
    printf '%s\n' "$out" >&2
    echo "" >&2
    echo "pre-push: a GOLDENMATCH_* flag in this push is missing from the canonical" >&2
    echo "          reference. Add it to docs-site/goldenmatch/tuning.mdx and commit." >&2
    exit 1
  fi
fi

echo "pre-push: derived docs OK." >&2
exit 0
