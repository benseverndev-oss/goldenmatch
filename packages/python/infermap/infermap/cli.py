"""infermap CLI — placeholder for Task 10."""
import typer

app = typer.Typer(name="infermap", help="Inference-driven schema mapping engine.")


@app.callback(invoke_without_command=True)
def main(ctx: typer.Context) -> None:
    """infermap CLI."""
    if ctx.invoked_subcommand is None:
        typer.echo("infermap — use --help for available commands.")
