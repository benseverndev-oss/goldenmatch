from __future__ import annotations

import json
import shutil
import tempfile
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from goldenmatch.web.runs import RunRef


@dataclass
class PreviewEntry:
    ref: RunRef
    tmp_dir: Path  # owns lineage.json + clusters.csv + data.csv on disk under tmp


class PreviewRegistry:
    """Bounded LRU of preview runs. Files live under per-entry tempdirs."""

    def __init__(self, max_entries: int = 8) -> None:
        self.max_entries = max_entries
        self._entries: "OrderedDict[str, PreviewEntry]" = OrderedDict()

    def put(self, run_name: str, lineage: dict, clusters_csv: str, source_csv: str) -> RunRef:
        td = Path(tempfile.mkdtemp(prefix="gm-preview-"))
        lp = td / f"{run_name}_lineage.json"
        cp = td / f"{run_name}_clusters.csv"
        dp = td / "data.csv"
        lp.write_text(json.dumps(lineage), encoding="utf-8")
        cp.write_text(clusters_csv, encoding="utf-8")
        dp.write_text(source_csv, encoding="utf-8")
        ref = RunRef(run_name=run_name, lineage_path=lp, clusters_path=cp)
        self._entries[run_name] = PreviewEntry(ref=ref, tmp_dir=td)
        self._entries.move_to_end(run_name)
        self._evict()
        return ref

    def get(self, run_name: str) -> RunRef | None:
        entry = self._entries.get(run_name)
        if entry is None:
            return None
        self._entries.move_to_end(run_name)
        return entry.ref

    def _evict(self) -> None:
        while len(self._entries) > self.max_entries:
            _, entry = self._entries.popitem(last=False)
            shutil.rmtree(entry.tmp_dir, ignore_errors=True)
