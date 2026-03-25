"""
GoldenFlow Schema Mapping Benchmark
Tests SchemaMapper accuracy across multiple column-naming conventions:
  - CRM style      : contact_name, email_addr, phone_num, ...
  - Warehouse style: first_last_name, email, phone, ...
  - Legacy style   : FULLNAME, EMAIL_ADDRESS, TEL, ...
  - Abbreviated    : nm, em, ph, ...
"""
from __future__ import annotations

import sys
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import goldenflow  # noqa: F401 – registers transforms
from goldenflow.mapping.schema_mapper import SchemaMapper

try:
    from rich.console import Console
    from rich.table import Table
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

# ── Target (canonical) schema ─────────────────────────────────────────────────

TARGET_COLS = ["full_name", "email", "phone", "address", "city", "state",
               "zip", "signup_date", "price", "active", "country", "gender"]

SAMPLE_DATA = {
    "full_name":    ["John Smith"],
    "email":        ["john@example.com"],
    "phone":        ["+15551234567"],
    "address":      ["123 Main St"],
    "city":         ["Philadelphia"],
    "state":        ["PA"],
    "zip":          ["19103"],
    "signup_date":  ["2024-01-01"],
    "price":        ["99.99"],
    "active":       ["True"],
    "country":      ["US"],
    "gender":       ["M"],
}

# ── Naming-convention pairs ───────────────────────────────────────────────────

NAMING_CONVENTIONS = {
    "CRM style": {
        "full_name":   "contact_name",
        "email":       "email_addr",
        "phone":       "phone_num",
        "address":     "street_addr",
        "city":        "city_name",
        "state":       "state_code",
        "zip":         "zip_code",
        "signup_date": "registration_date",
        "price":       "amount",
        "active":      "is_active",
        "country":     "country_code",
        "gender":      "gender_id",
    },
    "Warehouse style": {
        "full_name":   "first_last_name",
        "email":       "email",
        "phone":       "phone",
        "address":     "address",
        "city":        "city",
        "state":       "state",
        "zip":         "postal_code",
        "signup_date": "created_at",
        "price":       "price",
        "active":      "active",
        "country":     "country",
        "gender":      "gender",
    },
    "Legacy style": {
        "full_name":   "FULLNAME",
        "email":       "EMAIL_ADDRESS",
        "phone":       "TEL",
        "address":     "ADDR",
        "city":        "CITY",
        "state":       "STATE",
        "zip":         "ZIPCODE",
        "signup_date": "SIGNUP_DT",
        "price":       "PRICE_AMT",
        "active":      "ACTIVE_FLG",
        "country":     "CTRY",
        "gender":      "GNDR",
    },
    "Abbreviated": {
        "full_name":   "nm",
        "email":       "em",
        "phone":       "ph",
        "address":     "addr",
        "city":        "cty",
        "state":       "st",
        "zip":         "zp",
        "signup_date": "dt",
        "price":       "pr",
        "active":      "act",
        "country":     "cntry",
        "gender":      "gnd",
    },
    "Snake_case aliases": {
        "full_name":   "full_name",
        "email":       "email_address",
        "phone":       "phone_number",
        "address":     "mailing_address",
        "city":        "city",
        "state":       "state",
        "zip":         "zip_code",
        "signup_date": "date_signed_up",
        "price":       "unit_price",
        "active":      "account_active",
        "country":     "country",
        "gender":      "gender",
    },
    "Verbose descriptive": {
        "full_name":   "customer_full_name",
        "email":       "customer_email",
        "phone":       "customer_phone",
        "address":     "customer_address",
        "city":        "customer_city",
        "state":       "customer_state",
        "zip":         "customer_zip",
        "signup_date": "customer_signup_date",
        "price":       "product_price",
        "active":      "account_is_active",
        "country":     "customer_country",
        "gender":      "customer_gender",
    },
}


def run_convention(
    mapper: SchemaMapper,
    target_df: pl.DataFrame,
    canonical_to_alias: dict[str, str],
) -> dict:
    # Build source DataFrame using aliases
    source_data = {alias: SAMPLE_DATA[orig] for orig, alias in canonical_to_alias.items()}
    source_df = pl.DataFrame(source_data)

    mappings = mapper.map(source_df, target_df)
    mapped = {m.source: (m.target, m.confidence) for m in mappings}

    correct = 0
    total = len(canonical_to_alias)
    details = []
    for orig, alias in canonical_to_alias.items():
        predicted, conf = mapped.get(alias, (None, 0.0))
        is_correct = predicted == orig
        if is_correct:
            correct += 1
        details.append({
            "source_col":       alias,
            "expected_target":  orig,
            "predicted_target": predicted,
            "confidence":       conf,
            "correct":          is_correct,
        })

    return {
        "correct": correct,
        "total": total,
        "accuracy": round(correct / total, 4),
        "details": details,
    }


def print_summary_table(convention_results: dict) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(
            title="Schema Mapping Accuracy — Naming Convention Benchmark",
            box=box.ROUNDED,
            show_lines=True,
        )
        table.add_column("Convention", style="cyan")
        table.add_column("Correct / Total", justify="center")
        table.add_column("Accuracy", justify="right")
        table.add_column("Missed columns")

        for conv_name, res in convention_results.items():
            missed = [d["source_col"] for d in res["details"] if not d["correct"]]
            acc = res["accuracy"]
            color = "green" if acc >= 0.8 else ("yellow" if acc >= 0.5 else "red")
            table.add_row(
                conv_name,
                f"{res['correct']} / {res['total']}",
                f"[{color}]{acc:.2%}[/{color}]",
                ", ".join(missed) if missed else "[dim]none[/dim]",
            )
        console.print(table)
    else:
        print("\n=== Schema Mapping Accuracy by Naming Convention ===")
        print(f"{'Convention':<25} {'Correct':>8} {'Total':>6} {'Accuracy':>10}  Missed")
        print("-" * 75)
        for conv_name, res in convention_results.items():
            missed = [d["source_col"] for d in res["details"] if not d["correct"]]
            print(
                f"{conv_name:<25} {res['correct']:>8} {res['total']:>6} "
                f"{res['accuracy']:>10.2%}  {', '.join(missed) if missed else '-'}"
            )


def print_detail_table(conv_name: str, res: dict) -> None:
    if not HAS_RICH:
        return
    console = Console()
    table = Table(title=f"Detail — {conv_name}", box=box.SIMPLE)
    table.add_column("Source col", style="cyan")
    table.add_column("Expected target", style="magenta")
    table.add_column("Predicted target")
    table.add_column("Conf.", justify="right")
    table.add_column("OK?", justify="center")
    for d in res["details"]:
        ok_str = "[green]yes[/green]" if d["correct"] else "[red]no[/red]"
        conf_str = f"{d['confidence']:.3f}" if d["confidence"] else "—"
        pred = d["predicted_target"] or "[dim]—[/dim]"
        table.add_row(d["source_col"], d["expected_target"], pred, conf_str, ok_str)
    console.print(table)


def main() -> None:
    mapper = SchemaMapper(auto_threshold=0.6, suggest_threshold=0.35)
    target_df = pl.DataFrame(SAMPLE_DATA)

    print("GoldenFlow Schema Mapping Benchmark\n")

    convention_results = {}
    for conv_name, col_map in NAMING_CONVENTIONS.items():
        res = run_convention(mapper, target_df, col_map)
        convention_results[conv_name] = res
        print(f"  {conv_name:<25} {res['correct']}/{res['total']}  ({res['accuracy']:.2%})")

    print()
    print_summary_table(convention_results)

    # Print detail for any convention with less-than-perfect accuracy
    for conv_name, res in convention_results.items():
        if res["accuracy"] < 1.0:
            print_detail_table(conv_name, res)

    overall = sum(r["accuracy"] for r in convention_results.values()) / len(convention_results)
    print(f"\nOverall average mapping accuracy: {overall:.2%}")
    print("\nSchema mapping benchmark complete.")


if __name__ == "__main__":
    main()
