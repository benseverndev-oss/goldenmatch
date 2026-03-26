"""Stage registry -- discover, register, and retrieve stages."""
from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import sys
from pathlib import Path
from typing import Any

from goldenpipe.models.stage import StageInfo


class StageRegistry:
    """Discovers and stores pipeline stages."""

    def __init__(self) -> None:
        self._stages: dict[str, Any] = {}

    def register(self, stage: Any) -> None:
        """Register a stage by its info.name."""
        self._stages[stage.info.name] = stage

    def get(self, name: str) -> Any:
        """Retrieve a stage by name. Raises KeyError if not found."""
        if name not in self._stages:
            raise KeyError(f"Stage '{name}' not found in registry")
        return self._stages[name]

    def list_all(self) -> dict[str, StageInfo]:
        """Return {name: StageInfo} for all registered stages."""
        return {name: s.info for name, s in self._stages.items()}

    def discover(self, stages_dir: Path | None = None) -> None:
        """Discover stages from entry points and optional local directory."""
        # Always register built-in LoadStage
        from goldenpipe.adapters import LoadStage
        self._stages["load"] = LoadStage()

        self._discover_entry_points()
        if stages_dir is not None:
            self._discover_local(stages_dir)

    def _discover_entry_points(self) -> None:
        """Load stages from goldenpipe.stages entry points."""
        try:
            eps = importlib.metadata.entry_points(group="goldenpipe.stages")
        except TypeError:
            eps = importlib.metadata.entry_points().get("goldenpipe.stages", [])

        for ep in eps:
            try:
                obj = ep.load()
                if isinstance(obj, type):
                    # It's a class — instantiate it
                    obj = obj()
                if hasattr(obj, "info") and hasattr(obj, "run"):
                    self._stages[ep.name] = obj
            except Exception:
                pass

    def _discover_local(self, stages_dir: Path) -> None:
        """Load stages from .py files in a local directory."""
        if not stages_dir.is_dir():
            return

        for py_file in sorted(stages_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            module_name = f"goldenpipe._local_stages.{py_file.stem}"
            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                continue

            for attr_name in dir(module):
                obj = getattr(module, attr_name)
                if hasattr(obj, "info") and hasattr(obj, "run"):
                    self._stages[obj.info.name] = obj
