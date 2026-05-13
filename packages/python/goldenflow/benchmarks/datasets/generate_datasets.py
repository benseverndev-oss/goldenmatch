"""
Generate synthetic benchmark datasets for GoldenFlow quality benchmarks.
Produces 5,000 rows with planted transform targets across all categories.
"""
from __future__ import annotations

import csv
import json
import os
import random
import sys
from pathlib import Path

# Ensure goldenflow is importable
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SEED = 42
N_ROWS = 5000
OUT_DIR = Path(__file__).parent / "goldenflow_bench"

# ── Seed data pools ──────────────────────────────────────────────────────────

FIRST_NAMES = [
    "John", "Jane", "Robert", "Mary", "Michael", "Patricia",
    "William", "Linda", "David", "Barbara", "Richard", "Susan",
    "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
    "James", "Nancy",
]
LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Wilson", "Taylor", "Anderson", "Thomas",
    "Jackson", "White", "Harris", "Martin", "Thompson", "Moore",
    "Young", "Allen",
]
SPECIAL_LAST = ["McDonald", "O'Brien", "Van Houten", "St. Claire", "Fitzgerald"]
TITLES = ["Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Rev."]

CITIES = [
    "Philadelphia", "Austin", "Denver", "Portland", "Atlanta",
    "Chicago", "Houston", "Phoenix", "Seattle", "Boston",
    "Miami", "Detroit", "Minneapolis", "San Diego", "Dallas",
]

STATES_FULL = [
    "Pennsylvania", "Texas", "Colorado", "Oregon", "Georgia",
    "Illinois", "California", "Arizona", "Washington", "Massachusetts",
    "Florida", "Michigan", "Minnesota", "New York", "Virginia",
]
STATES_ABBR = ["PA", "TX", "CO", "OR", "GA", "IL", "CA", "AZ", "WA", "MA",
               "FL", "MI", "MN", "NY", "VA"]
STATES_LOWER = [s.lower() for s in STATES_FULL]
STATES_MIXED = [s.title() for s in STATES_LOWER]  # "New York" already titlecase

STREET_NAMES = ["Main", "Oak", "Elm", "Maple", "Cedar", "Pine", "River", "Hill"]
STREET_TYPES_FULL = ["Street", "Avenue", "Boulevard", "Drive", "Lane", "Road"]
STREET_TYPES_ABBR = ["St", "Ave", "Blvd", "Dr", "Ln", "Rd"]


def build_full_name(rng: random.Random) -> str:
    """Return one of several name formats."""
    variant = rng.randint(0, 3)
    fn = rng.choice(FIRST_NAMES)
    ln = rng.choice(LAST_NAMES + SPECIAL_LAST)

    if variant == 0:
        # Needs strip
        return f"  {fn} {ln}  "
    elif variant == 1:
        # Needs strip_titles + title_case
        title = rng.choice(TITLES)
        return f"{title} {fn.upper()} {ln.upper()}"
    elif variant == 2:
        # Needs name_proper
        return f"{fn.lower()} {ln.lower()}"
    else:
        # Comma-separated: "Smith, John"
        return f"{ln}, {fn}"


def build_email(rng: random.Random) -> str:
    fn = rng.choice(FIRST_NAMES).lower()
    ln = rng.choice(LAST_NAMES).lower()
    domain = rng.choice(["test.com", "example.org", "demo.net"])
    variant = rng.randint(0, 3)
    if variant == 0:
        return f"{fn}@{domain}".upper()  # needs lowercase
    elif variant == 1:
        return f"  {fn}.{ln}@{domain}  "  # needs strip
    elif variant == 2:
        return "not-an-email"  # invalid
    else:
        return f"{fn}.{ln}@{domain}"  # already fine


def build_phone(rng: random.Random) -> str:
    area = rng.randint(200, 999)
    mid = rng.randint(100, 999)
    last = rng.randint(1000, 9999)
    variant = rng.randint(0, 4)
    if variant == 0:
        return f"({area}) {mid}-{last}"
    elif variant == 1:
        return f"{area}.{mid}.{last}"
    elif variant == 2:
        return f"+1-{area}-{mid}-{last}"
    elif variant == 3:
        return f"{area}{mid}{last}"
    else:
        return "555-CALL-NOW"  # invalid


def build_address(rng: random.Random) -> str:
    num = rng.randint(1, 9999)
    street = rng.choice(STREET_NAMES)
    variant = rng.randint(0, 1)
    if variant == 0:
        stype = rng.choice(STREET_TYPES_FULL)
    else:
        stype = rng.choice(STREET_TYPES_ABBR)
    return f"{num} {street} {stype}"


def build_state(rng: random.Random) -> str:
    idx = rng.randint(0, len(STATES_FULL) - 1)
    variant = rng.randint(0, 3)
    if variant == 0:
        return STATES_FULL[idx]        # "Pennsylvania" – needs abbreviate
    elif variant == 1:
        return STATES_ABBR[idx]        # "PA" – already abbreviated
    elif variant == 2:
        return STATES_LOWER[idx]       # "pennsylvania" – needs capitalize + abbreviate
    else:
        return STATES_MIXED[idx]       # "Pennsylvania" title-cased


def build_zip(rng: random.Random) -> str:
    variant = rng.randint(0, 3)
    if variant == 0:
        return str(rng.randint(10000, 99999))  # ok
    elif variant == 1:
        return str(rng.randint(1000, 9999))    # needs zero-pad
    elif variant == 2:
        return f"{rng.randint(10000, 99999)}-{rng.randint(1000, 9999)}"  # needs strip +4
    else:
        return "abcde"  # invalid


def build_signup_date(rng: random.Random) -> str:
    year = rng.randint(2020, 2024)
    month = rng.randint(1, 12)
    day = rng.randint(1, 28)
    variant = rng.randint(0, 4)
    months_str = [
        "Jan", "Feb", "Mar", "Apr", "May", "Jun",
        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
    ]
    months_long = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    if variant == 0:
        return f"{month:02d}/{day:02d}/{year}"   # US format
    elif variant == 1:
        return f"{year}-{month:02d}-{day:02d}"   # ISO already
    elif variant == 2:
        return f"{months_str[month-1]} {day}, {year}"  # "Jan 5, 2023"
    elif variant == 3:
        return f"{months_long[month-1]} {day}, {year}"  # "March 15, 2024"
    else:
        return "invalid-date"  # invalid


def build_price(rng: random.Random) -> str:
    variant = rng.randint(0, 3)
    if variant == 0:
        dollars = rng.randint(100, 9999)
        cents = rng.randint(0, 99)
        return f"${dollars:,}.{cents:02d}"
    elif variant == 1:
        return f"${rng.randint(1, 999)}.{rng.randint(0, 99):02d}"
    elif variant == 2:
        return "free"  # non-numeric
    else:
        return f"${rng.randint(0, 9)}.{rng.randint(0, 99):02d}"


def build_active(rng: random.Random) -> str:
    choices = ["Yes", "Y", "1", "True", "No", "false", "maybe", "NO", "TRUE", "0"]
    return rng.choice(choices)


def build_country(rng: random.Random) -> str:
    return rng.choice(["USA", "United States", "U.S.A.", "US", "Canada",
                        "canada", "CANADA", "united states", "usa"])


def build_gender(rng: random.Random) -> str:
    return rng.choice(["Male", "m", "Female", "F", "Other", "M", "female", "MALE"])


def build_ssn(rng: random.Random) -> str:
    a = rng.randint(100, 899)
    b = rng.randint(10, 99)
    c = rng.randint(1000, 9999)
    variant = rng.randint(0, 3)
    if variant == 0:
        return f"{a:03d}-{b:02d}-{c:04d}"  # standard with dashes
    elif variant == 1:
        return f"{a:03d}{b:02d}{c:04d}"   # no dashes
    elif variant == 2:
        return "000-00-0000"  # invalid (all zeros)
    else:
        return "not-an-ssn"  # invalid


def build_notes(rng: random.Random) -> str:
    choices = [
        "N/A", "NULL", "", "  ", "actual notes about the customer",
        "café au lait preference", "Follow up needed", "VIP client",
        "none", "nil", "Call back Monday",
    ]
    return rng.choice(choices)


def build_pct(rng: random.Random) -> str:
    variant = rng.randint(0, 3)
    if variant == 0:
        return f"{rng.randint(1, 100)}%"
    elif variant == 1:
        return f"{rng.uniform(0, 1):.2%}"  # e.g. "0.50%"
    elif variant == 2:
        return str(rng.randint(0, 100))    # bare number
    else:
        return f"{rng.uniform(0, 100):.1f}%"


# ── Generation ────────────────────────────────────────────────────────────────

def generate(n: int = N_ROWS, seed: int = SEED) -> list[dict]:
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        rows.append({
            "full_name":    build_full_name(rng),
            "email":        build_email(rng),
            "phone":        build_phone(rng),
            "address":      build_address(rng),
            "city":         rng.choice(CITIES),
            "state":        build_state(rng),
            "zip":          build_zip(rng),
            "signup_date":  build_signup_date(rng),
            "price":        build_price(rng),
            "active":       build_active(rng),
            "country":      build_country(rng),
            "gender":       build_gender(rng),
            "ssn":          build_ssn(rng),
            "notes":        build_notes(rng),
            "pct":          build_pct(rng),
        })
    return rows


def count_transform_targets(rows: list[dict]) -> dict:
    """Count how many rows each planted transform will affect."""
    counts = {
        "full_name_strip": 0,
        "full_name_strip_titles": 0,
        "email_lowercase": 0,
        "email_strip": 0,
        "phone_e164_valid": 0,
        "phone_e164_invalid": 0,
        "address_standardize": 0,
        "state_abbreviate_needed": 0,
        "state_already_abbr": 0,
        "zip_ok": 0,
        "zip_pad": 0,
        "zip_strip_plus4": 0,
        "zip_invalid": 0,
        "date_iso": 0,
        "date_invalid": 0,
        "price_parseable": 0,
        "price_non_numeric": 0,
        "active_true": 0,
        "active_false": 0,
        "active_ambiguous": 0,
        "notes_null": 0,
        "notes_actual": 0,
    }

    import phonenumbers
    for row in rows:
        fn = row["full_name"]
        if fn != fn.strip() or fn.startswith("  "):
            counts["full_name_strip"] += 1
        if any(t in fn for t in ["Dr.", "Mr.", "Mrs.", "Ms.", "Prof.", "Rev."]):
            counts["full_name_strip_titles"] += 1

        em = row["email"]
        if em != em.lower() and "@" in em:
            counts["email_lowercase"] += 1
        if em != em.strip():
            counts["email_strip"] += 1

        ph = row["phone"]
        try:
            parsed = phonenumbers.parse(ph, "US")
            if phonenumbers.is_possible_number(parsed):
                counts["phone_e164_valid"] += 1
            else:
                counts["phone_e164_invalid"] += 1
        except Exception:
            counts["phone_e164_invalid"] += 1

        addr = row["address"]
        needs_std = any(full in addr for full in ["Street", "Avenue", "Boulevard", "Drive", "Lane", "Road"])
        if needs_std:
            counts["address_standardize"] += 1

        st = row["state"]
        if len(st.strip()) == 2:
            counts["state_already_abbr"] += 1
        else:
            counts["state_abbreviate_needed"] += 1

        z = row["zip"]
        if z == "abcde":
            counts["zip_invalid"] += 1
        elif "-" in z and z.replace("-", "").isdigit():
            counts["zip_strip_plus4"] += 1
        elif z.isdigit() and len(z) < 5:
            counts["zip_pad"] += 1
        else:
            counts["zip_ok"] += 1

        d = row["signup_date"]
        if d == "invalid-date":
            counts["date_invalid"] += 1
        else:
            counts["date_iso"] += 1

        p = row["price"]
        if p == "free":
            counts["price_non_numeric"] += 1
        else:
            counts["price_parseable"] += 1

        a = row["active"].lower()
        if a in {"yes", "y", "1", "true"}:
            counts["active_true"] += 1
        elif a in {"no", "false", "0"}:
            counts["active_false"] += 1
        else:
            counts["active_ambiguous"] += 1

        n = row["notes"].strip().lower()
        if n in {"n/a", "null", "", "none", "nil", "nan", "-"}:
            counts["notes_null"] += 1
        else:
            counts["notes_actual"] += 1

    return counts


def build_ground_truth(rows: list[dict]) -> dict:
    counts = count_transform_targets(rows)
    n = len(rows)

    planted = [
        {
            "column": "full_name",
            "transform": "strip",
            "expected_change_count": counts["full_name_strip"],
            "description": "Full names with leading/trailing whitespace that need stripping",
            "sample_before": ["  John Smith  "],
            "sample_after": ["John Smith"],
        },
        {
            "column": "full_name",
            "transform": "strip_titles",
            "expected_change_count": counts["full_name_strip_titles"],
            "description": "Full names with honorific titles (Dr., Mr., etc.) to strip",
            "sample_before": ["DR. JANE DOE"],
            "sample_after": ["JANE DOE"],
        },
        {
            "column": "email",
            "transform": "lowercase",
            "expected_change_count": counts["email_lowercase"],
            "description": "Email addresses in UPPERCASE that need lowercasing",
            "sample_before": ["JOHN@TEST.COM"],
            "sample_after": ["john@test.com"],
        },
        {
            "column": "email",
            "transform": "strip",
            "expected_change_count": counts["email_strip"],
            "description": "Email addresses with surrounding whitespace",
            "sample_before": ["  jane@test.com  "],
            "sample_after": ["jane@test.com"],
        },
        {
            "column": "phone",
            "transform": "phone_e164",
            "expected_change_count": counts["phone_e164_valid"],
            "description": "Phone numbers in various formats that should normalize to E.164",
            "sample_before": ["(555) 123-4567", "555.987.6543", "+1-555-456-7890"],
            "sample_after": ["+15551234567", "+15559876543", "+15554567890"],
        },
        {
            "column": "address",
            "transform": "address_standardize",
            "expected_change_count": counts["address_standardize"],
            "description": "Street addresses with full street type words needing abbreviation",
            "sample_before": ["123 Main Street"],
            "sample_after": ["123 Main St"],
        },
        {
            "column": "state",
            "transform": "state_abbreviate",
            "expected_change_count": counts["state_abbreviate_needed"],
            "description": "State names as full words or lowercase that need abbreviating",
            "sample_before": ["Pennsylvania", "new york"],
            "sample_after": ["PA", "NY"],
        },
        {
            "column": "zip",
            "transform": "zip_normalize",
            "expected_change_count": counts["zip_pad"] + counts["zip_strip_plus4"],
            "description": "ZIP codes needing zero-padding or +4 suffix stripping",
            "sample_before": ["9001", "10001-1234"],
            "sample_after": ["09001", "10001"],
        },
        {
            "column": "signup_date",
            "transform": "date_iso8601",
            "expected_change_count": counts["date_iso"],
            "description": "Dates in various formats to normalise to ISO 8601",
            "sample_before": ["03/15/2024", "Jan 5, 2023", "March 15, 2024"],
            "sample_after": ["2024-03-15", "2023-01-05", "2024-03-15"],
        },
        {
            "column": "price",
            "transform": "currency_strip",
            "expected_change_count": n,
            "description": "Currency strings like '$1,234.56' needing numeric extraction; 'free' maps to null",
            "sample_before": ["$1,234.56", "$99.99", "free"],
            "sample_after": [1234.56, 99.99, None],
        },
        {
            "column": "active",
            "transform": "boolean_normalize",
            "expected_change_count": counts["active_true"] + counts["active_false"],
            "description": "Boolean-like strings (Yes/No/1/True) to normalize to True/False",
            "sample_before": ["Yes", "Y", "1", "True", "No", "false"],
            "sample_after": [True, True, True, True, False, False],
        },
        {
            "column": "notes",
            "transform": "null_standardize",
            "expected_change_count": counts["notes_null"],
            "description": "Null-sentinel strings (N/A, NULL, blank) to convert to None",
            "sample_before": ["N/A", "NULL", ""],
            "sample_after": [None, None, None],
        },
        {
            "column": "pct",
            "transform": "percentage_normalize",
            "expected_change_count": n,
            "description": "Percentage strings like '85%' to normalize to 0.85",
            "sample_before": ["85%", "100%", "50"],
            "sample_after": [0.85, 1.0, 0.50],
        },
    ]

    ground_truth = {
        "dataset": "goldenflow_bench_v1",
        "rows": n,
        "columns": 15,
        "planted_transforms": planted,
        "zero_config_expected": {
            "columns_that_should_transform": [
                "full_name", "email", "phone", "state", "zip",
                "signup_date", "notes",
            ],
            "columns_that_should_not_transform": ["city"],
        },
    }
    return ground_truth


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Generating {N_ROWS} rows with seed={SEED}…")
    rows = generate(N_ROWS, SEED)

    # Write CSV
    csv_path = OUT_DIR / "data.csv"
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  CSV  -> {csv_path}  ({os.path.getsize(csv_path):,} bytes)")

    # Write ground truth
    gt = build_ground_truth(rows)
    gt_path = OUT_DIR / "ground_truth.json"
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump(gt, f, indent=2, default=str)
    print(f"  GT   -> {gt_path}")

    # Summary
    print("\nPlanted transform summary:")
    for pt in gt["planted_transforms"]:
        print(f"  {pt['column']:15s} {pt['transform']:25s} {pt['expected_change_count']:5d} rows")

    print("\nDone.")


if __name__ == "__main__":
    main()
