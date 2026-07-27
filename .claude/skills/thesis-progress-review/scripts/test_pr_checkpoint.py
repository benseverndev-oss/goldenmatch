from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import pr_checkpoint as pc


def raw_pr(
    number: int,
    created_at: str,
    *,
    state: str = "open",
    draft: bool = False,
    sha: str | None = None,
) -> dict:
    return {
        "number": number,
        "title": f"PR {number}",
        "html_url": f"https://example.test/pull/{number}",
        "state": state,
        "draft": draft,
        "created_at": created_at,
        "updated_at": created_at,
        "merged_at": None,
        "closed_at": None,
        "base": {"ref": "main"},
        "head": {"ref": f"branch-{number}", "sha": sha or f"sha-{number}"},
        "user": {"login": "author"},
    }


class ParseRepoTests(unittest.TestCase):
    def test_parses_common_remote_forms(self) -> None:
        cases = {
            "https://github.com/acme/repo.git": "acme/repo",
            "git@github.com:acme/repo.git": "acme/repo",
            "ssh://git@github.com/acme/repo.git": "acme/repo",
        }
        for remote, expected in cases.items():
            with self.subTest(remote=remote):
                self.assertEqual(pc.parse_repo_from_remote(remote), expected)


class FetchTests(unittest.TestCase):
    def test_checkpoint_mode_stops_at_checkpoint_and_orders_oldest_first(self) -> None:
        pages = {
            1: [
                raw_pr(12, "2026-07-27T12:00:00Z"),
                raw_pr(11, "2026-07-27T11:00:00Z"),
                raw_pr(10, "2026-07-27T10:00:00Z"),
            ]
        }

        def api(endpoint: str):
            page = int(endpoint.rsplit("page=", 1)[1])
            return pages.get(page, [])

        result = pc.fetch_new_prs(
            "acme/repo", previous_checkpoint=10, cutoff=None, api=api
        )
        self.assertEqual([item["number"] for item in result], [11, 12])

    def test_first_run_filters_by_cutoff(self) -> None:
        pages = {
            1: [
                raw_pr(3, "2026-07-27T12:00:00Z"),
                raw_pr(2, "2026-07-24T12:00:00Z"),
                raw_pr(1, "2026-07-20T12:00:00Z"),
            ]
        }

        def api(endpoint: str):
            page = int(endpoint.rsplit("page=", 1)[1])
            return pages.get(page, [])

        cutoff = datetime(2026, 7, 23, tzinfo=timezone.utc)
        result = pc.fetch_new_prs(
            "acme/repo", previous_checkpoint=None, cutoff=cutoff, api=api
        )
        self.assertEqual([item["number"] for item in result], [2, 3])


class TrackedOpenTests(unittest.TestCase):
    def test_only_material_changes_resurface(self) -> None:
        tracked = {
            "4": {
                "head_sha": "same",
                "state": "open",
                "draft": False,
                "title": "PR 4",
                "base": "main",
            },
            "5": {
                "head_sha": "old",
                "state": "open",
                "draft": True,
                "title": "PR 5",
                "base": "main",
            },
        }

        def fetch(_repo: str, number: int):
            pr = pc.normalize_pr(
                raw_pr(
                    number,
                    "2026-07-27T12:00:00Z",
                    draft=(number == 5),
                    sha=("same" if number == 4 else "new"),
                )
            )
            return pr

        changed = pc.changed_tracked_prs("acme/repo", tracked, fetch=fetch)
        self.assertEqual([item["number"] for item in changed], [5])


class CheckpointBoundaryTests(unittest.TestCase):
    def test_first_checkpoint_uses_lookback_instead_of_all_history(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            state_path = Path(td) / "state.json"
            args = type(
                "Args",
                (),
                {
                    "repo": "acme/repo",
                    "state_file": str(state_path),
                    "through": 12,
                    "lookback_days": 4,
                },
            )()
            seen: dict[str, object] = {}
            recent = [pc.normalize_pr(raw_pr(12, "2026-07-27T12:00:00Z"))]

            def fake_new(repo: str, *, previous_checkpoint, cutoff, api=pc.gh_api_json):
                seen["repo"] = repo
                seen["previous"] = previous_checkpoint
                seen["cutoff"] = cutoff
                return recent

            original_new = pc.fetch_new_prs
            original_fetch = pc.fetch_pr
            original_refresh = pc.refresh_tracked_open_prs
            try:
                pc.fetch_new_prs = fake_new
                pc.fetch_pr = lambda _repo, _number: recent[0]
                pc.refresh_tracked_open_prs = lambda *_a, **_kw: {
                    "12": pc.tracked_signature(recent[0])
                }
                self.assertEqual(pc.checkpoint_command(args), 0)
            finally:
                pc.fetch_new_prs = original_new
                pc.fetch_pr = original_fetch
                pc.refresh_tracked_open_prs = original_refresh

            self.assertIsNone(seen["previous"])
            self.assertIsNotNone(seen["cutoff"])
            saved = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["last_reviewed_pr"], 12)
            self.assertIn("12", saved["tracked_open_prs"])


class StateTests(unittest.TestCase):
    def test_atomic_state_round_trip_and_repo_guard(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "state.json"
            data = {
                "schema_version": pc.SCHEMA_VERSION,
                "repo": "acme/repo",
                "last_reviewed_pr": 42,
                "tracked_open_prs": {},
            }
            pc.write_json_atomic(path, data)
            self.assertEqual(pc.load_state(path, "acme/repo")["last_reviewed_pr"], 42)
            with self.assertRaises(pc.CheckpointError):
                pc.load_state(path, "other/repo")


if __name__ == "__main__":
    unittest.main()
