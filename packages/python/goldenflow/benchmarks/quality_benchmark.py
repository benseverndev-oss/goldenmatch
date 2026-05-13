"""
GoldenFlow Quality Benchmark
Measures per-transform accuracy, zero-config precision/recall/F1,
and schema mapping accuracy against the synthetic benchmark dataset.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import polars as pl

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Ensure all transforms are registered
import goldenflow  # noqa: F401 – side-effects register transforms
from goldenflow.engine.transformer import TransformEngine
from goldenflow.mapping.schema_mapper import SchemaMapper
from goldenflow.transforms import get_transform

try:
    from rich import box
    from rich.console import Console
    from rich.table import Table
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

BENCH_DIR = Path(__file__).parent / "datasets" / "goldenflow_bench"
DATA_CSV = BENCH_DIR / "data.csv"
GT_JSON = BENCH_DIR / "ground_truth.json"


def load_data() -> tuple[pl.DataFrame, dict]:
    if not DATA_CSV.exists():
        print("Dataset not found – run benchmarks/datasets/generate_datasets.py first.")
        sys.exit(1)
    df = pl.read_csv(DATA_CSV, infer_schema=False)
    with open(GT_JSON, encoding="utf-8") as f:
        gt = json.load(f)
    return df, gt


# ── Per-transform accuracy ────────────────────────────────────────────────────

def measure_transform_accuracy(df: pl.DataFrame, gt: dict) -> list[dict]:
    results = []

    for pt in gt["planted_transforms"]:
        col = pt["column"]
        transform_name = pt["transform"]
        expected_changes = pt["expected_change_count"]
        total = len(df)

        info = get_transform(transform_name)
        if info is None:
            results.append({
                "column": col, "transform": transform_name,
                "status": "MISSING", "accuracy": 0.0,
                "applied": 0, "expected": expected_changes,
            })
            continue

        try:
            series = df[col]
            if info.mode == "series":
                transformed = info.func(series)
                if isinstance(transformed, tuple):
                    transformed = transformed[0]
            elif info.mode == "expr":
                # Apply via with_columns
                new_df = df.with_columns(info.func(col).alias(col))
                transformed = new_df[col]
            else:
                # dataframe mode – skip per-column accuracy
                results.append({
                    "column": col, "transform": transform_name,
                    "status": "DATAFRAME_MODE", "accuracy": None,
                    "applied": 0, "expected": expected_changes,
                })
                continue

            # Count actual changes (handle nulls: None != original value counts as a change)
            before_str = series.cast(pl.Utf8)
            after_str = transformed.cast(pl.Utf8)
            # A row is "changed" if: (a) values differ where both non-null, or
            # (b) before was non-null and after became null (null_standardize case)
            both_nonnull_changed = (
                before_str.is_not_null() & after_str.is_not_null() & (before_str != after_str)
            ).sum()
            became_null = (before_str.is_not_null() & after_str.is_null()).sum()
            changed = both_nonnull_changed + became_null

            # Accuracy: how close are we to the expected change count?
            # We treat this as: |changed - expected| / total
            # "perfect" accuracy = 1.0 when changed == expected
            if expected_changes == 0:
                accuracy = 1.0 if changed == 0 else max(0.0, 1.0 - changed / total)
            else:
                accuracy = 1.0 - abs(changed - expected_changes) / total

            results.append({
                "column": col,
                "transform": transform_name,
                "status": "OK",
                "accuracy": round(max(0.0, accuracy), 4),
                "applied": int(changed),
                "expected": expected_changes,
            })

        except Exception as e:
            results.append({
                "column": col, "transform": transform_name,
                "status": f"ERROR: {e}", "accuracy": 0.0,
                "applied": 0, "expected": expected_changes,
            })

    return results


# ── Zero-config precision / recall ────────────────────────────────────────────

def measure_zero_config(df: pl.DataFrame, gt: dict) -> dict:
    engine = TransformEngine()  # no config → auto mode

    t0 = time.perf_counter()
    result = engine.transform_df(df)
    elapsed = time.perf_counter() - t0

    transformed_cols = {
        r.column
        for r in result.manifest.records
        if r.affected_rows > 0
    }

    should_transform = set(gt["zero_config_expected"]["columns_that_should_transform"])
    should_not = set(gt["zero_config_expected"]["columns_that_should_not_transform"])

    # True positives: columns that were transformed AND should be
    tp = transformed_cols & should_transform
    # False positives: columns that were transformed but should NOT be
    fp = transformed_cols & should_not
    # False negatives: columns that should be transformed but were NOT
    fn = should_transform - transformed_cols

    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else 1.0
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0

    return {
        "transformed_cols": sorted(transformed_cols),
        "should_transform": sorted(should_transform),
        "should_not": sorted(should_not),
        "tp": sorted(tp),
        "fp": sorted(fp),
        "fn": sorted(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "elapsed_s": round(elapsed, 3),
    }


# ── Schema mapping accuracy ───────────────────────────────────────────────────

def measure_schema_mapping() -> dict:
    """
    Build a source DataFrame with aliased column names and a target DataFrame
    with standard names, run SchemaMapper, check accuracy.
    """
    # Standard target schema
    target_data = {
        "full_name": ["John Smith"],
        "email": ["john@example.com"],
        "phone": ["+15551234567"],
        "address": ["123 Main St"],
        "city": ["Philadelphia"],
        "state": ["PA"],
        "zip": ["19103"],
        "signup_date": ["2024-01-01"],
        "price": ["99.99"],
        "active": ["True"],
        "country": ["US"],
        "gender": ["M"],
    }
    target_df = pl.DataFrame(target_data)

    naming_styles = {
        "crm_style": {
            "full_name": "contact_name",
            "email": "email_addr",
            "phone": "phone_num",
            "address": "street_addr",
            "city": "city_name",
            "state": "state_code",
            "zip": "zip_code",
            "signup_date": "registration_date",
            "price": "amount",
            "active": "is_active",
            "country": "country_code",
            "gender": "gender_id",
        },
        "warehouse_style": {
            "full_name": "first_last_name",
            "email": "email",
            "phone": "phone",
            "address": "address",
            "city": "city",
            "state": "state",
            "zip": "postal_code",
            "signup_date": "created_at",
            "price": "price",
            "active": "active",
            "country": "country",
            "gender": "gender",
        },
        "legacy_style": {
            "full_name": "FULLNAME",
            "email": "EMAIL_ADDRESS",
            "phone": "TEL",
            "address": "ADDR",
            "city": "CITY",
            "state": "STATE",
            "zip": "ZIPCODE",
            "signup_date": "SIGNUP_DT",
            "price": "PRICE_AMT",
            "active": "ACTIVE_FLG",
            "country": "CTRY",
            "gender": "GNDR",
        },
        "abbreviated": {
            "full_name": "nm",
            "email": "em",
            "phone": "ph",
            "address": "addr",
            "city": "cty",
            "state": "st",
            "zip": "zp",
            "signup_date": "dt",
            "price": "pr",
            "active": "act",
            "country": "cntry",
            "gender": "gnd",
        },
    }

    mapper = SchemaMapper(auto_threshold=0.6, suggest_threshold=0.4)
    style_results = {}

    for style_name, col_map in naming_styles.items():
        source_data = {aliased: target_data[orig] for orig, aliased in col_map.items()}
        source_df = pl.DataFrame(source_data)

        mappings = mapper.map(source_df, target_df)

        # Build a lookup: source_col -> target_col from mapper output
        mapped = {m.source: m.target for m in mappings}

        # Check correctness: source aliased col should map to the right target col
        correct = 0
        total = len(col_map)
        details = []
        for orig_col, aliased_col in col_map.items():
            predicted_target = mapped.get(aliased_col)
            is_correct = predicted_target == orig_col
            if is_correct:
                correct += 1
            details.append({
                "source": aliased_col,
                "expected_target": orig_col,
                "predicted_target": predicted_target,
                "correct": is_correct,
                "confidence": next(
                    (m.confidence for m in mappings if m.source == aliased_col), 0.0
                ),
            })

        style_results[style_name] = {
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 4),
            "details": details,
        }

    return style_results


# ── Rich printing helpers ─────────────────────────────────────────────────────

def print_transform_table(results: list[dict]) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(title="Per-Transform Accuracy", box=box.ROUNDED, show_lines=True)
        table.add_column("Column", style="cyan")
        table.add_column("Transform", style="magenta")
        table.add_column("Accuracy", justify="right")
        table.add_column("Applied", justify="right")
        table.add_column("Expected", justify="right")
        table.add_column("Status")
        for r in results:
            acc_str = f"{r['accuracy']:.2%}" if r["accuracy"] is not None else "N/A"
            acc_color = "green" if (r["accuracy"] or 0) >= 0.9 else (
                "yellow" if (r["accuracy"] or 0) >= 0.7 else "red"
            )
            table.add_row(
                r["column"], r["transform"],
                f"[{acc_color}]{acc_str}[/{acc_color}]",
                str(r["applied"]), str(r["expected"]),
                r["status"],
            )
        console.print(table)
    else:
        print("\n=== Per-Transform Accuracy ===")
        print(f"{'Column':<15} {'Transform':<25} {'Accuracy':>10} {'Applied':>8} {'Expected':>8} Status")
        print("-" * 80)
        for r in results:
            acc_str = f"{r['accuracy']:.2%}" if r["accuracy"] is not None else "N/A"
            print(f"{r['column']:<15} {r['transform']:<25} {acc_str:>10} {r['applied']:>8} {r['expected']:>8}  {r['status']}")


def print_zero_config_table(zc: dict) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(title="Zero-Config Precision / Recall / F1", box=box.ROUNDED)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", justify="right", style="bold")
        table.add_row("Precision", f"{zc['precision']:.2%}")
        table.add_row("Recall",    f"{zc['recall']:.2%}")
        table.add_row("F1",        f"{zc['f1']:.2%}")
        table.add_row("Elapsed",   f"{zc['elapsed_s']:.3f}s")
        console.print(table)

        detail = Table(title="Zero-Config Column Details", box=box.SIMPLE)
        detail.add_column("Column", style="cyan")
        detail.add_column("Should Transform?")
        detail.add_column("Was Transformed?")
        detail.add_column("Result")
        all_cols = sorted(set(zc["should_transform"]) | set(zc["should_not"]) | set(zc["transformed_cols"]))
        for col in all_cols:
            should = col in zc["should_transform"]
            was = col in zc["transformed_cols"]
            if should and was:
                result = "[green]TP[/green]"
            elif not should and not was:
                result = "[dim]TN[/dim]"
            elif should and not was:
                result = "[red]FN[/red]"
            else:
                result = "[yellow]FP[/yellow]"
            detail.add_row(col, "yes" if should else "no", "yes" if was else "no", result)
        console.print(detail)
    else:
        print("\n=== Zero-Config Metrics ===")
        print(f"  Precision : {zc['precision']:.2%}")
        print(f"  Recall    : {zc['recall']:.2%}")
        print(f"  F1        : {zc['f1']:.2%}")
        print(f"  Elapsed   : {zc['elapsed_s']:.3f}s")


def print_schema_mapping_table(style_results: dict) -> None:
    if HAS_RICH:
        console = Console()
        table = Table(title="Schema Mapping Accuracy by Naming Style", box=box.ROUNDED)
        table.add_column("Style", style="cyan")
        table.add_column("Correct", justify="right")
        table.add_column("Total", justify="right")
        table.add_column("Accuracy", justify="right")
        for style, res in style_results.items():
            acc = res["accuracy"]
            color = "green" if acc >= 0.8 else ("yellow" if acc >= 0.5 else "red")
            table.add_row(
                style,
                str(res["correct"]),
                str(res["total"]),
                f"[{color}]{acc:.2%}[/{color}]",
            )
        console.print(table)
    else:
        print("\n=== Schema Mapping Accuracy ===")
        print(f"{'Style':<20} {'Correct':>8} {'Total':>6} {'Accuracy':>10}")
        print("-" * 50)
        for style, res in style_results.items():
            print(f"{style:<20} {res['correct']:>8} {res['total']:>6} {res['accuracy']:>10.2%}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Loading benchmark dataset…")
    df, gt = load_data()
    print(f"  {len(df)} rows × {len(df.columns)} columns loaded.\n")

    # 1. Per-transform accuracy
    print("Running per-transform accuracy checks…")
    transform_results = measure_transform_accuracy(df, gt)
    print_transform_table(transform_results)

    avg_acc = sum(r["accuracy"] for r in transform_results if r["accuracy"] is not None) / max(
        1, sum(1 for r in transform_results if r["accuracy"] is not None)
    )
    print(f"\n  Average accuracy across transforms: {avg_acc:.2%}\n")

    # 2. Zero-config
    print("Running zero-config precision/recall benchmark…")
    zc = measure_zero_config(df, gt)
    print_zero_config_table(zc)

    # 3. Schema mapping
    print("\nRunning schema mapping accuracy benchmark…")
    sm = measure_schema_mapping()
    print_schema_mapping_table(sm)

    overall_sm = sum(r["accuracy"] for r in sm.values()) / len(sm)
    print(f"\n  Average schema mapping accuracy: {overall_sm:.2%}")

    print("\nQuality benchmark complete.")


if __name__ == "__main__":
    main()
