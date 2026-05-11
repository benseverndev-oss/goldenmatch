from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from goldenmatch.web.registry import PreviewRegistry

if TYPE_CHECKING:
    from goldenmatch.config.schemas import RulesPayload


@dataclass
class AppState:
    project_root: Path
    config_path: Path | None
    labels_path: Path
    # In-memory edited rules; seeded lazily from goldenmatch.yml on first
    # /api/v1/rules read. Stored as a validated RulesPayload so Task 5's
    # preview can feed the matching engine without re-validating per request.
    rules: RulesPayload | None = None
    runs_dir: Path | None = None
    registry: PreviewRegistry = field(default_factory=PreviewRegistry)

    @classmethod
    def from_project_dir(cls, project_dir: Path, runs_dir: Path | None = None) -> AppState:
        cfg = project_dir / "goldenmatch.yml"
        return cls(
            project_root=project_dir,
            config_path=cfg if cfg.exists() else None,
            labels_path=project_dir / "labels.jsonl",
            runs_dir=runs_dir or project_dir,
        )
