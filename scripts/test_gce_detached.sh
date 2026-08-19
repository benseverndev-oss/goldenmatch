#!/usr/bin/env bash
# Self-contained test for scripts/gce_detached.sh, against a stubbed host.
#
# The helper exists because a real SSH drop killed run 32259409214, and the
# failure it guards against is by nature not reproducible on demand. So the
# logic is tested here instead: a `gcloud` / `docker` stub stands in for the
# node, and the three outcomes that matter are asserted.
#
#   1. success  -- streams output INCLUDING the last line, returns 0
#   2. failure  -- the body's exit code is the function's exit code
#   3. killed   -- process gone with no exit code written -> ::error:: and rc 1
#
# Case 1's last line is not a formality. The loop breaks the moment the rc file
# appears, which is normally before the poll that would stream the closing
# chunk, and on these harnesses the closing lines ARE the measurements. An
# earlier draft of the helper dropped them; this test is why that was caught.
#
# Run: bash scripts/test_gce_detached.sh
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
export WD="$TMP/w"; mkdir -p "$WD"

gcloud() {
  if [ "$2" = "scp" ]; then cp "$4" "$WD/$(basename "$5")"; return 0; fi
  if [ "$2" = "ssh" ]; then
    local cmd=""; shift
    while [ $# -gt 0 ]; do [ "$1" = "--command" ] && { cmd="$2"; break; }; shift; done
    HOME="$WD" sh -c "$cmd"; return $?
  fi
}
docker() {
  if [ "$1" = "exec" ] && [ "$2" = "-d" ]; then ( HOME="$WD" sh -c "$6" ) & return 0; fi
  if [ "$1" = "exec" ] && [ "$3" = "pgrep" ]; then [ "${STUB_DEAD:-0}" = "1" ] && return 1 || return 0; fi
  return 0
}
export -f gcloud docker

# shellcheck source=/dev/null
. "$HERE/gce_detached.sh"
export GCE_DETACHED_POLL_S=1 GCE_DETACHED_WORKDIR="$WD"

fails=0
check() { # check <label> <expected> <actual>
  if [ "$2" = "$3" ]; then echo "  ok   $1"; else echo "  FAIL $1: expected '$2', got '$3'"; fails=$((fails + 1)); fi
}

echo "case 1: success, streams the closing line"
out=$(cd "$TMP" && gce_detached h ok <<'REMOTE'
echo first
echo "RESULT: ap=0.700397"
REMOTE
); rc=$?
check "rc" 0 "$rc"
case "$out" in *"RESULT: ap=0.700397"*) echo "  ok   closing line streamed";;
                *) echo "  FAIL closing line missing from output"; fails=$((fails + 1));; esac

echo "case 2: the body's exit code propagates"
rm -f "$WD"/*.rc "$WD"/*.log
(cd "$TMP" && gce_detached h bad <<'REMOTE'
echo before
exit 42
REMOTE
) >/dev/null 2>&1; rc=$?
check "rc" 42 "$rc"

echo "case 3: killed without an exit code"
rm -f "$WD"/*.rc "$WD"/*.log
# Output goes to a FILE, not `$(...)`: command substitution waits for every
# writer on the pipe, and the stub's backgrounded job is one of them, so
# capturing this case inline blocks for the body's full sleep.
(cd "$TMP" && STUB_DEAD=1 gce_detached h dead <<'REMOTE'
echo starting
sleep 8
REMOTE
) > "$TMP/case3.out" 2>&1; rc=$?
check "rc" 1 "$rc"
if grep -q '::error::' "$TMP/case3.out"; then echo "  ok   emitted ::error::";
else echo "  FAIL no ::error:: annotation"; fails=$((fails + 1)); fi

[ "$fails" -eq 0 ] && { echo "PASS"; exit 0; } || { echo "FAILED: $fails"; exit 1; }
