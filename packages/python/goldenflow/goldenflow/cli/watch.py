"""Watch a directory for file changes and auto-transform."""
from __future__ import annotations

import time
from pathlib import Path

from rich.console import Console

console = Console()


def watch_directory(
    path: Path,
    config_path: Path | None = None,
    output_dir: Path | None = None,
    interval: float = 2.0,
) -> None:
    """Watch a directory and transform new/changed CSV files."""
    from goldenflow.config.loader import load_config
    from goldenflow.config.schema import GoldenFlowConfig
    from goldenflow.engine.transformer import TransformEngine

    cfg = load_config(config_path) if config_path else GoldenFlowConfig()
    engine = TransformEngine(config=cfg)
    out = output_dir or path

    seen: dict[str, float] = {}  # path -> last modified time

    console.print(f"[bold]Watching[/bold] {path} (interval: {interval}s)")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    try:
        while True:
            for f in path.glob("*.csv"):
                mtime = f.stat().st_mtime
                if str(f) not in seen or seen[str(f)] < mtime:
                    # Skip already-transformed files
                    if "_transformed" in f.name:
                        continue
                    console.print(f"[cyan]Detected:[/cyan] {f.name}")
                    try:
                        result = engine.transform_file(f, output_dir=out)
                        console.print(
                            f"  [green]Transformed:[/green] {len(result.manifest.records)} transforms applied"
                        )
                    except Exception as e:
                        console.print(f"  [red]Error:[/red] {e}")
                    seen[str(f)] = mtime
            time.sleep(interval)
    except KeyboardInterrupt:
        console.print("\n[dim]Watch stopped.[/dim]")
