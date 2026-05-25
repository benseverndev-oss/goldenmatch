"""Post or update a PR comment with GoldenFlow transform results."""
import glob
import json
import os
import subprocess

RESULTS_DIR = "/tmp/goldenflow-results"
MARKER = "<!-- goldenflow -->"


def load_results() -> list[dict]:
    results = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json"))):
        try:
            with open(path) as f:
                results.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    return results


def format_comment(results: list[dict]) -> str:
    lines = [MARKER, "## GoldenFlow Results", ""]
    if not results:
        lines.append("No data files transformed.")
        return "\n".join(lines)

    lines.append("| File | Rows | Transforms | Errors |")
    lines.append("|------|------|------------|--------|")
    total_transforms = 0
    total_errors = 0
    for r in results:
        if "error" in r:
            lines.append(f"| {r.get('file', '?')} | - | - | {r['error'][:60]} |")
            total_errors += 1
            continue
        total_transforms += r.get("transforms_applied", 0)
        total_errors += r.get("errors", 0)
        lines.append(
            f"| {r['file']} | {r.get('rows', 0)} | "
            f"{r.get('transforms_applied', 0)} | {r.get('errors', 0)} |"
        )
    lines.append("")
    lines.append(
        f"**{len(results)} file(s) transformed, {total_transforms} transforms applied, "
        f"{total_errors} error(s)**"
    )
    return "\n".join(lines)


def find_existing_comment() -> int | None:
    repo = os.environ.get("GH_REPO", "")
    pr = os.environ.get("PR_NUMBER", "")
    if not repo or not pr:
        return None
    try:
        result = subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{pr}/comments", "--jq",
             f'[.[] | select(.body | contains("{MARKER}"))][0].id'],
            capture_output=True, text=True, check=True,
        )
        cid = result.stdout.strip()
        return int(cid) if cid else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def post_comment(body: str) -> None:
    repo = os.environ.get("GH_REPO", "")
    pr = os.environ.get("PR_NUMBER", "")
    if not repo or not pr:
        print("Not a PR context — skipping comment.")
        return
    existing = find_existing_comment()
    if existing:
        subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/comments/{existing}",
             "-X", "PATCH", "-f", f"body={body}"],
            check=True, capture_output=True,
        )
        print(f"Updated existing comment #{existing}")
    else:
        subprocess.run(
            ["gh", "api", f"repos/{repo}/issues/{pr}/comments",
             "-f", f"body={body}"],
            check=True, capture_output=True,
        )
        print("Posted new comment")


def main() -> None:
    post_comment(format_comment(load_results()))


if __name__ == "__main__":
    main()
