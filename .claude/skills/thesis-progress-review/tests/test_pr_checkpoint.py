from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "pr_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("pr_checkpoint", SCRIPT_PATH)
assert SPEC and SPEC.loader
pr_checkpoint = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr_checkpoint)


def raw_pr(number: int, created_at: str, *, title: str | None = None) -> dict:
    return {
        "number": number,
        "title": title or f"PR {number}",
        "body": "body",
        "state": "closed",
        "draft": False,
        "created_at": created_at,
        "updated_at": created_at,
        "merged_at": created_at,
        "closed_at": created_at,
        "html_url": f"https://github.com/acme/repo/pull/{number}",
        "user": {"login": "alice"},
        "labels": [{"name": "feature"}],
        "base": {"ref": "main"},
        "head": {"ref": f"feature/{number}"},
    }


class RepoParsingTests(unittest.TestCase):
    def test_https_remote(self) -> None:
        self.assertEqual(
            pr_checkpoint.parse_repo_from_remote(
                "https://github.com/benseverndev-oss/goldenmatch.git"
            ),
            "benseverndev-oss/goldenmatch",
        )

    def test_scp_ssh_remote(self) -> None:
        self.assertEqual(
            pr_checkpoint.parse_repo_from_remote(
                "git@github.com:benseverndev-oss/goldenmatch.git"
            ),
            "benseverndev-oss/goldenmatch",
        )

    def test_ssh_url_remote(self) -> None:
        self.assertEqual(
            pr_checkpoint.parse_repo_from_remote(
                "ssh://git@github.com/benseverndev-oss/goldenmatch.git"
            ),
            "benseverndev-oss/goldenmatch",
        )

    def test_non_github_remote_is_rejected(self) -> None:
        with self.assertRaises(pr_checkpoint.ReviewStateError):
            pr_checkpoint.parse_repo_from_remote("https://gitlab.com/acme/repo.git")


class SelectionTests(unittest.TestCase):
    def test_first_run_uses_rolling_cutoff_and_returns_chronological_order(self) -> None:
        pages = {
            1: [
                raw_pr(12, "2026-07-27T10:00:00Z"),
                raw_pr(11, "2026-07-25T10:00:00Z"),
                raw_pr(10, "2026-07-22T09:59:59Z"),
            ]
        }
        selected = pr_checkpoint.collect_new_prs(
            lambda page: pages.get(page, []),
            checkpoint_number=None,
            cutoff=datetime(2026, 7, 23, 10, 0, tzinfo=timezone.utc),
        )
        self.assertEqual([pr["number"] for pr in selected], [11, 12])

    def test_incremental_run_stops_at_checkpoint(self) -> None:
        pages = {
            1: [
                raw_pr(15, "2026-07-27T12:00:00Z"),
                raw_pr(14, "2026-07-27T11:00:00Z"),
                raw_pr(13, "2026-07-27T10:00:00Z"),
                raw_pr(12, "2026-07-26T10:00:00Z"),
            ]
        }
        selected = pr_checkpoint.collect_new_prs(
            lambda page: pages.get(page, []),
            checkpoint_number=13,
            cutoff=datetime(2000, 1, 1, tzinfo=timezone.utc),
        )
        self.assertEqual([pr["number"] for pr in selected], [14, 15])

    def test_duplicate_prs_are_not_returned_twice(self) -> None:
        full_page = [raw_pr(number, "2026-07-27T10:00:00Z") for number in range(200, 100, -1)]
        pages = {
            1: full_page,
            2: [raw_pr(101, "2026-07-27T10:00:00Z"), raw_pr(100, "2026-07-22T00:00:00Z")],
        }
        selected = pr_checkpoint.collect_new_prs(
            lambda page: pages.get(page, []),
            checkpoint_number=None,
            cutoff=datetime(2026, 7, 23, tzinfo=timezone.utc),
        )
        numbers = [pr["number"] for pr in selected]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertEqual(numbers[0], 101)
        self.assertEqual(numbers[-1], 200)


class StateTests(unittest.TestCase):
    def test_build_checkpoint_refuses_to_move_backward(self) -> None:
        existing = {
            "version": 1,
            "repository": "acme/repo",
            "last_reviewed_pr_number": 20,
        }
        with self.assertRaises(pr_checkpoint.ReviewStateError):
            pr_checkpoint.build_checkpoint(
                "acme/repo",
                raw_pr(19, "2026-07-27T10:00:00Z"),
                existing,
            )

    def test_atomic_write_round_trips_and_repo_mismatch_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state_path = Path(temp_dir) / "checkpoint.json"
            payload = pr_checkpoint.build_checkpoint(
                "acme/repo",
                raw_pr(21, "2026-07-27T10:00:00Z"),
                None,
                now=datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc),
            )
            pr_checkpoint.write_state_atomic(state_path, payload)

            loaded = pr_checkpoint.load_state(state_path, "acme/repo")
            self.assertEqual(loaded, payload)
            self.assertEqual(
                json.loads(state_path.read_text(encoding="utf-8"))["last_run_at"],
                "2026-07-27T12:00:00Z",
            )
            with self.assertRaises(pr_checkpoint.ReviewStateError):
                pr_checkpoint.load_state(state_path, "other/repo")


if __name__ == "__main__":
    unittest.main()
