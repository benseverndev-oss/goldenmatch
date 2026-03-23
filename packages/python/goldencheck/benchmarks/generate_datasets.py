"""
Generate synthetic datasets with known, planted quality issues for GoldenCheck benchmarking.

Run:
    python benchmarks/generate_datasets.py
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import polars as pl

SEED = 42
N_ROWS = 5000
OUT_DIR = Path(__file__).parent / "datasets" / "goldencheck_bench"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_date(rng: random.Random, year_min: int = 2015, year_max: int = 2023) -> str:
    y = rng.randint(year_min, year_max)
    m = rng.randint(1, 12)
    d = rng.randint(1, 28)
    return f"{y}-{m:02d}-{d:02d}"


def _date_to_wrong_format(iso: str) -> str:
    """Convert YYYY-MM-DD to MM-DD-YYYY."""
    y, m, d = iso.split("-")
    return f"{m}-{d}-{y}"


FIRST_NAMES = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael", "Linda",
    "William", "Barbara", "David", "Elizabeth", "Richard", "Susan", "Joseph", "Jessica",
    "Thomas", "Sarah", "Charles", "Karen", "Daniel", "Lisa", "Matthew", "Nancy",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis",
    "Wilson", "Taylor", "Anderson", "Thomas", "Jackson", "White", "Harris", "Martin",
]
COUNTRIES_VALID = [
    "US", "CA", "GB", "AU", "DE", "FR", "JP", "IN", "BR", "MX",
    "IT", "ES", "NL", "SE", "NO", "DK", "FI", "SG", "NZ", "ZA",
]
COUNTRIES_INVALID = ["XX", "ZZ", "QQ", "AB", "YY", "KK", "WW", "PP"]

STATUS_VALID = ["active", "inactive", "pending"]
STATUS_MISSPELLED = ["actve", "ACTIVE", "Active", "inactve", "INACTIVE", "Inactive",
                     "pendig", "PENDING", "Pending", "activ", "actief", "inactvie"]

AGE_WORD_STRINGS = [
    "thirty", "twenty-five", "forty", "fifty-two", "sixty", "eighteen",
    "seventy", "thirty-three", "forty-five", "twenty", "fifty", "sixty-four",
    "twenty-eight", "forty-one", "thirty-seven",
]

GIBBERISH_EMAILS = [
    "not-an-email", "5551234567", "http://example.com", "user at domain dot com",
    "###@@@!!!", "plaintext", "+1-555-123-4567", "N/A", "none",
    "@nodomain", "noatsign.com", "double@@at.com", "space in@email.com",
    "tab\tin@email.com", ".leadingdot@email.com", "trailing@dot.", "mising@tld",
    "555.123.4567", "http://bad-email.org", "random gibberish",
]


def generate_goldencheck_bench() -> None:
    rng = random.Random(SEED)

    # ------------------------------------------------------------------
    # Phase 1: build clean baseline rows
    # ------------------------------------------------------------------
    customer_ids = list(range(1, N_ROWS + 1))
    first_names: list[str | None] = [rng.choice(FIRST_NAMES) for _ in range(N_ROWS)]
    last_names: list[str | None] = [rng.choice(LAST_NAMES) for _ in range(N_ROWS)]
    emails: list[str | None] = [f"user{i}@example.com" for i in range(N_ROWS)]
    phones: list[str] = [
        f"({rng.randint(200,999)}) {rng.randint(200,999)}-{rng.randint(1000,9999)}"
        for _ in range(N_ROWS)
    ]
    ages: list[int | str] = [rng.randint(18, 80) for _ in range(N_ROWS)]
    incomes: list[float] = [round(rng.uniform(20_000, 150_000), 2) for _ in range(N_ROWS)]
    statuses: list[str] = [rng.choice(STATUS_VALID) for _ in range(N_ROWS)]
    signup_dates: list[str] = [_random_date(rng, 2015, 2020) for _ in range(N_ROWS)]
    last_logins: list[str] = [_random_date(rng, 2020, 2023) for _ in range(N_ROWS)]
    countries: list[str] = [rng.choice(COUNTRIES_VALID) for _ in range(N_ROWS)]
    zip_codes: list[str] = [
        f"{rng.randint(10000,99999):05d}" for _ in range(N_ROWS)
    ]
    shipping_addresses: list[str | None] = [
        f"{rng.randint(1, 9999)} {rng.choice(['Main', 'Oak', 'Elm', 'Maple', 'Cedar'])} St"
        for _ in range(N_ROWS)
    ]
    shipping_cities: list[str | None] = [
        rng.choice(["Springfield", "Shelbyville", "Ogdenville", "North Haverbrook", "Capital City"])
        for _ in range(N_ROWS)
    ]
    shipping_zips: list[str | None] = [
        f"{rng.randint(10000,99999):05d}" for _ in range(N_ROWS)
    ]

    # ------------------------------------------------------------------
    # Phase 2: plant issues — track every affected row index
    # ------------------------------------------------------------------
    planted_issues: list[dict] = []

    # --- uniqueness: 10 duplicate customer_ids ---
    dup_source_rows = rng.sample(range(N_ROWS), 10)
    dup_target_rows = rng.sample(
        [i for i in range(N_ROWS) if i not in dup_source_rows], 10
    )
    for src, tgt in zip(dup_source_rows, dup_target_rows):
        customer_ids[tgt] = customer_ids[src]
    planted_issues.append({
        "column": "customer_id",
        "check": "uniqueness",
        "expected_severity": "warning",
        "planted_count": 10,
        "description": "10 duplicate customer IDs",
        "affected_rows": sorted(dup_target_rows),
    })

    # --- nullability: 5 nulls in first_name ---
    null_first_name_rows = rng.sample(range(N_ROWS), 5)
    for i in null_first_name_rows:
        first_names[i] = None
    planted_issues.append({
        "column": "first_name",
        "check": "nullability",
        "expected_severity": "info",
        "planted_count": 5,
        "description": "5 null first_name values in a required column",
        "affected_rows": sorted(null_first_name_rows),
    })

    # --- type_inference: 3 last_name values that are numbers ---
    numeric_last_name_rows = rng.sample(range(N_ROWS), 3)
    for i in numeric_last_name_rows:
        last_names[i] = str(rng.randint(10000, 99999))
    planted_issues.append({
        "column": "last_name",
        "check": "type_inference",
        "expected_severity": "warning",
        "planted_count": 3,
        "description": "3 last_name values that are numeric strings (like '12345')",
        "affected_rows": sorted(numeric_last_name_rows),
    })

    # --- format_detection: 20 non-email values in email column ---
    bad_email_rows = rng.sample(range(N_ROWS), 20)
    bad_email_values = rng.choices(GIBBERISH_EMAILS, k=20)
    for i, val in zip(bad_email_rows, bad_email_values):
        emails[i] = val
    planted_issues.append({
        "column": "email",
        "check": "format_detection",
        "expected_severity": "warning",
        "planted_count": 20,
        "description": "20 non-email values (phone numbers, URLs, gibberish) in email column",
        "affected_rows": sorted(bad_email_rows),
    })

    # --- pattern_consistency: mixed phone formats ---
    # Baseline is (NNN) NNN-NNNN. Plant two minority formats.
    phone_fmt2_rows = rng.sample(range(N_ROWS), 50)   # NNN-NNN-NNNN
    phone_fmt3_rows = rng.sample(
        [i for i in range(N_ROWS) if i not in phone_fmt2_rows], 50
    )  # NNNNNNNNNN
    for i in phone_fmt2_rows:
        phones[i] = f"{rng.randint(200,999)}-{rng.randint(200,999)}-{rng.randint(1000,9999)}"
    for i in phone_fmt3_rows:
        phones[i] = f"{rng.randint(2000000000,9999999999)}"
    planted_issues.append({
        "column": "phone",
        "check": "pattern_consistency",
        "expected_severity": "warning",
        "planted_count": 100,
        "description": "Mixed phone formats: (NNN) NNN-NNNN vs NNN-NNN-NNNN vs NNNNNNNNNN",
        "affected_rows": sorted(phone_fmt2_rows + phone_fmt3_rows),
    })

    # --- type_inference: 15 age values as word strings ---
    age_word_rows = rng.sample(range(N_ROWS), 15)
    for i in age_word_rows:
        ages[i] = rng.choice(AGE_WORD_STRINGS)
    # --- range_distribution: 5 age outliers ---
    age_outlier_rows = rng.sample(
        [i for i in range(N_ROWS) if i not in age_word_rows], 5
    )
    age_outlier_values = [999, -1, 150, 200, -5]
    for i, val in zip(age_outlier_rows, age_outlier_values):
        ages[i] = val
    # Convert age column: everything numeric must be a float/int; word strings stay as strings.
    # Store as strings so polars sees a mixed column (all written as string in CSV)
    ages_str = [str(a) for a in ages]
    planted_issues.append({
        "column": "age",
        "check": "type_inference",
        "expected_severity": "warning",
        "planted_count": 15,
        "description": "15 age values stored as word strings like 'thirty'",
        "affected_rows": sorted(age_word_rows),
    })
    planted_issues.append({
        "column": "age",
        "check": "range_distribution",
        "expected_severity": "warning",
        "planted_count": 5,
        "description": "5 age outliers (e.g., 999, -1, 150)",
        "affected_rows": sorted(age_outlier_rows),
    })

    # --- range_distribution: 8 extreme income outliers ---
    income_outlier_rows = rng.sample(range(N_ROWS), 8)
    income_outlier_values = [9_999_999.99, 8_888_888.88, 7_777_777.77,
                              9_500_000.00, 8_000_000.00, 9_100_000.00,
                              7_500_000.00, 8_250_000.00]
    for i, val in zip(income_outlier_rows, income_outlier_values):
        incomes[i] = val
    planted_issues.append({
        "column": "income",
        "check": "range_distribution",
        "expected_severity": "warning",
        "planted_count": 8,
        "description": "8 extreme income outliers (e.g., 9,999,999.99)",
        "affected_rows": sorted(income_outlier_rows),
    })

    # --- cardinality: 12 misspelled status values ---
    misspelled_status_rows = rng.sample(range(N_ROWS), 12)
    for i in misspelled_status_rows:
        statuses[i] = rng.choice(STATUS_MISSPELLED)
    planted_issues.append({
        "column": "status",
        "check": "cardinality",
        "expected_severity": "info",
        "planted_count": 12,
        "description": "12 misspelled status values (e.g., 'actve', 'ACTIVE') increasing cardinality",
        "affected_rows": sorted(misspelled_status_rows),
    })

    # --- pattern_consistency: 10 signup_dates in wrong format MM-DD-YYYY ---
    wrong_fmt_signup_rows = rng.sample(range(N_ROWS), 10)
    for i in wrong_fmt_signup_rows:
        signup_dates[i] = _date_to_wrong_format(signup_dates[i])
    planted_issues.append({
        "column": "signup_date",
        "check": "pattern_consistency",
        "expected_severity": "warning",
        "planted_count": 10,
        "description": "10 signup_date values in MM-DD-YYYY instead of YYYY-MM-DD",
        "affected_rows": sorted(wrong_fmt_signup_rows),
    })

    # --- temporal_order: 15 rows where last_login < signup_date ---
    # We need signup_date to be a proper ISO date for the temporal profiler to parse it,
    # so pick only from rows that still have valid ISO format signup dates.
    valid_iso_rows = [
        i for i in range(N_ROWS)
        if i not in wrong_fmt_signup_rows
    ]
    temporal_violation_rows = rng.sample(valid_iso_rows, 15)
    for i in temporal_violation_rows:
        # set last_login to 1-3 years before signup_date
        y, m, d = signup_dates[i].split("-")
        back_years = rng.randint(1, 3)
        new_year = int(y) - back_years
        if new_year < 2010:
            new_year = 2010
        last_logins[i] = f"{new_year}-{m}-{d}"
    planted_issues.append({
        "column": "last_login",
        "check": "temporal_order",
        "expected_severity": "error",
        "planted_count": 15,
        "description": "15 rows where last_login < signup_date (temporal violation)",
        "affected_rows": sorted(temporal_violation_rows),
    })

    # --- country: 8 invalid 2-letter codes ---
    invalid_country_rows = rng.sample(range(N_ROWS), 8)
    for i in invalid_country_rows:
        countries[i] = rng.choice(COUNTRIES_INVALID)
    planted_issues.append({
        "column": "country",
        "check": "pattern_consistency",
        "expected_severity": "warning",
        "planted_count": 8,
        "description": "8 invalid country codes like 'XX', 'ZZ'",
        "affected_rows": sorted(invalid_country_rows),
    })

    # --- zip_code: mixed formats (5-digit vs 9-digit vs with spaces) ---
    zip9_rows = rng.sample(range(N_ROWS), 60)           # ZIP+4 e.g. 12345-6789
    zip_space_rows = rng.sample(
        [i for i in range(N_ROWS) if i not in zip9_rows], 40
    )  # e.g. "12345 6789"
    for i in zip9_rows:
        zip_codes[i] = (
            f"{rng.randint(10000,99999):05d}-{rng.randint(1000,9999):04d}"
        )
    for i in zip_space_rows:
        zip_codes[i] = (
            f"{rng.randint(10000,99999):05d} {rng.randint(1000,9999):04d}"
        )
    planted_issues.append({
        "column": "zip_code",
        "check": "pattern_consistency",
        "expected_severity": "warning",
        "planted_count": 100,
        "description": "Mixed zip code formats: 5-digit vs ZIP+4 (NNNNN-NNNN) vs space-separated",
        "affected_rows": sorted(zip9_rows + zip_space_rows),
    })

    # --- null_correlation: correlated nulls in shipping_address/city/zip ---
    correlated_null_rows = rng.sample(range(N_ROWS), 30)
    for i in correlated_null_rows:
        shipping_addresses[i] = None
        shipping_cities[i] = None
        shipping_zips[i] = None
    planted_issues.append({
        "column": "shipping_address",
        "check": "null_correlation",
        "expected_severity": "info",
        "planted_count": 30,
        "description": "30 rows with correlated nulls across shipping_address, shipping_city, shipping_zip",
        "affected_rows": sorted(correlated_null_rows),
    })

    # ------------------------------------------------------------------
    # Phase 3: assemble DataFrame and write CSV
    # ------------------------------------------------------------------
    df = pl.DataFrame(
        {
            "customer_id": customer_ids,
            "first_name": first_names,
            "last_name": last_names,
            "email": emails,
            "phone": phones,
            "age": ages_str,
            "income": incomes,
            "status": statuses,
            "signup_date": signup_dates,
            "last_login": last_logins,
            "country": countries,
            "zip_code": zip_codes,
            "shipping_address": shipping_addresses,
            "shipping_city": shipping_cities,
            "shipping_zip": shipping_zips,
        }
    )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUT_DIR / "dirty.csv"
    df.write_csv(csv_path)
    print(f"Wrote {len(df)} rows to {csv_path}")

    # ------------------------------------------------------------------
    # Phase 4: build ground_truth manifest
    # ------------------------------------------------------------------
    # Issue category summaries
    def _sum_planted(checks: list[str], cols: list[str]) -> int:
        return sum(
            issue["planted_count"]
            for issue in planted_issues
            if issue["check"] in checks and issue["column"] in cols
        )

    issue_categories = {
        "type_inference": {
            "columns": ["age", "last_name"],
            "total_planted": _sum_planted(["type_inference"], ["age", "last_name"]),
        },
        "nullability": {
            "columns": ["first_name"],
            "total_planted": _sum_planted(["nullability"], ["first_name"]),
        },
        "uniqueness": {
            "columns": ["customer_id"],
            "total_planted": _sum_planted(["uniqueness"], ["customer_id"]),
        },
        "format_detection": {
            "columns": ["email"],
            "total_planted": _sum_planted(["format_detection"], ["email"]),
        },
        "range_distribution": {
            "columns": ["age", "income"],
            "total_planted": _sum_planted(["range_distribution"], ["age", "income"]),
        },
        "cardinality": {
            "columns": ["status"],
            "total_planted": _sum_planted(["cardinality"], ["status"]),
        },
        "pattern_consistency": {
            "columns": ["phone", "signup_date", "zip_code", "country"],
            "total_planted": _sum_planted(["pattern_consistency"], ["phone", "signup_date", "zip_code", "country"]),
        },
        "temporal_order": {
            "columns": ["signup_date", "last_login"],
            "total_planted": _sum_planted(["temporal_order"], ["last_login"]),
        },
        "null_correlation": {
            "columns": ["shipping_address", "shipping_city", "shipping_zip"],
            "total_planted": _sum_planted(["null_correlation"], ["shipping_address"]),
        },
    }

    manifest = {
        "dataset": "goldencheck_bench_v1",
        "rows": N_ROWS,
        "columns": len(df.columns),
        "planted_issues": planted_issues,
        "total_planted_issues": sum(i["planted_count"] for i in planted_issues),
        "issue_categories": issue_categories,
    }

    gt_path = OUT_DIR / "ground_truth.json"
    gt_path.write_text(json.dumps(manifest, indent=2))
    print(f"Wrote ground truth to {gt_path}")
    print(f"Total planted issues: {manifest['total_planted_issues']}")
    print(f"Issue categories: {list(issue_categories.keys())}")

    return manifest


if __name__ == "__main__":
    generate_goldencheck_bench()
