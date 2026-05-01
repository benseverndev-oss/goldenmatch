"""Schedule periodic transforms."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

from rich.console import Console

console = Console()


def _parse_cron_interval(cron_expr: str) -> float:
    """Simple cron-like interval parser. Supports: '5m', '1h', '30s', '1d'."""
    expr = cron_expr.strip().lower()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    if expr[-1] in multipliers:
        try:
            return float(expr[:-1]) * multipliers[expr[-1]]
        except ValueError:
            pass
    raise ValueError(f"Invalid interval: {cron_expr}. Use format like '5m', '1h', '30s'")


def run_schedule(
    path: Path,
    interval: str = "1h",
    config_path: Path | None = None,
    output_dir: Path | None = None,
) -> None:
    """Run transforms on a schedule."""
    from goldenflow.config.loader import load_config
    from goldenflow.config.schema import GoldenFlowConfig
    from goldenflow.engine.transformer import TransformEngine

    cfg = load_config(config_path) if config_path else GoldenFlowConfig()
    engine = TransformEngine(config=cfg)
    seconds = _parse_cron_interval(interval)
    out = output_dir or path.parent

    console.print(f"[bold]Scheduled:[/bold] transform {path} every {interval}")
    console.print("[dim]Press Ctrl+C to stop[/dim]\n")

    run_count = 0
    try:
        while True:
            run_count += 1
            ts = datetime.now().strftime("%H:%M:%S")
            console.print(f"[cyan]Run #{run_count}[/cyan] at {ts}")
            try:
                result = engine.transform_file(path, output_dir=out)
                console.print(
                    f"  [green]Done:[/green] {len(result.manifest.records)} transforms, "
                    f"{len(result.manifest.errors)} errors"
                )
            except Exception as e:
                console.print(f"  [red]Error:[/red] {e}")
            time.sleep(seconds)
    except KeyboardInterrupt:
        console.print(f"\n[dim]Stopped after {run_count} runs.[/dim]")
