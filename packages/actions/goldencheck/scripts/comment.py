"""Post or update a PR comment with GoldenCheck scan results."""
import glob
import json
import os
import subprocess
import sys

RESULTS_DIR = "/tmp/goldencheck-results"
MARKER = "<!-- goldencheck -->"


def load_results():
    """Load all JSON result files."""
    results = []
    for path in sorted(glob.glob(os.path.join(RESULTS_DIR, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
            data["_filename"] = os.path.basename(path).replace(".json", "")
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            pass
    return results


def format_comment(results):
    """Format results as a markdown PR comment."""
    lines = [MARKER, "## GoldenCheck Results", ""]

    if not results:
        lines.append("No data files scanned.")
        return "\n".join(lines)

    # Summary table
    lines.append("| File | Errors | Warnings | Findings |")
    lines.append("|------|--------|----------|----------|")

    total_errors = 0
    total_warnings = 0
    all_findings = []

    for r in results:
        findings = r.get("findings", [])
        errors = sum(1 for f in findings if f.get("severity", "").lower() == "error")
        warnings = sum(1 for f in findings if f.get("severity", "").lower() == "warning")
        total_errors += errors
        total_warnings += warnings
        lines.append(f"| {r['_filename']} | {errors} | {warnings} | {len(findings)} |")

        for f in findings:
            if f.get("severity", "").lower() in ("error", "warning"):
                all_findings.append((r["_filename"], f))

    lines.append("")
    lines.append(f"**{len(results)} file(s) scanned, {total_errors} errors, {total_warnings} warnings**")

    # Top findings (collapsible)
    if all_findings:
        lines.append("")
        lines.append("<details><summary>Top findings</summary>")
        lines.append("")
        for filename, f in all_findings[:20]:
            sev = f.get("severity", "?").upper()
            col = f.get("column", "?")
            msg = f.get("message", "")[:80]
            lines.append(f"- {sev} [{filename} \u2192 {col}] {msg}")
        if len(all_findings) > 20:
            lines.append(f"- ... and {len(all_findings) - 20} more")
        lines.append("")
        lines.append("</details>")

    return "\n".join(lines)


def find_existing_comment():
    """Find an existing GoldenCheck comment on the PR."""
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
        comment_id = result.stdout.strip()
        return int(comment_id) if comment_id else None
    except (subprocess.CalledProcessError, ValueError):
        return None


def post_comment(body):
    """Post or update the PR comment."""
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


def main():
    results = load_results()
    body = format_comment(results)
    post_comment(body)


if __name__ == "__main__":
    main()
