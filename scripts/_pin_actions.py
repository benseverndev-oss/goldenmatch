"""One-shot helper to pin GitHub Actions references to SHAs.

Reads each `.github/workflows/*.yml` file and rewrites every
`uses: <repo>@<tag>` line to `uses: <repo>@<sha>  # <tag>` using the
SHA mapping below. Run once; the file is not part of CI.

Discarded after the scorecard hardening PR lands — kept only so the
mapping is reproducible if Dependabot ever needs a fresh round of pins.
"""
from __future__ import annotations
import re
from pathlib import Path

# repo@tag  ->  sha (from `gh api repos/<repo>/commits/<tag>`, 2026-05-11)
PIN_MAP: dict[str, str] = {
    "actions/cache@v4":                                "0057852bfaa89a56745cba8c7296529d2fc39830",
    "actions/checkout@v4":                             "34e114876b0b11c390a56381ad16ebd13914f8d5",
    "actions/configure-pages@v5":                      "983d7736d9b0ae728b81ab479565c72886d7745b",
    "actions/deploy-pages@v4":                         "d6db90164ac5ed86f2b6aed7e0febac5b3c0c03e",
    "actions/setup-node@v4":                           "49933ea5288caeca8642d1e84afbd3f7d6820020",
    "actions/setup-python@v5":                         "a26af69be951a213d495a4c3e4e4022e16d87065",
    "actions/upload-artifact@v4":                      "ea165f8d65b6e75b540449e92b4886f43607fa02",
    "actions/upload-pages-artifact@v3":                "56afc609e74202658d3ffba0e8f6dda462b719fa",
    "astral-sh/setup-uv@v3":                           "caf0cab7a618c569241d31dcd442f54681755d39",
    "dorny/paths-filter@v3":                           "d1c1ffe0248fe513906c8e24db8ea791d46f8590",
    "dtolnay/rust-toolchain@stable":                   "29eef336d9b2848a0b548edc03f92a220660cdb8",
    "pnpm/action-setup@v4":                            "b906affcce14559ad1aafd4ab0e942779e9f58b1",
    "pypa/gh-action-pypi-publish@release/v1":          "cef221092ed1bacb1cc03d23a2d87d1d172e277b",
    "ruby/setup-ruby@v1":                              "6aaa311d81eba98ae12eaffbcb63296ace0efcde",
    "Swatinem/rust-cache@v2":                          "e18b497796c12c097a38f9edb9d0641fb99eee32",
    "docker/build-push-action@v6":                     "10e90e3645eae34f1e60eeb005ba3a3d33f178e8",
    "docker/login-action@v3":                          "c94ce9fb468520275223c153574b00df6fe4bcc9",
    "docker/metadata-action@v5":                       "c299e40c65443455700f0fdfc63efafe5b349051",
    "docker/setup-buildx-action@v3":                   "8d2750c68a42422c14e847fe6c8ac0403b4cbd6f",
    "docker/setup-qemu-action@v3":                     "c7c53464625b32c7a7e944ae62b3e17d2b600130",
    "github/codeql-action/upload-sarif@v3":            "7fd177fa680c9881b53cdab4d346d32574c9f7f4",
    "github/codeql-action/init@v3":                    "7fd177fa680c9881b53cdab4d346d32574c9f7f4",
    "github/codeql-action/analyze@v3":                 "7fd177fa680c9881b53cdab4d346d32574c9f7f4",
    "ossf/scorecard-action@v2.4.0":                    "62b2cac7ed8198b15735ed49ab1e5cf35480ba46",
}

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


def pin_line(line: str) -> str:
    # Match `<indent>- uses: <repo>@<tag>` (with or without surrounding whitespace).
    m = re.match(r"^(\s*-?\s*uses:\s*)([^@\s]+)@([^\s#]+)(.*)$", line)
    if not m:
        return line
    prefix, repo, tag, tail = m.groups()
    key = f"{repo}@{tag}"
    sha = PIN_MAP.get(key)
    if not sha:
        return line  # unknown action, leave alone (will need PIN_MAP update)
    # If tail already contains a `#` comment, keep only the trailing whitespace removed
    tail = tail.rstrip()
    return f"{prefix}{repo}@{sha}  # {tag}{tail}\n"


def main() -> int:
    changed = 0
    for path in sorted(WORKFLOWS.glob("*.yml")):
        original = path.read_text(encoding="utf-8")
        out_lines = [pin_line(line) for line in original.splitlines(keepends=True)]
        new = "".join(out_lines)
        if new != original:
            path.write_text(new, encoding="utf-8")
            print(f"pinned {path.name}")
            changed += 1
    print(f"\n{changed} workflow file(s) modified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
