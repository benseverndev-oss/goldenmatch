#!/usr/bin/env bash
# Run a long command in the `pyenv` container on a GCE host, DETACHED from the
# SSH session that starts it, and poll for completion.
#
# ## Why this exists
#
# `gcloud compute ssh HOST --command "<hours of work>"` ties the workload's
# lifetime to one SSH session opened from a GitHub runner. Run 32259409214 lost
# that session 11 minutes into a 50M FS training run:
#
#     [scale] pass 0 on ['blk']: 274,999,996 pairs -> 445 patterns in 174.90s
#     [scale] pass 1 on ['last']: 217,026,993 pairs -> 192 patterns in 143.45s
#     client_loop: send disconnect: Broken pipe
#     ERROR: (gcloud.compute.ssh) [/usr/bin/ssh] exited with return code [255]
#
# The harness was healthy and mid-pass. The transport died, so five VMs and the
# whole run died with it. Longer runs are strictly likelier to hit this, and a
# Splink arm at 50M is the longest thing this lane does.
#
# It costs more than time. The Splink step is `continue-on-error: true` on
# purpose, so a dropped SSH there does not fail the job -- it records
# `splink: MISSING`, which reads as *Splink could not do 50M*. That publishes an
# infrastructure blip as an engine result. A comparison is not fair if one arm's
# transport failures are attributed to the engine.
#
# ## Why `docker exec -d` and not `nohup` / `setsid`
#
# The hosts run Container-Optimized OS, whose userland is deliberately minimal;
# neither `setsid` nor `nohup` is guaranteed. Testing an earlier draft of this
# helper against a stub host failed on exactly that (`setsid: command not
# found`). Docker is guaranteed -- running containers IS what these nodes are
# for -- and `docker exec -d` hands the process to the daemon, which outlives
# any SSH session by construction. Fewer assumptions, and the one it does make
# cannot be false on a node that got this far.
#
# Liveness uses `pgrep` inside the container rather than a stall timeout: a
# quiet stage is not a dead one (Splink's EM iterations go minutes without
# printing), and any threshold long enough to be safe is too long to be useful.
#
# ## Usage
#
#     source scripts/gce_detached.sh
#     gce_detached "$MASTER" fs <<REMOTE
#     export GOLDENMATCH_SPARK_JAR=/w/goldenmatch-spark.jar
#     python spark_fs_train_scale.py --rows 50000000 --out /w/out.json
#     REMOTE
#
# The body runs INSIDE the container (workdir `/w`, which is the host `$HOME`),
# under `set -e`, in a subshell. Writes `<tag>.log` and `<tag>.rc` next to it;
# streams new log lines to stdout. Returns the body's exit code, so a genuine
# failure still fails the step.

gce_detached() {
  local host="$1" tag="$2"
  local script="gce_detached_${tag}.sh"
  local poll="${GCE_DETACHED_POLL_S:-30}"
  # The container's view of the host `$HOME` (`docker run -v $HOME:/w -w /w`).
  # Overridable so this is testable off a real node.
  local wd="${GCE_DETACHED_WORKDIR:-/w}"

  {
    echo '('
    echo 'set -e'
    cat
    echo ')'
    printf 'echo $? > %s/%s.rc\n' "$wd" "$tag"
  } > "$script"

  gcloud compute scp --quiet "$script" "$host:~/$script"
  gcloud compute ssh "$host" --quiet --command \
    "rm -f ~/${tag}.rc ~/${tag}.log && docker exec -d pyenv sh -c 'sh ${wd}/${script} > ${wd}/${tag}.log 2>&1' && echo '[gce_detached] launched ${tag}'"

  local last=0 rc="" n alive
  while true; do
    sleep "$poll"

    # Stream what is new. `|| true` throughout: a failed poll is a blip to
    # retry, not a reason to abandon a job that is still running.
    n=$(gcloud compute ssh "$host" --quiet --command "wc -l < ~/${tag}.log 2>/dev/null || echo 0" 2>/dev/null | tr -dc '0-9') || true
    [ -n "${n:-}" ] || n="$last"
    if [ "$n" -gt "$last" ]; then
      gcloud compute ssh "$host" --quiet --command "sed -n '$((last + 1)),\$p' ~/${tag}.log" 2>/dev/null || true
      last="$n"
    fi

    rc=$(gcloud compute ssh "$host" --quiet --command "cat ~/${tag}.rc 2>/dev/null" 2>/dev/null | tr -dc '0-9') || true
    [ -n "${rc:-}" ] && break

    # No exit code yet: still running, or dead without writing one?
    alive=$(gcloud compute ssh "$host" --quiet --command "docker exec pyenv pgrep -f '${script}' >/dev/null 2>&1 && echo 1 || echo 0" 2>/dev/null | tr -dc '01') || true
    if [ "${alive:-1}" = "0" ]; then
      # The process can exit between that pgrep and the rc write.
      sleep 5
      rc=$(gcloud compute ssh "$host" --quiet --command "cat ~/${tag}.rc 2>/dev/null" 2>/dev/null | tr -dc '0-9') || true
      [ -n "${rc:-}" ] && break
      gcloud compute ssh "$host" --quiet --command "tail -50 ~/${tag}.log" 2>/dev/null || true
      echo "::error::${tag} died without writing an exit code (killed on the host -- the OOM killer is the usual cause)"
      return 1
    fi
  done

  # Final drain. The loop breaks the moment the rc file appears, which is
  # normally BEFORE the poll that would have streamed the last chunk -- and on
  # these harnesses the closing lines are the measurements themselves. Without
  # this the run's own results are the thing most likely to be missing from the
  # log.
  n=$(gcloud compute ssh "$host" --quiet --command "wc -l < ~/${tag}.log 2>/dev/null || echo 0" 2>/dev/null | tr -dc '0-9') || true
  if [ -n "${n:-}" ] && [ "$n" -gt "$last" ]; then
    gcloud compute ssh "$host" --quiet --command "sed -n '$((last + 1)),\$p' ~/${tag}.log" 2>/dev/null || true
  fi

  echo "[gce_detached] ${tag} exited rc=${rc}"
  return "$rc"
}
