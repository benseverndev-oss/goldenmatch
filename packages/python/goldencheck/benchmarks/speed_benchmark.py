import time
import tracemalloc
import tempfile
from pathlib import Path
import polars as pl
from goldencheck.engine.scanner import scan_file

def generate_dataset(n_rows: int, path: Path):
    """Generate a realistic dataset with intentional quality issues."""
    import random
    random.seed(42)

    data = {
        "id": list(range(n_rows)),
        "name": [f"Person {i}" for i in range(n_rows)],
        "email": [f"user{i}@test.com" if random.random() > 0.05 else "bad-email" for i in range(n_rows)],
        "age": [random.randint(18, 80) if random.random() > 0.02 else 999 for _ in range(n_rows)],
        "status": [random.choice(["active", "inactive", "pending"]) if random.random() > 0.03 else "UNKNOWN" for _ in range(n_rows)],
        "price": [round(random.uniform(1, 500), 2) if random.random() > 0.01 else 99999.99 for _ in range(n_rows)],
        "created": [f"2024-{random.randint(1,12):02d}-{random.randint(1,28):02d}" for _ in range(n_rows)],
        "phone": [f"({random.randint(100,999)}) {random.randint(100,999)}-{random.randint(1000,9999)}" if random.random() > 0.1 else f"{random.randint(1000000000, 9999999999)}" for _ in range(n_rows)],
    }
    # Add some nulls
    for i in random.sample(range(n_rows), min(n_rows // 20, n_rows)):
        data["email"][i] = None

    df = pl.DataFrame(data)
    df.write_csv(path)

def run_speed_benchmark():
    sizes = [1_000, 10_000, 100_000, 1_000_000]
    results = []

    for n in sizes:
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            tmp_path = Path(f.name)

        print(f"\nGenerating {n:,} rows...")
        generate_dataset(n, tmp_path)
        file_size_mb = tmp_path.stat().st_size / (1024 * 1024)

        print(f"Scanning {n:,} rows ({file_size_mb:.1f} MB)...")
        tracemalloc.start()
        start = time.perf_counter()

        findings, profile = scan_file(tmp_path)

        elapsed = time.perf_counter() - start
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        rows_per_sec = n / elapsed
        results.append({
            "rows": n,
            "time_s": elapsed,
            "memory_mb": peak / (1024 * 1024),
            "rows_per_sec": rows_per_sec,
            "findings": len(findings),
            "file_mb": file_size_mb,
        })

        print(f"  Time: {elapsed:.2f}s | Memory: {peak/(1024*1024):.1f} MB | {rows_per_sec:,.0f} rows/sec | {len(findings)} findings")

        tmp_path.unlink()

    # Print summary table
    print(f"\n{'='*80}")
    print(f"{'SPEED BENCHMARK RESULTS':^80}")
    print(f"{'='*80}")
    print(f"{'Rows':<12} {'File MB':<10} {'Time (s)':<12} {'Memory (MB)':<14} {'Rows/sec':<14} {'Findings'}")
    print(f"{'-'*80}")
    for r in results:
        print(f"{r['rows']:>10,}  {r['file_mb']:>8.1f}  {r['time_s']:>10.2f}  {r['memory_mb']:>12.1f}  {r['rows_per_sec']:>12,.0f}  {r['findings']:>8}")
    print(f"{'='*80}")

if __name__ == "__main__":
    run_speed_benchmark()
