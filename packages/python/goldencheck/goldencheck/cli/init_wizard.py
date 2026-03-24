"""Interactive setup wizard for goldencheck init."""
from __future__ import annotations

from pathlib import Path

import typer

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.triage import auto_triage
from goldencheck.config.schema import GoldenCheckConfig, ColumnRule, Settings
from goldencheck.config.writer import save_config


GITHUB_CI_TEMPLATE = """name: Data Quality
on: [push, pull_request]
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: benzsevern/goldencheck-action@v1
        with:
          files: "{pattern}"
          fail-on: error
"""

GITLAB_CI_TEMPLATE = """data-quality:
  image: python:3.12
  script:
    - pip install goldencheck
    - goldencheck validate {file} --no-tui --fail-on error
"""


def run_init_wizard(file: Path, *, yes: bool = False) -> None:
    """Run the interactive init wizard."""
    typer.echo(f"Scanning {file}...")

    findings, profile = scan_file(file)
    findings = apply_confidence_downgrade(findings, llm_boost=False)

    errors = sum(1 for f in findings if f.severity.name == "ERROR")
    warnings = sum(1 for f in findings if f.severity.name == "WARNING")
    infos = sum(1 for f in findings if f.severity.name == "INFO")
    typer.echo(f"Found {len(findings)} issues ({errors} errors, {warnings} warnings, {infos} info).\n")

    # Ask questions (or use defaults with --yes)
    if yes:
        ci_choice = "none"
        domain = None
    else:
        ci_choice = typer.prompt(
            "What CI do you use?",
            type=typer.Choice(["github", "gitlab", "none"], case_sensitive=False),
            default="none",
        )
        domain_choice = typer.prompt(
            "Domain? Improves detection for industry data",
            type=typer.Choice(["healthcare", "finance", "ecommerce", "none"], case_sensitive=False),
            default="none",
        )
        domain = domain_choice if domain_choice != "none" else None
        typer.confirm("Enable LLM boost? ~$0.01/scan", default=False)  # reserved for future use

    # Re-scan with domain if selected
    if domain:
        findings, profile = scan_file(file, domain=domain)
        findings = apply_confidence_downgrade(findings, llm_boost=False)

    # Auto-triage
    triage = auto_triage(findings)
    typer.echo(f"Auto-pinning {len(triage.pin)} high-confidence findings as rules...\n")

    # Build config
    config = GoldenCheckConfig(settings=Settings(fail_on="error"))
    for f in triage.pin:
        if f.column not in config.columns:
            config.columns[f.column] = ColumnRule(type="string")

    # Write goldencheck.yml
    config_path = Path("goldencheck.yml")
    save_config(config, config_path)
    typer.echo(f"  \u2713 {config_path}  ({len(config.columns)} rules)")

    # Write CI workflow
    if ci_choice == "github":
        ci_dir = Path(".github/workflows")
        ci_dir.mkdir(parents=True, exist_ok=True)
        ci_path = ci_dir / "goldencheck.yml"
        pattern = f"**/*{file.suffix}" if file.suffix else "**/*.csv"
        ci_path.write_text(GITHUB_CI_TEMPLATE.format(pattern=pattern))
        typer.echo(f"  \u2713 {ci_path}")
    elif ci_choice == "gitlab":
        ci_path = Path(".gitlab-ci.yml")
        ci_path.write_text(GITLAB_CI_TEMPLATE.format(file=file))
        typer.echo(f"  \u2713 {ci_path}")

    typer.echo(f"\nNext: git add goldencheck.yml{' .github/' if ci_choice == 'github' else ''} && git push")
