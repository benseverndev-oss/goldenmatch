"""Scan a CSV with the healthcare domain pack for clinical data types.

The domain pack teaches GoldenCheck about healthcare-specific types like
patient IDs, diagnosis codes, and clinical dates -- reducing false positives
and adding domain-specific checks.

Usage:
    python domain_pack.py patients.csv
"""
from pathlib import Path
import sys

from goldencheck import scan_file, list_available_domains


def main():
    # Show all available domain packs
    domains = list_available_domains()
    print(f"Available domains: {', '.join(domains)}\n")

    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("patients.csv")
    findings, profile = scan_file(path, domain="healthcare")

    print(f"Scanned {profile.row_count} rows with healthcare domain pack")
    print(f"Found {len(findings)} issues:\n")

    for f in findings:
        print(f"  [{f.severity.name}] {f.column}: {f.message}")


if __name__ == "__main__":
    main()
