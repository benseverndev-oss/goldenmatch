from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from goldenmatch.web.registry import PreviewRegistry

if TYPE_CHECKING:
    from goldenmatch.config.schemas import GoldenMatchConfig, RulesPayload


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
    # v1.7-v1.12 controller telemetry from the most recent zero-config call
    # (autoconfig or auto_config=true run). Stashed here so the workbench can
    # surface stop_reason, RunHistory decisions, ComplexityProfile health, and
    # IndicatorContext signals without re-running the controller. Loosely typed
    # (Any) to dodge import cycles — the serialization layer in
    # web/controller_telemetry.py is the type-safe boundary.
    last_controller_profile: Any = None
    last_controller_history: Any = None
    last_controller_committed_config: GoldenMatchConfig | None = None
    last_controller_source: str | None = None  # "autoconfig" or "run"
    last_controller_run_name: str | None = None  # run_name when source="run"
    last_controller_recorded_at: str | None = None  # ISO timestamp

    @classmethod
    def from_project_dir(cls, project_dir: Path, runs_dir: Path | None = None) -> AppState:
        cfg = project_dir / "goldenmatch.yml"
        return cls(
            project_root=project_dir,
            config_path=cfg if cfg.exists() else None,
            labels_path=project_dir / "labels.jsonl",
            runs_dir=runs_dir or project_dir,
        )
