from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AppState:
    project_root: Path
    config_path: Path | None
    labels_path: Path
    rules: dict[str, Any] | None = None  # populated lazily by /api/v1/rules
    runs_dir: Path | None = None  # defaults to project_root if None
    registry: Any = field(default=None)  # filled in Task 5

    @classmethod
    def from_project_dir(cls, project_dir: Path, runs_dir: Path | None = None) -> "AppState":
        cfg = project_dir / "goldenmatch.yml"
        return cls(
            project_root=project_dir,
            config_path=cfg if cfg.exists() else None,
            labels_path=project_dir / "labels.jsonl",
            runs_dir=runs_dir or project_dir,
        )
