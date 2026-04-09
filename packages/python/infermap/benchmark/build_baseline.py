"""Build benchmark/baselines/main.json from two report files."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def main_impl(
    python_path: Path,
    ts_path: Path,
    commit: str,
    output: Path,
) -> None:
    py = json.loads(Path(python_path).read_text(encoding="utf-8"))
    ts = json.loads(Path(ts_path).read_text(encoding="utf-8"))

    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    baseline = {
        "version": 1,
        "updated_at": updated_at,
        "commit": commit,
        "python": py,
        "typescript": ts,
    }

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(baseline, indent=2) + "\n", encoding="utf-8")

    metadata_path = output.parent / "main.metadata.json"
    metadata = {
        "version": 1,
        "commit": commit,
        "updated_at": updated_at,
        "python_runner": py.get("runner_version", "unknown"),
        "ts_runner": ts.get("runner_version", "unknown"),
        "infermap_python": py.get("infermap_version", "unknown"),
        "infermap_ts": ts.get("infermap_version", "unknown"),
        "changelog": [],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {output} and {metadata_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--ts", type=Path, required=True)
    parser.add_argument("--commit", type=str, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    main_impl(args.python, args.ts, args.commit, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
