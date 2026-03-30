"""Use domain packs for industry-specific validation.

Usage:
    pip install goldencheck
    python examples/domain_packs.py
"""
if __name__ == "__main__":
    import goldencheck

    print("Available domain packs:")
    for domain in goldencheck.list_domains():
        print(f"  {domain}")

    print("\nTo scan with a domain pack:")
    print("  goldencheck scan data.csv --domain healthcare")
