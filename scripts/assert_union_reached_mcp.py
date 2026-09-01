"""Fail if the combined coverage.xml never actually EXECUTED the MCP surface.

`goldenmatch/mcp/*` is omitted by the pyproject coverage config, so the only way
an mcp module reaches this report is through the sweep coverage collected under
.coveragerc-sweep. Its absence means the union silently degraded to the old
shard-only coverage -- which would mark every MCP-only module uncovered and turn
it into a deletion candidate.

A present-but-unexecuted mcp class is NOT enough: `source = goldenmatch` makes
coverage.py enumerate every file under the package whether or not it ever ran,
so `goldenmatch/mcp/*` entries show up with line-rate 0 even when the sweep
coverage was never combined in. Only a line with `hits > 0` proves the sweep
actually reached the report.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from coverage_paths import normalize  # noqa: E402


def main(path: str) -> int:
    root = ET.parse(Path(path)).getroot()
    mcp_classes = [
        cls
        for cls in root.iter("class")
        if "goldenmatch/mcp/" in normalize(cls.get("filename", ""))
    ]
    covered = [
        cls
        for cls in mcp_classes
        if any(int(line.get("hits", "0")) > 0 for line in cls.iter("line"))
    ]
    print(
        f"{sum(1 for _ in root.iter('class'))} measured modules, "
        f"{len(mcp_classes)} of them under goldenmatch/mcp/, "
        f"{len(covered)} of those with at least one executed line"
    )
    if not mcp_classes:
        print(
            "FAIL no goldenmatch/mcp/ module in the combined coverage. The sweep "
            "coverage did not reach the report; every MCP-only module would now "
            "read as a dead-code candidate.",
            file=sys.stderr,
        )
        return 1
    if not covered:
        print(
            "FAIL goldenmatch/mcp/ modules are present but NONE have an executed "
            "line. `source = goldenmatch` makes coverage.py enumerate every file "
            "under the package regardless of whether it ran, so presence alone "
            "proves nothing -- the sweep coverage was not actually combined in.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
