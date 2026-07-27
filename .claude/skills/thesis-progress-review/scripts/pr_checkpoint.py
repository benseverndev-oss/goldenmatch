#!/usr/bin/env python3
"""Checkpointed pull-request scanner for the thesis-progress-review skill.

The checkpoint is intentionally local to the clone/worktree. By default it lives
under Git metadata (``git rev-parse --git-path``), so it cannot be committed by
accident.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

SCHEMA_VERSION = 2
DEFAULT_STATE_NAME = "goldenmatch-thesis-progress-review.json"
PER_PAGE = 100


class CheckpointError(RuntimeError):
    """A user-facing scanner/checkpoint error."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def run_command(args: list[str]) -> str:
    try:
        proc = subprocess.run(
            args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise CheckpointError(f"required command not found: {args[0]}") from exc
    except subprocess.CalledProcessError as exc:
        detail = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        raise CheckpointError(f"{' '.join(args)} failed: {detail}") from exc
    return proc.stdout.strip()


def parse_repo_from_remote(remote: str) -> str:
    """Return ``owner/repo`` from common Git remote URL forms."""
    value = remote.strip().rstrip("/")
    patterns = (
        r"^(?:https?://|ssh://git@)[^/]+/(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
        r"^[^@]+@[^:]+:(?P<repo>[^/]+/[^/]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.match(pattern, value)
        if match:
            return match.group("repo")
    raise CheckpointError(f"cannot infer owner/repo from origin URL: {remote!r}")


def detect_repo() -> str:
    try:
        remote = run_command(["git", "remote", "get-url", "origin"])
        return parse_repo_from_remote(remote)
    except CheckpointError:
        try:
            repo = run_command(
                ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"]
            )
        except CheckpointError as exc:
            raise CheckpointError(
                "cannot determine repository; pass --repo owner/name or configure origin"
            ) from exc
        if "/" not in repo:
            raise CheckpointError(f"unexpected repository name from gh: {repo!r}")
        return repo


def default_state_path() -> Path:
    try:
        raw = run_command(["git", "rev-parse", "--git-path", DEFAULT_STATE_NAME])
    except CheckpointError as exc:
        raise CheckpointError(
            "not inside a Git worktree; pass --state-file explicitly"
        ) from exc
    return Path(raw)


def load_state(path: Path, repo: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "schema_version": SCHEMA_VERSION,
            "repo": repo,
            "last_reviewed_pr": None,
            "tracked_open_prs": {},
        }
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CheckpointError(f"cannot read checkpoint {path}: {exc}") from exc
    if data.get("repo") != repo:
        raise CheckpointError(
            f"checkpoint belongs to {data.get('repo')!r}, not requested repo {repo!r}"
        )
    version = int(data.get("schema_version", 1))
    if version not in (1, SCHEMA_VERSION):
        raise CheckpointError(
            f"unsupported checkpoint schema {version}; expected 1 or {SCHEMA_VERSION}"
        )
    data.setdefault("tracked_open_prs", {})
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def gh_api_json(endpoint: str) -> Any:
    raw = run_command(
        [
            "gh",
            "api",
            "-H",
            "Accept: application/vnd.github+json",
            "-H",
            "X-GitHub-Api-Version: 2022-11-28",
            endpoint,
        ]
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CheckpointError(f"gh api returned invalid JSON for {endpoint}") from exc


def normalize_pr(pr: dict[str, Any]) -> dict[str, Any]:
    merged_at = pr.get("merged_at")
    state = "merged" if merged_at else str(pr.get("state", "open")).lower()
    return {
        "number": int(pr["number"]),
        "title": str(pr.get("title") or ""),
        "url": str(pr.get("html_url") or ""),
        "state": state,
        "draft": bool(pr.get("draft", False)),
        "created_at": pr.get("created_at"),
        "updated_at": pr.get("updated_at"),
        "merged_at": merged_at,
        "closed_at": pr.get("closed_at"),
        "base": (pr.get("base") or {}).get("ref"),
        "head": (pr.get("head") or {}).get("ref"),
        "head_sha": (pr.get("head") or {}).get("sha"),
        "author": (pr.get("user") or {}).get("login"),
    }


def tracked_signature(pr: dict[str, Any]) -> dict[str, Any]:
    return {
        "head_sha": pr.get("head_sha"),
        "state": pr.get("state"),
        "draft": bool(pr.get("draft", False)),
        "title": pr.get("title"),
        "base": pr.get("base"),
    }


def fetch_pr(repo: str, number: int) -> dict[str, Any]:
    return normalize_pr(gh_api_json(f"repos/{repo}/pulls/{number}"))


def fetch_new_prs(
    repo: str,
    *,
    previous_checkpoint: int | None,
    cutoff: datetime | None,
    api: Callable[[str], Any] = gh_api_json,
) -> list[dict[str, Any]]:
    """Fetch newly created PRs, newest-first from GitHub, then return oldest-first."""
    selected: list[dict[str, Any]] = []
    page = 1
    done = False
    while not done:
        endpoint = (
            f"repos/{repo}/pulls?state=all&sort=created&direction=desc"
            f"&per_page={PER_PAGE}&page={page}"
        )
        batch = api(endpoint)
        if not isinstance(batch, list):
            raise CheckpointError(f"unexpected pulls response for page {page}")
        if not batch:
            break
        for raw in batch:
            number = int(raw["number"])
            created_at = parse_timestamp(raw.get("created_at"))
            if previous_checkpoint is not None and number <= previous_checkpoint:
                done = True
                break
            if cutoff is not None and created_at is not None and created_at < cutoff:
                done = True
                break
            selected.append(normalize_pr(raw))
        if len(batch) < PER_PAGE:
            break
        page += 1
    selected.sort(key=lambda item: (item.get("created_at") or "", item["number"]))
    return selected


def changed_tracked_prs(
    repo: str,
    tracked: dict[str, Any],
    *,
    fetch: Callable[[str, int], dict[str, Any]] = fetch_pr,
) -> list[dict[str, Any]]:
    changed: list[dict[str, Any]] = []
    for key, prior in sorted(tracked.items(), key=lambda item: int(item[0])):
        number = int(key)
        current = fetch(repo, number)
        if tracked_signature(current) != {
            "head_sha": prior.get("head_sha"),
            "state": prior.get("state"),
            "draft": bool(prior.get("draft", False)),
            "title": prior.get("title"),
            "base": prior.get("base"),
        }:
            current["previous_snapshot"] = prior
            changed.append(current)
    return changed


def scan_command(args: argparse.Namespace) -> int:
    repo = args.repo or detect_repo()
    state_path = Path(args.state_file) if args.state_file else default_state_path()
    state = load_state(state_path, repo)
    previous = state.get("last_reviewed_pr")
    if previous is not None:
        previous = int(previous)
        cutoff = None
        mode = "checkpoint"
    else:
        if args.lookback_days <= 0:
            raise CheckpointError("--lookback-days must be positive")
        cutoff = utc_now() - timedelta(days=args.lookback_days)
        mode = "lookback"

    new_prs = fetch_new_prs(
        repo, previous_checkpoint=previous, cutoff=cutoff
    )
    tracked_updates = changed_tracked_prs(
        repo, state.get("tracked_open_prs") or {}
    )
    candidate = new_prs[-1]["number"] if new_prs else previous
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repo": repo,
        "state_file": str(state_path),
        "mode": mode,
        "generated_at": iso_utc(utc_now()),
        "lookback_days": args.lookback_days if mode == "lookback" else None,
        "cutoff": iso_utc(cutoff) if cutoff else None,
        "previous_checkpoint": previous,
        "checkpoint_candidate": candidate,
        "new_pr_count": len(new_prs),
        "tracked_update_count": len(tracked_updates),
        "review_item_count": len(new_prs) + len(tracked_updates),
        "pull_requests": new_prs,
        "tracked_updates": tracked_updates,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


def refresh_tracked_open_prs(
    repo: str,
    existing: dict[str, Any],
    reviewed_new_prs: Iterable[dict[str, Any]],
    *,
    fetch: Callable[[str, int], dict[str, Any]] = fetch_pr,
) -> dict[str, Any]:
    tracked: dict[str, Any] = {}
    candidates = {int(number) for number in existing}
    candidates.update(int(pr["number"]) for pr in reviewed_new_prs)
    for number in sorted(candidates):
        current = fetch(repo, number)
        if current.get("state") == "open":
            tracked[str(number)] = tracked_signature(current)
    return tracked


def checkpoint_command(args: argparse.Namespace) -> int:
    repo = args.repo or detect_repo()
    state_path = Path(args.state_file) if args.state_file else default_state_path()
    state = load_state(state_path, repo)
    previous = state.get("last_reviewed_pr")
    previous_int = int(previous) if previous is not None else None
    through = int(args.through)
    if previous_int is not None and through < previous_int:
        raise CheckpointError(
            f"refusing to move checkpoint backwards: {through} < {previous_int}"
        )

    if previous_int is None:
        if args.lookback_days <= 0:
            raise CheckpointError("--lookback-days must be positive")
        checkpoint_cutoff = utc_now() - timedelta(days=args.lookback_days)
    else:
        checkpoint_cutoff = None

    reviewed_new_prs = fetch_new_prs(
        repo,
        previous_checkpoint=previous_int,
        cutoff=checkpoint_cutoff,
    )
    reviewed_new_prs = [pr for pr in reviewed_new_prs if pr["number"] <= through]
    if through != previous_int and through not in {pr["number"] for pr in reviewed_new_prs}:
        boundary = (
            f"the first-run {args.lookback_days}-day window"
            if previous_int is None
            else f"PRs after checkpoint #{previous_int}"
        )
        raise CheckpointError(
            f"PR #{through} is not inside {boundary}; run scan again and use its candidate"
        )
    target = fetch_pr(repo, through)
    if target["number"] != through:
        raise CheckpointError(f"GitHub returned the wrong PR for #{through}")

    tracked = refresh_tracked_open_prs(
        repo,
        state.get("tracked_open_prs") or {},
        reviewed_new_prs,
    )
    updated = {
        "schema_version": SCHEMA_VERSION,
        "repo": repo,
        "last_reviewed_pr": through,
        "last_reviewed_created_at": target.get("created_at"),
        "last_reviewed_url": target.get("url"),
        "last_reviewed_at": iso_utc(utc_now()),
        "tracked_open_prs": tracked,
    }
    write_json_atomic(state_path, updated)
    print(
        json.dumps(
            {
                "repo": repo,
                "state_file": str(state_path),
                "previous_checkpoint": previous_int,
                "last_reviewed_pr": through,
                "tracked_open_prs": sorted(int(n) for n in tracked),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan or advance the local thesis-progress PR checkpoint."
    )
    parser.add_argument("--repo", help="GitHub repository in owner/name form")
    parser.add_argument(
        "--state-file",
        help=(
            "override checkpoint path; default is Git metadata via "
            f"`git rev-parse --git-path {DEFAULT_STATE_NAME}`"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser("scan", help="list PRs needing review")
    scan.add_argument(
        "--lookback-days",
        type=int,
        default=4,
        help="first-run lookback window; ignored after a checkpoint exists (default: 4)",
    )
    scan.set_defaults(func=scan_command)

    checkpoint = subparsers.add_parser(
        "checkpoint", help="advance after every reported PR has been reviewed"
    )
    checkpoint.add_argument("--through", type=int, required=True, metavar="PR_NUMBER")
    checkpoint.add_argument(
        "--lookback-days",
        type=int,
        default=4,
        help=(
            "first-run review window; pass the same value used by scan "
            "(ignored after a checkpoint exists; default: 4)"
        ),
    )
    checkpoint.set_defaults(func=checkpoint_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except CheckpointError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
