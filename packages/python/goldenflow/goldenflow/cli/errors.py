from __future__ import annotations

import contextlib

import typer
from rich.console import Console

console = Console(stderr=True)


@contextlib.contextmanager
def cli_error_handler():
    """Wrap CLI commands with user-friendly error messages."""
    try:
        yield
    except FileNotFoundError as e:
        console.print(f"[red]File not found:[/red] {e}")
        raise typer.Exit(1)
    except PermissionError as e:
        console.print(f"[red]Permission denied:[/red] {e}")
        raise typer.Exit(1)
    except ValueError as e:
        console.print(f"[red]Invalid input:[/red] {e}")
        raise typer.Exit(1)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted.[/dim]")
        raise typer.Exit(130)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        console.print("[dim]Use --help for usage information.[/dim]")
        raise typer.Exit(1)
