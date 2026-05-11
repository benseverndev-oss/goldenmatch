"""Transform clinical data using the healthcare domain pack.

Domain packs teach GoldenFlow about field types specific to an industry
(e.g., patient IDs, diagnosis codes) so it applies the right transforms.

Usage:
    python domain_pack.py patients.csv
"""
import sys
from pathlib import Path

from goldenflow import TransformEngine, list_transforms, load_domain


def main():
    # Load the healthcare domain pack
    pack = load_domain("healthcare")
    print(f"Loaded domain pack: {pack.name}")
    print(f"Domain types: {', '.join(pack.type_defs.keys())}\n")

    # Show all registered transforms
    transforms = list_transforms()
    print(f"Total registered transforms: {len(transforms)}\n")

    # Run transforms with domain pack
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("patients.csv")
    engine = TransformEngine()
    result = engine.transform_file(path)

    print(f"Applied {len(result.manifest.records)} transforms")
    for rec in result.manifest.records:
        print(f"  {rec.column}: {rec.transform}")


if __name__ == "__main__":
    main()
