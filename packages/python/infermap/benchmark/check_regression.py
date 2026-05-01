"""Check a delta.json for regressions beyond a threshold."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main_impl(delta_file: Path, fail_over: float = 0.02) -> int:
    delta_file = Path(delta_file)
    if not delta_file.exists():
        print(f"No delta file at {delta_file}; skipping regression check")
        return 0

    data = json.loads(delta_file.read_text(encoding="utf-8"))
    worst = 0.0
    for lang in ("python", "typescript"):
        if lang in data:
            f1 = float(data[lang]["overall"].get("f1", 0.0))
            worst = min(worst, f1)

    if worst < -fail_over:
        print(
            f"REGRESSION: worst F1 drop is {-worst:.4f}, exceeds {fail_over}",
            file=sys.stderr,
        )
        return 1
    print(f"OK: worst F1 change is {worst:+.4f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("delta_file", type=Path)
    parser.add_argument("--fail-over", type=float, default=0.02)
    args = parser.parse_args()
    return main_impl(args.delta_file, args.fail_over)


if __name__ == "__main__":
    raise SystemExit(main())
