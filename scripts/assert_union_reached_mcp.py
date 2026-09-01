"""Fail if the combined coverage.xml never saw the MCP surface.

`goldenmatch/mcp/*` is omitted by the pyproject coverage config, so the only way
an mcp module reaches this report is through the sweep coverage collected under
.coveragerc-sweep. Its absence means the union silently degraded to the old
shard-only coverage -- which would mark every MCP-only module uncovered and turn
it into a deletion candidate.
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main(path: str) -> int:
    root = ET.parse(Path(path)).getroot()
    names = [c.get("filename", "") for c in root.iter("class")]
    mcp = [n for n in names if "goldenmatch/mcp/" in n.replace("\\", "/")]
    print(f"{len(names)} measured modules, {len(mcp)} of them under goldenmatch/mcp/")
    if not mcp:
        print(
            "FAIL no goldenmatch/mcp/ module in the combined coverage. The sweep "
            "coverage did not reach the report; every MCP-only module would now "
            "read as a dead-code candidate.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1]))
