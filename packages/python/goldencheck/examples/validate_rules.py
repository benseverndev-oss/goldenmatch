"""Validate a CSV file against rules defined in a goldencheck.yml config.

Usage:
    python validate_rules.py data.csv goldencheck.yml
"""
from pathlib import Path
import sys

from goldencheck import validate_file, load_config, Severity


def main():
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data.csv")
    config_path = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("goldencheck.yml")

    config = load_config(config_path)
    findings = validate_file(csv_path, config)

    errors = [f for f in findings if f.severity == Severity.ERROR]
    warnings = [f for f in findings if f.severity == Severity.WARNING]

    print(f"Validation complete: {len(errors)} errors, {len(warnings)} warnings")
    for f in findings:
        print(f"  [{f.severity.name}] {f.column}: {f.check} - {f.message}")

    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
