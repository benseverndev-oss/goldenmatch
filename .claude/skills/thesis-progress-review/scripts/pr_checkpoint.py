#!/usr/bin/env python3
"""Incremental pull-request checkpoint helper for the GoldenMatch thesis review skill.

The script intentionally keeps state under Git metadata by default so a review
checkpoint is local to the clone/worktree and never appears as a repository
change. It only mutates state through the explicit ``checkpoint`` command.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlparse

STATE_VERSION = 1
STATE_FILENAME = "goldenmatch-thesis-progress-review.json"
PAGE_SIZE = 100
MAX_PAGES = 1_000
REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


class ReviewStateError(RuntimeError):
    """Raised when repository discovery, GitHub access, or state is invalid."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat_z(value: datetime) -> str:
    value = value.astimezone(timezone.utc).replace(microsecond=0)
    return value.isoformat().replace("+00:00", "Z")


def parse_github_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ReviewStateError(f"Invalid GitHub timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def normalize_repo(value: str) -> str:
    normalized = value.strip().removesuffix(".git").strip("/")
    if not REPO_PATTERN.fullmatch(normalized):
        raise ReviewStateError(
            f"Repository must be in owner/name form, got {value!r}."
        )
    return normalized


def parse_repo_from_remote(remote: str) -> str:
    """Extract owner/name from common GitHub HTTPS and SSH remote forms."""

    value = remote.strip()
    if not value:
        raise ReviewStateError("The origin remote is empty.")

    if value.startswith("git@github.com:"):
        return normalize_repo(value.split(":", 1)[1])

    parsed = urlparse(value)
    if parsed.scheme in {"http", "https", "ssh", "git"}:
        host = (parsed.hostname or "").lower()
        if host != "github.com":
            raise ReviewStateError(
                f"Origin must point to github.com, got host {host or '<none>'!r}."
            )
        return normalize_repo(parsed.path)

    if value.startswith("github.com/"):
        return normalize_repo(value.removeprefix("github.com/"))

    raise ReviewStateError(f"Unsupported GitHub remote format: {remote!r}")


def run_text(command: Sequence[str], *, cwd: Path | None = None) -> str:
    env = os.environ.copy()
    env.setdefault("GH_PAGER", "cat")
    env.setdefault("PAGER", "cat")
    env.setdefault("NO_COLOR", "1")
    try:
        completed = subprocess.run(
            list(command),
            cwd=str(cwd) if cwd else None,
            env=env,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise ReviewStateError(f"Required command not found: {command[0]}") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        rendered = " ".join(command)
        raise ReviewStateError(f"Command failed ({rendered}): {detail}")
    return completed.stdout.strip()


def infer_repo(explicit: str | None = None) -> str:
    if explicit:
        return normalize_repo(explicit)

    github_repository = os.environ.get("GITHUB_REPOSITORY")
    if github_repository:
        return normalize_repo(github_repository)

    remote = run_text(["git", "remote", "get-url", "origin"])
    return parse_repo_from_remote(remote)


def resolve_state_path(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()

    git_path = run_text(["git", "rev-parse", "--git-path", STATE_FILENAME])
    return Path(git_path).expanduser().resolve()


def load_state(path: Path, repo: str) -> dict[str, Any] | None:
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewStateError(f"Cannot read checkpoint state at {path}: {exc}") from exc

    if not isinstance(payload, dict):
        raise ReviewStateError(f"Checkpoint state at {path} must be a JSON object.")
    if payload.get("version") != STATE_VERSION:
        raise ReviewStateError(
            f"Unsupported checkpoint version at {path}: {payload.get('version')!r}."
        )
    if payload.get("repository") != repo:
        raise ReviewStateError(
            "Checkpoint repository mismatch: "
            f"state is for {payload.get('repository')!r}, current repository is {repo!r}."
        )

    number = payload.get("last_reviewed_pr_number")
    if not isinstance(number, int) or number <= 0:
        raise ReviewStateError(
            f"Checkpoint at {path} has an invalid last_reviewed_pr_number."
        )
    return payload


def gh_api_json(repo: str, endpoint: str, fields: Mapping[str, Any] | None = None) -> Any:
    command = ["gh", "api", "--method", "GET", endpoint]
    if fields:
        for key, value in fields.items():
            command.extend(["-f", f"{key}={value}"])
    output = run_text(command)
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ReviewStateError(
            f"GitHub CLI returned invalid JSON for {repo} endpoint {endpoint}: {exc}"
        ) from exc


def compact_pr(raw: Mapping[str, Any]) -> dict[str, Any]:
    user = raw.get("user") or {}
    base = raw.get("base") or {}
    head = raw.get("head") or {}
    labels = raw.get("labels") or []
    return {
        "number": int(raw["number"]),
        "title": raw.get("title") or "",
        "author": user.get("login"),
        "state": raw.get("state"),
        "draft": bool(raw.get("draft", False)),
        "created_at": raw.get("created_at"),
        "updated_at": raw.get("updated_at"),
        "merged_at": raw.get("merged_at"),
        "closed_at": raw.get("closed_at"),
        "base_ref": base.get("ref"),
        "head_ref": head.get("ref"),
        "labels": [label.get("name") for label in labels if label.get("name")],
        "url": raw.get("html_url"),
        "body": raw.get("body") or "",
    }


def collect_new_prs(
    fetch_page: Callable[[int], Sequence[Mapping[str, Any]]],
    *,
    checkpoint_number: int | None,
    cutoff: datetime,
) -> list[dict[str, Any]]:
    """Collect PRs in reverse API order, returning a chronological unique list."""

    selected: list[dict[str, Any]] = []
    seen: set[int] = set()

    for page in range(1, MAX_PAGES + 1):
        raw_page = fetch_page(page)
        if not isinstance(raw_page, Sequence) or isinstance(raw_page, (str, bytes)):
            raise ReviewStateError(f"GitHub pull request page {page} is not a JSON array.")
        if not raw_page:
            break

        reached_boundary = False
        for raw in raw_page:
            if not isinstance(raw, Mapping):
                raise ReviewStateError(
                    f"GitHub pull request page {page} contains a non-object item."
                )
            number = int(raw["number"])
            if number in seen:
                continue
            seen.add(number)

            if checkpoint_number is not None:
                if number <= checkpoint_number:
                    reached_boundary = True
                    break
            else:
                created_at = parse_github_timestamp(str(raw.get("created_at")))
                if created_at < cutoff:
                    reached_boundary = True
                    break

            selected.append(compact_pr(raw))

        if reached_boundary or len(raw_page) < PAGE_SIZE:
            break
    else:
        raise ReviewStateError(
            f"Stopped after {MAX_PAGES} GitHub API pages without finding a review boundary."
        )

    selected.sort(key=lambda pr: (parse_github_timestamp(pr["created_at"]), pr["number"]))
    return selected


def fetch_new_prs(
    repo: str,
    state: Mapping[str, Any] | None,
    *,
    lookback_days: int,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], datetime]:
    if lookback_days <= 0:
        raise ReviewStateError("--lookback-days must be greater than zero.")

    current_time = (now or utc_now()).astimezone(timezone.utc)
    cutoff = current_time - timedelta(days=lookback_days)
    checkpoint_number = (
        int(state["last_reviewed_pr_number"]) if state is not None else None
    )

    def fetch_page(page: int) -> Sequence[Mapping[str, Any]]:
        payload = gh_api_json(
            repo,
            f"repos/{repo}/pulls",
            {
                "state": "all",
                "sort": "created",
                "direction": "desc",
                "per_page": PAGE_SIZE,
                "page": page,
            },
        )
        if not isinstance(payload, list):
            raise ReviewStateError(
                f"GitHub pull request endpoint returned {type(payload).__name__}, expected list."
            )
        return payload

    return (
        collect_new_prs(
            fetch_page,
            checkpoint_number=checkpoint_number,
            cutoff=cutoff,
        ),
        cutoff,
    )


def build_checkpoint(
    repo: str,
    raw_pr: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    number = int(raw_pr["number"])
    if number <= 0:
        raise ReviewStateError("Checkpoint pull request number must be positive.")

    if existing is not None:
        previous = int(existing["last_reviewed_pr_number"])
        if number < previous:
            raise ReviewStateError(
                f"Refusing to move checkpoint backward from PR #{previous} to PR #{number}."
            )

    return {
        "version": STATE_VERSION,
        "repository": repo,
        "last_reviewed_pr_number": number,
        "last_reviewed_pr_created_at": raw_pr.get("created_at"),
        "last_reviewed_pr_title": raw_pr.get("title") or "",
        "last_reviewed_pr_url": raw_pr.get("html_url"),
        "last_reviewed_pr_state": raw_pr.get("state"),
        "last_reviewed_pr_merged_at": raw_pr.get("merged_at"),
        "last_run_at": isoformat_z(now or utc_now()),
    }


def write_state_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise ReviewStateError(f"Cannot write checkpoint state at {path}: {exc}") from exc


def checkpoint_candidate(prs: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    if not prs:
        return None
    last = prs[-1]
    return {
        "number": last["number"],
        "title": last["title"],
        "created_at": last["created_at"],
        "url": last["url"],
    }


def command_scan(args: argparse.Namespace) -> int:
    repo = infer_repo(args.repo)
    state_path = resolve_state_path(args.state_file)
    state = load_state(state_path, repo)
    prs, cutoff = fetch_new_prs(
        repo,
        state,
        lookback_days=args.lookback_days,
    )
    result = {
        "repository": repo,
        "mode": "incremental" if state else "first-run-lookback",
        "lookback_days": args.lookback_days,
        "first_run_cutoff": isoformat_z(cutoff) if state is None else None,
        "state_file": str(state_path),
        "previous_checkpoint": state,
        "new_pr_count": len(prs),
        "checkpoint_candidate": checkpoint_candidate(prs),
        "pull_requests": prs,
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def command_show_state(args: argparse.Namespace) -> int:
    repo = infer_repo(args.repo)
    state_path = resolve_state_path(args.state_file)
    state = load_state(state_path, repo)
    print(
        json.dumps(
            {
                "repository": repo,
                "state_file": str(state_path),
                "checkpoint": state,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def command_checkpoint(args: argparse.Namespace) -> int:
    repo = infer_repo(args.repo)
    state_path = resolve_state_path(args.state_file)
    existing = load_state(state_path, repo)
    raw_pr = gh_api_json(repo, f"repos/{repo}/pulls/{args.through}")
    if not isinstance(raw_pr, Mapping):
        raise ReviewStateError(
            f"GitHub returned {type(raw_pr).__name__} for PR #{args.through}, expected object."
        )
    payload = build_checkpoint(repo, raw_pr, existing)
    write_state_atomic(state_path, payload)
    print(json.dumps({"state_file": str(state_path), "checkpoint": payload}, indent=2))
    return 0


def add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--repo",
        help="GitHub repository in owner/name form. Defaults to GITHUB_REPOSITORY or origin.",
    )
    parser.add_argument(
        "--state-file",
        help=(
            "Override the checkpoint path. Defaults to a file under Git metadata "
            f"({STATE_FILENAME})."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find pull requests not yet included in a GoldenMatch thesis-progress review "
            "and explicitly checkpoint the highest fully reviewed PR."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan = subparsers.add_parser(
        "scan",
        help="Print new PR metadata without modifying the checkpoint.",
    )
    add_common_arguments(scan)
    scan.add_argument(
        "--lookback-days",
        type=int,
        default=4,
        help="Rolling UTC lookback used only when no checkpoint exists (default: 4).",
    )
    scan.set_defaults(handler=command_scan)

    checkpoint = subparsers.add_parser(
        "checkpoint",
        help="Advance state through the highest PR that was fully reviewed.",
    )
    add_common_arguments(checkpoint)
    checkpoint.add_argument(
        "--through",
        type=int,
        required=True,
        metavar="PR_NUMBER",
        help="Highest fully reviewed pull request number.",
    )
    checkpoint.set_defaults(handler=command_checkpoint)

    show_state = subparsers.add_parser(
        "show-state",
        help="Print the current local checkpoint without changing it.",
    )
    add_common_arguments(show_state)
    show_state.set_defaults(handler=command_show_state)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except ReviewStateError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
