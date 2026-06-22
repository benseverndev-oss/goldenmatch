"""Oracle script: generate golden parity vectors for autoconfig-core.

NEVER re-implements the rules — always calls the real Python functions.
Output:
  packages/rust/extensions/autoconfig-core/golden/planner_vectors.json
  packages/rust/extensions/autoconfig-core/golden/classifier_vectors.json

Run:
  GOLDENMATCH_NATIVE=0 POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8 \
  PYTHONPATH="D:/show_case/gm-autoconfig-core/packages/python/goldenmatch" \
  "D:/show_case/goldenmatch/.venv/Scripts/python.exe" scripts/gen_autoconfig_golden.py
"""
from __future__ import annotations

import json
import os
import types

# ── Force pure-Python path (oracle must never call the thing under test) ───────
os.environ["GOLDENMATCH_NATIVE"] = "0"
os.environ["POLARS_SKIP_CPU_CHECK"] = "1"

# ── Imports ────────────────────────────────────────────────────────────────────
from pathlib import Path

import goldenmatch.core.autoconfig_planner_rules as _rules_mod
import polars as pl
from goldenmatch.core.autoconfig_planner import apply_planner_rules
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES  # noqa: PLC2701
from goldenmatch.core.runtime_profile import RuntimeProfile

OUT_DIR = Path(__file__).parent.parent / "packages/rust/extensions/autoconfig-core/golden"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ── Helper: build a ComplexityProfile duck-type with only what the planner needs ──
def _make_profile(estimated_pair_count: int) -> types.SimpleNamespace:
    """Minimal duck-type satisfying `profile.blocking.estimated_pair_count`."""
    blocking = types.SimpleNamespace(estimated_pair_count=estimated_pair_count)
    return types.SimpleNamespace(blocking=blocking)


def _make_runtime(available_ram_gb: float, cpu_count: int, disk_free_gb: float = 500.0) -> RuntimeProfile:
    return RuntimeProfile(
        available_ram_gb=available_ram_gb,
        cpu_count=cpu_count,
        disk_free_gb=disk_free_gb,
    )


def _call_planner(
    n_rows_full: int,
    estimated_pair_count: int,
    available_ram_gb: float,
    cpu_count: int,
    disk_free_gb: float = 500.0,
    bucket_available: bool = False,
    ray_available: bool = False,
    ray_auto_select: bool = False,
    user_backend: str | None = None,
) -> dict:
    """Call apply_planner_rules with monkeypatched capabilities and return
    the serialisable {input, expected} dict."""
    profile = _make_profile(estimated_pair_count)
    runtime = _make_runtime(available_ram_gb, cpu_count, disk_free_gb)

    # Monkeypatch native_enabled("block_scoring") + GOLDENMATCH_PLANNER_BUCKET
    # to control caps.bucket_available as the Rust layer sees it.
    original_native_enabled = _rules_mod.native_enabled

    def _patched_native_enabled(component: str) -> bool:
        if component == "block_scoring":
            return bucket_available
        return False

    _rules_mod.native_enabled = _patched_native_enabled

    # Monkeypatch _has_ray
    original_has_ray = _rules_mod._has_ray

    def _patched_has_ray() -> bool:
        return ray_available

    _rules_mod._has_ray = _patched_has_ray

    # Set ray auto-select env
    if ray_auto_select:
        os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "1"
    else:
        os.environ["GOLDENMATCH_ENABLE_DISTRIBUTED_RAY"] = "0"

    # Disable bucket opt-out so bucket_available fully controls it
    os.environ.pop("GOLDENMATCH_PLANNER_BUCKET", None)

    context: dict | None = None
    if user_backend is not None:
        context = {"user_backend": user_backend}

    try:
        plan = apply_planner_rules(profile, runtime, n_rows_full, DEFAULT_RULES, context=context)
    finally:
        _rules_mod.native_enabled = original_native_enabled
        _rules_mod._has_ray = original_has_ray

    # Build the PlannerInput-shaped dict (what Rust deserializes)
    input_dict = {
        "n_rows_full": n_rows_full,
        "estimated_pair_count": estimated_pair_count,
        "runtime": {
            "available_ram_gb": available_ram_gb,
            "cpu_count": cpu_count,
            "disk_free_gb": disk_free_gb,
        },
        "caps": {
            "bucket_available": bucket_available,
            "ray_available": ray_available,
            "ray_auto_select": ray_auto_select,
            "user_backend": user_backend,  # None -> JSON null
        },
    }

    # Build the ExecutionPlan-shaped dict (what Rust must produce)
    # pair_spill_threshold: Python uses "ram"/"duckdb"/"disk_per_worker"/None
    # Rust serializes SpillThreshold as snake_case: "ram"/"duckdb"/"disk_per_worker"
    # chunk_size: Python int or None
    expected_dict = {
        "backend": plan.backend,
        "chunk_size": plan.chunk_size,  # int or None -> null
        "max_workers": plan.max_workers,
        "pair_spill_threshold": plan.pair_spill_threshold,  # str or None -> null
        "clustering_strategy": plan.clustering_strategy,
        "rule_name": plan.rule_name,
    }

    vec = {"input": input_dict, "expected": expected_dict}

    # Assert round-trip BEFORE returning
    round_tripped = json.loads(json.dumps(vec))
    assert round_tripped == vec, f"Round-trip mismatch for vector: {vec}"

    return vec


# ── Planner vectors ─────────────────────────────────────────────────────────────

def gen_planner_vectors() -> list[dict]:
    vectors: list[dict] = []

    def add(**kwargs) -> None:
        v = _call_planner(**kwargs)
        vectors.append(v)

    # ── Rule 1: pathological (n_rows <= 1) ────────────────────────────────────
    # pair_spill_threshold=null here (pathological plan leaves it None)
    add(n_rows_full=0, estimated_pair_count=0, available_ram_gb=64.0, cpu_count=16)
    add(n_rows_full=1, estimated_pair_count=0, available_ram_gb=64.0, cpu_count=16)
    add(n_rows_full=1, estimated_pair_count=0, available_ram_gb=8.0, cpu_count=4)
    add(n_rows_full=0, estimated_pair_count=0, available_ram_gb=64.0, cpu_count=16, bucket_available=True)

    # ── Rule 2: simple plan ────────────────────────────────────────────────────
    # n_rows < 100k AND pairs < 50M; pair_spill_threshold=null
    add(n_rows_full=50_000, estimated_pair_count=1_000_000, available_ram_gb=64.0, cpu_count=16)
    add(n_rows_full=50_000, estimated_pair_count=1_000_000, available_ram_gb=64.0, cpu_count=16, bucket_available=True)
    add(n_rows_full=99_999, estimated_pair_count=49_999_999, available_ram_gb=64.0, cpu_count=16, bucket_available=True)
    add(n_rows_full=99_999, estimated_pair_count=49_999_999, available_ram_gb=64.0, cpu_count=2)
    add(n_rows_full=2, estimated_pair_count=1, available_ram_gb=64.0, cpu_count=16)
    # cpu_count < 4 -- max_workers = min(4, cpu_count)
    add(n_rows_full=10_000, estimated_pair_count=500_000, available_ram_gb=64.0, cpu_count=2)
    add(n_rows_full=10_000, estimated_pair_count=500_000, available_ram_gb=64.0, cpu_count=4)

    # ── Rule 3: fast_box ──────────────────────────────────────────────────────
    # n_rows >= 100k, pairs < 50M, ram >= 32; pair_spill_threshold=null
    add(n_rows_full=100_000, estimated_pair_count=1_000_000, available_ram_gb=64.0, cpu_count=16)
    add(n_rows_full=100_000, estimated_pair_count=1_000_000, available_ram_gb=64.0, cpu_count=16, bucket_available=True)
    add(n_rows_full=200_000, estimated_pair_count=10_000_000, available_ram_gb=32.0, cpu_count=8)
    add(n_rows_full=500_000, estimated_pair_count=40_000_000, available_ram_gb=48.0, cpu_count=8)
    # cpu_count cap: min(16, cpu_count)
    add(n_rows_full=100_000, estimated_pair_count=1_000_000, available_ram_gb=48.0, cpu_count=4)

    # ── Rule 3b: bucket_suggested ─────────────────────────────────────────────
    # n_rows in [100k, 750k], ram < 32, pairs < 50M, ram-safe; pair_spill_threshold=null
    add(n_rows_full=200_000, estimated_pair_count=100_000, available_ram_gb=16.0, cpu_count=8, bucket_available=True)
    add(n_rows_full=750_000, estimated_pair_count=100_000, available_ram_gb=16.0, cpu_count=8, bucket_available=True)
    add(n_rows_full=100_000, estimated_pair_count=5_000_000, available_ram_gb=20.0, cpu_count=4, bucket_available=True)
    # Upper boundary exclusive: 750_001 is NOT in the band
    # (falls to no_rule_matched with 20GB and small pairs)
    add(n_rows_full=750_001, estimated_pair_count=100_000, available_ram_gb=16.0, cpu_count=8)

    # ── Rule 4: chunked ───────────────────────────────────────────────────────
    # pairs in [50M, 5B), ram >= 16GB; pair_spill_threshold="ram"
    add(n_rows_full=5_000_000, estimated_pair_count=100_000_000, available_ram_gb=32.0, cpu_count=16)
    add(n_rows_full=5_000_000, estimated_pair_count=50_000_000, available_ram_gb=16.0, cpu_count=8)
    add(n_rows_full=10_000_000, estimated_pair_count=4_999_999_999, available_ram_gb=64.0, cpu_count=16)
    # cpu_count cap
    add(n_rows_full=5_000_000, estimated_pair_count=200_000_000, available_ram_gb=32.0, cpu_count=4)
    # boundary: pairs == 50M (not < 50M, so simple and fast_box miss; chunked fires)
    add(n_rows_full=50_000, estimated_pair_count=50_000_000, available_ram_gb=32.0, cpu_count=8)

    # ── Rule 6: ray ──────────────────────────────────────────────────────────
    # n_rows >= 50M, ray_auto_select=True, ray_available=True; pair_spill_threshold="disk_per_worker"
    # Use pairs >= 5B so chunked is skipped (chunked requires pairs < 5B)
    add(
        n_rows_full=50_000_000, estimated_pair_count=6_000_000_000,
        available_ram_gb=64.0, cpu_count=16,
        ray_available=True, ray_auto_select=True,
    )
    add(
        n_rows_full=100_000_000, estimated_pair_count=10_000_000_000,
        available_ram_gb=64.0, cpu_count=32,
        ray_available=True, ray_auto_select=True,
    )
    # ray with ray_auto_select=False falls through to duckdb
    add(
        n_rows_full=50_000_000, estimated_pair_count=6_000_000_000,
        available_ram_gb=64.0, cpu_count=16,
        ray_available=True, ray_auto_select=False,
    )
    # ray with ray_available=False falls through to duckdb
    add(
        n_rows_full=50_000_000, estimated_pair_count=6_000_000_000,
        available_ram_gb=64.0, cpu_count=16,
        ray_available=False, ray_auto_select=True,
    )

    # ── Rule 5: duckdb ────────────────────────────────────────────────────────
    # pairs >= 5B OR ram < 16GB; pair_spill_threshold="duckdb"
    add(n_rows_full=10_000_000, estimated_pair_count=5_000_000_000, available_ram_gb=64.0, cpu_count=16)
    add(n_rows_full=1_000_000, estimated_pair_count=1_000_000, available_ram_gb=14.0, cpu_count=4)
    add(n_rows_full=1_000_000, estimated_pair_count=5_000_000_000, available_ram_gb=8.0, cpu_count=8)
    # min(DUCKDB_MAX_WORKERS, cpu_count) = min(8, cpu_count)
    add(n_rows_full=1_000_000, estimated_pair_count=5_000_000_000, available_ram_gb=64.0, cpu_count=4)
    add(n_rows_full=1_000_000, estimated_pair_count=5_000_000_000, available_ram_gb=64.0, cpu_count=16)

    # ── no_rule_matched fallback ──────────────────────────────────────────────
    # (n_rows > 750k so bucket_suggested misses; pairs < 50M so chunked misses;
    #  ram >= 16 so duckdb misses -- a gap in the rule table)
    add(n_rows_full=800_000, estimated_pair_count=5_000_000, available_ram_gb=20.0, cpu_count=8)
    add(n_rows_full=760_000, estimated_pair_count=10_000_000, available_ram_gb=20.0, cpu_count=8)

    # ── Rule 7 (user_override) ─────────────────────────────────────────────────
    # user_backend set; fires before every other rule; pair_spill_threshold=null
    add(n_rows_full=50_000, estimated_pair_count=1_000, available_ram_gb=64.0, cpu_count=16,
        user_backend="polars-direct")
    add(n_rows_full=50_000, estimated_pair_count=1_000, available_ram_gb=64.0, cpu_count=16, bucket_available=True,
        user_backend="bucket")
    add(n_rows_full=50_000, estimated_pair_count=1_000, available_ram_gb=64.0, cpu_count=16,
        user_backend="duckdb")
    add(n_rows_full=50_000, estimated_pair_count=1_000, available_ram_gb=64.0, cpu_count=16,
        ray_available=True, ray_auto_select=True, user_backend="ray")
    # user_backend="chunked" -- chunk_size should be non-null
    add(n_rows_full=500_000, estimated_pair_count=1_000_000, available_ram_gb=64.0, cpu_count=16,
        user_backend="chunked")
    add(n_rows_full=500_000, estimated_pair_count=1_000_000, available_ram_gb=16.0, cpu_count=4,
        user_backend="chunked")
    # user_override beats pathological (n_rows=0)
    add(n_rows_full=0, estimated_pair_count=0, available_ram_gb=64.0, cpu_count=16,
        user_backend="ray")
    # user_override with cpu_count cap (min(16, cpu_count))
    add(n_rows_full=50_000, estimated_pair_count=1_000, available_ram_gb=64.0, cpu_count=4,
        user_backend="polars-direct")

    # ── Extra boundary vectors ─────────────────────────────────────────────────
    # simple at exactly cpu_count=1
    add(n_rows_full=10_000, estimated_pair_count=100_000, available_ram_gb=64.0, cpu_count=1)
    # fast_box vs bucket_suggested boundary (ram=31.9 vs ram=32.0)
    add(n_rows_full=200_000, estimated_pair_count=5_000_000, available_ram_gb=31.9, cpu_count=8, bucket_available=True)
    add(n_rows_full=200_000, estimated_pair_count=5_000_000, available_ram_gb=32.0, cpu_count=8, bucket_available=True)
    # chunked boundary pair count (exactly 50M -- falls into chunked, not simple/fast_box)
    add(n_rows_full=100_000, estimated_pair_count=50_000_000, available_ram_gb=32.0, cpu_count=8)
    # duckdb boundary at exactly 5B pairs
    add(n_rows_full=5_000_000, estimated_pair_count=5_000_000_000, available_ram_gb=64.0, cpu_count=16)

    return vectors


# ── Classifier vectors ──────────────────────────────────────────────────────────

def gen_classifier_vectors() -> list[dict]:
    """Generate classifier vectors by calling profile_columns on one-column frames."""
    from goldenmatch.core.autoconfig import profile_columns

    vectors: list[dict] = []

    def add_col(col_name: str, col_values: list[str], dtype_hint: str = "Utf8") -> None:
        """Call profile_columns on a one-column frame and emit {input, expected}."""
        # Build a polars frame with the given values
        df = pl.DataFrame({col_name: col_values})

        profiles = profile_columns(df, sample_size=len(col_values) + 1)
        assert len(profiles) == 1, f"Expected 1 profile for {col_name}, got {len(profiles)}"
        p = profiles[0]

        # Reconstruct full non-null values list (mirroring what profile_columns does)
        col_series = df[col_name]
        vals = [
            str(v) for v in col_series.drop_nulls().to_list()
            if v is not None and str(v).strip()
        ]

        # Compute the needs_llm_escalation flag (from _llm_classify_columns predicate)
        high_confidence_types = {"date", "geo", "email", "identifier"}
        needs_llm = (
            (p.confidence < 0.8 or p.col_type in ("string", "numeric"))
            and p.col_type not in high_confidence_types
        )

        null_count = col_series.null_count()
        total_rows = col_series.len()
        null_rate = null_count / total_rows if total_rows > 0 else 0.0
        cardinality_ratio = len(set(vals)) / total_rows if total_rows > 0 else 0.0
        avg_len = sum(len(v) for v in vals) / len(vals) if vals else 0.0

        input_dict = {
            "name": p.name,
            "dtype": str(df[col_name].dtype),
            "sample_values": vals,
            "null_rate": null_rate,
            "cardinality_ratio": cardinality_ratio,
            "avg_len": avg_len,
        }
        expected_dict = {
            "name": p.name,
            "dtype": str(df[col_name].dtype),
            "col_type": p.col_type,
            "confidence": p.confidence,
            "null_rate": null_rate,
            "cardinality_ratio": cardinality_ratio,
            "avg_len": avg_len,
            "needs_llm_escalation": needs_llm,
        }

        vec = {"input": input_dict, "expected": expected_dict}
        # Round-trip assertion
        round_tripped = json.loads(json.dumps(vec))
        assert round_tripped == vec, f"Round-trip mismatch for column {col_name}"
        vectors.append(vec)

    # ── email ──────────────────────────────────────────────────────────────────
    add_col("email_address", [f"user{i}@example.com" for i in range(20)])
    add_col("contact_email", [f"a{i}@b.org" for i in range(15)])

    # ── name ──────────────────────────────────────────────────────────────────
    add_col("first_name", ["Alice", "Bob", "Carol", "Dave", "Eve", "Frank", "Grace", "Hank", "Iris", "Jack"])
    add_col("last_name", ["Smith", "Jones", "Brown", "White", "Green", "Hall", "Lee", "King", "Scott", "Hill"])
    # name matched by data (no name pattern in col_name, but values look like names)
    add_col("foobar_col", ["Alice Smith", "Bob Jones", "Carol White", "Dave Brown",
                            "Eve Green", "Frank Hall", "Grace Lee", "Hank King"])

    # ── phone ──────────────────────────────────────────────────────────────────
    add_col("phone_number", ["5551234567", "4155556789", "2125559876", "7185554321",
                              "9175551234", "3125558765", "8005553456", "6175559988",
                              "9495554321", "2015551234"])
    add_col("mobile", ["(555) 123-4567", "(415) 555-6789", "(212) 555-9876", "(718) 555-4321",
                        "(917) 555-1234", "(312) 555-8765", "(800) 555-3456", "(617) 555-9988"])

    # ── zip ────────────────────────────────────────────────────────────────────
    add_col("zip_code", ["12345", "90210", "10001", "60601", "33101", "94105", "20001",
                          "11201", "77001", "30301"])
    add_col("postal", ["10001", "10002", "10003", "10004", "10005", "10006", "10007", "10008"])

    # ── address ────────────────────────────────────────────────────────────────
    add_col("street_address", [
        "123 Main St", "456 Oak Ave", "789 Pine Rd", "321 Elm Dr",
        "654 Maple Blvd", "987 Cedar Ln", "111 First Ct", "222 Second Way",
        "333 Third Pl", "444 Fourth Cir",
    ])
    add_col("address_line_1", [
        "1 Park Ave", "2 Broadway", "3 Fifth Ave", "4 Madison Ave",
        "5 Lexington Ave", "6 Park St", "7 Main Rd", "8 Oak Blvd",
    ])

    # ── geo (city / state / country) ──────────────────────────────────────────
    add_col("city", ["New York"] * 8 + ["Los Angeles"] * 7)
    add_col("state", ["NY", "CA", "TX", "FL", "WA", "OR", "IL", "GA", "PA", "OH"])
    add_col("country", ["USA"] * 8 + ["Canada"] * 7)
    add_col("state_cd", ["NY", "CA", "TX", "FL", "WA", "OR", "IL", "GA"])

    # ── identifier ────────────────────────────────────────────────────────────
    add_col("user_id", [str(i) for i in range(10000, 10020)])
    add_col("account_no", [f"ACC{i:06d}" for i in range(20)])
    add_col("recordID", [f"REC{i:08d}" for i in range(20)])
    # cardinality guard: near-unique numeric values -> identifier
    add_col("some_numeric_id", [str(i * 100 + 7) for i in range(15)])
    # S2a's identifier floor max(0.95, 1 - 1/sqrt(n)) only changes behavior at
    # SCALE (n > ~400, where the floor rises above 0.95); small-n behavior is
    # unchanged. The stricter-at-scale direction is covered by the Rust + Python
    # unit tests (large n would bloat these committed golden vectors), so no
    # S2a-specific classifier vectors are added here.

    # ── date ──────────────────────────────────────────────────────────────────
    add_col("created_at", ["2023-01-15"] * 10)  # name authoritative
    add_col("dob", ["1990-05-10", "1985-03-22", "1972-11-01", "2000-07-04",
                     "1988-12-25", "1995-06-15", "1965-09-03", "1978-02-28"])
    add_col("birth_date", ["01/15/1990", "03/22/1985", "11/01/1972", "07/04/2000",
                            "12/25/1988", "06/15/1995", "09/03/1965", "02/28/1978"])
    # mm/dd/yyyy dates -- data profiling catches these as dates
    add_col("registration_date", [
        "01/15/2023", "12/31/2022", "07/04/2021", "03/17/2020",
        "11/11/2019", "05/05/2018", "08/08/2017", "02/14/2016",
    ])

    # ── year ──────────────────────────────────────────────────────────────────
    add_col("birth_year", ["1990", "1985", "1972", "2000", "1988", "1995", "1965", "1978"])
    add_col("publication_year", ["2001", "2010", "1999", "2023", "1985", "2015", "2007", "2019"])
    # Float-promoted year column
    add_col("year_col", ["2001.0", "2010.0", "1999.0", "2023.0", "1985.0", "2015.0", "2007.0"])

    # ── numeric ────────────────────────────────────────────────────────────────
    add_col("price", ["10.99", "24.99", "5.49", "199.99", "0.99", "49.99", "12.50", "75.00"])
    add_col("total_amount", ["1.5", "2.7", "100", "42.0", "0", "999", "-3.14", "50.25"])

    # ── description ───────────────────────────────────────────────────────────
    long_text = "This is a very long description that exceeds fifty characters easily here, providing context."
    add_col("notes", [long_text] * 8)
    add_col("abstract", ["A very detailed analysis of the molecular structure and behavior of protein complexes in solution."] * 6)

    # ── multi_name ────────────────────────────────────────────────────────────
    multi = "Smith, John; Doe, Jane; Bloom, Alice; Roberts, Tom; Williams, Bob"
    add_col("authors", [multi] * 10)

    # ── string (generic / ambiguous) ──────────────────────────────────────────
    add_col("category", ["foo123", "bar456", "baz789", "qux000", "xyz111",
                          "abc222", "def333", "ghi444"])
    add_col("tag", ["alpha1", "beta2", "gamma3", "delta4", "epsilon5",
                     "zeta6", "eta7", "theta8"])

    # ── lookbehind edge cases ──────────────────────────────────────────────────
    # "city" matches geo ((?<![a-z])city); "municipality" should NOT
    add_col("city_name", ["New York"] * 10)   # city in col name -> geo
    # "recordId" vs "recordID" (case sensitivity in ID pattern)
    add_col("record_id", [f"R{i:06d}" for i in range(15)])
    add_col("guid_col", [f"{i:08x}-{i:04x}-{i:04x}-{i:04x}-{i:012x}" for i in range(15)])
    add_col("uuid", [f"{i:08x}-{i:04x}-{i:04x}-{i:04x}-{i:012x}" for i in range(15)])

    # ── name column with data disagreement (name=phone, data=email) ───────────
    # The merge rules will resolve this; let profile_columns decide
    add_col("phone_contact", [f"user{i}@domain.com" for i in range(15)])

    # ── county (lookbehind: not inside a word) ─────────────────────────────────
    add_col("county", ["Bronx", "Queens", "Brooklyn", "Manhattan", "Staten Island",
                        "Nassau", "Suffolk", "Westchester", "Rockland", "Orange"])

    return vectors


# ── S1 extrapolation vectors ─────────────────────────────────────────────────────

def gen_extrapolation_vectors() -> list[dict]:
    """S1 (spec 2026-06-22): drive the REAL BlockingProfile.extrapolate_to oracle
    across a grid covering every branch (linear fallback, Chao1 richness, pairs
    cap, n_blocks cap, noop, identity). The Rust extrapolate_pair_count kernel
    must reproduce these byte-for-byte."""
    from goldenmatch.core.complexity_profile import BlockingProfile

    # (total_comparisons, n_blocks, singleton_block_count, chao1_f1, chao1_f2,
    #  n_rows_sample, n_rows_full)
    cases: list[tuple] = [
        # linear fallback (chao1 None) + realistic quadratic (cap inert)
        (100, 10, 0, None, None, 1_000, 100_000),
        (5_000, 50, 2, None, None, 2_000, 200_000),
        (50, 5, 1, None, None, 1_000, 10_000),
        (0, 0, 0, None, None, 1_000, 100_000),
        (2_000_000, 500, 10, None, None, 20_000, 1_000_000),
        # identity (ns == nf)
        (5_000, 10, 2, None, None, 2_000, 2_000),
        (5_000, 10, 2, 3, 1, 2_000, 2_000),  # Chao1 still adds richness at ratio 1
        # all-pairs PAIRS cap: pathological tc > C(ns,2)=45
        (50, 2, 0, None, None, 10, 20),
        (100, 2, 0, None, None, 10, 20),
        (45, 2, 0, None, None, 10, 20),  # exactly C(10,2): still inert (raw=180<190)
        # Chao1 present (various)
        (100, 50, 0, 10, 5, 1_000, 100_000),
        (100, 50, 0, 0, 0, 1_000, 100_000),  # measured-zero saturation
        (1_000, 100, 0, 20, 3, 5_000, 1_000_000),
        (500, 30, 0, 5, 2, 1_000, 50_000),
        (2_000_000, 500, 10, 50, 10, 20_000, 1_000_000),
        # Chao1 n_blocks cap (richness exceeds n_full)
        (10, 10, 0, 1_000, 0, 2_000, 50),
        (10, 5, 0, 500, 1, 1_000, 100),
        # singleton scaling
        (5_000, 50, 7, None, None, 2_000, 200_000),
        (5_000, 50, 7, 4, 2, 2_000, 200_000),
        # noop (bad args)
        (10, 5, 0, None, None, 0, 100),
        (10, 5, 0, None, None, 100, 0),
        (10, 5, 0, 3, 1, 0, 0),
    ]
    # Grid top-up for extra variety (keeps the count comfortably above 30).
    for tc in (250, 7_500):
        for f1, f2 in ((None, None), (8, 3)):
            for ns, nf in ((1_000, 50_000), (4_000, 800_000), (2_000, 2_000)):
                cases.append((tc, 40, 3, f1, f2, ns, nf))

    vectors: list[dict] = []
    for tc, nb, sbc, f1, f2, ns, nf in cases:
        bp = BlockingProfile(
            n_blocks=nb,
            total_comparisons=tc,
            singleton_block_count=sbc,
            chao1_f1=f1,
            chao1_f2=f2,
        )
        out = bp.extrapolate_to(ns, nf)
        vec = {
            "input": {
                "total_comparisons": tc,
                "n_blocks": nb,
                "singleton_block_count": sbc,
                "chao1_f1": f1,  # None -> JSON null
                "chao1_f2": f2,
                "n_rows_sample": ns,
                "n_rows_full": nf,
            },
            "expected": {
                "n_blocks": out.n_blocks,
                "total_comparisons": out.total_comparisons,
                "singleton_block_count": out.singleton_block_count,
            },
        }
        round_tripped = json.loads(json.dumps(vec))
        assert round_tripped == vec, f"Round-trip mismatch for vector: {vec}"
        vectors.append(vec)
    return vectors


# ── S2b sparse-match floor vectors ────────────────────────────────────────────────

def gen_sparse_match_floor_vectors() -> list[dict]:
    """S2b (spec 2026-06-22): drive the REAL pure-Python sparse_match_floor oracle.
    The Rust `sparse_match_floor` kernel must reproduce these byte-for-byte."""
    from goldenmatch.core.indicators import _sparse_match_floor_py

    estimated = [
        0, 1, 50, 99, 100, 101, 499, 500, 999, 1_000, 1_001,
        4_899, 4_900, 4_999, 5_000, 5_001, 10_000, 50_000,
        1_000_000, 100_000_000, 9_007_199_254_740_991,  # JS MAX_SAFE_INTEGER
    ]
    vectors: list[dict] = []
    for ep in estimated:
        floor = _sparse_match_floor_py(ep)
        vec = {"input": {"estimated_pairs": ep}, "expected": {"floor": floor}}
        round_tripped = json.loads(json.dumps(vec))
        assert round_tripped == vec, f"Round-trip mismatch for vector: {vec}"
        vectors.append(vec)
    return vectors


# ── S3 exact-matchkey floor vectors ────────────────────────────────────────────────

def gen_exact_matchkey_floor_vectors() -> list[dict]:
    """S3 (spec 2026-06-22): drive the REAL pure-Python exact_matchkey_floor oracle.
    The Rust `exact_matchkey_floor` kernel must reproduce these byte-for-byte."""
    from goldenmatch.core.autoconfig import _exact_matchkey_floor_py

    # All 13 core ColType serde names + a couple of unknown/edge strings.
    col_types = [
        "email", "name", "phone", "zip", "address", "geo", "identifier",
        "description", "numeric", "date", "string", "year", "multi_name",
        "totally_unknown_type", "",
    ]
    vectors: list[dict] = []
    for ct in col_types:
        floor = _exact_matchkey_floor_py(ct)
        vec = {"input": {"col_type": ct}, "expected": {"floor": floor}}
        round_tripped = json.loads(json.dumps(vec))
        assert round_tripped == vec, f"Round-trip mismatch for vector: {vec}"
        vectors.append(vec)
    return vectors


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    print("Generating planner vectors...")
    planner_vectors = gen_planner_vectors()
    print(f"  Generated {len(planner_vectors)} planner vectors")
    assert len(planner_vectors) >= 40, f"Expected >= 40 planner vectors, got {len(planner_vectors)}"

    print("Generating classifier vectors...")
    classifier_vectors = gen_classifier_vectors()
    print(f"  Generated {len(classifier_vectors)} classifier vectors")
    assert len(classifier_vectors) >= 30, f"Expected >= 30 classifier vectors, got {len(classifier_vectors)}"

    print("Generating extrapolation vectors...")
    extrapolation_vectors = gen_extrapolation_vectors()
    print(f"  Generated {len(extrapolation_vectors)} extrapolation vectors")
    assert len(extrapolation_vectors) >= 30, (
        f"Expected >= 30 extrapolation vectors, got {len(extrapolation_vectors)}"
    )

    print("Generating sparse-match-floor vectors...")
    sparse_floor_vectors = gen_sparse_match_floor_vectors()
    print(f"  Generated {len(sparse_floor_vectors)} sparse-match-floor vectors")
    assert len(sparse_floor_vectors) >= 15, (
        f"Expected >= 15 sparse-match-floor vectors, got {len(sparse_floor_vectors)}"
    )

    print("Generating exact-matchkey-floor vectors...")
    matchkey_floor_vectors = gen_exact_matchkey_floor_vectors()
    print(f"  Generated {len(matchkey_floor_vectors)} exact-matchkey-floor vectors")
    assert len(matchkey_floor_vectors) >= 13, (
        f"Expected >= 13 exact-matchkey-floor vectors, got {len(matchkey_floor_vectors)}"
    )

    # Write planner vectors
    planner_path = OUT_DIR / "planner_vectors.json"
    with open(planner_path, "w", encoding="utf-8") as f:
        json.dump(planner_vectors, f, indent=2)
    print(f"Wrote {planner_path}")

    # Write classifier vectors
    classifier_path = OUT_DIR / "classifier_vectors.json"
    with open(classifier_path, "w", encoding="utf-8") as f:
        json.dump(classifier_vectors, f, indent=2)
    print(f"Wrote {classifier_path}")

    # Write extrapolation vectors
    extrapolation_path = OUT_DIR / "extrapolation_vectors.json"
    with open(extrapolation_path, "w", encoding="utf-8") as f:
        json.dump(extrapolation_vectors, f, indent=2)
    print(f"Wrote {extrapolation_path}")

    # Write sparse-match-floor vectors
    sparse_floor_path = OUT_DIR / "sparse_match_floor_vectors.json"
    with open(sparse_floor_path, "w", encoding="utf-8") as f:
        json.dump(sparse_floor_vectors, f, indent=2)
    print(f"Wrote {sparse_floor_path}")

    # Write exact-matchkey-floor vectors
    matchkey_floor_path = OUT_DIR / "exact_matchkey_floor_vectors.json"
    with open(matchkey_floor_path, "w", encoding="utf-8") as f:
        json.dump(matchkey_floor_vectors, f, indent=2)
    print(f"Wrote {matchkey_floor_path}")

    # Final summary
    print(
        f"\nDone. {len(planner_vectors)} planner + {len(classifier_vectors)} classifier "
        f"+ {len(extrapolation_vectors)} extrapolation + {len(sparse_floor_vectors)} "
        f"sparse-match-floor + {len(matchkey_floor_vectors)} exact-matchkey-floor vectors."
    )


if __name__ == "__main__":
    main()
