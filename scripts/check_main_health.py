#!/usr/bin/env python3
"""main-health: surface any workflow that is currently RED on `main`.

Motivation
----------
Only `ci-required` gates PRs. The suite also carries ~90 *separate* workflows
(`push` / `schedule` triggered) that are NOT in `ci-required` -- so when one of
them goes red on `main` (e.g. `goldengraph-pipeline`, `bench-suggest-quality`'s
nightly gym-gate, `publish-containers`), nothing surfaces it: PRs stay green,
the merge queue is unaffected, and the red rots for days. This closes that gap.

What it does
------------
For every ACTIVE workflow, it reads the latest COMPLETED run whose head branch
is `main` and classifies its conclusion. Workflows with no main run (pure
`workflow_dispatch` / `pull_request` lanes) are skipped -- absence of a main run
is not a failure. The RED set is written to the step summary and reconciled into
a single tracking issue (the durable, notifying surface): opened/updated when
anything is red, closed when `main` is clean again.

Deliberately exits 0 even when reds exist -- this workflow is the *watcher*; its
own run status must not become just another silent red on `main`. The signal is
the tracking issue, not this job's conclusion. (`--fail-on-red` flips that for
local/manual use.)

Stdlib only; all GitHub IO goes through the pre-authenticated `gh` CLI.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import Iterable, Optional

# Conclusions that mean "this lane is broken on main".
RED_CONCLUSIONS = frozenset({"failure", "timed_out", "startup_failure"})
# Conclusions we treat as fine / not-actionable (cancelled is usually a manual
# supersede or a newer run in flight, not a broken lane).
OK_CONCLUSIONS = frozenset({"success", "skipped", "neutral", "cancelled", "action_required", None})

TRACKER_MARKER = "<!-- main-health-tracker -->"
TRACKER_LABEL = "main-health"


def classify(conclusion: Optional[str]) -> str:
    """Map a run conclusion to 'red' | 'ok'. Unknown conclusions are red (fail loud)."""
    if conclusion in RED_CONCLUSIONS:
        return "red"
    if conclusion in OK_CONCLUSIONS:
        return "ok"
    # An unrecognised conclusion string is treated as red so a new GitHub status
    # value can never silently pass as healthy.
    return "red"


def red_workflows(runs: Iterable[dict]) -> list[dict]:
    """Given [{name, conclusion, html_url, ...}] pick the red ones, name-sorted."""
    reds = [r for r in runs if classify(r.get("conclusion")) == "red"]
    return sorted(reds, key=lambda r: r.get("name", "").lower())


# --------------------------------------------------------------------------- IO


def _gh(*args: str, check: bool = True) -> str:
    """Run `gh <args>` and return stdout. Raises on non-zero unless check=False."""
    proc = subprocess.run(
        ["gh", *args],
        capture_output=True,
        text=True,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout


def _gh_api(path: str, *extra: str) -> object:
    return json.loads(_gh("api", path, *extra) or "null")


def list_active_workflows(repo: str) -> list[dict]:
    out: list[dict] = []
    page = 1
    while True:
        data = _gh_api(f"repos/{repo}/actions/workflows?per_page=100&page={page}")
        chunk = (data or {}).get("workflows", [])
        if not chunk:
            break
        out.extend(w for w in chunk if w.get("state") == "active")
        if len(chunk) < 100:
            break
        page += 1
    return out


def latest_main_run(repo: str, workflow_id: int) -> Optional[dict]:
    """Latest COMPLETED run of this workflow with head branch = main, or None."""
    data = _gh_api(
        f"repos/{repo}/actions/workflows/{workflow_id}/runs"
        f"?branch=main&status=completed&exclude_pull_requests=true&per_page=1"
    )
    runs = (data or {}).get("workflow_runs", [])
    return runs[0] if runs else None


def collect_main_runs(repo: str) -> list[dict]:
    """One record per workflow that has run on main: name, conclusion, html_url."""
    records: list[dict] = []
    for wf in list_active_workflows(repo):
        run = latest_main_run(repo, wf["id"])
        if run is None:
            continue  # never runs on main (dispatch/PR-only) -- not a failure
        records.append(
            {
                "name": wf["name"],
                "path": wf.get("path", ""),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url", ""),
                "run_number": run.get("run_number"),
                "updated_at": run.get("updated_at", ""),
            }
        )
    return records


# --------------------------------------------------------------- surfaces (issue)


def find_tracker_issue(repo: str) -> Optional[dict]:
    data = _gh_api(
        f"repos/{repo}/issues?state=open&labels={TRACKER_LABEL}&per_page=20"
    )
    for issue in data or []:
        if "pull_request" in issue:
            continue
        if TRACKER_MARKER in (issue.get("body") or ""):
            return issue
    return None


def _issue_body(reds: list[dict]) -> str:
    lines = [
        TRACKER_MARKER,
        "",
        f"**{len(reds)} workflow(s) are currently failing on `main`.**",
        "",
        "This issue is maintained automatically by the `main-health` workflow. "
        "It is opened/updated when a non-`ci-required` lane goes red on `main` "
        "and closed automatically once `main` is clean again.",
        "",
        "| Workflow | Conclusion | Latest main run |",
        "| --- | --- | --- |",
    ]
    for r in reds:
        run_ref = f"[#{r['run_number']}]({r['html_url']})" if r.get("html_url") else "-"
        lines.append(f"| `{r['name']}` | {r['conclusion']} | {run_ref} |")
    lines.append("")
    lines.append("_Fix the lane, then this issue closes itself on the next scheduled run._")
    return "\n".join(lines)


def ensure_label(repo: str) -> None:
    # Idempotent: create the label if missing; ignore "already exists".
    _gh(
        "label", "create", TRACKER_LABEL,
        "--repo", repo,
        "--color", "B60205",
        "--description", "Automated main-branch health tracker",
        "--force",
        check=False,
    )


def upsert_tracker(repo: str, reds: list[dict]) -> str:
    existing = find_tracker_issue(repo)
    if reds:
        ensure_label(repo)
        title = f"main-health: {len(reds)} workflow(s) failing on main"
        body = _issue_body(reds)
        if existing:
            _gh(
                "issue", "edit", str(existing["number"]),
                "--repo", repo, "--title", title, "--body", body,
            )
            return f"updated tracking issue #{existing['number']}"
        out = _gh(
            "issue", "create", "--repo", repo,
            "--title", title, "--body", body, "--label", TRACKER_LABEL,
        )
        return f"opened tracking issue: {out.strip()}"
    # Clean
    if existing:
        _gh(
            "issue", "comment", str(existing["number"]), "--repo", repo,
            "--body", "`main` is green again across all lanes -- auto-closing.",
        )
        _gh("issue", "close", str(existing["number"]), "--repo", repo)
        return f"closed tracking issue #{existing['number']} (main clean)"
    return "main clean; no tracker issue"


# ------------------------------------------------------------------------- main


def write_step_summary(records: list[dict], reds: list[dict]) -> None:
    import os

    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## main-health", ""]
    if reds:
        lines.append(f"🔴 **{len(reds)} of {len(records)} lanes red on `main`:**")
        lines.append("")
        lines.append("| Workflow | Conclusion | Run |")
        lines.append("| --- | --- | --- |")
        for r in reds:
            run_ref = f"[#{r['run_number']}]({r['html_url']})" if r.get("html_url") else "-"
            lines.append(f"| `{r['name']}` | {r['conclusion']} | {run_ref} |")
    else:
        lines.append(f"🟢 all {len(records)} main lanes green.")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", default="benseverndev-oss/goldenmatch")
    ap.add_argument(
        "--no-issue", action="store_true",
        help="skip tracking-issue reconciliation (report only)",
    )
    ap.add_argument(
        "--fail-on-red", action="store_true",
        help="exit 1 when any lane is red (default: exit 0, the issue is the signal)",
    )
    args = ap.parse_args(argv)

    records = collect_main_runs(args.repo)
    reds = red_workflows(records)

    print(f"scanned {len(records)} workflow(s) with a main run; {len(reds)} red")
    for r in reds:
        print(f"  RED  {r['name']}  ({r['conclusion']})  {r['html_url']}")

    write_step_summary(records, reds)

    if not args.no_issue:
        try:
            print(upsert_tracker(args.repo, reds))
        except RuntimeError as exc:
            # Never let issue plumbing mask the health signal.
            print(f"::warning::tracker issue update failed: {exc}", file=sys.stderr)

    return 1 if (reds and args.fail_on_red) else 0


if __name__ == "__main__":
    raise SystemExit(main())
