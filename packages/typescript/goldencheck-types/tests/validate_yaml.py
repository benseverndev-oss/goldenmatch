"""Validate all domain YAML files in domains/."""
import sys
from pathlib import Path
import yaml

VALID_SUPPRESS = {
    "uniqueness", "nullability", "format_detection", "type_inference",
    "range_distribution", "cardinality", "pattern_consistency",
    "temporal_order", "encoding_detection", "sequence_detection",
    "drift_detection",
}

def validate_domain(path: Path) -> list[str]:
    errors = []
    with open(path) as f:
        data = yaml.safe_load(f)

    if not data:
        errors.append(f"{path.name}: empty file")
        return errors

    if "description" not in data or not data["description"]:
        errors.append(f"{path.name}: missing 'description' field")

    if "types" not in data or not data["types"]:
        errors.append(f"{path.name}: missing 'types' dict or no types defined")
        return errors

    for name, cfg in data["types"].items():
        if "name_hints" not in cfg or not cfg["name_hints"]:
            errors.append(f"{path.name}/{name}: missing 'name_hints'")
        elif not all(isinstance(h, str) for h in cfg["name_hints"]):
            errors.append(f"{path.name}/{name}: name_hints must be strings")

        if "suppress" not in cfg:
            errors.append(f"{path.name}/{name}: missing 'suppress' list")
        else:
            for s in cfg.get("suppress", []):
                if s not in VALID_SUPPRESS:
                    errors.append(f"{path.name}/{name}: invalid suppress value '{s}'")

    return errors


def main():
    domains_dir = Path(__file__).parent.parent / "domains"
    all_errors = []

    for path in sorted(domains_dir.glob("*.yaml")):
        errors = validate_domain(path)
        all_errors.extend(errors)
        if not errors:
            print(f"  OK: {path.name}")
        else:
            for e in errors:
                print(f"  FAIL: {e}")

    if all_errors:
        print(f"\n{len(all_errors)} error(s) found")
        sys.exit(1)
    else:
        print(f"\nAll domains valid")


if __name__ == "__main__":
    main()
