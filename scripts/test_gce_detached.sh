#!/usr/bin/env bash
# Self-contained test for scripts/gce_detached.sh, against a stubbed host.
#
# The helper exists because a real SSH drop killed run 32259409214, and the
# failure it guards against is by nature not reproducible on demand. So the
# logic is tested here instead: a `gcloud` / `docker` stub stands in for the
# node, and the outcomes that matter are asserted.
#
#   1. success   -- streams output INCLUDING the last line, returns 0
#   2. failure   -- the body's exit code is the function's exit code
#   3. killed    -- process gone, no exit code written -> ::error:: and rc 1
#   4. no probe  -- liveness UNANSWERABLE is not death; the body still finishes
#   5. no container -- a vanished container IS death, and is caught promptly
#
# Cases 1 and 4 are the ones written in blood. Case 1's last line is not a
# formality: the loop breaks the moment the rc file appears, normally before the
# poll that would stream the closing chunk, and on these harnesses the closing
# lines ARE the measurements. Case 4 is run 32267837230, where the probe was
# `pgrep ... || echo 0` and `python:3.12-slim` ships no procps -- so "cannot
# answer" and "dead" produced the same value, and a healthy 1M run was declared
# dead at 30 seconds while it went on to finish and write its results.
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
  # `docker inspect -f '{{.State.Running}}' pyenv` -- the host-side container
  # check. STUB_NO_CONTAINER simulates the container having vanished.
  if [ "$1" = "inspect" ]; then
    [ "${STUB_NO_CONTAINER:-0}" = "1" ] && return 1
    echo true; return 0
  fi
  # The liveness probe: `docker exec pyenv sh -c '<probe>'`. STUB_NO_PROBE
  # simulates a container where the probe itself cannot run, which is the
  # condition that broke run 32267837230 (no procps, so no pgrep).
  if [ "$1" = "exec" ] && [ "$2" = "pyenv" ] && [ "$3" = "sh" ]; then
    [ "${STUB_NO_PROBE:-0}" = "1" ] && return 127
    sh -c "$5"; return $?
  fi
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
rm -f "$WD"/*.rc "$WD"/*.log "$WD"/*.pid
# A faithful kill rather than a fake pid: the wrapper records its OWN pid before
# running the body, so planting a stale one would just be overwritten. `kill -9
# $$` takes the script down before it can write its rc, which is exactly what an
# OOM kill looks like from the poller's side.
# Output goes to a FILE, not `$(...)`: command substitution waits for every
# writer on the pipe, and the stub's backgrounded job is one of them.
(cd "$TMP" && gce_detached h dead <<'REMOTE'
echo starting
kill -9 $$
REMOTE
) > "$TMP/case3.out" 2>&1; rc=$?
check "rc" 1 "$rc"
if grep -q '::error::' "$TMP/case3.out"; then echo "  ok   emitted ::error::";
else echo "  FAIL no ::error:: annotation"; fails=$((fails + 1)); fi

echo "case 4: probe unavailable is NOT death"
# The regression from run 32267837230. The old probe collapsed "pgrep is not
# installed" into "process is dead" and killed a healthy 1M run at 30 seconds.
# An unanswerable probe must keep waiting, so this body still returns 0.
rm -f "$WD"/*.rc "$WD"/*.log "$WD"/*.pid
(cd "$TMP" && STUB_NO_PROBE=1 gce_detached h noprobe <<'REMOTE'
sleep 3
echo survived
REMOTE
) > "$TMP/case4.out" 2>&1; rc=$?
check "rc" 0 "$rc"
if grep -q 'survived' "$TMP/case4.out"; then echo "  ok   ran to completion despite an unanswerable probe";
else echo "  FAIL body did not complete"; fails=$((fails + 1)); fi

echo "case 5: vanished container is an unambiguous death"
# Without this branch a dead container falls into `unknown` and the poller idles
# until the job timeout, billing the whole cluster for nothing.
rm -f "$WD"/*.rc "$WD"/*.log "$WD"/*.pid
(cd "$TMP" && STUB_NO_CONTAINER=1 gce_detached h gone <<'REMOTE'
sleep 30
REMOTE
) > "$TMP/case5.out" 2>&1; rc=$?
check "rc" 1 "$rc"
if grep -q '::error::' "$TMP/case5.out"; then echo "  ok   emitted ::error:: promptly";
else echo "  FAIL no ::error:: annotation"; fails=$((fails + 1)); fi

[ "$fails" -eq 0 ] && { echo "PASS"; exit 0; } || { echo "FAILED: $fails"; exit 1; }
